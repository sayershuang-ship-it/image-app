# Plan 023: "Compare" mode — one prompt, several models, side-by-side results with cost

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat bb87099..HEAD -- app.py templates/studio.html static/app.js tests/test_app.py`
> Changes from plans 018–022 are expected. Locate functions by name.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW (additive; no existing route changes)
- **Depends on**: plans/019-shared-generation-request-parser.md (hard); plans/022 (soft — its per-result `usage` is shown if present)
- **Category**: direction (feature)
- **Planned at**: commit `bb87099`, 2026-09-26

## Why this matters

The app now offers four image models (`gpt-image-2`, `gpt-image-2.5-sunburst`,
`gpt-image-2.5-flare`, `gemini-3.1-flash-lite-image`) with different prices
and uncertain quality trade-offs. Right now, comparing them means generating
once, switching the dropdown, and generating again, then scrolling between
results. A Compare mode sends one prompt to 2–4 selected models at once and
shows the results in a grid, one column per model, with the per-image cost.

## Current state

- Backend job system: `_run_generation(job_id, prompt, image_b64, quality, size, n, model, original_prompt)`
  (`app.py:795`) runs a provider in a thread and writes `_jobs[job_id]`.
  `/batch-generate` (`app.py:957-994`) is the closest pattern: it creates one
  job per item and returns `{batch_id, jobs:[{job_id, prompt}], estimated_cost}`.
- After plan 019: `_parse_generation_request(data)` returns
  `{original_prompt, prompt, image_b64, model, quality, size, n}` and raises
  `_BadRequest`; `_parse_model_settings(data)` validates one model.
- Quality levels differ per model: gemini has only `standard`; 2.5 has
  `low…max`. `_parse_model_settings` already coerces an invalid quality to the
  model's first quality.
- Sizes differ per model: gemini has no custom sizes and no 2048x2048.
  `validate_size_for_model` returns an error for unsupported sizes.
- Frontend: `templates/studio.html`.
  - Mode toggle: buttons `#mode-single` and `#mode-batch`, `setMode(m)` (around line 670),
    and `state.mode`.
  - Ctrl+Enter and the generate button dispatch on `state.mode` (around lines 1038 and 1058).
  - `doBatchGenerate()` (around lines 1112-1170) is the pattern to copy: it
    renders placeholder slots, POSTs, then runs `Promise.all` over `pollJob`
    for each job and fills each slot.
  - Models come from `/api/models` into `MODELS` (`loadModels`, around line 686).
- Shared helpers in `static/app.js`: `$`, `escHtml`, `escAttr`, `toast`,
  `pollJob(jobId, startTime, onTick)`. **All interpolated strings must go
  through `escHtml`/`escAttr`** (see plan 011 — stored XSS was fixed this way).

## Scope
**In scope**: `app.py` (new route only), `templates/studio.html`, `tests/test_app.py`.
**Out of scope**: `static/app.js` (reuse, don't modify), other pages, `_run_generation`, and existing routes.

## Steps

### Step 1: Backend route `POST /compare-generate`
Body: `{prompt, models: [ids], quality, size, image_b64?, negative_prompt?, variables?}`.
- Validate that `models` is a list of 2–4 unique ids; otherwise return 400.
- For each model, call `_parse_generation_request({**data, "model": m, "n": 1})`.
  Collect per-model errors instead of aborting: `{"model": m, "error": msg}`.
  If **every** model fails, return 400 with the list of errors.
- Start one `_run_generation` thread per valid model (the same thread pattern
  as `/batch-generate`). Store `compare_id` in each job dict.
- Response: `{compare_id, jobs: [{model, job_id, quality, estimated_cost}], skipped: [{model, error}], estimated_cost: <sum>}`.
  `quality` is the value after coercion, so the UI can show "gemini ran at standard".

**Verify**: `venv/bin/python -c "import app"` → exit 0.

### Step 2: Backend tests
Patch `app.threading.Thread`, and `monkeypatch.setenv` both keys:
- `test_compare_starts_one_job_per_model` → 2 models → 2 jobs, 2 Thread starts.
- `test_compare_skips_unsupported_size`: `size: "2048x2048"` with `["gpt-image-2", "gemini-3.1-flash-lite-image"]`
  → 1 job, and gemini is listed in `skipped`.
- `test_compare_rejects_single_model` → 400.
- `test_compare_rejects_duplicates` → 400.
- `test_compare_coerces_quality_per_model`: `quality:"high"` → the gemini job reports `standard`.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass.

### Step 3: UI — the mode button and model picker
- Add `<button class="mode-btn" id="mode-compare">Compare</button>` next to the existing two buttons.
- In compare mode, hide `#model-select` and the count group, and show a
  checkbox list built from `MODELS` (label = `escHtml(m.name)`). Pre-check the
  first two. `setMode` must handle three modes (toggle `active` classes and visibility for all three).
- `updateButtons()` disables Generate unless 2–4 boxes are checked.
- The cost badge in compare mode shows the sum of `calcCost` per checked model (reuse the existing `calcCost` function by passing each model's config — read it first to see how it uses `currentModel`, and pass the model explicitly if needed).

**Verify**: run the app (`./start.sh`) → `/studio` → click Compare → checkboxes are listed; Generate is disabled with 1 box checked and enabled with 2.

### Step 4: UI — `doCompareGenerate()`
Copy the structure of `doBatchGenerate`: render one column per model with a
header (`escHtml(name)`, the quality actually used, `$cost`) and a spinner.
POST to `/compare-generate`, then fill each column as its `pollJob` resolves. Render
`skipped` models as a column with the escaped error. On completion, add the results to
`state.gallery` and `state.sessionCost` the same way `doBatchGenerate` does. If a result has
`usage` (plan 022), show `usage.total_tokens` in small muted text.
Wire Ctrl+Enter and the generate button: `state.mode === 'compare' ? doCompareGenerate() : ...`.

**Verify**: with the app running and keys set, compare `gpt-image-2` (low) against gemini on a short prompt → two columns fill in; the history page shows 2 new rows.
If no keys are available, verify the rendering with the browser devtools by stubbing `fetch` instead, and say so in your report.

## Done criteria
- [ ] Tests exit 0 with ≥ 5 new tests
- [ ] `grep -n "compare-generate" app.py templates/studio.html` → matches in both
- [ ] `grep -n '\${[a-zA-Z_.]*name' templates/studio.html` — every model-name interpolation in the new code is wrapped in `escHtml`/`escAttr`
- [ ] Single and Batch modes still work (manual check)
- [ ] Only in-scope files are modified

## STOP conditions
- Plan 019 has not landed (`grep -n _parse_generation_request app.py` is empty).
- `calcCost` in studio.html cannot price a non-current model without refactoring shared code (report; don't refactor `static/app.js`).

## Maintenance notes
- Reference images: models with `supports_edit: False` (gemini) still accept a reference in `_generate_gemini`, so compare passes `image_b64` to all of them. If that changes, filter here.
- A future "pick winner → re-run at high quality" button would fit on each column header.
