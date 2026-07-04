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


from unittest.mock import MagicMock


def test_classify_batch_parses_response_and_validates_category():
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = (
        '[{"id": 1, "category": "人像攝影 Portrait & Fashion Photography", '
        '"thumbnail_prompt": "A 35mm film portrait of a young woman in soft window light"},'
        '{"id": 2, "category": "not-a-real-category", "thumbnail_prompt": "x"}]'
    )
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    row1 = {"id": 1, "title": "T1", "prompt": "some prompt"}
    row2 = {"id": 2, "title": "T2", "prompt": "some other prompt"}
    results = mt.classify_batch(fake_client, [row1, row2])

    # Row 1: valid category, kept as-is.
    assert results[0] == {
        "id": 1,
        "category": "人像攝影 Portrait & Fashion Photography",
        "thumbnail_prompt": "A 35mm film portrait of a young woman in soft window light",
    }
    # Row 2: invalid category from the model, dropped rather than written.
    assert len(results) == 1


def test_apply_classification_results_updates_rows():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt)
               VALUES (1, 'official', 'T1', 'some prompt', '', '')"""
        )
        mt.apply_classification_results(conn, [
            {"id": 1, "category": "人像攝影 Portrait & Fashion Photography",
             "thumbnail_prompt": "A concrete rewritten prompt"},
        ])
        row = conn.execute("SELECT category, thumbnail_prompt FROM templates WHERE id=1").fetchone()
        assert row[0] == "人像攝影 Portrait & Fashion Photography"
        assert row[1] == "A concrete rewritten prompt"


def test_generate_thumbnail_writes_file_and_returns_true(tmp_path):
    fake_part = MagicMock()
    fake_part.inline_data.mime_type = "image/jpeg"
    fake_part.inline_data.data = b"\xff\xd8\xff\xe0fakejpegbytes"
    fake_candidate = MagicMock()
    fake_candidate.content.parts = [fake_part]
    fake_response = MagicMock()
    fake_response.candidates = [fake_candidate]

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response

    out_path = str(tmp_path / "1.jpg")
    ok = mt.generate_thumbnail(fake_client, "a cat sitting on a windowsill", out_path)

    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read() == b"\xff\xd8\xff\xe0fakejpegbytes"


def test_generate_thumbnail_returns_false_on_no_image_data(tmp_path):
    fake_response = MagicMock()
    fake_response.candidates = []
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response

    out_path = str(tmp_path / "2.jpg")
    ok = mt.generate_thumbnail(fake_client, "a prompt", out_path)

    assert ok is False
    assert not os.path.exists(out_path)


def test_run_thumbnail_batch_sets_thumbnail_path(tmp_path):
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt)
               VALUES (1, 'official', 'T1', 'p', 'cat', 'a concrete prompt')"""
        )
        conn.commit()

        fake_part = MagicMock()
        fake_part.inline_data.mime_type = "image/jpeg"
        fake_part.inline_data.data = b"\xff\xd8fakejpeg"
        fake_candidate = MagicMock()
        fake_candidate.content.parts = [fake_part]
        fake_response = MagicMock()
        fake_response.candidates = [fake_candidate]
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = fake_response

        succeeded, failed = mt.run_thumbnail_batch(conn, fake_client, str(tmp_path))

        assert (succeeded, failed) == (1, 0)
        row = conn.execute("SELECT thumbnail_path FROM templates WHERE id=1").fetchone()
        assert row[0] == "static/template_thumbs/1.jpg"
        assert os.path.exists(os.path.join(str(tmp_path), "1.jpg"))


def test_main_dry_run_limits_classification_and_thumbnail_work(monkeypatch, tmp_path):
    db_path = _fresh_db()
    monkeypatch.setattr(mt, "DB_PATH", db_path)
    monkeypatch.setattr(mt, "STATIC_THUMB_DIR", str(tmp_path))
    monkeypatch.setattr(mt, "OFFICIAL_JSON_PATH",
                         os.path.join(os.path.dirname(__file__), "..", "official_templates.json"))

    def fake_classify(client, batch):
        return [{"id": r["id"], "category": mt.CATEGORIES[0], "thumbnail_prompt": "a concrete prompt"}
                for r in batch]

    def fake_generate_thumbnail(client, prompt, out_path):
        with open(out_path, "wb") as f:
            f.write(b"fake")
        return True

    monkeypatch.setattr(mt, "classify_batch", fake_classify)
    monkeypatch.setattr(mt, "generate_thumbnail", fake_generate_thumbnail)
    monkeypatch.setattr(mt, "get_openai_client", lambda: MagicMock())
    monkeypatch.setattr(mt, "get_gemini_client", lambda: MagicMock())

    mt.main(["--dry-run", "3"])

    with sqlite3.connect(db_path) as conn:
        classified = conn.execute("SELECT COUNT(*) FROM templates WHERE category != ''").fetchone()[0]
        thumbnailed = conn.execute("SELECT COUNT(*) FROM templates WHERE thumbnail_path IS NOT NULL").fetchone()[0]
    assert classified == 3
    assert thumbnailed == 3
