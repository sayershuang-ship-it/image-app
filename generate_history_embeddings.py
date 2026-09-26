#!/usr/bin/env python3
"""
generate_history_embeddings.py — backfill embeddings for successful rows in
the prompts (history) table via local Ollama (bge-m3), for
/api/history/search to rank against. New rows are embedded on save; this
covers rows created before that, or while Ollama was down.

Prerequisite: `ollama pull bge-m3` (once per machine) and the Ollama daemon
running locally (http://localhost:11434).

Run: venv/bin/python generate_history_embeddings.py [--db PATH] [--dry-run N]

Safe to re-run: rows that already have an embedding are skipped.
"""

import argparse
import json
import os
import sqlite3

from app import embed_text

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.db")


def get_rows_without_embedding(conn: sqlite3.Connection, limit=None) -> list:
    conn.row_factory = sqlite3.Row
    query = ("SELECT id, prompt, original_prompt FROM prompts "
             "WHERE success=1 AND embedding IS NULL ORDER BY id")
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    return conn.execute(query).fetchall()


def run_embedding_batch(conn: sqlite3.Connection, limit=None) -> tuple:
    rows = get_rows_without_embedding(conn, limit=limit)
    succeeded, failed = 0, 0
    for row in rows:
        text = row["original_prompt"] or row["prompt"]
        try:
            vector = embed_text(text)
        except Exception as exc:
            print(f"  [embedding failed] id={row['id']} — {exc}")
            failed += 1
            continue
        conn.execute(
            "UPDATE prompts SET embedding = ? WHERE id = ?",
            (json.dumps(vector).encode(), row["id"]),
        )
        conn.commit()
        succeeded += 1
    return succeeded, failed


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DB_PATH,
                        help="Path to the SQLite database (default: prompts.db next to this script)")
    parser.add_argument("--dry-run", type=int, default=None,
                        help="Only embed the first N rows missing an embedding")
    args = parser.parse_args(argv)

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(prompts)").fetchall()}
        if "embedding" not in cols:
            raise SystemExit("prompts.embedding column missing — start the app once "
                             "(init_db) to migrate the schema, then re-run.")
        succeeded, failed = run_embedding_batch(conn, limit=args.dry_run)
        total = conn.execute("SELECT COUNT(*) FROM prompts WHERE success=1").fetchone()[0]
        still_missing = conn.execute(
            "SELECT COUNT(*) FROM prompts WHERE success=1 AND embedding IS NULL").fetchone()[0]
        print(f"Embedded {succeeded} rows, {failed} failed. "
              f"{total} successful rows, {still_missing} still missing an embedding. "
              f"Re-run this script to retry those.")


if __name__ == "__main__":
    main()
