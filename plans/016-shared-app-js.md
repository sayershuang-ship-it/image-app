# Plan 016: Consolidate duplicated page JS into static/app.js

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- templates/ static/`
> This plan MUST run after plans 007, 008, 011, 013, 014 (they edit the same
> helpers being consolidated). Read the current state of each helper in the
> live files — the excerpts below describe shapes, not exact current bytes.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: MED (touches every page's JS; pure refactor, no behavior change)
- **Depends on**: plans 007, 008, 011, 013, 014 (execute this LAST among the frontend plans)
- **Category**: tech-debt
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

Every page template carries private copies of the same helpers: `toast`,
`escHtml`, `escAttr`, `parseTs` (after plan 014), `pollJob` (studio +
templates), export-ZIP download logic (gallery + history, ~30 near-identical
lines), and theme handling (studio + index). The copies have already diverged
(history's `toast` ignores the error style; templates.html had no escaping at
all until plan 011). Consolidating into one `static/app.js` makes the next
fix a one-file change and stops the divergence class. This is a
**behavior-preserving refactor**.

## Current state

- `templates/base_app.html` — shared shell; loads `static/app.css` (line 7);
  has a `{% block page_js %}` at line 30. Four pages extend it (studio,
  gallery, history, templates). `templates/index.html` is standalone (does
  NOT extend the base — leave it out of this refactor).
- Duplicated helpers (locations as of this writing; re-locate with grep):
  - `toast`: studio.html (`function toast(msg, type)` with 4 s timeout),
    gallery.html (defaults type to `'success'`), history.html (success-only —
    the divergence to fix by adopting the studio/gallery signature).
  - `escHtml`: studio.html, gallery.html, history.html, templates.html
    (added by plan 011).
  - `escAttr`: studio.html, templates.html (plan 011).
  - `parseTs`: gallery.html, history.html (added by plan 014).
  - `pollJob(jobId, startTime)` in studio.html vs `pollJob(jobId)` in
    templates.html — unify on `pollJob(jobId, startTime, onTick)` where
    `onTick(elapsedSeconds)` lets each page update its own progress UI.
  - Export-ZIP click handlers: gallery.html and history.html — extract
    `exportZip(ids)` (fetch → blob → anchor download) into the shared file;
    keep per-page button-state code in the pages.
  - `const $ = ...` selector shorthand: all pages.
- All pages use vanilla JS with `addEventListener`; no modules/bundler — the
  shared file must be a plain classic script defining globals.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |

## Scope

**In scope**:
- `static/app.js` (create)
- `templates/base_app.html` (one `<script src>` line)
- `templates/studio.html`, `templates/gallery.html`,
  `templates/history.html`, `templates/templates.html` (delete local copies,
  adjust call sites)

**Out of scope**:
- `templates/index.html` (standalone landing page by design — do not wire it
  to app.js).
- Any behavior change whatsoever (signatures may unify, observable behavior
  must not — except history's toast gaining the error style, which is the
  documented intent).
- CSS.

## Git workflow

- Commit style: `refactor: extract shared page JS into static/app.js`
- Commit per step so each page conversion is separately revertable.

## Steps

### Step 1: Create static/app.js with the shared helpers

Define (as globals, classic script): `$`, `escHtml`, `escAttr`, `parseTs`,
`toast(msg, type)` (studio's implementation: appends to `#toast-container`,
class `toast <type>`, 4 s auto-remove), `sleep(ms)`,
`pollJob(jobId, startTime, onTick)` (1.5 s interval, 180 s timeout, throws on
`failed`/timeout/non-OK poll — port studio's version and call
`onTick(elapsedSeconds)` each loop when provided), and `exportZip(ids)`
(gallery/history's fetch-blob-anchor sequence, returns the count or throws).

Add `<script src="{{ url_for('static', filename='app.js') }}"></script>`
in `templates/base_app.html` immediately BEFORE `{% block page_js %}` so page
scripts can use the globals.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass.

### Step 2: Convert one page (gallery) end-to-end

Delete gallery's local `$`, `toast`, `escHtml`, `parseTs`, and the export-ZIP
fetch block (now `await exportZip(ids)` inside the existing try/catch,
keeping the button-state lines). Confirm no other local code shadows the
globals (`grep -n "function toast\|function escHtml\|const \$ =" templates/gallery.html`
→ 0 matches).

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass. If a browser is
available: load `/gallery`, open a modal, export a ZIP — console must show no
`ReferenceError`.

### Step 3: Convert history, studio, templates the same way

One commit each. For studio, `pollJob` call sites pass an `onTick` that
updates `#gen-elapsed` (preserving current behavior); for templates.html,
`onTick` calls `setStatus(...)`. For history, adopt the shared `toast` and
update the handful of `toast('...')` calls that should be errors
(`'Restore failed'`, `'Delete failed'`, `'Clear failed'`, `'Export failed...'`)
to pass `'error'`.

**Verify after each page**: `venv/bin/python -m pytest tests/ -q` → all pass;
`grep -n "function pollJob\|function toast\|function escHtml\|function escAttr\|function parseTs" templates/*.html`
shrinks stepwise to 0.

### Step 4: Browser sweep (if available)

Visit all four app pages; exercise: generate (studio), suggestion dropdown,
batch mode, gallery modal + export, history restore/delete/clear/export,
templates community tab + generate. Zero console errors. If no browser, note
the skip and rely on the grep + pytest gates.

## Test plan

- Existing pytest smoke tests (`test_app_pages_render`, `test_nav_consistent`)
  catch template/`url_for` breakage.
- The grep done-criteria below are the duplication gates.

## Done criteria

- [ ] `static/app.js` exists and `base_app.html` loads it before `page_js`
- [ ] `grep -rn "function toast\|function escHtml\|function escAttr\|function parseTs\|function pollJob" templates/` → 0 matches (all in app.js now)
- [ ] `venv/bin/python -m pytest tests/ -q` exits 0
- [ ] `git diff` shows no logic changes beyond deletions + call-site
      signature adjustments described above
- [ ] `plans/README.md` status row updated

## STOP conditions

- Prerequisite plans (007/008/011/013/014) are not yet DONE in
  `plans/README.md` — running this first guarantees conflicts.
- A page's local helper turns out to differ behaviorally from the shared
  version in a way not documented above (report the diff instead of silently
  picking one).
- Any console `ReferenceError` you cannot trace to a missed conversion.

## Maintenance notes

- New pages extending `base_app.html` get the helpers for free; keep
  page-specific logic in `page_js`.
- index.html intentionally keeps its own tiny theme script — revisit only if
  the landing page ever merges into the app shell.
- Reviewer: this should be a red-heavy diff; be suspicious of any green lines
  that aren't call-site adjustments or app.js itself.
