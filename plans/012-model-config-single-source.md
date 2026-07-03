# Plan 012: Pages read model config from /api/models — one source of truth

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- templates/studio.html templates/templates.html app.py tests/test_app.py`
> Changes from plans 006–011 are expected. Compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (touches the studio's core control flow)
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md; run AFTER 009 (cost fix) to avoid merge conflicts in the same regions
- **Category**: tech-debt + bug
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

Model metadata (qualities, sizes, prices, capabilities) is maintained in three
places: `_MODELS` in `app.py`, `MODEL_DEFAULTS` in `templates/studio.html`,
and hardcoded `<select>` options in `templates/templates.html`. The backend
already exposes `/api/models` — but **no page calls it**. The copies have
already drifted and produced a real bug: studio's Gemini cost table has the
key `'1024x1792'` written twice and `'1792x1024'` missing
(`templates/studio.html:685`), so the cost badge for 16:9 Gemini shows the
$0.042 fallback instead of $0.001. The templates page also shows
High/Medium/Low quality for Gemini, which only supports "standard". Making
the pages fetch `/api/models` removes the drift class entirely.

## Current state

- `app.py:632-646` — `GET /api/models` returns
  `{"models": [{id, name, provider, qualities, sizes, max_n, supports_edit, cost_table}, ...], "google_key_set": bool}`.
- `app.py:183-210` — `_MODELS` (authoritative). Note it has **no size labels**
  ("1:1", "16:9" strings) — those exist only in the frontends.
- `templates/studio.html:666-689` — `MODEL_DEFAULTS` clone, including the
  duplicate-key bug at line 685:

```js
    costTable: {
      standard: {'1024x1024':0.001,'1024x1792':0.001,'1024x1792':0.001,'1536x1024':0.001,'1024x1536':0.001},
    },
```

- `templates/studio.html:690-736` — `currentModel`, `calcCost`,
  `updateCostBadge`, `updateModelOptions`, model/quality/size listeners.
  `updateModelOptions()` is called synchronously from `init()` (line 643)
  before any await.
- `templates/studio.html:936-951` — quality-change handler filters
  `2048x2048` to high-quality only for gpt-image-2 (keep this behavior).
- `templates/templates.html:250-271` — hardcoded model/size/quality selects
  (`#modelSelect`, `#sizeSelect`, `#qualitySelect`), read once in
  `generateImage()` (lines 624-627).
- Size labels currently in studio (`sizeLabels`) — keep them as a
  **frontend-only label map**; labels are presentation, not model truth.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |

## Scope

**In scope**:
- `templates/studio.html` (model-config section of the JS)
- `templates/templates.html` (populate the three selects dynamically)
- `tests/test_app.py` (contract test for `/api/models`)

**Out of scope**:
- `app.py` — `_MODELS` and `/api/models` are already correct; do not add
  size labels to the backend.
- `templates/gallery.html`, `templates/history.html` (no model config there).
- Changing which models/sizes/prices exist.

## Git workflow

- Commit style: `refactor: studio + templates pages load model config from /api/models`

## Steps

### Step 1: Contract test first

In `tests/test_app.py`, add `test_api_models_shape(client)`:

- `GET /api/models` → 200; body has `models` (list, len ≥ 2) and
  `google_key_set` (bool).
- For each model: keys `id, name, provider, qualities, sizes, max_n,
  supports_edit, cost_table` all present; `qualities` non-empty; every
  quality key appears in `cost_table`.
- Specifically assert the Gemini entry's `cost_table["standard"]` contains
  BOTH `"1792x1024"` and `"1024x1792"` (the exact drift that bit the studio).

**Verify**: `venv/bin/python -m pytest tests/ -q -k models` → passes.

### Step 2: Studio fetches /api/models

In `templates/studio.html`:

1. Keep a frontend label map (rename to make its role clear):

```js
const SIZE_LABELS = {'1024x1024':'1024×1024 (1:1)','1792x1024':'1792×1024 (16:9)','1024x1792':'1024×1792 (9:16)','1536x1024':'1536×1024 (3:2)','1024x1536':'1024×1536 (2:3)','2048x2048':'2048×2048 (1:1 HQ)'};
```

2. Replace `MODEL_DEFAULTS` with a `MODELS` object populated from the API,
   and keep a minimal built-in fallback so the page still works if the fetch
   fails:

```js
let MODELS = null;   // id → config, from /api/models
async function loadModels() {
  try {
    const d = await fetch('/api/models').then(r => r.json());
    MODELS = Object.fromEntries(d.models.map(m => [m.id, m]));
    modelSelect.innerHTML = d.models.map(m => `<option value="${m.id}">${escHtml(m.name)}</option>`).join('');
    modelSelect.value = MODELS[currentModel] ? currentModel : d.models[0].id;
    currentModel = modelSelect.value;
  } catch (_) { /* keep hardcoded <option>s as fallback; MODELS stays null */ }
  updateModelOptions();
}
```

3. `updateModelOptions()` / `calcCost()` read from
   `MODELS?.[currentModel]` with the shape `{qualities, sizes, cost_table}`
   (note: API uses `cost_table`, the old clone used `costTable` — update the
   property name). `hasQuality` becomes
   `cfg.qualities.length > 1 || cfg.qualities[0] !== 'standard'`.
   When `MODELS` is null (fetch failed), fall back to the previous hardcoded
   behavior for gpt-image-2 only.
4. In `init()` (line ~641), replace the synchronous `updateModelOptions()`
   call with `await loadModels()` (init is already async).
5. Keep the `2048x2048`-only-at-high filtering in the quality-change handler,
   driven by `currentModel === 'gpt-image-2'` as today.
6. Delete the now-unused `MODEL_DEFAULTS` object.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass; and
`grep -n "MODEL_DEFAULTS" templates/studio.html` → 0 matches.

### Step 3: Templates page populates selects from /api/models

In `templates/templates.html`:

1. In `init()`, fetch `/api/models` (same pattern), populate `#modelSelect`
   from the list, and add a change listener that rebuilds `#sizeSelect`
   (using `SIZE_LABELS`-style short labels matching the current option text
   format, e.g. `1:1 (1024)`) and `#qualitySelect` from the selected model's
   `sizes`/`qualities`. Preserve current defaults where still valid
   (size `1024x1792`, quality `high` for gpt-image-2).
2. Keep the existing hardcoded `<option>`s in the HTML as the no-JS/fetch-
   failure fallback; the dynamic rebuild replaces them on success.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass.

### Step 4: Manual smoke (if a browser is available)

Open `/studio`: switch model to Gemini → quality group hides, size list loses
2048×2048, cost badge shows `~$0.001` for 1792×1024. Switch back → gpt
qualities return. On `/templates`: selecting Gemini shows a single
"Standard" quality. If no browser, note the skip.

## Test plan

- New: `test_api_models_shape` (Step 1) — the drift guard.
- Existing render smoke tests cover template syntax.
- Full run: `venv/bin/python -m pytest tests/ -q` → all pass.

## Done criteria

- [ ] `venv/bin/python -m pytest tests/ -q` exits 0, including `test_api_models_shape`
- [ ] `grep -n "MODEL_DEFAULTS" templates/studio.html` → 0 matches
- [ ] `grep -c "api/models" templates/studio.html templates/templates.html` → ≥ 1 each
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- Studio's model-config section differs materially from the excerpts (drift).
- The fallback path turns out to require duplicating the full config again —
  if you can't keep the fallback under ~15 lines, report back with options
  instead of rebuilding the clone.
- `/api/models` response shape differs from the Step 1 assertions.

## Maintenance notes

- Adding a model now means editing only `_MODELS` in `app.py` (+ a label in
  `SIZE_LABELS` if it introduces a new size).
- Reviewer: pay attention to the async init ordering — cost badge and button
  states must still initialize correctly when the fetch is slow.
- Deliberately deferred: batch-mode cost estimation for Gemini in the badge
  (it reuses `calcCost`, which is now correct); provider abstraction on the
  backend (direction finding, separate decision).
