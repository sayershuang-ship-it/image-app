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

CATEGORIES = [
    "人像攝影 Portrait & Fashion Photography",
    "場景與街拍 Scenes & Documentary Photography",
    "UI 與介面設計 UI / App Interfaces",
    "海報與視覺設計 Posters & Visual Design",
    "字體排版 Typography & Calligraphy",
    "資訊圖表 Infographics & Data Viz",
    "電商與產品 E-Commerce & Product",
    "品牌識別 Brand & Identity",
    "插畫與藝術 Illustration & Art",
    "角色設計 Character Design",
    "建築與空間 Architecture & Space",
    "歷史文化 Historical & Cultural",
    "奇幻科幻／遊戲 Fantasy, Sci-Fi & Gaming",
    "其他／創意混搭 Comparisons, Mashups & Other",
]


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


def get_unclassified_rows(conn: sqlite3.Connection, limit=None) -> list:
    query = "SELECT id, title, prompt FROM templates WHERE category = ''"
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    return conn.execute(query).fetchall()


def build_batches(rows: list, batch_size: int = 20) -> list:
    return [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]


def classify_batch(client, batch: list) -> list:
    """Ask the LLM to assign each row a category (from CATEGORIES) and a
    concrete, placeholder-free thumbnail_prompt. Rows the model assigns an
    invalid category to are dropped from the result (logged, not written)."""
    rows_payload = [
        {"id": r["id"], "title": r["title"], "prompt": r["prompt"]} for r in batch
    ]
    categories_list = "\n".join(f"- {c}" for c in CATEGORIES)
    instructions = (
        "You are classifying AI image-generation prompt templates.\n\n"
        "For each item below, return a JSON array where each element has:\n"
        '  "id": the item\'s id, unchanged\n'
        '  "category": exactly one string from this fixed list (copy it verbatim, '
        "do not invent new categories):\n"
        f"{categories_list}\n\n"
        '  "thumbnail_prompt": a concrete, placeholder-free English prompt suitable '
        "for generating a single representative preview image. If the item's prompt "
        "already has no [bracketed] placeholders, copy it through with only minor "
        "trimming. If it has [bracketed] placeholders, rewrite it by filling every "
        "placeholder with a plausible concrete value, keeping everything else intact.\n\n"
        "Items:\n" + json.dumps(rows_payload, ensure_ascii=False) + "\n\n"
        "Output ONLY the JSON array, no markdown fences, no explanation."
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": instructions}],
        max_tokens=4000,
        temperature=0.2,
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.strip("`")
    if raw.startswith("json"):
        raw = raw[4:].strip()

    parsed = json.loads(raw)
    valid_categories = set(CATEGORIES)
    results = []
    for item in parsed:
        if item.get("category") not in valid_categories:
            print(f"  [skip] id={item.get('id')} — invalid category "
                  f"{item.get('category')!r}, will retry next run")
            continue
        results.append({
            "id": item["id"],
            "category": item["category"],
            "thumbnail_prompt": item["thumbnail_prompt"],
        })
    return results


def apply_classification_results(conn: sqlite3.Connection, results: list) -> None:
    for r in results:
        conn.execute(
            "UPDATE templates SET category = ?, thumbnail_prompt = ? WHERE id = ?",
            (r["category"], r["thumbnail_prompt"], r["id"]),
        )
    conn.commit()


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
