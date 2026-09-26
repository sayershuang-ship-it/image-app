import json
import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module
import generate_history_embeddings as ghe


def _fresh_db():
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    app_module.DB_PATH = db_path
    app_module.init_db()
    return db_path


def _insert(conn, pid, prompt, success=1, original=None, embedding=None):
    conn.execute(
        "INSERT INTO prompts (id, prompt, original_prompt, success, embedding) "
        "VALUES (?,?,?,?,?)",
        (pid, prompt, original, success,
         json.dumps(embedding).encode() if embedding is not None else None),
    )


def test_get_rows_without_embedding_only_successful_unembedded():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        _insert(conn, 1, "todo")
        _insert(conn, 2, "done", embedding=[0.1, 0.2])
        _insert(conn, 3, "failed", success=0)
        rows = ghe.get_rows_without_embedding(conn)
    assert [r["id"] for r in rows] == [1]


def test_run_embedding_batch_uses_original_prompt_and_skips_done_rows():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        _insert(conn, 1, "prompt + negative suffix", original="user words")
        _insert(conn, 2, "plain prompt")
        conn.commit()

        with patch("generate_history_embeddings.embed_text",
                   return_value=[0.1, 0.2, 0.3]) as mock_embed:
            succeeded, failed = ghe.run_embedding_batch(conn)

        assert (succeeded, failed) == (2, 0)
        assert [c.args[0] for c in mock_embed.call_args_list] == ["user words", "plain prompt"]
        row = conn.execute("SELECT embedding FROM prompts WHERE id=1").fetchone()
        assert json.loads(row[0]) == [0.1, 0.2, 0.3]

        with patch("generate_history_embeddings.embed_text") as mock_embed2:
            assert ghe.run_embedding_batch(conn) == (0, 0)
        mock_embed2.assert_not_called()


def test_run_embedding_batch_continues_past_a_failure():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        _insert(conn, 1, "bad")
        _insert(conn, 2, "good")
        conn.commit()

        def flaky_embed(text, model="bge-m3"):
            if text == "bad":
                raise ConnectionError("ollama down")
            return [0.5, 0.5]

        with patch("generate_history_embeddings.embed_text", side_effect=flaky_embed):
            assert ghe.run_embedding_batch(conn) == (1, 1)
        assert conn.execute("SELECT embedding FROM prompts WHERE id=1").fetchone()[0] is None
        assert json.loads(conn.execute(
            "SELECT embedding FROM prompts WHERE id=2").fetchone()[0]) == [0.5, 0.5]


def test_main_dry_run_limits_rows_and_uses_db_flag(capsys):
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        for i in range(1, 4):
            _insert(conn, i, f"p{i}")
    with patch("generate_history_embeddings.embed_text", return_value=[1.0]) as mock_embed:
        ghe.main(["--db", db_path, "--dry-run", "2"])
    assert mock_embed.call_count == 2
    out = capsys.readouterr().out
    assert "Embedded 2 rows, 0 failed" in out
    assert "1 still missing" in out


def test_main_refuses_unmigrated_db():
    db_path = os.path.join(tempfile.mkdtemp(), "old.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE prompts (id INTEGER PRIMARY KEY, prompt TEXT, success INTEGER)")
    with pytest.raises(SystemExit):
        ghe.main(["--db", db_path])
