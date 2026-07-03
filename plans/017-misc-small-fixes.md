# Plan 017: Small-fix bundle — /health contract, FTS 500s, SET_KEY_SECRET exposure, multi-result display

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index. Each fix is independent — if one hits a STOP condition,
> finish the others and report the one you skipped.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- app.py templates/studio.html templates/templates.html tests/test_app.py`
> Changes from plans 006–016 are expected. Compare each fix's "current"
> excerpt against the live code before doing that fix; on a mismatch, skip
> that fix and report.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md; Fix C touches `templates/studio.html` — coordinate after plan 012 lands to avoid conflicts
- **Category**: bug + security
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

Four confirmed but individually small defects, bundled to amortize overhead:
(A) `/health` doesn't return the field the studio badge reads; (B) FTS search
500s on quotes/hyphens and non-numeric `limit`; (C) the `SET_KEY_SECRET`
value is rendered into the `/studio` page source, defeating its purpose;
(D) the templates page discards all but the first image when n > 1.

## Current state & fixes

### Fix A — /health should report api_key_set

- `app.py:1069-1071`:

```python
@app.route("/health")
def health():
    return jsonify(status="ok")
```

- Consumer `templates/studio.html:873-882` (`checkHealth`) reads
  `d.api_key_set` — currently always undefined, so the badge never flips to
  "Ready" after a key is set elsewhere.

**Change**: return
`jsonify(status="ok", api_key_set=bool(os.environ.get("OPENAI_API_KEY")))`.
Note: the existing test `test_health` asserts the exact dict
`{"status": "ok"}` — update it to assert `status == "ok"` and
`isinstance(api_key_set, bool)` instead.

### Fix B — FTS search must not 500 on special characters

- `app.py:668-684` (`/api/search-prompts`):
  - `limit = min(int(request.args.get("limit", 8)), 20)` → `ValueError` → 500
    on `?limit=abc`.
  - `WHERE community_prompts_fts MATCH ?` with `q + "*"` → a query containing
    `"` or other FTS5 syntax (`-`, `(`) raises `sqlite3.OperationalError` → 500.

**Change**:

```python
    try:
        limit = min(int(request.args.get("limit", 8)), 20)
    except ValueError:
        limit = 8
    ...
    sanitized = q.replace('"', ' ').strip()
    if not sanitized:
        return jsonify(results=[])
    match = " ".join(f'"{tok}"' for tok in sanitized.split())
    if not q.endswith(" "):
        match += "*"
```

and pass `match` to the query. (Quoting each token disables FTS5 operator
parsing; the trailing `*` keeps prefix search. Wrap the `conn.execute` in
`try/except sqlite3.OperationalError: return jsonify(results=[])` as a final
belt-and-braces.)

### Fix C — stop rendering SET_KEY_SECRET into the page

- `app.py:802-807` passes `set_key_secret=os.environ.get("SET_KEY_SECRET", "")`
  into the template; `templates/studio.html:602` embeds it:
  `window._setKeySecret = {{ set_key_secret | tojson }};` and the submit code
  sends it as the `X-Set-Key-Secret` header (`templates/studio.html:820`).
  Anyone who can load `/studio` can read the secret — the header check then
  protects nothing.

**Change** (minimal, preserves UX for the common unset case):
- `app.py`: stop passing the secret value; pass a boolean instead:
  `set_key_secret_required=bool(os.environ.get("SET_KEY_SECRET"))`.
- `studio.html`: replace line 602 with
  `window._setKeyRequired = {{ set_key_secret_required | tojson }};`.
  In `submitKey()`, when `window._setKeyRequired` is true, obtain the secret
  via `prompt('SET_KEY_SECRET required:')` (once, keep in a JS variable for
  the session — NOT localStorage) and send it in the header; when false, send
  no header (matches the backend's skip-if-unset logic at `app.py:1097-1099`).

### Fix D — templates page shows all n results

- `templates/templates.html:637-649` — `generateImage()` renders only
  `results[0]` into `#resultArea`; with Count > 1 the remaining images are
  generated (and billed) but never shown.

**Change**: render every result:

```js
      document.getElementById('resultArea').innerHTML =
        results.map(r => `<img src="${escAttr(r.url)}" alt="Generated" style="max-height:100%;max-width:${Math.floor(100/results.length)}%;object-fit:contain" />`).join('');
```

Keep `currentImageB64` sourced from `results[0]` (download/copy act on the
first image; note this limitation with a code comment).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |
| App imports | `venv/bin/python -c "import app"` | exit 0 |

## Scope

**In scope**: `app.py` (the three cited blocks), `templates/studio.html`
(lines ~602 and `submitKey`), `templates/templates.html` (`generateImage`
result rendering), `tests/test_app.py`.

**Out of scope**: `/set-key`'s validation logic beyond the header handling;
FTS indexing/triggers (previously rejected — see plans/README.md); gallery
and history templates.

## Git workflow

- One commit per fix, e.g. `fix: /health reports api_key_set`,
  `fix: sanitize FTS query input`, `fix: stop exposing SET_KEY_SECRET in studio HTML`,
  `fix: templates page renders all generated images`.

## Steps

1. **Fix A** + update `test_health`.
   **Verify**: `venv/bin/python -m pytest tests/ -q -k health` → pass.
2. **Fix B** + add tests: `test_search_prompts_special_chars(client)` —
   `GET /api/search-prompts?q=%22quoted%22` → 200 (any result list);
   `GET /api/search-prompts?q=cat&limit=abc` → 200.
   **Verify**: `venv/bin/python -m pytest tests/ -q -k search` → pass.
3. **Fix C**.
   **Verify**: `grep -n "_setKeySecret\|set_key_secret=" app.py templates/studio.html`
   → 0 matches; `venv/bin/python -m pytest tests/ -q` → all pass.
4. **Fix D**.
   **Verify**: `venv/bin/python -m pytest tests/ -q` → all pass (template smoke).

## Test plan

- Updated: `test_health`. New: `test_search_prompts_special_chars`.
- Fixes C and D are template-level; covered by render smoke tests + the greps.

## Done criteria

- [ ] `venv/bin/python -m pytest tests/ -q` exits 0 (incl. updated/new tests)
- [ ] `curl`-equivalent checks impossible in CI here — rely on tests above
- [ ] `grep -rn "set_key_secret | tojson" templates/` → 0 matches
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- Any fix's "current" excerpt no longer matches (that area was refactored by
  another plan) — skip that fix, do the rest, report.
- Fix C: if you find OTHER consumers of `window._setKeySecret` beyond
  `submitKey` (grep first), report before changing the contract.

## Maintenance notes

- Fix B intentionally trades FTS operator syntax (users can no longer use
  `AND`/`NOT`) for robustness — acceptable for a suggestion dropdown; note it
  if a power-search UI is ever built.
- Fix C is defense-in-depth for a localhost tool; the standing decision that
  full CSRF/auth is out of scope for localhost remains (plans/README.md).
- Fix D: a nicer multi-image layout (grid + per-image download) is deliberate
  follow-up material, not this plan.
