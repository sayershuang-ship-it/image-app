#!/usr/bin/env python3
"""Sync prompts from awesome-gpt-image-2 repo into community_prompts table.

Usage:
  python sync_awesome_prompts.py        # dry run (print what would be inserted)
  python sync_awesome_prompts.py --run  # actually insert

For FPD styles, use:
  python import_fpd.py    # syncs 14 female-portrait-director routes from data/fpd_styles.json
"""
import os, re, sqlite3, sys

CASES_DIR = os.path.join(os.path.dirname(__file__), "awesome-prompts", "cases")
DB_PATH   = os.path.join(os.path.dirname(__file__), "prompts.db")

CATEGORY_NAMES = {
    "ad-creative": "廣告創意",
    "character":   "角色設計",
    "comparison":  "對比展示",
    "ecommerce":   "電商產品",
    "portrait":    "人像攝影",
    "poster":      "海報設計",
    "ui":          "UI 介面",
}

def parse_case_file(path, category):
    with open(path) as f:
        content = f.read()

    cases = []
    # Split by "### Case N:" — each is one case
    blocks = re.split(r'### Case \d+:\s*', content)
    for block in blocks[1:]:  # skip header
        # Extract title: [Title](url) (by [@author](url))
        title_m = re.match(r'\[([^\]]+)\]\(([^)]+)\)\s*\(by\s*\[([^\]]+)\]\(([^)]+)\)', block)
        if not title_m:
            continue
        title = title_m.group(1)
        source_url = title_m.group(2)
        author = title_m.group(3)

        # Extract first output image
        img_m = re.search(r'<img\s+src="([^"]+)"[^>]*>', block)
        image_url = img_m.group(1) if img_m else ""

        # Extract prompt from code block after "**Prompt:**"
        prompt_m = re.search(r'\*\*Prompt:\*\*\s*\n\s*```\s*\n(.*?)```', block, re.DOTALL)
        prompt = prompt_m.group(1).strip() if prompt_m else ""

        if prompt:
            cases.append({
                "title": title,
                "category": CATEGORY_NAMES.get(category, category),
                "platform": "X (Twitter)",
                "author": author,
                "image_url": image_url,
                "source_url": source_url,
                "prompt": prompt,
            })
    return cases

def main():
    conn = sqlite3.connect(DB_PATH)

    # Create table if not exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS community_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            category TEXT,
            platform TEXT,
            author TEXT,
            image_url TEXT,
            source_url TEXT,
            prompt TEXT,
            score INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Clear old awesome-prompts data (by source_url matching x.com)
    conn.execute("DELETE FROM community_prompts WHERE source_url LIKE '%x.com%'")
    
    total = 0
    for fname in sorted(os.listdir(CASES_DIR)):
        if not fname.endswith('.md') or fname.startswith('README'):
            continue
        # Skip localized versions
        if '_' in fname:
            continue
        category = fname.replace('.md', '')
        path = os.path.join(CASES_DIR, fname)
        cases = parse_case_file(path, category)
        for c in cases:
            conn.execute("""
                INSERT INTO community_prompts (title, category, platform, author, image_url, source_url, prompt)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (c["title"], c["category"], c["platform"], c["author"],
                  c["image_url"], c["source_url"], c["prompt"]))
            total += 1
        print(f"  {category}: {len(cases)} prompts")

    conn.commit()
    print(f"\nDone. Imported {total} prompts total.")
    conn.close()

if __name__ == "__main__":
    main()
