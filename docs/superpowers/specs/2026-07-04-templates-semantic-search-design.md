# Templates semantic search — design

## Purpose

The `/templates` page (see
`docs/superpowers/specs/2026-07-04-unified-templates-page-design.md` for the
unified `templates` table this builds on) has 977 templates browsable only by
clicking through 14 collapsible categories. There is no way to type what you
want and find a matching template — a user looking for "生日海報" has no way
to discover a template titled "Birthday Poster" or "City Series Poster"
unless they already know which category to open and scroll through it.

This adds a search box that does semantic (intent) matching, not literal
keyword matching, backed entirely by a local Ollama instance — no external
API calls, no per-query cost, no rate limits.

## Non-goals

- No change to the existing category-browsing UI (`renderTemplateList()`,
  `templates/templates.html:346-363`) — search is additive, shown instead of
  the category list only while the search box is non-empty.
- No re-ranking or filtering *within* an already-expanded category — search
  results replace the whole list with a single flat ranked list across all
  977 templates (decided during brainstorming: category structure is dropped
  entirely for the duration of a search).
- No embedding model training or fine-tuning — uses the off-the-shelf
  `bge-m3` model as served by Ollama.
- No fallback to a remote embedding API if local Ollama is unavailable —
  if Ollama isn't running or `bge-m3` isn't pulled, search fails with a clear
  error and the rest of the page (category browsing) keeps working.

## Architecture

**Embedding model:** `bge-m3` via Ollama (`ollama pull bge-m3`, ~1.2GB,
one-time local setup) — chosen over `nomic-embed-text` because the 977
templates are heavily bilingual (Traditional/Simplified Chinese category
names and titles, English/Chinese-mixed prompt text), and `bge-m3` is a
multilingual embedding model where `nomic-embed-text` is English-optimized.

**Two-phase design**, matching the pattern already established by
`migrate_templates.py`:

1. **Offline batch step** (`generate_template_embeddings.py`, one-time +
   re-runnable): for every `templates` row missing an embedding, concatenate
   `title + category + prompt` and POST it to Ollama's local
   `http://localhost:11434/api/embed` endpoint, store the returned float
   vector in a new `templates.embedding` column.
2. **Online query step** (`GET /api/templates/search?q=...`): embed only the
   user's search string (one Ollama call, small and fast), then compute
   cosine similarity against all 977 stored vectors in Python (no vector DB
   needed at this scale), return the top 30 by score.

This keeps the expensive part (embedding 977 templates) offline and one-time,
so every live search is a single small Ollama call plus an in-memory
similarity scan — no per-keystroke cost blowup, no dependency on the same
Gemini quota that the thumbnail pipeline already ran into.

## Data model

Add one column to the existing `templates` table (see prior spec for the
rest of the schema):

```sql
ALTER TABLE templates ADD COLUMN embedding BLOB;
```

Stored as the JSON-encoded list of floats (`json.dumps(vector).encode()`),
not raw binary struct packing — simpler to debug by hand (`sqlite3` CLI can
show it as readable-ish text), and at ~1024 dimensions × 977 rows the size
difference vs. packed binary floats is immaterial (a few MB either way).

## Components and data flow

### 1. `generate_template_embeddings.py` (new, root-level script)

Follows the same idempotent-batch shape as `migrate_templates.py`:

- `get_rows_without_embedding(conn, limit=None) -> list[sqlite3.Row]` — rows
  where `embedding IS NULL`.
- `embed_text(text: str) -> list[float]` — POSTs
  `{"model": "bge-m3", "input": text}` to
  `http://localhost:11434/api/embed`, returns the embedding vector from the
  response. Raises on any HTTP/connection error (caller catches per-row, same
  "log and continue" pattern as the Gemini thumbnail step).
- `run_embedding_batch(conn, limit=None) -> tuple[int, int]` — `(succeeded,
  failed)`, mirroring `run_thumbnail_batch`'s shape from `migrate_templates.py`.
- CLI: `venv/bin/python generate_template_embeddings.py [--dry-run N]`,
  prints a summary line matching `migrate_templates.py`'s style.

### 2. `GET /api/templates/search?q=<query>`

New Flask route in `app.py`, near the existing `/api/templates` route:

- Empty/missing `q` → `400 {"error": "q is required"}`.
- Embeds `q` via the same `embed_text()` function (imported from
  `generate_template_embeddings.py`).
- Loads all `(id, embedding)` pairs from `templates` where `embedding IS NOT
  NULL`, computes cosine similarity between the query vector and each, sorts
  descending, takes the top 30 ids.
- Fetches full row data for those 30 ids (same columns as `/api/templates`:
  `id, source, title, prompt, thumbnail_path, platform, author, source_url,
  score`), returns a **flat JSON array** (not grouped by category — this is
  the key shape difference from `/api/templates`), each item shaped
  identically to an item inside one of `/api/templates`'s category arrays,
  plus a `score` field (cosine similarity, 0-1) so the frontend can display
  relative match strength if useful:

```json
[
  {"id": 143, "title": "35mm Film Portrait", "source": "official",
   "prompt": "...", "thumbnail_url": "/static/template_thumbs/143.jpg",
   "platform": null, "author": null, "source_url": null, "similarity": 0.83}
]
```

- If Ollama is unreachable or `bge-m3` isn't pulled, the route catches the
  connection error and returns `503 {"error": "本地 Ollama 未啟動或缺少
  bge-m3 模型，請執行 ollama pull bge-m3 並確認 ollama serve 正在執行"}`.

### 3. Frontend — `templates/templates.html`

- Add a search `<input>` above `#templateList` in the sidebar (inside
  `.sidebar-header` or immediately below it — same visual area as the
  existing header/badge).
- On `input` event, debounced 300ms: if the trimmed value is non-empty, call
  `/api/templates/search?q=...` and render a flat list (reusing the same
  per-item markup as `renderTemplateList()` — thumbnail + title, no category
  header, no collapsing) into `#templateList`; if the value is empty, call
  `renderTemplateList()` again to restore the normal grouped view (using the
  already-fetched `TEMPLATE_DATA`, no re-fetch needed).
- On a 503 from the search endpoint, show the error message in place of the
  list (same treatment as the existing `catch` block in `loadTemplates()`)
  rather than silently falling back to category browsing — the user should
  know search is unavailable, not think their query just had no matches.
- Clicking a search-result item behaves exactly like clicking a
  category-list item: loads `prompt` into the editor textarea (reuses
  `selectTemplateItem`'s logic, adapted to index into the flat search-results
  array instead of `TEMPLATE_DATA[cat]`).

## Error handling

- `embed_text()` failures (Ollama down, model missing, malformed response)
  propagate as exceptions from the search route, caught once at the route
  level and turned into the 503 described above — no retry loop on the
  request path (retrying a down local service inline would just make the
  request hang).
- `generate_template_embeddings.py`'s batch loop catches failures per-row
  (same pattern as `migrate_templates.py`'s thumbnail step) and continues,
  since a single malformed response shouldn't abort embedding the other 976
  rows.
- Empty result set (a valid query that matches nothing above some
  similarity floor) is not treated as an error — the design does not impose
  a similarity threshold; the top 30 by score are always returned, even if
  the 30th-best match is a poor one. (A threshold could hide a genuinely
  relevant single result if the corpus has few close matches; showing "the
  best 30 we have" and letting the user judge is simpler and avoids picking
  an arbitrary cutoff number.)

## Testing

- `generate_template_embeddings.py`: unit tests mocking the Ollama HTTP call
  (`unittest.mock.patch` on the `requests.post` call inside `embed_text`),
  verifying `run_embedding_batch` stores the returned vector and skips rows
  that already have one (same idempotency test shape as
  `test_migrate_templates.py`).
- Cosine similarity ranking: a pure-function unit test with small hand-built
  vectors (e.g. 3-dimensional) verifying the top-N ordering is correct,
  independent of any Ollama call.
- `/api/templates/search`: a test with a fixture-seeded `templates` table
  (rows with known embeddings) and a mocked `embed_text`, asserting the
  response is a flat array sorted by descending similarity, and a separate
  test asserting a mocked connection failure produces the 503 error shape.
- Manual verification: after running the batch script for real, search for
  a Chinese query with no literal keyword overlap with any prompt (e.g.
  "生日海報") and confirm a plausible bilingual/English match (e.g. a poster
  or event template) appears in the top results.

## Setup step (not part of automated tests)

`ollama pull bge-m3` must be run once on the machine before
`generate_template_embeddings.py` can succeed. This is a manual,
environment-level step (like setting `OPENAI_API_KEY`/`GOOGLE_API_KEY` was
for the previous migration) — not something the application or its scripts
automate.
