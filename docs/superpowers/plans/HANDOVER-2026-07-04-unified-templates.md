# Handover — 2026-07-04 (Unified Templates Page)

## What shipped

Merged the two disconnected template sources (99 hardcoded JS official templates + ~878 DB community rows) into one `templates` SQLite table with an LLM-assigned 14-category taxonomy and per-template Gemini-generated thumbnails, served through `/api/templates` and rendered as a single unified sidebar list on `/templates`.

### New files
- `migrate_templates.py` — offline, resumable migration script. Steps: insert skeleton rows → GPT-4o classification (batches of 20) → Gemini thumbnail generation (sequential). Supports `--dry-run N`. Safe to re-run: skips already-inserted, already-classified, and already-thumbnailed rows.
- `extract_official_templates.js` — Node one-shot that evaluates `const TEMPLATES = {...}` from `templates.html` and dumps 99 entries as `official_templates.json` (committed). Avoids manual transcription errors.
- `official_templates.json` — the 99 official templates as a flat JSON array of `{tab, subcategory, title, prompt}`.
- `tests/test_migrate_templates.py` — 7 tests (idempotent insert, classification with invalid-category filtering, thumbnail generation, dry-run CLI orchestration).
- `tests/test_extract_official_templates.py` — 2 tests (99 items, known template titles present).

### Modified files
- `app.py` — `templates` table added to `init_db()` (13 columns with CHECK constraint on `source`). New `GET /api/templates` route, grouped by the 14-category taxonomy, with `thumbnail_url` derived from `thumbnail_path` (null-safe).
- `templates/templates.html` — hardcoded `const TEMPLATES` object (lines 298-458) removed. Tab bar removed. `renderTabs`/`switchTab`/`loadCommunityTab`/`renderCommunityTemplates`/`selectCommunity`/`renderTemplates`/`selectTemplate` deleted. Replaced with unified `loadTemplates()` → `renderTemplateList()` fetching from `/api/templates`, collapsible category sections with thumbnail images.
- `tests/test_app.py` — 3 new tests: table schema, `/api/templates` response shape (null thumbnail, grouped categories, source/platform fields), hardcoded-TEMPLATES-gone assertion.
- `.gitignore` — `static/template_thumbs/` added (generated JPEGs, not committed).

### Design spec and plan
- Design: `docs/superpowers/specs/2026-07-04-unified-templates-page-design.md`
- Implementation plan: `docs/superpowers/plans/2026-07-04-unified-templates-page.md`

## Final state

```
3bec309 fix: call init_db() in migration script to ensure templates table exists
db28f8e chore: gitignore generated template thumbnails
ecbc7b1 feat: rewrite templates page to use unified /api/templates endpoint
9891fcb feat: add GET /api/templates endpoint
5a8cf68 feat: wire up migrate_templates.py CLI with --dry-run and summary output
4732552 feat: migration script step D — batched Gemini thumbnail generation
f2c038c feat: migration script step C — batched LLM classification
c66d52d feat: migration script step A/B — load and insert skeleton template rows
7d49ca6 feat: extract official templates from JS into official_templates.json
efb2b3f feat: add templates table schema
```

- **Tests**: `venv/bin/python -m pytest tests/ -q` → 34 passed (22 original + 12 new)
- **Tree**: clean (all changes committed)

## Deviations from the plan

Three adjustments made during implementation:

1. **Thumbnail path stored as relative** (`static/template_thumbs/{id}.jpg`) instead of absolute path. The plan's `run_thumbnail_batch` stored `os.path.join(STATIC_THUMB_DIR, ...)` (absolute), which would break the endpoint's `thumbnail_url = f"/{thumb_path}"`. Fixed by writing to the absolute path but storing the relative one.

2. **`row_factory` save/restore** in `get_unclassified_rows()` and `get_rows_needing_thumbnail()`. The plan's functions used `r["id"]` key access but didn't set `row_factory = sqlite3.Row` — this broke when the caller's connection hadn't set it (e.g., in tests). Fixed with a temporary save/restore pattern.

3. **`init_db()` called from migration script**. The plan assumed the `templates` table already existed (created by `app.py` startup). Added `from app import init_db; init_db()` at the top of `main()` so the migration script works standalone on a fresh `prompts.db`.

## What still needs doing

### Manual migration run (requires API keys)

The data migration (`migrate_templates.py`) requires `OPENAI_API_KEY` and `GOOGLE_API_KEY` in the environment. The code is fully tested (all steps mocked), but the actual LLM classification and image generation batch has not been run.

```bash
# Set API keys
export OPENAI_API_KEY=sk-...
export GOOGLE_API_KEY=...

# 1. Sanity-check on 10 rows (~$0.01 for classification + $0.01 for thumbnails)
venv/bin/python migrate_templates.py --dry-run 10

# 2. Spot-check classification quality
sqlite3 prompts.db "SELECT title, category, thumbnail_prompt FROM templates WHERE category != '' LIMIT 5;"

# 3. Full run — this will process all ~977 rows
#    (~977 GPT-4o calls in batches of 20 = ~49 API calls,
#     ~977 Gemini image generations at $0.001 each ≈ $0.98)
venv/bin/python migrate_templates.py

# 4. If summary shows unfinished rows, re-run (idempotent)
venv/bin/python migrate_templates.py

# 5. Browser verification
venv/bin/python app.py
# Open http://localhost:5000/templates
# Confirm: all 14 categories visible, thumbnails load, clicking items populates editor
```

### Potential follow-up work

1. **Community data refresh** — `community_prompts` is untouched. If more prompts are imported later, the migration script can be re-run; it will only insert new `community_prompt_id` values it hasn't seen, classify them, and generate thumbnails.
2. **Template search/filter** — the `/templates` page currently shows all templates grouped by category. A search box (leveraging the existing FTS index on `community_prompts`, or a new one on `templates`) would help users find specific templates among 977 items.
3. **Thumbnail refresh** — if the LLM-authored `thumbnail_prompt` for official templates is ever updated (e.g., the model improves), deleting `thumbnail_path` and re-running the migration script's Step D would regenerate just the thumbnails without touching classification.

## Key design decisions

- **Source of truth**: The `templates` table is the single source of truth for the `/templates` page. `community_prompts` and its `/api/community-prompts` endpoint are left untouched (additive, not a rename), so rollback is just reverting the frontend/route changes.
- **Classification model**: `gpt-4o` (same as `/enhance-prompt`) — consistent with the app's existing OpenAI usage. Temperature 0.2 for consistency.
- **Thumbnail model**: `gemini-3.1-flash-lite-image` (same as `_generate_gemini`) — $0.001/image, 1:1 aspect ratio.
- **Resumability**: The migration script uses empty-string sentinels (`category = ''`, `thumbnail_path IS NULL`) rather than NULL, making SELECT-based resumption simpler.
- **Category taxonomy**: Fixed 14-item enum. The classification prompt constrains the LLM to pick from exactly these strings. Invalid categories returned by the model are dropped and retried on next run.
- **Template count**: 99 official (not the stale "21 + 65" UI badge claim, nor the spec's initial "65" estimate). Actual count verified by the extraction script.
