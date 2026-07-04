# Unified templates page — design

## Purpose

The `/templates` page currently has two disconnected prompt sources:

1. A hardcoded JS `TEMPLATES` object in `templates/templates.html` (5 tabs → 13
   subcategories → 65 placeholder-style templates like `Generate a [platform] UI
   screenshot...`). None of these have preview thumbnails.
2. The `community_prompts` SQLite table (878 rows), surfaced through a separate
   "🌐 社群精選" tab via `/api/community-prompts` (`app.py:876-902`). Its `category`
   column has 100+ distinct values with heavy duplication (e.g. `人像攝影` /
   `Portrait Photography` / `📸 人像攝影` are the same concept spelled three ways).
   835/878 rows have an `image_url` (the original community source image); 856/878
   have `prompt` text.

This redesign merges both into one data source with a clean, LLM-assigned
category taxonomy, and gives every template a Gemini-generated preview thumbnail
so browsing the page no longer means guessing from a name alone.

## Non-goals

- No change to `/generate`, `/api/v1/generate`, or any generation-quality logic.
  This is purely a templates-browsing feature.
- No live/on-demand thumbnail generation. All thumbnails are produced once by an
  offline batch script; the running Flask app only ever reads existing files.
- No re-scraping or refreshing of `community_prompts` source data — this design
  consumes the table as it exists today (878 rows) and does not touch the import
  pipeline that populates it.
- No deletion of the `community_prompts` table or its existing
  `/api/community-prompts` route in this pass — see "Migration & rollback" below
  for why it's left in place.

## Unified category taxonomy

Fourteen categories, chosen by collapsing the 13 official subcategories and the
100+ community category strings by subject matter:

1. 人像攝影 Portrait & Fashion Photography
2. 場景與街拍 Scenes & Documentary Photography
3. UI 與介面設計 UI / App Interfaces
4. 海報與視覺設計 Posters & Visual Design
5. 字體排版 Typography & Calligraphy
6. 資訊圖表 Infographics & Data Viz
7. 電商與產品 E-Commerce & Product
8. 品牌識別 Brand & Identity
9. 插畫與藝術 Illustration & Art
10. 角色設計 Character Design
11. 建築與空間 Architecture & Space
12. 歷史文化 Historical & Cultural
13. 奇幻科幻／遊戲 Fantasy, Sci-Fi & Gaming
14. 其他／創意混搭 Comparisons, Mashups & Other

This list is a fixed enum passed into every classification LLM call (see
"Classification" below) — the model picks one of these 14, it never invents a
new category name. This is what keeps the result clean where today's data is
messy.

## Data model

New table, `templates`, created via a plain `CREATE TABLE` in a migration
script (this project has no migration framework — `prompts.db` tables are
created ad hoc at startup, e.g. `community_prompts`'s own creation code is
elsewhere in the import tooling, not in `app.py`):

```sql
CREATE TABLE templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK(source IN ('official', 'community')),
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    prompt TEXT NOT NULL,
    thumbnail_prompt TEXT NOT NULL,
    thumbnail_path TEXT,
    platform TEXT,
    author TEXT,
    source_url TEXT,
    score INTEGER,
    community_prompt_id INTEGER
);
```

Column notes:
- `prompt`: what gets loaded into the editor textarea when a user clicks the
  template. For `source='official'` rows this retains `[placeholder]` syntax
  (that's the point of a template). For `source='community'` rows this is the
  original concrete prompt, copied verbatim from `community_prompts.prompt`
  (or, for the ~22 rows where that's empty, falls back to the row's `title`).
- `thumbnail_prompt`: always concrete, always placeholder-free. For official
  rows this is an LLM-authored rewrite of `prompt` with plausible concrete
  values substituted. For community rows this is identical to `prompt` unless
  `prompt` was empty, in which case it's also the `title` fallback.
- `thumbnail_path`: relative path like `static/template_thumbs/143.jpg`, null
  until the batch script has generated it. The frontend treats null as "no
  thumbnail yet" and shows a placeholder icon (never a broken `<img>`).
- `community_prompt_id`: nullable FK back to `community_prompts.id`, null for
  official rows. Lets the migration script be re-run idempotently (see below)
  and lets a future refresh join back to the source row if needed.

## Components and data flow

### 1. Official template transcription (input data, not code)

The 65 templates currently live only as JS literals in
`templates/templates.html:298-458`. The migration script needs them as Python
data. This is a one-time transcription: a Python list of dicts
`{category_tab, subcategory, title, prompt}` copied from the JS object,
committed as a plain data file, e.g. `scripts/official_templates_data.py`. No
placeholder-filling happens here — this file is a faithful copy of what's in
the JS today, prompt text unchanged.

### 2. Migration script — `scripts/migrate_templates.py`

Run manually once (`venv/bin/python scripts/migrate_templates.py`), not wired
into any Flask route or startup path. Supports `--dry-run N` to process only
the first N rows and print results without writing to the DB or calling the
image API, so the classification/rewrite prompt can be sanity-checked cheaply
before committing to the full 943-row run.

Steps, in order:

**Step A — Load rows.** Read the 65 dicts from
`scripts/official_templates_data.py` and all 878 rows from `community_prompts`
via `sqlite3`.

**Step B — Insert skeleton rows.** Insert into `templates` immediately with
`category` and `thumbnail_prompt` left as placeholders (empty string) —
this makes the rest of the script resumable: subsequent steps `UPDATE` rows
where the relevant column is still empty/null, so a re-run after a crash only
redoes unfinished work instead of re-inserting duplicates. `community_prompt_id`
is set here so re-runs can match "have I already inserted this community row?"
by that column instead of by re-scanning content.

**Step C — Batched classification + thumbnail-prompt authoring.** Process rows
in batches of 20 via the existing OpenAI chat client (`get_client()`,
`app.py:349`, same `gpt-4o` model already used by `/enhance-prompt`). One call
per batch, structured output requested as a JSON array:

```json
[
  {"id": 143, "category": "UI 與介面設計", "thumbnail_prompt": "A dark-mode iOS fitness app home screen showing today's step count, a calorie ring chart, and a bottom tab bar with 4 icons, clean modern sans-serif UI, high-fidelity screenshot"}
]
```

For community rows whose `prompt` is already concrete, the prompt sent to the
classifier still asks for `thumbnail_prompt`, but instructs the model to
"return the prompt unchanged unless it contains bracketed placeholders" — this
keeps the two source types going through one uniform code path instead of two
branches with duplicated batching/retry logic. Only rows with
`category = ''` are selected for a batch, so re-running the script after a
partial failure skips already-classified rows.

Batch failures (malformed JSON, API error) are caught per-batch, logged with
the row ids in that batch, and the script continues to the next batch rather
than aborting the whole run — a failed batch just leaves those rows'
`category` empty, to be retried on the next invocation.

**Step D — Batched thumbnail generation.** For every row where
`thumbnail_path IS NULL AND thumbnail_prompt != ''`, call Gemini via the same
pattern as `_generate_gemini` (`app.py:448-501`): model
`gemini-3.1-flash-lite-image`, `aspect_ratio="1:1"` (thumbnails are square
regardless of what size the full generation would use), `n=1`. Unlike
`_generate_gemini`, this script does not write to the `prompts`/job-tracking
tables — it decodes the returned base64 image, saves it directly to
`static/template_thumbs/{id}.jpg`, and sets `thumbnail_path` in the same
transaction as the file write (write file first, then commit the DB row, so a
crash between the two just looks like "no thumbnail yet" on the next run,
never a DB row pointing at a missing file).

Calls are made sequentially with a small delay (not concurrently) to stay
under whatever rate limit the Gemini API enforces — this script runs once,
optimizing for "doesn't get rate-limited" over "finishes in the minimum
possible time". At $0.001/image (`_MODELS["gemini-3.1-flash-lite-image"]`,
`app.py:219-222`) the full 943-row run costs under $1.

**Step E — Summary.** Print counts: rows inserted, rows classified, rows with
a thumbnail, rows still missing a thumbnail (and why, if the failure was
logged in step C or D) — so a human can tell at a glance whether a second run
is needed.

### 3. New endpoint — `GET /api/templates`

Replaces `/api/community-prompts` as what `templates.html` fetches (the old
route and the `community_prompts` table are left in place — see "Migration &
rollback"). Returns rows grouped by category, same shape style as the existing
`/api/community-prompts` grouping in `app.py:887-901`:

```json
{
  "人像攝影 Portrait & Fashion Photography": [
    {
      "id": 143,
      "title": "35mm Film Portrait",
      "source": "official",
      "prompt": "A vintage 35mm film photograph of [subject description]...",
      "thumbnail_url": "/static/template_thumbs/143.jpg",
      "platform": null,
      "author": null,
      "source_url": null
    }
  ]
}
```

`thumbnail_url` is `null` when `thumbnail_path` is null (script hasn't reached
that row yet, or generation failed for it) — the frontend must handle this
without erroring.

### 4. Frontend — `templates/templates.html` rewrite

Remove the hardcoded `TEMPLATES` JS object (`templates.html:298-458`) and the
tab bar's split between the 5 official tabs and the "🌐 社群精選" tab
(`renderTabs`/`switchTab`, `templates.html:497-514`). Replace with:

- A single sidebar list fetched from `/api/templates` on page load, grouped by
  the 14 unified categories (collapsible sections, reusing the existing
  `.cat-header` / `.cat-items` / `toggleCat()` styling already in the file —
  the visual chrome doesn't need to change, only what feeds it).
- Every item shows its thumbnail (40×40px, same treatment as the current
  community-tab thumbnail rendering at `templates.html:540`) — never blank,
  falling back to a generic placeholder icon when `thumbnail_url` is null.
  This removes the current asymmetry where official templates show no image
  and only community items do.
- Clicking an item loads `prompt` into the editor textarea exactly like today's
  `selectTemplate` / `selectCommunity` (`templates.html:553-570, 593-598`) —
  behavior unchanged, only the data source is unified.

## Error handling

- Migration script: per-batch try/except in classification (Step C) and
  per-row try/except in thumbnail generation (Step D), both logged and
  skipped rather than aborting the run, as described above.
- `/api/templates`: no new failure modes beyond what `/api/community-prompts`
  already has (a `sqlite3.connect` failure) — same try-implicit style (the
  existing route has no explicit error handling either; this one matches it).
- Frontend: `thumbnail_url: null` and any `<img>` load failure both fall back
  to the same placeholder treatment (`onerror` handler, matching the existing
  `onerror="this.style.display='none'"` pattern at `templates.html:540`, but
  swapped to show a placeholder box instead of hiding the element entirely,
  since every item is expected to have *some* visual now).

## Testing

- New endpoint test: `tests/test_app.py` gets a test hitting `/api/templates`
  against a small fixture-seeded `templates` table, asserting the response is
  grouped by category and that a row with `thumbnail_path = NULL` serializes
  `thumbnail_url` as `null` (not an empty string or missing key).
- Migration script: no automated test (it's a one-time offline script, not
  application code) — verified instead via `--dry-run 10`, manually inspecting
  the 10 classified rows and their authored `thumbnail_prompt`s before running
  the full batch, and via the Step E summary counts after the full run.
- Manual verification: after the full migration run, load `/templates` in a
  browser and confirm all 14 categories render, every visible item has a
  thumbnail (no broken images), and clicking both an official-source and a
  community-source item correctly populates the prompt textarea.

## Migration & rollback

`community_prompts` and `/api/community-prompts` are left untouched by this
change — `templates` is an additive table populated by copying from
`community_prompts`, not a rename or in-place transform. If the new page needs
to be rolled back, reverting the `templates.html` / route changes is
sufficient; no data cleanup is required since the old table and route still
exist and still work.
