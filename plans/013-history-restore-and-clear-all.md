# Plan 013: History fixes — restore carries the reference image; Clear All actually clears

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- templates/history.html templates/studio.html app.py tests/test_app.py`
> Changes from plans 006–012 are expected. Compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md
- **Category**: bug
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

Two related history-page defects:

1. **Restore drops the reference image.** History's "Restore" stuffs the
   full-size base64 reference image into `localStorage` — which (a) can exceed
   the ~5 MB quota and make the whole restore throw and fail, and (b) is
   pointless anyway because studio's `checkRestore()` only reads `prompt` and
   ignores `image_b64`. Passing just the row id and letting studio fetch the
   row fixes both.
2. **"Clear All" only deletes the 200 loaded rows.** `/history` is capped at
   `LIMIT 200`; Clear All loops over the loaded entries issuing 200 sequential
   DELETEs. Older rows silently survive in the 308 MB database while the UI
   claims "History cleared".

## Current state

- `templates/history.html:672-685` — `restoreEntry(id)`:

```js
    const data = await r.json();
    localStorage.setItem('image-studio-restore', JSON.stringify({
      prompt: data.prompt || '',
      image_b64: data.image_b64 || ''
    }));
```

  (`/use-history/<pid>` at `app.py:299-312` returns `{prompt, image_b64}`.)
- `templates/studio.html:884-895` — `checkRestore()` reads the key, uses only
  `data.prompt`, removes the key, toasts.
- `templates/history.html:731-744` — Clear All:

```js
$('#clear-all-btn').addEventListener('click', async () => {
  if (state.entries.length === 0) return;
  if (!confirm(`Delete all ${state.entries.length} history entries?`)) return;
  try {
    for (const e of state.entries) {
      await fetch('/history/' + e.id, { method: 'DELETE' });
    }
```

- `app.py:244-256` — `/history` has `LIMIT 200`.
- `app.py:293-297` — `DELETE /history/<int:pid>` (single row) exists; there is
  no delete-all route.
- Studio upload state: `state.imageB64` + `renderUploadPreview()` at
  `templates/studio.html:925-934` — the restore should set `state.imageB64`
  and call `renderUploadPreview(); updateButtons();` exactly like
  `useAsReference` does (`templates/studio.html:1166-1193`).
- Test conventions: `tests/test_app.py` inserts rows directly via
  `sqlite3.connect(app_module.DB_PATH)` — see `test_fb_post_requires_token_or_pid`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |
| App imports | `venv/bin/python -c "import app"` | exit 0 |

## Scope

**In scope**:
- `templates/history.html` (restoreEntry, clear-all handler)
- `templates/studio.html` (checkRestore only)
- `app.py` (new `DELETE /history` route only)
- `tests/test_app.py` (add tests)

**Out of scope**:
- `/use-history/<pid>` response shape (kept — studio now calls it).
- The 200-row `/history` LIMIT itself (pagination is a separate decision).
- Single-row delete UX (no-confirm single delete stays as is).

## Git workflow

- Commit style: `fix: restore carries reference image via pid; DELETE /history clears all rows`

## Steps

### Step 1: Restore passes the pid, not the payload

In `templates/history.html`, simplify `restoreEntry(id)` — no fetch needed
here anymore:

```js
async function restoreEntry(id) {
  localStorage.setItem('image-studio-restore', JSON.stringify({ pid: id }));
  toast('Restored — open Studio to continue');
}
```

(Keep the function async to avoid touching its callers.)

### Step 2: Studio fetches the row on restore

In `templates/studio.html`, rewrite `checkRestore()` (make it async and await
it in `init()`, which is already async):

```js
async function checkRestore() {
  try {
    const saved = localStorage.getItem('image-studio-restore');
    if (!saved) return;
    localStorage.removeItem('image-studio-restore');
    const data = JSON.parse(saved);
    if (data.pid) {
      const r = await fetch('/use-history/' + data.pid, { method: 'POST' });
      if (!r.ok) throw new Error();
      const d = await r.json();
      if (d.prompt) promptInput.value = d.prompt;
      if (d.image_b64) { state.imageB64 = d.image_b64; state.imageFile = null; renderUploadPreview(); }
    } else if (data.prompt) {
      promptInput.value = data.prompt;   // backward compat with old key shape
    }
    updateButtons();
    toast('Prompt restored from history', 'success');
  } catch (_) { toast('Restore failed', 'error'); }
}
```

In `init()`, change `checkRestore();` to `await checkRestore();`.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass (template smoke).

### Step 3: Add DELETE /history (all rows) to app.py

Next to the single-row delete (`app.py:293-297`):

```python
@app.route("/history", methods=["DELETE"])
def delete_all_history():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM prompts")
    return jsonify(status="ok", deleted=cur.rowcount)
```

**Verify**: `venv/bin/python -c "import app"` → exit 0.

### Step 4: Clear All uses the new route and reports the real count

Replace the loop in the clear-all handler:

```js
    const r = await fetch('/history', { method: 'DELETE' });
    if (!r.ok) throw new Error('Server error');
    const d = await r.json();
    state.entries = [];
    state.selected.clear();
    renderHistory();
    toast(`History cleared (${d.deleted} entries)`);
```

Also change the confirm text to make the scope honest:
`` confirm(`Delete ALL history entries (including older ones not shown)?`) ``.

### Step 5: Tests

In `tests/test_app.py`:

- `test_delete_all_history(client)`: insert 3 rows directly via sqlite3,
  `client.delete("/history")` → 200 with `deleted == 3`; then
  `client.get("/history")` → `[]`.
- Extend coverage of restore's server side: `test_use_history_roundtrip(client)`
  — insert a row with `prompt` and `image_b64`, POST `/use-history/<id>` →
  200 and both fields returned; POST `/use-history/999999` → 404.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass, incl. 2 new tests.

## Test plan

- New tests in Step 5; pattern: direct-sqlite insert style from
  `test_fb_post_requires_token_or_pid`.
- Manual (optional, if browser available): star a row in history → Restore →
  open Studio → prompt AND reference image thumbnail both appear.

## Done criteria

- [ ] `venv/bin/python -m pytest tests/ -q` exits 0, including the 2 new tests
- [ ] `grep -n "image_b64: data.image_b64" templates/history.html` → 0 matches
- [ ] `grep -n "d.image_b64" templates/studio.html` → ≥ 1 match inside checkRestore
- [ ] `grep -n 'methods=\["DELETE"\]' app.py` → 2 matches (single + all)
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- `checkRestore`/`restoreEntry` differ materially from the excerpts (drift).
- Adding `DELETE /history` conflicts with an existing route registration
  (Flask will raise on import — that means drift; report).
- CSRF concern: this endpoint deletes everything with one request. The repo's
  standing decision (plans/README.md "considered and rejected") is that
  JSON/localhost-only routes are acceptable without CSRF for this local tool
  — if the operator has since exposed the app beyond localhost, STOP and
  flag it instead.

## Maintenance notes

- If pagination lands later, Clear All semantics ("everything, not just the
  page") are now correct and must be preserved.
- The old `{prompt: ...}` localStorage shape is still honored (Step 2
  backward-compat branch); safe to remove after a few weeks.
- Reviewer: confirm `cur.rowcount` from a `DELETE` without WHERE returns the
  row count under the sqlite3 driver (it does; `executescript` wouldn't).
