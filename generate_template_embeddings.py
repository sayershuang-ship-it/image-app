#!/usr/bin/env python3
"""
generate_template_embeddings.py — one-time (and re-runnable) batch job that
embeds every templates row via local Ollama (bge-m3) and stores the vector
in templates.embedding, for /api/templates/search to rank against.

Prerequisite: `ollama pull bge-m3` (once per machine) and the Ollama daemon
running locally (http://localhost:11434).

Run: venv/bin/python generate_template_embeddings.py [--dry-run N]

Safe to re-run: rows that already have an embedding are skipped.
"""

import argparse
import json
import os
import sqlite3

from app import embed_text

DB_PATH = os.path.join(os.path.dirname(__file__), "prompts.db")


def get_rows_without_embedding(conn: sqlite3.Connection, limit=None) -> list:
    conn.row_factory = sqlite3.Row
    query = ("SELECT id, title, category, prompt FROM templates "
             "WHERE embedding IS NULL")
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    return conn.execute(query).fetchall()


def run_embedding_batch(conn: sqlite3.Connection, limit=None) -> tuple:
    rows = get_rows_without_embedding(conn, limit=limit)
    succeeded, failed = 0, 0
    for row in rows:
        text = f"{row['title']} {row['category']} {row['prompt']}"
        try:
            vector = embed_text(text)
        except Exception as exc:
            print(f"  [embedding failed] id={row['id']} — {exc}")
            failed += 1
            continue
        conn.execute(
            "UPDATE templates SET embedding = ? WHERE id = ?",
            (json.dumps(vector).encode(), row["id"]),
        )
        conn.commit()
        succeeded += 1
    return succeeded, failed


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=int, default=None,
                         help="Only embed the first N rows missing an embedding")
    args = parser.parse_args(argv)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        succeeded, failed = run_embedding_batch(conn, limit=args.dry_run)
        total = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        still_missing = conn.execute(
            "SELECT COUNT(*) FROM templates WHERE embedding IS NULL").fetchone()[0]
        print(f"Embedded {succeeded} rows, {failed} failed. "
              f"{total} total rows, {still_missing} still missing an embedding. "
              f"Re-run this script to retry those.")


if __name__ == "__main__":
    main()
