# Plan 005 (spike): Wire the existing Facebook page-posting backend into the gallery UI

> **Executor instructions**: This is a design/build spike, not a full feature
> plan. Follow the steps; where a step says "decide and record", write the
> decision into this file's "Decisions" section at the bottom. Honor STOP
> conditions. When done, update the status row in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 149315b..HEAD -- app.py templates/gallery.html`
> On mismatch with "Current state" excerpts, compare carefully before proceeding.

## Status

- **Priority**: P3
- **Effort**: M (coarse — spike)
- **Risk**: MED (posts to a real external service)
- **Depends on**: 003 (FB credentials must come from env first), 004 (gallery extends base — optional but avoids rebasing UI work)
- **Category**: direction
- **Planned at**: commit `149315b`, 2026-06-11

## Why this matters

The backend for posting generated images to a Facebook Page is fully built and committed — OAuth flow (`/fb-auth-url`, `/fb-callback` at app.py:717-772), multi-page token storage (`~/.hermes/fb_page_token`, app.py:685-715), posting (`/fb-post`, app.py:774-819), page listing (`/fb-pages`), and token validation (`/fb-token-test`) — but **no template references any of these routes** (verified by grep across templates/). This is classic unfinished intent: the user evidently wants to publish generated images to their FB pages, and the last mile (a UI) is missing. Closing this loop turns the app from "generate and hoard" into "generate and publish".

## Current state

- `app.py:774-819` — `fb_post()` accepts JSON `{page_id, message, image_path | image_url}`. **Note**: it does NOT accept a prompt-history `pid` or a base64 data URL — generated images live in the DB as `result_b64`, so the UI cannot call `fb_post` directly without either (a) a new param like `pid`, or (b) first saving to disk via `/save-to-pictures/<pid>` (app.py:938-956, returns a filename under `~/Pictures/OPENAI image 2.0/`).
- `templates/gallery.html` — image grid fed by `GET /history`; each card has a modal (`modal-star` button etc. around gallery.html:436); selection + `/export-zip` flow exists at gallery.html:628.
- `/fb-pages` (app.py:821-838) returns `{pages:[{id}]}` — IDs only, **no page names** (names are shown once in the OAuth callback HTML and not stored).

## Scope

**In scope**: `app.py` (one new param or endpoint), `templates/gallery.html`, `tests/test_app.py`.
**Out of scope**: posting to other platforms; scheduling; Instagram; changing token storage format; `spark.html`, `studio.html`.

## Steps

### Step 1: Extend the backend so the UI can post a history item

Add `pid` support to `fb_post()`: if JSON contains `pid`, load `result_b64` from the `prompts` table (same pattern as `download()` at app.py:900-919), and upload those bytes as multipart (same `files=` pattern already in `fb_post`). Decide and record: default `message` = the item's `prompt`? (Recommended: yes, truncated to ~500 chars, with the UI allowing edits.)

**Verify**: unit test with `requests.post` monkeypatched — `POST /fb-post` with `{pid, page_id}` reaches the patched call with multipart bytes; missing token still → 401.

### Step 2: Store page names at OAuth time

In `fb_callback()` the page name is available (`p.get('name')`); extend `_save_page_token` storage format to `pid:name:token` **only if** backward-compatible parsing is kept (lines without a name still parse). Update `/fb-pages` to return names.

**Verify**: existing token file (2-field lines) still parses; new 3-field lines round-trip.

### Step 3: Gallery UI

In the gallery card modal, add a "Post to Facebook" button:
- On click: `GET /fb-pages`; if 400 (no token), show a dialog linking to `/fb-auth-url`'s URL ("Connect Facebook").
- Else show page picker + editable message textarea (pre-filled with the prompt) + Post button → `POST /fb-post {pid, page_id, message}`.
- Success → toast with post id; failure → show the API error verbatim.

**Verify**: with no token file, the connect path renders; with a fake token, the post path issues the request (manual/browser check; automated coverage via the Step 1 test).

### Step 4: Record open questions

Write into "Decisions" below: Should batch-posting (multi-select → post N images) be in scope later? Should `/fb-post` require `SET_KEY_SECRET`-style protection before any non-localhost deployment?

## Done criteria

- [ ] `POST /fb-post` with `{pid, page_id}` posts a DB image (test with patched requests passes)
- [ ] Gallery modal has a working connect → pick page → post flow
- [ ] Full test suite passes
- [ ] "Decisions" section below is filled in
- [ ] `plans/README.md` status row updated

## STOP conditions

- The Meta Graph API version in app.py (`v22.0`) is rejected as expired by Facebook — report; version bump is a separate decision.
- Tokens in `~/.hermes/fb_page_token` turn out to be expired and re-auth fails — the OAuth app config is a human task; stop and report.
- Never post to a real page during testing without the operator explicitly providing a test page.

## Decisions

1. **Default message = prompt text (truncated to 500 chars)**. The `fb_post` endpoint
   auto-fills the message field from `prompts.prompt` when `pid` is provided and no
   `message` is in the JSON body. The UI pre-fills the textarea with the prompt so
   users can edit before posting. (Decided 2026-06-11)

2. **Page names stored in token file** (`pid:name:token` format, backward-compatible
   with old `pid:token` lines). `/fb-pages` now returns `{id, name}` for each page.
   (Decided 2026-06-11)

3. **Batch posting (multi-select → post N images)**: deferred. The current spike
   implements single-image posting from the gallery detail modal. Multi-select
   posting should be a follow-up feature.

4. **`/fb-post` protection**: currently no `SET_KEY_SECRET`-style guard. Acceptable
   for localhost-only use. MUST add authentication before any non-localhost
   deployment.

5. **API version `v22.0`**: not bumped during this spike. If Facebook rejects it
   as expired, the version string in `app.py` (3 occurrences) needs updating.

## Maintenance notes

- Page tokens expire; surface `/fb-token-test` status in the UI eventually.
- If this lands well, the same pattern (select image → publish) generalizes to other targets; keep the modal code generic enough to add a second target without rework.
