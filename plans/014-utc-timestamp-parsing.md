# Plan 014: Parse SQLite UTC timestamps correctly in gallery and history

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- templates/gallery.html templates/history.html`
> Changes from plans 006–013 are expected. Compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md; do BEFORE plan 016 (which consolidates the helper into shared JS)
- **Category**: bug
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

`prompts.prompt_ts` is written by SQLite's `CURRENT_TIMESTAMP`, which is
**UTC** in the format `YYYY-MM-DD HH:MM:SS`. The frontends parse it with
`new Date(str)`, which (a) treats that format as **local time** in
Chrome/Firefox — so for a UTC+8 user every displayed time is 8 hours early and
the gallery's Today/Week/Month filters misclassify recent generations — and
(b) is not guaranteed to parse at all with a space separator (Safari
historically returns `Invalid Date`). One tiny parse helper fixes display and
filtering on both pages.

## Current state

- Writer: `app.py:103` — `prompt_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
  (UTC, `YYYY-MM-DD HH:MM:SS`).
- Consumers of `item.prompt_ts` / `e.ts` / `img.ts` via `new Date(...)`:
  - `templates/gallery.html:461` (`ts: item.prompt_ts || ...`),
    `templates/gallery.html:476-487` (`getFilteredImages` date filters),
    `templates/gallery.html:550` (modal date display).
  - `templates/history.html:505` (`ts: item.prompt_ts || ...`),
    `templates/history.html:537-538` (list date display),
    `templates/history.html:631` (sidebar timestamp display).
- No shared JS file exists yet (plan 016 creates one) — for now the helper is
  duplicated in the two page scripts, matching current repo structure.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |

## Scope

**In scope**:
- `templates/gallery.html`, `templates/history.html` (JS only)

**Out of scope**:
- `app.py` / DB schema — do NOT change how timestamps are stored or returned
  (other consumers, e.g. export manifest, read the raw string).
- `templates/studio.html`, `templates/templates.html` (no ts parsing there).

## Git workflow

- Commit style: `fix: parse prompt_ts as UTC in gallery/history (display + filters)`

## Steps

### Step 1: Add a parse helper to both pages

Near the existing `escHtml` helper in each of `gallery.html` and
`history.html`, add:

```js
function parseTs(s) {
  if (!s) return new Date();
  // SQLite CURRENT_TIMESTAMP is UTC, "YYYY-MM-DD HH:MM:SS" — normalize to ISO UTC
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(s)) return new Date(s.replace(' ', 'T') + 'Z');
  return new Date(s);
}
```

### Step 2: Route every ts parse through it

- `gallery.html`: in `getFilteredImages()` replace both `new Date(img.ts)`
  occurrences with `parseTs(img.ts)`; in `openDetail` replace
  `new Date(currentDetail.ts)` with `parseTs(currentDetail.ts)`.
- `history.html`: in `renderHistory()` replace `new Date(e.ts)` with
  `parseTs(e.ts)`; in `openDetail` replace `new Date(entry.ts)` with
  `parseTs(entry.ts)`.
- Leave the `ts:` mapping lines (which keep the raw string) unchanged.

**Verify**: `grep -c "new Date(" templates/gallery.html templates/history.html`
→ remaining matches are only the `new Date()` no-arg calls (inside `parseTs`
and the `dayStart` construction) and `new Date().toISOString()` fallbacks.

### Step 3: Full suite

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass (template smoke).

### Step 4: Manual check (if browser available)

Generate an image, open History: the entry's time must match local wall-clock
time (not 8 h off for UTC+8). Gallery "Today" filter must include it. If no
browser, note the skip.

## Test plan

- No backend change → existing suite as regression gate. Frontend behavior is
  verified manually (Step 4); the repo has no JS test tooling — do not add any.

## Done criteria

- [ ] `grep -c "function parseTs" templates/gallery.html templates/history.html` → 1 each
- [ ] `grep -n "new Date(img.ts)\|new Date(e.ts)\|new Date(entry.ts)\|new Date(currentDetail.ts)" templates/gallery.html templates/history.html` → 0 matches
- [ ] `venv/bin/python -m pytest tests/ -q` exits 0
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- The ts-handling lines differ from the excerpts (drift — plan 016 may have
  already moved them into a shared file; if so, apply the same fix there
  once, and report the changed location).
- You find rows whose `prompt_ts` does NOT match the `YYYY-MM-DD HH:MM:SS`
  pattern (the regex guard handles them by falling through, but report what
  format they are).

## Maintenance notes

- Plan 016 should lift `parseTs` into the shared `static/app.js` and delete
  the duplicates.
- If the backend ever switches to emitting ISO-8601 with timezone, `parseTs`
  degrades gracefully (regex won't match, falls through to native parsing).
