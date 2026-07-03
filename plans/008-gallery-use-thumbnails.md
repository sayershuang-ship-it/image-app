# Plan 008: Gallery cards load thumbnails, not full-resolution originals

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- templates/gallery.html`
> Changes from plans 006/007 are expected. Compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md
- **Category**: perf
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

The gallery page renders up to 200 cards and, for every image stored as
base64 in SQLite (which is nearly all of them — gpt-image-2 returns
`b64_json`), sets the card `<img src>` to `/download/<id>` — the
full-resolution PNG, typically 1–4 MB each. Opening the gallery can pull
hundreds of megabytes out of the 308 MB SQLite DB and decode them all in the
browser. A purpose-built thumbnail endpoint `/api/thumb/<pid>` (256 px JPEG,
`Cache-Control: private, max-age=86400`) already exists and is already used by
the history page — the gallery just doesn't use it.

## Current state

- `app.py:269-291` — `GET /api/thumb/<int:pid>`: reads `result_b64`, resizes
  to ≤256 px, returns JPEG with cache headers; 404 if no image.
- `app.py:1112-1131` — `GET /download/<int:pid>`: returns the full PNG as an
  attachment (this is what the modal/full view should keep using).
- `templates/gallery.html:491-495` — the culprit:

```js
function getImageSrc(img) {
  if (img.url && img.url.startsWith('data:')) return img.url;
  if (img.url && (img.url.startsWith('http://') || img.url.startsWith('https://'))) return img.url;
  return '/download/' + img.id;
}
```

- Card usage at `templates/gallery.html:506-514`:
  `<img src="${getImageSrc(img)}" alt="${escHtml(img.label)}" loading="lazy" ...>`
- Modal usage at `templates/gallery.html:545`:
  `$('#modal-img').src = getImageSrc(currentDetail);`
- `img.url` is populated from `/history`'s `result_url` field
  (`templates/gallery.html:454`), which is an expired OpenAI URL or empty —
  for b64-stored rows it is empty, so the `/download/` fallback is the common
  path.
- Exemplar for the thumbnail pattern: `templates/history.html:539-540` uses
  `<img src="/api/thumb/${e.id}" ...>`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |

## Scope

**In scope**:
- `templates/gallery.html` (JS only: `getImageSrc`, card rendering, modal open)

**Out of scope**:
- `app.py` — both endpoints already behave correctly.
- `templates/history.html` — already uses thumbnails.
- The `/history` payload shape.

## Git workflow

- Commit style: `perf: gallery cards use /api/thumb, full image only in modal`

## Steps

### Step 1: Split thumbnail vs full-size source functions

In `templates/gallery.html`, replace `getImageSrc` with two functions:

```js
function getThumbSrc(img) {
  if (img.url && img.url.startsWith('data:')) return img.url;   // session data-URLs stay as-is
  return '/api/thumb/' + img.id;
}
function getFullSrc(img) {
  if (img.url && img.url.startsWith('data:')) return img.url;
  if (img.url && (img.url.startsWith('http://') || img.url.startsWith('https://'))) return img.url;
  return '/download/' + img.id;
}
```

Note: the old code preferred remote `http(s)` `result_url` for cards; those
OpenAI URLs expire and the `onerror` handler currently hides the card. Using
`/api/thumb` for cards also fixes silently vanishing cards for expired URLs
(the row still 404s only if `result_b64` is truly absent).

### Step 2: Use them at the two call sites

- Card render (line ~508): `src="${getThumbSrc(img)}"`.
- Modal open (line ~545): `$('#modal-img').src = getFullSrc(currentDetail);`.
- Remove the now-unused `getImageSrc` (verify no other references:
  `grep -n getImageSrc templates/gallery.html` → 0 matches after edit).

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass (page render smoke
tests cover template syntax).

### Step 3: Manual smoke check (if a browser is available)

Start the app (`./start.sh`), open `http://localhost:5001/gallery`, and check
in devtools' Network tab that card images come from `/api/thumb/<id>` and are
~10–50 KB each; click a card and confirm the modal loads `/download/<id>`.
If no browser is available, note that this step was skipped in your report.

## Test plan

- Existing smoke test `test_app_pages_render` covers `/gallery` template
  syntax. No new backend behavior to test (endpoints unchanged).
- Optional: add `test_thumb_404_without_image(client)` asserting
  `GET /api/thumb/999999` → 404, modeled on `test_history_empty` — cheap
  contract lock for the endpoint the gallery now depends on.

## Done criteria

- [ ] `grep -c "api/thumb" templates/gallery.html` → ≥ 1
- [ ] `grep -n "'/download/' + img.id" templates/gallery.html` appears only inside `getFullSrc`
- [ ] `venv/bin/python -m pytest tests/ -q` exits 0
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- `getImageSrc` in the live file differs from the excerpt (drift).
- You find the modal intentionally relying on the card's already-loaded
  full-size image (it does not today — but if plan 016 restructured this
  first, report instead of merging conflicting edits).

## Maintenance notes

- If pagination or infinite scroll is added to the gallery later, keep
  thumbnails for cards; the 200-row `/history` LIMIT is the real cap today.
- Reviewer: check the modal still shows full resolution (not the 256 px thumb).
