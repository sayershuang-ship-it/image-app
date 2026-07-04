import json
import os
import sqlite3
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module
import generate_template_embeddings as gte


def _fresh_db():
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    app_module.DB_PATH = db_path
    app_module.init_db()
    return db_path


def test_get_rows_without_embedding_excludes_already_embedded():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt) "
            "VALUES (1, 'official', 'T1', 'p1', 'c', 'tp1')"
        )
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt, embedding) "
            "VALUES (2, 'official', 'T2', 'p2', 'c', 'tp2', ?)",
            (json.dumps([0.1, 0.2]).encode(),),
        )
        conn.row_factory = sqlite3.Row
        rows = gte.get_rows_without_embedding(conn)
    assert [r["id"] for r in rows] == [1]


def test_run_embedding_batch_stores_vector_and_skips_done_rows():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt) "
            "VALUES (1, 'official', 'T1', 'some prompt', 'UI', 'a concrete prompt')"
        )
        conn.commit()

        with patch("generate_template_embeddings.embed_text", return_value=[0.1, 0.2, 0.3]):
            succeeded, failed = gte.run_embedding_batch(conn)

        assert (succeeded, failed) == (1, 0)
        row = conn.execute("SELECT embedding FROM templates WHERE id=1").fetchone()
        assert json.loads(row[0]) == [0.1, 0.2, 0.3]

        # Re-running must not re-embed the same row.
        with patch("generate_template_embeddings.embed_text") as mock_embed:
            succeeded2, failed2 = gte.run_embedding_batch(conn)
        assert (succeeded2, failed2) == (0, 0)
        mock_embed.assert_not_called()


def test_run_embedding_batch_continues_past_a_failure():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt) "
            "VALUES (1, 'official', 'T1', 'p1', 'c', 'tp1')"
        )
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt) "
            "VALUES (2, 'official', 'T2', 'p2', 'c', 'tp2')"
        )
        conn.commit()

        def flaky_embed(text, model="bge-m3"):
            if "tp1" in text or "T1" in text:
                raise ConnectionError("ollama down")
            return [0.5, 0.5]

        with patch("generate_template_embeddings.embed_text", side_effect=flaky_embed):
            succeeded, failed = gte.run_embedding_batch(conn)

        assert (succeeded, failed) == (1, 1)
        row2 = conn.execute("SELECT embedding FROM templates WHERE id=2").fetchone()
        assert json.loads(row2[0]) == [0.5, 0.5]
        row1 = conn.execute("SELECT embedding FROM templates WHERE id=1").fetchone()
        assert row1[0] is None
