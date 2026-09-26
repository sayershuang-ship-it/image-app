#!/usr/bin/env python3
"""
migrate_images_to_files.py — one-time migration moving generated images out of
the `prompts.result_b64` column into files under images/ (next to the DB),
with 256px JPEG thumbnails under images/thumbs/. Also shrinks full-size
reference uploads stored on failed rows (success=0) to thumbnails.

Run:
  venv/bin/python migrate_images_to_files.py                    # dry run (default)
  venv/bin/python migrate_images_to_files.py --apply --vacuum   # real run

--apply refuses to run unless <db>.bak-before-images exists.
Safe to re-run: only rows with result_b64 set and result_path unset are migrated.
"""

import argparse
import base64
import io
import os
import sqlite3
import sys

from PIL import Image

import app as app_module

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.db")
COMMIT_EVERY = 20


def _is_larger_than_thumb(b64: str) -> bool:
    try:
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
    except Exception:
        return False
    return max(img.size) > app_module.THUMB_MAX


def _pending_result_ids(conn) -> list:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(prompts)").fetchall()}
    where = "result_b64 IS NOT NULL"
    if "result_path" in cols:
        where += " AND result_path IS NULL"
    return [r[0] for r in conn.execute(f"SELECT id FROM prompts WHERE {where} ORDER BY id")]


def _pending_failure_ids(conn) -> list:
    ids = []
    for (pid,) in conn.execute(
            "SELECT id FROM prompts WHERE success=0 AND image_b64 IS NOT NULL ORDER BY id").fetchall():
        b64 = conn.execute("SELECT image_b64 FROM prompts WHERE id=?", (pid,)).fetchone()[0]
        if _is_larger_than_thumb(b64):
            ids.append(pid)
    return ids


def dry_run(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result_ids = _pending_result_ids(conn)
        b64_bytes = 0
        for pid in result_ids:
            b64_bytes += conn.execute(
                "SELECT LENGTH(result_b64) FROM prompts WHERE id=?", (pid,)).fetchone()[0]
        failure_ids = _pending_failure_ids(conn)
    finally:
        conn.close()
    return {"rows": len(result_ids), "b64_bytes": b64_bytes, "failure_rows": len(failure_ids)}


def apply(db_path: str) -> dict:
    app_module.DB_PATH = db_path
    app_module.init_db()  # adds result_path / thumb_path columns if absent
    images_dir = app_module._images_dir()
    migrated = failed = bytes_moved = thumbed = 0
    conn = sqlite3.connect(db_path)
    try:
        for i, pid in enumerate(_pending_result_ids(conn), 1):
            b64 = conn.execute("SELECT result_b64 FROM prompts WHERE id=?", (pid,)).fetchone()[0]
            try:
                img_bytes = base64.b64decode(b64)
                result_path, thumb_path = app_module._write_image_files(pid, img_bytes)
                with open(os.path.join(images_dir, result_path), "rb") as f:
                    if f.read() != img_bytes:
                        raise ValueError("written file does not match decoded result_b64")
            except Exception as exc:
                print(f"  [skip] id={pid}: {exc}; row left unchanged")
                failed += 1
                continue
            conn.execute(
                "UPDATE prompts SET result_path=?, thumb_path=?, result_b64=NULL WHERE id=?",
                (result_path, thumb_path, pid))
            migrated += 1
            bytes_moved += len(img_bytes)
            if i % COMMIT_EVERY == 0:
                conn.commit()
        conn.commit()

        for pid in _pending_failure_ids(conn):
            b64 = conn.execute("SELECT image_b64 FROM prompts WHERE id=?", (pid,)).fetchone()[0]
            thumb = app_module.make_thumbnail(b64)
            if thumb != b64:
                conn.execute("UPDATE prompts SET image_b64=? WHERE id=?", (thumb, pid))
                thumbed += 1
        conn.commit()
    finally:
        conn.close()
    return {"rows": migrated, "failed": failed, "bytes_moved": bytes_moved,
            "failure_rows": thumbed}


def vacuum(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=DEFAULT_DB, help="path to prompts.db")
    parser.add_argument("--apply", action="store_true",
                        help="actually migrate (default is a dry run)")
    parser.add_argument("--vacuum", action="store_true",
                        help="run VACUUM at the end (only with --apply)")
    args = parser.parse_args(argv)
    db_path = os.path.abspath(args.db)

    if not os.path.isfile(db_path):
        print(f"Error: database not found: {db_path}", file=sys.stderr)
        return 1

    size_before = os.path.getsize(db_path)
    if not args.apply:
        stats = dry_run(db_path)
        print(f"[dry run] {stats['rows']} rows would be migrated "
              f"(~{stats['b64_bytes'] * 3 // 4} image bytes, {stats['b64_bytes']} base64 bytes).")
        print(f"[dry run] {stats['failure_rows']} failed rows have a full-size reference "
              f"image that would be thumbnailed.")
        print(f"[dry run] DB size: {size_before} bytes. Nothing was changed. "
              f"Re-run with --apply to migrate.")
        return 0

    backup = db_path + ".bak-before-images"
    if not os.path.exists(backup):
        print(f"Error: backup not found: {backup}", file=sys.stderr)
        print("Create it first with:", file=sys.stderr)
        print(f"  sqlite3 {db_path} \".backup '{backup}'\"", file=sys.stderr)
        return 2

    stats = apply(db_path)
    if args.vacuum:
        vacuum(db_path)
    size_after = os.path.getsize(db_path)
    print(f"Rows migrated: {stats['rows']} ({stats['failed']} skipped with errors).")
    print(f"Bytes moved to files: {stats['bytes_moved']}.")
    print(f"Failed rows with reference image thumbnailed: {stats['failure_rows']}.")
    print(f"DB size before: {size_before} bytes, after: {size_after} bytes"
          f"{'' if args.vacuum else ' (run with --vacuum to reclaim space)'}.")
    return 0 if stats["failed"] == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
