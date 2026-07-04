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
STATIC_THUMB_DIR = os.path.join(os.path.dirname(__file__), "static", "template_thumbs")

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
    existing_official_pairs = {
        (r[0], r[1]) for r in conn.execute(
            "SELECT title, prompt FROM templates WHERE source='official'"
        ).fetchall()
    }

    inserted = 0
    for item in official:
        # Dedup by (title, prompt), not title alone: many official templates
        # share the same title (e.g. "Standard" appears 13 times across
        # different subcategories) but have distinct prompt text.
        key = (item["title"], item["prompt"])
        if key in existing_official_pairs:
            continue
        conn.execute(
            """INSERT INTO templates (source, title, prompt, category, thumbnail_prompt)
               VALUES ('official', ?, ?, '', '')""",
            (item["title"], item["prompt"]),
        )
        existing_official_pairs.add(key)
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
    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.row_factory = saved


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
        max_tokens=8000,
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


def get_rows_needing_thumbnail(conn: sqlite3.Connection, limit=None) -> list:
    query = ("SELECT id, thumbnail_prompt FROM templates "
             "WHERE thumbnail_path IS NULL AND thumbnail_prompt != ''")
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    saved = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.row_factory = saved


def generate_thumbnail(client, thumbnail_prompt: str, out_path: str) -> bool:
    """Generate one square thumbnail via Gemini and save it to out_path.
    Never raises — returns False on any failure so batch runs can continue."""
    try:
        from google.genai import types as genai_types

        config = genai_types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            image_config=genai_types.ImageConfig(aspect_ratio="1:1"),
        )
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=[thumbnail_prompt],
            config=config,
        )
        for candidate in response.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                if part.inline_data and part.inline_data.mime_type and \
                   part.inline_data.mime_type.startswith("image/"):
                    with open(out_path, "wb") as f:
                        f.write(part.inline_data.data)
                    return True
        return False
    except Exception as exc:
        print(f"  [thumbnail failed] {exc}")
        return False


def run_thumbnail_batch(conn: sqlite3.Connection, client, static_dir: str, limit=None) -> tuple:
    os.makedirs(static_dir, exist_ok=True)
    rows = get_rows_needing_thumbnail(conn, limit=limit)
    succeeded, failed = 0, 0
    for row in rows:
        rel_path = os.path.join("static", "template_thumbs", f"{row['id']}.jpg")
        out_path = os.path.join(static_dir, f"{row['id']}.jpg")
        if generate_thumbnail(client, row["thumbnail_prompt"], out_path):
            conn.execute(
                "UPDATE templates SET thumbnail_path = ? WHERE id = ?",
                (rel_path, row["id"]),
            )
            conn.commit()
            succeeded += 1
        else:
            failed += 1
    return succeeded, failed


def get_openai_client():
    from app import get_client
    return get_client()


def get_gemini_client():
    from app import get_google_client
    return get_google_client()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=int, default=None,
                         help="Only classify/thumbnail the first N unfinished rows")
    args = parser.parse_args(argv)
    limit = args.dry_run

    from app import init_db
    init_db()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        official = load_official_templates(OFFICIAL_JSON_PATH)
        community = load_community_rows(DB_PATH)
        inserted = insert_skeleton_rows(conn, official, community)
        print(f"Step A/B: inserted {inserted} new skeleton rows.")

        openai_client = get_openai_client()
        unclassified = get_unclassified_rows(conn, limit=limit)
        classified_count = 0
        for batch in build_batches(unclassified):
            try:
                results = classify_batch(openai_client, batch)
            except Exception as exc:
                ids = [r["id"] for r in batch]
                print(f"  [batch failed] ids={ids} — {exc}; will retry next run")
                continue
            apply_classification_results(conn, results)
            classified_count += len(results)
        print(f"Step C: classified {classified_count}/{len(unclassified)} candidate rows.")

        gemini_client = get_gemini_client()
        succeeded, failed = run_thumbnail_batch(conn, gemini_client, STATIC_THUMB_DIR, limit=limit)
        print(f"Step D: generated {succeeded} thumbnails, {failed} failed.")

        total = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        still_uncategorized = conn.execute(
            "SELECT COUNT(*) FROM templates WHERE category = ''").fetchone()[0]
        still_unthumbnailed = conn.execute(
            "SELECT COUNT(*) FROM templates WHERE thumbnail_path IS NULL").fetchone()[0]
        print(f"Summary: {total} total rows, {still_uncategorized} still uncategorized, "
              f"{still_unthumbnailed} still missing a thumbnail. Re-run this script to retry those.")


if __name__ == "__main__":
    main()
