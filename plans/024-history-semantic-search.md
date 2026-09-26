# Plan 024: Semantic search over your own generation history

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat bb87099..HEAD -- app.py templates/history.html tests/test_app.py generate_template_embeddings.py`
> Changes from plans 018–023 are expected. Locate functions by name.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW (additive; degrades gracefully without Ollama)
- **Depends on**: plans/021-move-images-out-of-sqlite.md (both add `prompts` columns in `init_db`; run after it)
- **Category**: direction (feature)
- **Planned at**: commit `bb87099`, 2026-09-26

## Why this matters

The History page filters with a substring match on the prompt text
(`getFilteredEntries`, `templates/history.html:504-511`). "The rainy neon city
thing I made last month" won't match a prompt that says "cyberpunk street at night,
wet asphalt". The app already has a working semantic-search stack for
the templates library: a local Ollama `bge-m3` embedding, cosine similarity,
and a 503 with a helpful message when Ollama is down. Reusing it for the
`prompts` table gives history search by meaning, with no new dependency.

## Current state

- `embed_text(text, model="bge-m3") -> list` (`app.py:76-86`) calls Ollama at
  `OLLAMA_URL = "http://localhost:11434"` and raises on any failure.
- `cosine_similarity(a, b)` (`app.py:89-95`).
- Reference endpoint to mirror: `/api/templates/search` (`app.py:1160-1207`).
  It embeds the query; on exception it returns 503 with a Chinese instruction
  message (`本地 Ollama 未啟動或缺少 bge-m3 模型…`). It loads rows
  `WHERE embedding IS NOT NULL`, `json.loads` each embedding (skipping malformed ones
  with a `print`), sorts by similarity, and takes the top 30.
- Embeddings are stored as JSON text in a BLOB column (`templates.embedding`).
  The batch script `generate_template_embeddings.py` backfills them. Use it as
  the CLI pattern; its tests are in `tests/test_generate_template_embeddings.py`.
- Tests mock embeddings with `patch("app.embed_text", return_value=[0.9, 0.1])`
  (see `tests/test_app.py` around lines 435-480).
- The History page loads `/history` (latest 200 rows) into `state.entries`,
  has `#search-input` bound to `state.search`, and renders via `renderHistory()`.
- Successful rows are saved by `save_prompt` from the provider threads.

## Scope
**In scope**: `app.py`, `templates/history.html`, `tests/test_app.py`, a new
`generate_history_embeddings.py`, and a new `tests/test_generate_history_embeddings.py`.
**Out of scope**: the templates search endpoint, `embed_text` itself, and other pages.

## Steps

### Step 1: Column + embed-on-save (best effort)
- Add `embedding BLOB` to `prompts` (same `init_db` migration loop).
- Add `_embed_prompt_async(pid, text)`. It starts a daemon thread that calls
  `embed_text(text)` and runs `UPDATE prompts SET embedding=? WHERE id=?` with
  `json.dumps(vec)`. It **swallows all exceptions** (Ollama is optional), and prints one line on failure.
- Call it from `save_prompt` only when `success` is truthy. Use
  `original_prompt or prompt` as the text (the user's words, not the
  negative-prompt-suffixed version).

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass. Existing tests must not hit the network. Add
an autouse fixture **inside the new tests only** if needed. Prefer to make
`_embed_prompt_async` a no-op when `app.config["TESTING"]` is true, unless a test patches it explicitly.

### Step 2: `GET /api/history/search?q=`
Mirror `/api/templates/search`: return 400 without `q`, 503 (the same message) when the embedding
fails, and score rows `WHERE success=1 AND embedding IS NOT NULL`. Return the top 30 rows in the **same
field shape as `GET /history`** (same SELECT columns, plus `similarity`), so the
page can render them with its existing code.

**Verify**: new tests pass (below).

### Step 3: Backfill script
`generate_history_embeddings.py`, modeled on `generate_template_embeddings.py`:
it embeds rows where `success=1 AND embedding IS NULL`, commits as it goes, and supports
`--db` and a dry-run flag matching the template script's style. The executor
does NOT run it against the real `prompts.db`. Put the command in the report for the operator.

### Step 4: History page UI
Next to `#search-input`, add a small toggle button `語意` (semantic).
When it is on, pressing Enter in the search box calls `/api/history/search?q=…`,
replaces `state.entries` with the results, and calls `renderHistory()` with the local
substring filter bypassed. Clearing the box or turning the toggle off reloads `/history`. On 503, show
the server's error via `toast(msg, 'error')`. Escape everything as the page already does.

**Verify**: with Ollama running (`ollama list` shows `bge-m3`), searching a
paraphrase of a known prompt returns it first. With Ollama stopped, a toast shows the 503 message.

## Test plan
In `tests/test_app.py`:
- `test_history_search_ranks_by_similarity`: insert 2 successful rows with
  embeddings `[1,0]` and `[0,1]` via SQL; patch `app.embed_text` to return `[0.9, 0.1]` → the first result is the `[1,0]` row, and it has `similarity`.
- `test_history_search_excludes_failed_rows`.
- `test_history_search_ollama_down_503` (`side_effect=ConnectionError`).
- `test_history_search_requires_q` → 400.
- `test_save_prompt_triggers_embedding_for_success_only`: patch `_embed_prompt_async` and assert that it is called once for success and never for a failure.
New `tests/test_generate_history_embeddings.py`, modeled on `tests/test_generate_template_embeddings.py`.

## Done criteria
- [ ] Tests exit 0 with ≥ 6 new tests; no test makes a real HTTP call to Ollama
- [ ] `grep -n "/api/history/search" app.py templates/history.html` → matches in both
- [ ] The real `prompts.db` is not modified by the executor
- [ ] Only in-scope files are modified or created

## STOP conditions
- Plan 021 has not landed (`grep -n result_path app.py` is empty). Wait for it.
- Embedding in a thread at save time causes SQLite "database is locked" errors in tests. Report it rather than adding retries.

## Maintenance notes
- Full-scan cosine similarity in Python is fine for hundreds to a few thousand rows. Past ~5k rows, consider caching vectors in memory or using sqlite-vec.
- If the embedding model changes, both the template and history embeddings must be regenerated (the vectors are not comparable).
