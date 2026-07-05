#!/usr/bin/env python3
"""Sync prompts from GPT-Image2-Skill gallery into community_prompts table.

Usage:
  python import_gpt_image_skill.py        # dry run (print what would be imported)
  python import_gpt_image_skill.py --run  # actually insert
"""
import os, re, sqlite3, sys

GALLERY_DIR = os.path.join(os.path.dirname(__file__), "gpt-image-skill",
                           "skills", "gpt-image", "references")
DB_PATH = os.path.join(os.path.dirname(__file__), "prompts.db")

# Map source category (from gallery file name) to display name
CATEGORY_NAMES = {
    "anime-and-manga":                    "Anime & Manga",
    "architecture-and-interior":          "Architecture & Interior",
    "beauty-and-lifestyle":               "Beauty & Lifestyle",
    "brand-systems-and-identity":         "Brand Systems & Identity",
    "character-design":                   "Character Design",
    "cinematic-and-animation":            "Cinematic & Animation",
    "cinematic-film-references":          "Cinematic Film References",
    "data-visualization":                 "Data Visualization",
    "edit-endpoint-showcase":             "Edit Endpoint Showcase",
    "events-and-experience":              "Events & Experience",
    "fashion-editorial":                  "Fashion Editorial",
    "fine-art-painting":                  "Fine Art & Painting",
    "gaming":                             "Gaming",
    "illustration":                       "Illustration",
    "infographics-and-field-guides":      "Infographics & Field Guides",
    "ink-and-chinese":                    "Ink & Chinese",
    "isometric":                          "Isometric",
    "more-illustration-styles":           "More Illustration Styles",
    "official-openai-cookbook-examples":  "Official OpenAI Cookbook",
    "photography":                        "Photography",
    "pixel-art":                          "Pixel Art",
    "product-and-food":                   "Product & Food",
    "research-paper-figures":             "Research Paper Figures",
    "retro-and-cyberpunk":                "Retro & Cyberpunk",
    "scientific-and-educational":         "Scientific & Educational",
    "screen-photography":                 "Screen Photography",
    "tattoo-design":                      "Tattoo Design",
    "technical-illustration":             "Technical Illustration",
    "typography-and-posters":             "Typography & Posters",
    "ui-ux-mockups":                      "UI/UX Mockups",
    "watercolor":                         "Watercolor",
}

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/wuyoscar/GPT-Image2-Skill/main"
GITHUB_BLOB_BASE = "https://github.com/wuyoscar/GPT-Image2-Skill/blob/main"


def parse_gallery_file(path, category):
    """Parse a single gallery-*.md file and return list of prompt dicts."""
    with open(path) as f:
        content = f.read()

    cases = []
    # Split on "### No. N ·" — each is one prompt entry
    blocks = re.split(r'### No\. \d+ ·\s*', content)
    for block in blocks[1:]:  # skip header
        # Extract title (rest of heading line)
        title_m = re.match(r'(.+?)\n', block)
        if not title_m:
            continue
        title = title_m.group(1).strip()

        # Extract image slug from "- Image: `docs/<category>/<slug>.png`"
        img_m = re.search(r'- Image:\s*`docs/[^/]+/([^`]+\.png)`', block)
        image_slug = img_m.group(1) if img_m else ""

        # Build image_url from slug
        image_url = ""
        if image_slug:
            image_url = f"{GITHUB_RAW_BASE}/docs/{category}/{image_slug}"

        # Extract metadata line
        meta_m = re.search(
            r'- Metadata:\s*(.+?)\s*·\s*`([^`]*)`\s*·\s*`([^`]*)`\s*·\s*(.+?)\n',
            block)
        if not meta_m:
            continue
        meta_category = meta_m.group(1).strip()
        orientation = meta_m.group(2).strip()
        dimensions = meta_m.group(3).strip()
        attribution_raw = meta_m.group(4).strip()

        # Parse attribution: "Curated" or "Author: @handle · Source: [Platform](url)"
        author = ""
        source_url = ""
        platform = "GPT-Image2-Skill"
        if attribution_raw == "Curated":
            author = "Curated"
        else:
            author_m = re.match(r'Author:\s*(.+?)\s*·\s*Source:\s*\[([^\]]+)\]\(([^)]+)\)',
                                attribution_raw)
            if author_m:
                author = author_m.group(1).strip()
                source_url = author_m.group(3).strip()

        # Extract prompt from ```text code block
        prompt_m = re.search(r'```text\s*\n(.*?)```', block, re.DOTALL)
        prompt = prompt_m.group(1).strip() if prompt_m else ""

        if prompt:
            cases.append({
                "title": title,
                "category": CATEGORY_NAMES.get(category, category),
                "platform": platform,
                "author": author,
                "image_url": image_url,
                "source_url": source_url,
                "prompt": prompt,
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

    if dry_run:
        print("=== DRY RUN (add --run to actually import) ===\n")

    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)

    if not dry_run:
        # Delete old GPT-Image2-Skill entries before re-importing
        conn.execute(
            "DELETE FROM community_prompts WHERE platform = 'GPT-Image2-Skill'")

    total = 0
    gallery_files = sorted(
        f for f in os.listdir(GALLERY_DIR)
        if f.startswith("gallery-") and f.endswith(".md"))

    for fname in gallery_files:
        # Extract category slug from filename: "gallery-beauty-and-lifestyle.md"
        category = fname.replace("gallery-", "").replace(".md", "")
        path = os.path.join(GALLERY_DIR, fname)
        cases = parse_gallery_file(path, category)

        for i, c in enumerate(cases):
            # Build route_id: "gimg2-NNN" (padded entry number, derived from order)
            entry_num = total + i + 1
            route_id = f"gimg2-{entry_num:03d}"

            if dry_run:
                print(f"  [{route_id}] {c['title']}")
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
                      c["image_url"], c["source_url"], c["prompt"], 85, route_id))

        print(f"  {fname}: {len(cases)} prompts")
        total += len(cases)

    if not dry_run:
        conn.commit()
        rebuild_fts(conn)
        print(f"\nDone. Imported {total} prompts total. FTS index rebuilt.")
    else:
        print(f"\nWould import {total} prompts total. Add --run to execute.")

    conn.close()


if __name__ == "__main__":
    main()
