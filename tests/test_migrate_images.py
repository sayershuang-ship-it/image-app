import base64
import io
import os
import sqlite3
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module
import migrate_images_to_files as mi


def _png_b64(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _legacy_db():
    """A DB in the pre-plan-021 shape: no result_path/thumb_path columns,
    images stored inline as base64."""
    db_path = os.path.join(tempfile.mkdtemp(), "prompts.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, prompt TEXT NOT NULL,
                image_b64 TEXT, result_b64 TEXT, success INTEGER DEFAULT 1,
                error_msg TEXT)
        """)
        for i in range(25):  # > one commit batch of 20
            conn.execute("INSERT INTO prompts (prompt, result_b64) VALUES (?, ?)",
                         (f"p{i}", _png_b64(40, 30)))
        conn.execute("INSERT INTO prompts (prompt, image_b64, success, error_msg) "
                     "VALUES ('failed', ?, 0, 'boom')", (_png_b64(2000, 2000),))
    return db_path


def _snapshot(db_path):
    with open(db_path, "rb") as f:
        return f.read()


def test_missing_db_is_clean_error(capsys):
    rc = mi.main(["--db", os.path.join(tempfile.mkdtemp(), "nope.db")])
    assert rc == 1
    assert "database not found" in capsys.readouterr().err


def test_dry_run_changes_nothing(capsys):
    db_path = _legacy_db()
    before = _snapshot(db_path)
    assert mi.main(["--db", db_path]) == 0
    out = capsys.readouterr().out
    assert "25 rows would be migrated" in out
    assert "1 failed rows" in out
    assert _snapshot(db_path) == before
    assert not os.path.exists(os.path.join(os.path.dirname(db_path), "images"))


def test_apply_without_backup_refuses(capsys):
    db_path = _legacy_db()
    before = _snapshot(db_path)
    assert mi.main(["--db", db_path, "--apply"]) == 2
    err = capsys.readouterr().err
    assert f"sqlite3 {db_path} \".backup '{db_path}.bak-before-images'\"" in err
    assert _snapshot(db_path) == before


def test_apply_with_backup_migrates_and_is_idempotent(capsys):
    db_path = _legacy_db()
    with sqlite3.connect(db_path) as conn:
        originals = dict(conn.execute("SELECT id, result_b64 FROM prompts WHERE success=1"))
    open(db_path + ".bak-before-images", "wb").close()

    assert mi.main(["--db", db_path, "--apply", "--vacuum"]) == 0
    out = capsys.readouterr().out
    assert "Rows migrated: 25 " in out
    assert "reference image thumbnailed: 1." in out

    images_dir = os.path.join(os.path.dirname(db_path), "images")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM prompts WHERE success=1").fetchall()
        failed = conn.execute("SELECT image_b64 FROM prompts WHERE success=0").fetchone()[0]
    for row in rows:
        assert row["result_b64"] is None
        with open(os.path.join(images_dir, row["result_path"]), "rb") as f:
            assert f.read() == base64.b64decode(originals[row["id"]])
        assert os.path.exists(os.path.join(images_dir, row["thumb_path"]))
    assert max(Image.open(io.BytesIO(base64.b64decode(failed))).size) <= app_module.THUMB_MAX

    assert mi.main(["--db", db_path, "--apply"]) == 0
    out = capsys.readouterr().out
    assert "Rows migrated: 0 " in out
    assert "reference image thumbnailed: 0." in out
