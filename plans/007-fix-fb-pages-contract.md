# Plan 007: Fix the /fb-pages response-shape mismatch so the Facebook posting UI works

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- templates/gallery.html app.py tests/test_app.py`
> Plan 006 intentionally commits changes to `app.py` after `00bd8e2`; that is
> expected. Compare the "Current state" excerpts against the live code before
> proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md
- **Category**: bug
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

The "Post to Facebook" feature in the gallery is completely broken: the
backend returns `{"pages": [...]}` but the frontend treats the response as a
bare array. `fbPages.length` is always `undefined`, so the code always falls
into the "not authorized" branch and the page picker never renders — even for
a fully authorized user with saved page tokens. A one-line contract fix plus a
contract test restores the feature and prevents the same drift recurring.

## Current state

- `app.py:1030-1050` — the `/fb-pages` route. Returns
  `jsonify(pages=pages)` (line 1050), i.e. `{"pages": [{"id":..., "name":...}, ...]}`.
  On no token file / no entries it returns
  `jsonify(error="No Facebook token configured. Visit /fb-auth-url to authorize."), 400`.
- `templates/gallery.html:609-624` — the consumer, inside the
  `#modal-fb-post` click handler:

```js
    // Load pages
    const r = await fetch('/fb-pages');
    if (r.status === 400 || r.status === 401) {
      $('#fb-auth-hint').style.display = '';
      return;
    }
    fbPages = await r.json();
    if (!fbPages || !fbPages.length) {          // BUG: fbPages is {pages:[...]}
      $('#fb-auth-hint').style.display = '';
      return;
    }

    // Show page picker
    const sel = $('#fb-page-select');
    sel.innerHTML = fbPages.map(p =>            // BUG: .map on an object
```

- `app.py:951-955` — `fb_callback` builds an HTML success page by
  interpolating Facebook page names without escaping:

```python
        html = "<h1>✅ Authorized!</h1><ul>"
        for p in pages:
            html += f"<li><b>{p.get('name', 'Unknown')}</b> — ID: {p['id']}</li>"
```

- Test conventions: `tests/test_app.py` uses a `client` fixture from
  `tests/conftest.py` (temp DB per test); FB tests swap
  `app_module.FB_PAGE_TOKEN_FILE` to a temp file — see
  `test_fb_post_requires_token_or_pid` in `tests/test_app.py` as the exemplar
  and match its style.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |
| App imports | `venv/bin/python -c "import app"` | exit 0 |

## Scope

**In scope**:
- `templates/gallery.html` (the JS block quoted above only)
- `app.py` (only the `fb_callback` escaping fix, lines ~951-955)
- `tests/test_app.py` (add tests)

**Out of scope**:
- The `/fb-pages` response shape — keep `{"pages": [...]}`; fix the consumer.
- `_load_page_token` / `_save_page_token` and the token file format.
- Any other JS in `gallery.html`.

## Git workflow

- Branch: work on `main` or `advisor/007-fb-pages-contract` per operator preference.
- Commit style: `fix: gallery reads /fb-pages payload shape; escape page names in fb_callback`

## Steps

### Step 1: Fix the consumer in gallery.html

Replace the two buggy lines so the object shape is unwrapped:

```js
    const d = await r.json();
    fbPages = d.pages || [];
    if (!fbPages.length) {
      $('#fb-auth-hint').style.display = '';
      return;
    }
```

(`sel.innerHTML = fbPages.map(...)` below then works unchanged.)

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass (renders still OK).

### Step 2: Escape page names in fb_callback

In `app.py`, import nothing new — use `markupsafe.escape` (ships with Flask):
at the top of the function or module, `from markupsafe import escape`, then:

```python
            html += f"<li><b>{escape(p.get('name', 'Unknown'))}</b> — ID: {escape(p['id'])}</li>"
```

**Verify**: `venv/bin/python -c "import app"` → exit 0.

### Step 3: Add a contract test

In `tests/test_app.py`, add `test_fb_pages_shape(client)`:

1. Write a temp token file with one line `pid1:Page One:tok1` and point
   `app_module.FB_PAGE_TOKEN_FILE` at it (copy the temp-file pattern from
   `test_fb_post_requires_token_or_pid`, including the `try/finally` restore).
2. `rv = client.get("/fb-pages")` → assert status 200 and
   `rv.get_json() == {"pages": [{"id": "pid1", "name": "Page One"}]}`.
3. Point `FB_PAGE_TOKEN_FILE` at a nonexistent path → assert `/fb-pages`
   returns 400 with an `error` key.

**Verify**: `venv/bin/python -m pytest tests/ -q -k fb` → passes.

## Test plan

- New: `test_fb_pages_shape` (above) — locks the `{"pages": [...]}` contract.
- Pattern: `test_fb_post_requires_token_or_pid` in `tests/test_app.py`.
- Full run: `venv/bin/python -m pytest tests/ -q` → all pass.

## Done criteria

- [ ] `venv/bin/python -m pytest tests/ -q` exits 0, including the new test
- [ ] `grep -n "fbPages = d.pages" templates/gallery.html` → 1 match
- [ ] `grep -n "escape(p.get('name'" app.py` → 1 match
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- The `gallery.html` excerpt above doesn't match the live file (drift).
- `/fb-pages` in `app.py` no longer returns `jsonify(pages=pages)` — the
  contract may have been fixed on the other side already; report instead of
  double-fixing.

## Maintenance notes

- Plan 016 (shared app.js) will move some gallery helpers; it must not break
  this handler. The contract test is the guard.
- Manual end-to-end FB posting still requires real tokens; the operator should
  smoke-test once with a real page after this lands.
