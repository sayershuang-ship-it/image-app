import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module
import migrate_templates as mt


def _fresh_db():
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    app_module.DB_PATH = db_path
    app_module.init_db()
    return db_path


def test_insert_skeleton_rows_is_idempotent():
    db_path = _fresh_db()
    official = [
        {"tab": "Design & Info", "subcategory": "UI & Interface",
         "title": "Standard", "prompt": "Generate a [platform] UI..."},
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO community_prompts (title, category, prompt) VALUES (?, ?, ?)",
            ("Test Community Row", "人像攝影", "A photo of a cat"),
        )
        community_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.row_factory = sqlite3.Row
        community_rows = conn.execute("SELECT * FROM community_prompts").fetchall()

        inserted_first = mt.insert_skeleton_rows(conn, official, community_rows)
        assert inserted_first == 2  # 1 official + 1 community

        # Re-running with the same input must not duplicate rows
        inserted_second = mt.insert_skeleton_rows(conn, official, community_rows)
        assert inserted_second == 0

        total = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        assert total == 2

        official_row = conn.execute(
            "SELECT * FROM templates WHERE source='official'"
        ).fetchone()
        assert official_row["prompt"] == "Generate a [platform] UI..."
        assert official_row["category"] == ""
        assert official_row["thumbnail_prompt"] == ""

        community_row = conn.execute(
            "SELECT * FROM templates WHERE source='community'"
        ).fetchone()
        assert community_row["community_prompt_id"] == community_id
        assert community_row["prompt"] == "A photo of a cat"
