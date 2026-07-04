# Unified Templates Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the hardcoded JS `TEMPLATES` object and the `community_prompts` DB
table into one new `templates` table with an LLM-assigned unified category
taxonomy and a Gemini-generated thumbnail for every entry, served through a
single `/api/templates` endpoint and rendered as one unified list on the
`/templates` page.

**Architecture:** A new `templates` SQLite table is populated once by an
offline, resumable migration script (`migrate_templates.py`, repo root — matches
the existing `import_fpd.py` / `sync_awesome_prompts.py` convention of
standalone root-level scripts). The script batches calls to the existing
`gpt-4o` OpenAI client (already used by `/enhance-prompt`) for classification,
and to Gemini (`gemini-3.1-flash-lite-image`, same model `_generate_gemini`
already uses) for thumbnail images. A new `GET /api/templates` route reads the
finished table; `templates.html` is rewritten to fetch from it instead of the
hardcoded JS object + `/api/community-prompts`.

**Tech Stack:** Python 3, Flask, sqlite3, `openai` client (already a dependency,
`app.py:26`), `google.genai` client (already a dependency, `app.py:452`), plain
JS/HTML on the frontend (no build step — this project has none).

## Global Constraints

- Design source: `docs/superpowers/specs/2026-07-04-unified-templates-page-design.md`.
- **Correction to the spec's item count:** the spec estimated "65 official
  templates" and "943 total rows." The actual JS `TEMPLATES` object in
  `templates/templates.html:298-458` contains **99 items** (37 in the four
  "core" tabs + 62 in the "Gallery Examples" tab), not 65 — the page's
  `<span class="badge">21 + 65 examples</span>` (`templates.html:243`) is stale
  UI copy that doesn't match the actual object. So the real total is
  **99 official + 878 community = 977 rows**. This changes no architecture,
  only the numbers referenced in logging/cost estimates below.
- Gemini thumbnail cost: `_MODELS["gemini-3.1-flash-lite-image"]["cost_table"]["standard"]["1024x1024"] == 0.001`
  (`app.py:219-222`) → 977 rows ≈ **$0.98** for the full thumbnail batch.
- The unified category taxonomy is a **fixed 14-item enum** (spec, "Unified
  category taxonomy" section) — every classification call must be constrained
  to pick from exactly these 14 strings, never invent new ones.
- `community_prompts` and `/api/community-prompts` (`app.py:876-902`) are left
  in place, untouched — `templates` is additive, not a rename.
- No new Python dependencies — `openai` and `google-genai` are already
  installed and imported in `app.py`.

---

### Task 1: `templates` table schema

**Files:**
- Modify: `app.py:105-160` (inside `init_db()`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: a `templates` table other tasks insert into and query, with exact
  columns: `id, source, title, category, prompt, thumbnail_prompt,
  thumbnail_path, platform, author, source_url, score, community_prompt_id`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_templates_table_created(client):
    """init_db() creates the templates table with the expected columns."""
    with sqlite3.connect(app_module.DB_PATH) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()}
    assert cols == {
        "id", "source", "title", "category", "prompt", "thumbnail_prompt",
        "thumbnail_path", "platform", "author", "source_url", "score",
        "community_prompt_id",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_table_created -v`
Expected: FAIL with `sqlite3.OperationalError: no such table: templates`

- [ ] **Step 3: Add the table to `init_db()`**

In `app.py`, immediately after the existing `community_prompts_fts` block
(after the line `SELECT id, title, prompt, category FROM community_prompts`
around `app.py:159`), add:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL CHECK(source IN ('official', 'community')),
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL,
                thumbnail_prompt TEXT NOT NULL DEFAULT '',
                thumbnail_path TEXT,
                platform TEXT,
                author TEXT,
                source_url TEXT,
                score INTEGER,
                community_prompt_id INTEGER
            )
        """)
```

(`category` and `thumbnail_prompt` default to `''` rather than being nullable —
the migration script's resumability logic in Task 3 selects on
`category = ''` / `thumbnail_prompt = ''`, which is simpler than juggling NULL
checks.)

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_table_created -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: add templates table schema"
```

---

### Task 2: Extract official templates from the JS object into JSON

**Files:**
- Create: `extract_official_templates.js`
- Create (generated output, committed): `official_templates.json`
- Test: `tests/test_extract_official_templates.py`

**Interfaces:**
- Produces: `official_templates.json` — a JSON array on disk, each element
  `{"tab": str, "subcategory": str, "title": str, "prompt": str}`. Task 3
  reads this file by path `official_templates.json` (repo root).

Hand-retranscribing 99 multi-paragraph prompts from JS into Python risks
transcription errors. Since `templates/templates.html` embeds a real JS object
literal (`const TEMPLATES = {...}`), the reliable way to get an exact copy is
to have Node itself evaluate that object and dump it as JSON — no manual
retyping of prompt text.

- [ ] **Step 1: Write the failing test**

Create `tests/test_extract_official_templates.py`:

```python
import json
import os

JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "official_templates.json")


def test_official_templates_json_exists_and_has_99_items():
    assert os.path.exists(JSON_PATH), "run: node extract_official_templates.js"
    with open(JSON_PATH) as f:
        data = json.load(f)
    assert len(data) == 99
    for item in data:
        assert set(item.keys()) == {"tab", "subcategory", "title", "prompt"}
        assert item["prompt"].strip() != ""


def test_known_template_present():
    with open(JSON_PATH) as f:
        data = json.load(f)
    titles = {item["title"] for item in data}
    assert "35mm Film Portrait" in titles
    assert "Photo to Designer Toy" in titles  # appears twice (Character Design tab + Gallery Examples tab) — both kept, that's fine
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_extract_official_templates.py -v`
Expected: FAIL with `AssertionError: run: node extract_official_templates.js`
(file doesn't exist yet)

- [ ] **Step 3: Write `extract_official_templates.js`**

```javascript
// extract_official_templates.js
// One-time extraction tool: reads the `const TEMPLATES = {...}` object literal
// out of templates/templates.html and dumps it as official_templates.json,
// shaped as a flat array of {tab, subcategory, title, prompt}.
//
// Run: node extract_official_templates.js

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'templates', 'templates.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const startMarker = 'const TEMPLATES = ';
const startIdx = html.indexOf(startMarker);
if (startIdx === -1) {
  throw new Error('Could not find "const TEMPLATES = " in templates/templates.html');
}
const afterStart = startIdx + startMarker.length;
const endIdx = html.indexOf('\n};\n', afterStart);
if (endIdx === -1) {
  throw new Error('Could not find end of TEMPLATES object literal');
}
const objectLiteral = html.slice(afterStart, endIdx + 2); // include closing "}"

// eslint-disable-next-line no-eval
const TEMPLATES = eval('(' + objectLiteral + ')');

const flat = [];
for (const [tab, subcats] of Object.entries(TEMPLATES)) {
  for (const [subcategory, items] of Object.entries(subcats)) {
    for (const item of items) {
      flat.push({ tab, subcategory, title: item.name, prompt: item.prompt });
    }
  }
}

fs.writeFileSync(
  path.join(__dirname, 'official_templates.json'),
  JSON.stringify(flat, null, 2) + '\n'
);

console.log(`Wrote ${flat.length} templates to official_templates.json`);
```

- [ ] **Step 4: Run the extraction and verify the test passes**

Run: `node extract_official_templates.js`
Expected: `Wrote 99 templates to official_templates.json`

Run: `venv/bin/python -m pytest tests/test_extract_official_templates.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add extract_official_templates.js official_templates.json tests/test_extract_official_templates.py
git commit -m "feat: extract official templates from JS into official_templates.json"
```

---

### Task 3: Migration script — load & insert skeleton rows (idempotent)

**Files:**
- Create: `migrate_templates.py`
- Test: `tests/test_migrate_templates.py`

**Interfaces:**
- Consumes: `official_templates.json` (Task 2), `community_prompts` table
  (existing, `app.py:139-151`), `templates` table (Task 1).
- Produces:
  - `load_official_templates(json_path: str) -> list[dict]`
  - `load_community_rows(db_path: str) -> list[sqlite3.Row]`
  - `insert_skeleton_rows(conn: sqlite3.Connection, official: list[dict], community: list[sqlite3.Row]) -> int`
    — returns count of rows newly inserted (0 on a no-op re-run).

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrate_templates.py`:

```python
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module
import migrate_templates as mt


def _fresh_db():
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    app_module.DB_PATH = db_path
    app_module.init_db()
    return db_path


def test_insert_skeleton_rows_is_idempotent():
    db_path = _fresh_db()
    official = [
        {"tab": "Design & Info", "subcategory": "UI & Interface",
         "title": "Standard", "prompt": "Generate a [platform] UI..."},
    ]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO community_prompts (title, category, prompt) VALUES (?, ?, ?)",
            ("Test Community Row", "人像攝影", "A photo of a cat"),
        )
        community_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        community_rows = conn.execute("SELECT * FROM community_prompts").fetchall()
        conn.row_factory = sqlite3.Row
        community_rows = conn.execute("SELECT * FROM community_prompts").fetchall()

        inserted_first = mt.insert_skeleton_rows(conn, official, community_rows)
        assert inserted_first == 2  # 1 official + 1 community

        # Re-running with the same input must not duplicate rows
        inserted_second = mt.insert_skeleton_rows(conn, official, community_rows)
        assert inserted_second == 0

        total = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        assert total == 2

        official_row = conn.execute(
            "SELECT * FROM templates WHERE source='official'"
        ).fetchone()
        assert official_row["prompt"] == "Generate a [platform] UI..."
        assert official_row["category"] == ""
        assert official_row["thumbnail_prompt"] == ""

        community_row = conn.execute(
            "SELECT * FROM templates WHERE source='community'"
        ).fetchone()
        assert community_row["community_prompt_id"] == community_id
        assert community_row["prompt"] == "A photo of a cat"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_migrate_templates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'migrate_templates'`

- [ ] **Step 3: Write `migrate_templates.py` (Steps A + B only for now)**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_migrate_templates.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migrate_templates.py tests/test_migrate_templates.py
git commit -m "feat: migration script step A/B — load and insert skeleton template rows"
```

---

### Task 4: Migration script — batched LLM classification + thumbnail-prompt authoring

**Files:**
- Modify: `migrate_templates.py`
- Test: `tests/test_migrate_templates.py`

**Interfaces:**
- Consumes: `templates` rows with `category = ''` (Task 3's output).
- Produces:
  - `CATEGORIES: list[str]` — the fixed 14-item taxonomy constant.
  - `get_unclassified_rows(conn: sqlite3.Connection, limit: int|None = None) -> list[sqlite3.Row]`
  - `build_batches(rows: list, batch_size: int = 20) -> list[list[sqlite3.Row]]`
  - `classify_batch(client, batch: list[sqlite3.Row]) -> list[dict]` — each
    dict `{"id": int, "category": str, "thumbnail_prompt": str}`.
  - `apply_classification_results(conn: sqlite3.Connection, results: list[dict]) -> None`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_migrate_templates.py`:

```python
from unittest.mock import MagicMock


def test_classify_batch_parses_response_and_validates_category():
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = (
        '[{"id": 1, "category": "人像攝影 Portrait & Fashion Photography", '
        '"thumbnail_prompt": "A 35mm film portrait of a young woman in soft window light"},'
        '{"id": 2, "category": "not-a-real-category", "thumbnail_prompt": "x"}]'
    )
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    row1 = {"id": 1, "title": "T1", "prompt": "some prompt"}
    row2 = {"id": 2, "title": "T2", "prompt": "some other prompt"}
    results = mt.classify_batch(fake_client, [row1, row2])

    # Row 1: valid category, kept as-is.
    assert results[0] == {
        "id": 1,
        "category": "人像攝影 Portrait & Fashion Photography",
        "thumbnail_prompt": "A 35mm film portrait of a young woman in soft window light",
    }
    # Row 2: invalid category from the model, dropped rather than written.
    assert len(results) == 1


def test_apply_classification_results_updates_rows():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt)
               VALUES (1, 'official', 'T1', 'some prompt', '', '')"""
        )
        mt.apply_classification_results(conn, [
            {"id": 1, "category": "人像攝影 Portrait & Fashion Photography",
             "thumbnail_prompt": "A concrete rewritten prompt"},
        ])
        row = conn.execute("SELECT category, thumbnail_prompt FROM templates WHERE id=1").fetchone()
        assert row[0] == "人像攝影 Portrait & Fashion Photography"
        assert row[1] == "A concrete rewritten prompt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_migrate_templates.py -k classify_batch -v`
Expected: FAIL with `AttributeError: module 'migrate_templates' has no attribute 'classify_batch'`

- [ ] **Step 3: Add classification logic to `migrate_templates.py`**

Add near the top, after the existing constants:

```python
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
```

Add classification functions (append to the file, above `def main():`):

```python
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
```

Note: `classify_batch`'s test passes plain `dict` rows (not `sqlite3.Row`) —
the function only uses `r["id"]`, `r["title"]`, `r["prompt"]` indexing, which
both types support identically, so this works against real `sqlite3.Row`
objects from `get_unclassified_rows` too.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_migrate_templates.py -v`
Expected: PASS (all tests in the file, including Task 3's)

- [ ] **Step 5: Commit**

```bash
git add migrate_templates.py tests/test_migrate_templates.py
git commit -m "feat: migration script step C — batched LLM classification"
```

---

### Task 5: Migration script — batched Gemini thumbnail generation

**Files:**
- Modify: `migrate_templates.py`
- Test: `tests/test_migrate_templates.py`

**Interfaces:**
- Consumes: `templates` rows with `thumbnail_path IS NULL AND thumbnail_prompt != ''`.
- Produces:
  - `get_rows_needing_thumbnail(conn, limit=None) -> list[sqlite3.Row]`
  - `generate_thumbnail(client, thumbnail_prompt: str, out_path: str) -> bool` —
    writes a JPEG to `out_path`, returns `True` on success, `False` on failure
    (never raises — callers loop over many rows and must not abort the batch).
  - `run_thumbnail_batch(conn, client, static_dir: str, limit=None) -> tuple[int, int]`
    — returns `(succeeded, failed)` counts.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_migrate_templates.py`:

```python
def test_generate_thumbnail_writes_file_and_returns_true(tmp_path):
    fake_part = MagicMock()
    fake_part.inline_data.mime_type = "image/jpeg"
    fake_part.inline_data.data = b"\xff\xd8\xff\xe0fakejpegbytes"
    fake_candidate = MagicMock()
    fake_candidate.content.parts = [fake_part]
    fake_response = MagicMock()
    fake_response.candidates = [fake_candidate]

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response

    out_path = str(tmp_path / "1.jpg")
    ok = mt.generate_thumbnail(fake_client, "a cat sitting on a windowsill", out_path)

    assert ok is True
    with open(out_path, "rb") as f:
        assert f.read() == b"\xff\xd8\xff\xe0fakejpegbytes"


def test_generate_thumbnail_returns_false_on_no_image_data(tmp_path):
    fake_response = MagicMock()
    fake_response.candidates = []
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response

    out_path = str(tmp_path / "2.jpg")
    ok = mt.generate_thumbnail(fake_client, "a prompt", out_path)

    assert ok is False
    assert not os.path.exists(out_path)


def test_run_thumbnail_batch_sets_thumbnail_path(tmp_path):
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt)
               VALUES (1, 'official', 'T1', 'p', 'cat', 'a concrete prompt')"""
        )
        conn.commit()

        fake_part = MagicMock()
        fake_part.inline_data.mime_type = "image/jpeg"
        fake_part.inline_data.data = b"\xff\xd8fakejpeg"
        fake_candidate = MagicMock()
        fake_candidate.content.parts = [fake_part]
        fake_response = MagicMock()
        fake_response.candidates = [fake_candidate]
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = fake_response

        succeeded, failed = mt.run_thumbnail_batch(conn, fake_client, str(tmp_path))

        assert (succeeded, failed) == (1, 0)
        row = conn.execute("SELECT thumbnail_path FROM templates WHERE id=1").fetchone()
        assert row[0] == os.path.join(str(tmp_path), "1.jpg")
        assert os.path.exists(row[0])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_migrate_templates.py -k thumbnail -v`
Expected: FAIL with `AttributeError: module 'migrate_templates' has no attribute 'generate_thumbnail'`

- [ ] **Step 3: Add thumbnail generation logic to `migrate_templates.py`**

Append (above `def main():`):

```python
def get_rows_needing_thumbnail(conn: sqlite3.Connection, limit=None) -> list:
    query = ("SELECT id, thumbnail_prompt FROM templates "
             "WHERE thumbnail_path IS NULL AND thumbnail_prompt != ''")
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    return conn.execute(query).fetchall()


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
        out_path = os.path.join(static_dir, f"{row['id']}.jpg")
        if generate_thumbnail(client, row["thumbnail_prompt"], out_path):
            conn.execute(
                "UPDATE templates SET thumbnail_path = ? WHERE id = ?",
                (out_path, row["id"]),
            )
            conn.commit()
            succeeded += 1
        else:
            failed += 1
    return succeeded, failed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_migrate_templates.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add migrate_templates.py tests/test_migrate_templates.py
git commit -m "feat: migration script step D — batched Gemini thumbnail generation"
```

---

### Task 6: Wire up the full migration CLI (`--dry-run`, orchestration, summary)

**Files:**
- Modify: `migrate_templates.py` (replace the `main()` from Task 3)
- Test: `tests/test_migrate_templates.py`

**Interfaces:**
- Consumes: every function from Tasks 3-5.
- Produces: the final `main()` entry point; no new names other tasks depend on.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_migrate_templates.py`:

```python
def test_main_dry_run_limits_classification_and_thumbnail_work(monkeypatch, tmp_path):
    db_path = _fresh_db()
    monkeypatch.setattr(mt, "DB_PATH", db_path)
    monkeypatch.setattr(mt, "STATIC_THUMB_DIR", str(tmp_path))
    monkeypatch.setattr(mt, "OFFICIAL_JSON_PATH",
                         os.path.join(os.path.dirname(__file__), "..", "official_templates.json"))

    fake_openai_response = MagicMock()
    fake_openai_response.choices = [MagicMock()]

    def fake_classify(client, batch):
        return [{"id": r["id"], "category": mt.CATEGORIES[0], "thumbnail_prompt": "a concrete prompt"}
                for r in batch]

    def fake_generate_thumbnail(client, prompt, out_path):
        with open(out_path, "wb") as f:
            f.write(b"fake")
        return True

    monkeypatch.setattr(mt, "classify_batch", fake_classify)
    monkeypatch.setattr(mt, "generate_thumbnail", fake_generate_thumbnail)
    monkeypatch.setattr(mt, "get_openai_client", lambda: MagicMock())
    monkeypatch.setattr(mt, "get_gemini_client", lambda: MagicMock())

    mt.main(["--dry-run", "3"])

    with sqlite3.connect(db_path) as conn:
        classified = conn.execute("SELECT COUNT(*) FROM templates WHERE category != ''").fetchone()[0]
        thumbnailed = conn.execute("SELECT COUNT(*) FROM templates WHERE thumbnail_path IS NOT NULL").fetchone()[0]
    assert classified == 3
    assert thumbnailed == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_migrate_templates.py -k dry_run -v`
Expected: FAIL — `main()` currently takes no args and doesn't call classify/thumbnail steps.

- [ ] **Step 3: Replace `main()` in `migrate_templates.py`**

Add near the top (with the other constants):

```python
STATIC_THUMB_DIR = os.path.join(os.path.dirname(__file__), "static", "template_thumbs")


def get_openai_client():
    from app import get_client
    return get_client()


def get_gemini_client():
    from app import get_google_client
    return get_google_client()
```

Replace the existing `main()` function with:

```python
def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=int, default=None,
                         help="Only classify/thumbnail the first N unfinished rows")
    args = parser.parse_args(argv)
    limit = args.dry_run

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
            results = classify_batch(openai_client, batch)
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
```

Delete the old standalone `main()` definition from Task 3 (this replaces it —
there must be only one `main()` in the file).

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_migrate_templates.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add migrate_templates.py tests/test_migrate_templates.py
git commit -m "feat: wire up migrate_templates.py CLI with --dry-run and summary output"
```

---

### Task 7: `GET /api/templates` endpoint

**Files:**
- Modify: `app.py` (add route near `/api/community-prompts`, `app.py:876-902`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `templates` table (Task 1).
- Produces: `GET /api/templates` → `dict[str, list[dict]]`, grouped by
  `category`, each item shaped
  `{"id", "title", "source", "prompt", "thumbnail_url", "platform", "author", "source_url"}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_api_templates_groups_by_category_and_nulls_missing_thumbnail(client):
    with sqlite3.connect(app_module.DB_PATH) as conn:
        conn.execute(
            """INSERT INTO templates
                   (source, title, category, prompt, thumbnail_prompt, thumbnail_path)
               VALUES ('official', 'T1', 'UI 與介面設計 UI / App Interfaces', 'p1', 'tp1', NULL)"""
        )
        conn.execute(
            """INSERT INTO templates
                   (source, title, category, prompt, thumbnail_prompt, thumbnail_path,
                    platform, author, source_url, score)
               VALUES ('community', 'T2', '人像攝影 Portrait & Fashion Photography', 'p2', 'tp2',
                       'static/template_thumbs/2.jpg', 'Instagram', 'someone', 'https://x.test', 5)"""
        )

    rv = client.get("/api/templates")
    assert rv.status_code == 200
    data = rv.get_json()

    assert "UI 與介面設計 UI / App Interfaces" in data
    assert data["UI 與介面設計 UI / App Interfaces"][0]["thumbnail_url"] is None

    portrait_items = data["人像攝影 Portrait & Fashion Photography"]
    assert portrait_items[0]["thumbnail_url"] == "/static/template_thumbs/2.jpg"
    assert portrait_items[0]["source"] == "community"
    assert portrait_items[0]["platform"] == "Instagram"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_app.py::test_api_templates_groups_by_category_and_nulls_missing_thumbnail -v`
Expected: FAIL with 404 (`/api/templates` doesn't exist yet)

- [ ] **Step 3: Add the route to `app.py`**

Immediately after the existing `/api/community-prompts` route
(`app.py:876-902`), add:

```python
@app.route("/api/templates")
def templates_api():
    """Return the unified templates table grouped by category."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, source, title, category, prompt, thumbnail_path,
                   platform, author, source_url, score
            FROM templates
            ORDER BY category, id
        """).fetchall()
    grouped = {}
    for r in rows:
        cat = r["category"] or "其他／創意混搭 Comparisons, Mashups & Other"
        if cat not in grouped:
            grouped[cat] = []
        thumb_path = r["thumbnail_path"]
        thumbnail_url = f"/{thumb_path}" if thumb_path else None
        grouped[cat].append({
            "id": r["id"],
            "title": r["title"],
            "source": r["source"],
            "prompt": r["prompt"],
            "thumbnail_url": thumbnail_url,
            "platform": r["platform"],
            "author": r["author"],
            "source_url": r["source_url"],
            "score": r["score"],
        })
    return jsonify(grouped)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_app.py -v`
Expected: PASS (all tests, including the new one and Task 1's)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: add GET /api/templates endpoint"
```

---

### Task 8: Rewrite `templates.html` to use the unified endpoint

**Files:**
- Modify: `templates/templates.html`
- Test: `tests/test_app.py` (existing `test_app_pages_render` and
  `test_nav_consistent` already cover `/templates`; add one more)

**Interfaces:**
- Consumes: `GET /api/templates` (Task 7).
- Produces: no new interfaces — this is the leaf of the chain.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_templates_page_has_no_hardcoded_templates_object(client):
    """The old hardcoded TEMPLATES JS object must be gone — page now fetches
    everything from /api/templates."""
    rv = client.get("/templates")
    body = rv.data.decode()
    assert "const TEMPLATES = {" not in body
    assert "/api/templates" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_page_has_no_hardcoded_templates_object -v`
Expected: FAIL (the hardcoded object is still there, `/api/templates` isn't referenced)

- [ ] **Step 3: Rewrite the page JS**

In `templates/templates.html`, delete the entire hardcoded `const TEMPLATES = {...}`
block (`templates.html:298-458`) and the tab/community-specific rendering
functions that only existed to bridge the two old sources: `renderTabs`,
`switchTab`, `loadCommunityTab`, `renderCommunityTemplates`, `selectCommunity`,
`renderTemplates`, `selectTemplate` (`templates.html:497-598`), and the
`currentTab` / `communityData` state variables. Replace that whole block with:

```javascript
let TEMPLATE_DATA = null;   // { category: [ {id, title, source, prompt, thumbnail_url, ...} ] }

async function loadTemplates() {
  const list = document.getElementById('templateList');
  list.innerHTML = '<div class="tpl-item" style="color:var(--muted)">Loading...</div>';
  try {
    const res = await fetch('/api/templates');
    TEMPLATE_DATA = await res.json();
    renderTemplateList();
  } catch (e) {
    list.innerHTML = '<div class="tpl-item" style="color:oklch(55% 0.17 25)">Failed: ' + escHtml(e.message) + '</div>';
  }
}

function renderTemplateList() {
  const list = document.getElementById('templateList');
  if (!TEMPLATE_DATA) return;
  const cats = Object.entries(TEMPLATE_DATA);
  list.innerHTML = cats.map(([cat, items]) => {
    const itemHtml = items.map((t, i) => {
      const thumbTag = t.thumbnail_url
        ? '<img src="' + escAttr(t.thumbnail_url) + '" style="width:40px;height:40px;object-fit:cover;border-radius:4px;margin-right:8px;flex-shrink:0" onerror="this.src=\'/static/template_thumb_placeholder.svg\'" loading="lazy" />'
        : '<div style="width:40px;height:40px;border-radius:4px;margin-right:8px;flex-shrink:0;background:var(--border)"></div>';
      return '<div class="tpl-item" data-cat="' + escAttr(cat) + '" data-idx="' + i +
             '" style="display:flex;align-items:center;gap:4px" title="' + escAttr(t.title) + '">' +
             thumbTag + '<span style="overflow:hidden;text-overflow:ellipsis">' + escHtml(t.title) + '</span></div>';
    }).join('');
    return '<div class="category"><div class="cat-header" onclick="toggleCat(this)"><span>' +
           escHtml(cat) + '</span><span class="badge" style="font-size:10px;color:var(--muted);margin-left:auto;margin-right:4px">' +
           items.length + '</span><span class="arrow">▼</span></div><div class="cat-items" style="max-height:600px">' +
           itemHtml + '</div></div>';
  }).join('');
  list.querySelectorAll('.tpl-item[data-cat]').forEach(el => {
    el.addEventListener('click', () => selectTemplateItem(parseInt(el.dataset.idx), el.dataset.cat, el));
  });
}

function selectTemplateItem(idx, cat, el) {
  document.querySelectorAll('.tpl-item').forEach(x => x.classList.remove('selected'));
  if (el) el.classList.add('selected');
  const item = TEMPLATE_DATA[cat][idx];
  document.getElementById('promptInput').value = item.prompt;
}

function toggleCat(el) {
  el.classList.toggle('collapsed');
  el.nextElementSibling.classList.toggle('hidden');
}
```

Update the `sidebar-header` badge (`templates.html:243`, currently
`<span class="badge">21 + 65 examples</span>`, which was already stale before
this change) to just `<span class="badge">Templates</span>` — the item count
now varies at runtime and isn't worth hardcoding.

Remove the `<div class="tab-bar" id="tabBar"></div>` element
(`templates.html:245`) — there's only one unified list now, no tabs.

Update `init()` (`templates.html:491-495`) to:

```javascript
async function init() {
  await loadTemplates();
  await loadTplModels();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_app.py -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 5: Commit**

```bash
git add templates/templates.html
git commit -m "feat: rewrite templates page to use unified /api/templates endpoint"
```

---

### Task 9: Run the full migration and manually verify

This task has no automated test — it's the actual one-time data-pipeline
execution the rest of the plan built toward. Verification is manual, per the
spec's "Testing" section.

**Files:** none (operational task only)

- [ ] **Step 1: Sanity-check on a small sample**

Run: `venv/bin/python migrate_templates.py --dry-run 10`

Expected: prints `Step A/B: inserted 977 new skeleton rows.` (99 official +
878 community) on the *first* run, then `Step C: classified 10/977...` and
`Step D: generated <=10 thumbnails...`. Manually inspect 2-3 of the 10
classified rows and their `thumbnail_prompt`:

```bash
sqlite3 prompts.db "SELECT title, category, thumbnail_prompt FROM templates WHERE category != '' LIMIT 3;"
```

Confirm categories are exactly one of the 14 `CATEGORIES` strings and
`thumbnail_prompt` reads as a coherent, placeholder-free sentence.

- [ ] **Step 2: Run the full migration**

Run: `venv/bin/python migrate_templates.py`

This re-runs Step A/B (no-op, already inserted) and processes all remaining
~967 rows through Steps C and D. Expect this to take a while (sequential
Gemini calls) — check the final summary line for `still uncategorized` /
`still missing a thumbnail` counts; if either is non-zero, run
`venv/bin/python migrate_templates.py` again to retry just those rows.

- [ ] **Step 3: Manual browser verification**

Run: `venv/bin/python app.py` (or however the dev server is normally started),
open `/templates`.

Confirm:
- All 14 categories appear in the sidebar.
- Every visible item shows a thumbnail image (not a broken image icon —
  rows still missing a thumbnail show the gray placeholder box, not a broken
  `<img>`).
- Clicking an official-source item (e.g. search for "35mm Film Portrait")
  loads its `[placeholder]`-bearing prompt into the editor.
- Clicking a community-source item loads its concrete prompt into the editor.
- The existing `/templates` → Generate flow (unrelated to this change) still
  works end to end for at least one selected template.

- [ ] **Step 4: Run the full test suite one more time**

Run: `venv/bin/python -m pytest tests/ -v`
Expected: all tests pass (this confirms the migration didn't corrupt the
`templates` schema or break any existing route).

- [ ] **Step 5: Commit** (only if Steps 1-2 modified tracked files — the
  migration writes to `prompts.db` and `static/template_thumbs/`, neither of
  which should be committed; check `.gitignore` covers `prompts.db` and add
  `static/template_thumbs/` to `.gitignore` if it doesn't already ignore
  generated static assets)

```bash
git status --short
# If static/template_thumbs/ or prompts.db show as untracked/modified and
# aren't already gitignored, add an ignore rule instead of committing
# hundreds of generated JPEGs:
echo "static/template_thumbs/" >> .gitignore
git add .gitignore
git commit -m "chore: gitignore generated template thumbnails"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1 = schema, Task 2 = official template extraction,
  Tasks 3-6 = the four-step migration script + CLI, Task 7 = `/api/templates`,
  Task 8 = frontend rewrite, Task 9 = the actual batch run + manual
  verification the spec's "Testing" section calls for. All spec sections are
  covered.
- **Corrected count:** spec said "65 official / 943 total" — verified against
  the actual `templates/templates.html` source and corrected to 99 official /
  977 total in Global Constraints; every task's assertions use the corrected
  numbers.
- **Type/name consistency check:** `thumbnail_url` (endpoint, Task 7) matches
  what Task 8's frontend reads; `thumbnail_prompt` / `thumbnail_path` /
  `community_prompt_id` column names are identical across Tasks 1, 3, 4, 5, 7;
  `CATEGORIES` (Task 4) is the same 14-item list the spec and Global
  Constraints define; `get_client()` / `get_google_client()` (Task 6) match
  the real function names in `app.py:185` and `app.py:64`.
