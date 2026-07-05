#!/usr/bin/env python3
"""Sync prompts from Ultimate-ChatGPT-Image collection into community_prompts.

Parses the single README.md from the cloned repo and imports all prompts.

Usage:
  python import_ultimate_chatgpt.py        # dry run (print what would be imported)
  python import_ultimate_chatgpt.py --run  # actually insert
"""
import os, re, sqlite3, sys

README_PATH = os.path.join(os.path.dirname(__file__),
                           "ultimate-chatgpt-image-prompts", "README.md")
DB_PATH = os.path.join(os.path.dirname(__file__), "prompts.db")

# Category number → display name (from README table of contents)
CATEGORY_MAP = {
    1:  "3D Miniatures & Dioramas",
    2:  "Product Photography",
    3:  "Character Design",
    4:  "Food & Culinary",
    5:  "Fantasy & Sci-Fi",
    6:  "Sports & Action",
    7:  "Urban Cityscapes",
    8:  "Architecture & Interiors",
    9:  "Nature & Landscapes",
    10: "Logo & Branding",
    11: "Vintage & Retro",
    12: "Cinematic Posters",
    13: "Anime & Manga",
    14: "Minimalist Icons",
    15: "Miscellaneous",
    16: "Portrait Photography",
    17: "Fashion Photography",
}

PLATFORM = "Ultimate-ChatGPT-Image"

# Prompts that are just placeholders — skip these
SKIP_PROMPTS = {
    "Prompt below",
    "-> The results: Pick yours",
}


def parse_readme(path):
    """Parse the README and return list of prompt dicts."""
    with open(path) as f:
        content = f.read()

    # Split by "### N.M. " headings
    blocks = re.split(r'### (\d+)\.(\d+)\.\s*', content)

    cases = []
    # blocks[0] = everything before first ### N.M.
    # blocks[1] = category_num, blocks[2] = entry_num, blocks[3] = content
    # blocks[4] = category_num, blocks[5] = entry_num, blocks[6] = content, etc.
    for i in range(1, len(blocks) - 2, 3):
        cat_num_str = blocks[i]
        entry_num_str = blocks[i + 1]
        entry_content = blocks[i + 2]

        try:
            cat_num = int(cat_num_str)
        except ValueError:
            continue

        category = CATEGORY_MAP.get(cat_num)
        if category is None:
            continue

        # Extract title (first line of the entry content)
        title_m = re.match(r'(.+?)\n', entry_content)
        if not title_m:
            continue
        title = title_m.group(1).strip()

        # Extract image URL from ![alt](url)
        img_m = re.search(r'!\[([^\]]*)\]\(([^)]+)\)', entry_content)
        image_url = img_m.group(2) if img_m else ""

        # Extract prompt from code block after "**Prompt:**"
        prompt_m = re.search(
            r'\*\*Prompt:\*\*\s*\n\s*```\s*\n(.*?)```',
            entry_content, re.DOTALL)
        if not prompt_m:
            continue
        prompt = prompt_m.group(1).strip()

        # Skip placeholder prompts
        if prompt in SKIP_PROMPTS or len(prompt) < 15:
            continue

        # Extract source URL
        src_m = re.search(
            r'\*\*Source:\*\*\s*\[([^\]]+)\]\(([^)]+)\)',
            entry_content)
        source_url = src_m.group(2) if src_m else ""
        author = src_m.group(1) if src_m else ""

        cases.append({
            "title": title,
            "category": category,
            "platform": PLATFORM,
            "author": author,
            "image_url": image_url,
            "source_url": source_url,
            "prompt": prompt,
            "route_id": f"ult-{cat_num:02d}-{entry_num_str}",
        })

    return cases


def ensure_schema(conn):
    """Ensure route_id column and unique index exist for UPSERT."""
    try:
        conn.execute("ALTER TABLE community_prompts ADD COLUMN route_id TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_route_id ON community_prompts(route_id)")


def rebuild_fts(conn):
    """Rebuild the FTS5 index from community_prompts."""
    conn.execute("DELETE FROM community_prompts_fts")
    conn.execute("""
        INSERT INTO community_prompts_fts(rowid, title, prompt, category)
        SELECT id, title, prompt, category FROM community_prompts
    """)


def main():
    dry_run = "--run" not in sys.argv

    if not os.path.exists(README_PATH):
        print(f"ERROR: README not found at {README_PATH}")
        print("Clone the repo first:")
        print("  git clone https://github.com/0aicoder0/Ultimate-ChatGPT-Image-and-Nano-Banana-Pro-Collection.git ultimate-chatgpt-image-prompts")
        sys.exit(1)

    if dry_run:
        print("=== DRY RUN (add --run to actually import) ===\n")

    cases = parse_readme(README_PATH)

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)

    if not dry_run:
        conn.execute(
            "DELETE FROM community_prompts WHERE platform = ?", (PLATFORM,))

    # Count by category for reporting
    cat_counts = {}
    for c in cases:
        cat = c["category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

        if dry_run:
            print(f"  [{c['route_id']}] {c['title']}")
            print(f"         category: {c['category']}")
            print(f"         author: {c['author']}")
            print(f"         prompt: {c['prompt'][:80]}...")
            print()
        else:
            conn.execute("""
                INSERT INTO community_prompts
                    (title, category, platform, author, image_url,
                     source_url, prompt, score, route_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(route_id) DO UPDATE SET
                    title = excluded.title,
                    category = excluded.category,
                    author = excluded.author,
                    image_url = excluded.image_url,
                    source_url = excluded.source_url,
                    prompt = excluded.prompt
            """, (c["title"], c["category"], c["platform"], c["author"],
                  c["image_url"], c["source_url"], c["prompt"], 80, c["route_id"]))

    if not dry_run:
        conn.commit()
        rebuild_fts(conn)

    print(f"\nCategory breakdown:")
    for cat, count in sorted(cat_counts.items()):
        print(f"  {cat}: {count}")

    total = len(cases)
    if not dry_run:
        print(f"\nDone. Imported {total} prompts. FTS index rebuilt.")
    else:
        print(f"\nWould import {total} prompts. Add --run to execute.")

    conn.close()


if __name__ == "__main__":
    main()
