#!/usr/bin/env python3
"""
migrate_templates.py — one-time migration merging the hardcoded official
templates (official_templates.json) and community_prompts rows into the
unified `templates` table, with LLM-assigned categories and Gemini-generated
thumbnails.

Run: venv/bin/python migrate_templates.py [--dry-run N]

Safe to re-run: already-inserted rows are skipped (by community_prompt_id for
community rows, by (source, title) for official rows); already-classified
rows (category != '') are skipped; already-thumbnailed rows
(thumbnail_path IS NOT NULL) are skipped.
"""

import argparse
import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "prompts.db")
OFFICIAL_JSON_PATH = os.path.join(os.path.dirname(__file__), "official_templates.json")


def load_official_templates(json_path: str) -> list:
    with open(json_path) as f:
        return json.load(f)


def load_community_rows(db_path: str) -> list:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM community_prompts").fetchall()


def insert_skeleton_rows(conn: sqlite3.Connection, official: list, community: list) -> int:
    """Insert rows not already present. Returns count of rows newly inserted."""
    existing_community_ids = {
        r[0] for r in conn.execute(
            "SELECT community_prompt_id FROM templates WHERE community_prompt_id IS NOT NULL"
        ).fetchall()
    }
    existing_official_titles = {
        r[0] for r in conn.execute(
            "SELECT title FROM templates WHERE source='official'"
        ).fetchall()
    }

    inserted = 0
    for item in official:
        if item["title"] in existing_official_titles:
            continue
        conn.execute(
            """INSERT INTO templates (source, title, prompt, category, thumbnail_prompt)
               VALUES ('official', ?, ?, '', '')""",
            (item["title"], item["prompt"]),
        )
        existing_official_titles.add(item["title"])
        inserted += 1

    for row in community:
        if row["id"] in existing_community_ids:
            continue
        prompt_text = (row["prompt"] or "").strip() or row["title"]
        conn.execute(
            """INSERT INTO templates
                   (source, title, prompt, category, thumbnail_prompt,
                    platform, author, source_url, score, community_prompt_id)
               VALUES ('community', ?, ?, '', '', ?, ?, ?, ?, ?)""",
            (row["title"], prompt_text, row["platform"], row["author"],
             row["source_url"], row["score"], row["id"]),
        )
        existing_community_ids.add(row["id"])
        inserted += 1

    conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=int, default=None,
                         help="Only process the first N unclassified/unthumbnailed rows")
    args = parser.parse_args()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        official = load_official_templates(OFFICIAL_JSON_PATH)
        community = load_community_rows(DB_PATH)
        inserted = insert_skeleton_rows(conn, official, community)
        print(f"Inserted {inserted} new skeleton rows "
              f"({len(official)} official + {len(community)} community candidates).")


if __name__ == "__main__":
    main()
