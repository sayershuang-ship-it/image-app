# Plan 003: Remove the hardcoded Facebook App Secret from app.py and require env configuration

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 149315b..HEAD -- app.py`
> If `app.py` changed since this plan was written, compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, treat it
> as a STOP condition. (Plan 001 also touches app.py — its change is to
> `init_db()` only and does not conflict.)

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 001 (test suite as regression gate)
- **Category**: security
- **Planned at**: commit `149315b`, 2026-06-11

## Why this matters

`app.py:36-37` hardcodes a real Facebook App ID and **App Secret** as fallback defaults, and these values are committed to git history. An app secret in a repo is burned: anyone with repo access can mint access tokens for the app, and the OAuth flow at `/fb-callback` exchanges codes using it. The fix is to stop reading defaults from source, and the secret must be **rotated in the Meta developer console** (removal alone does not un-leak it — it stays in git history).

## Current state

- `app.py:36-37`:
  ```python
  FB_APP_ID     = os.environ.get("FB_APP_ID", "<hardcoded app id>")
  FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "<hardcoded 32-hex secret>")
  ```
  (Actual values intentionally not reproduced here; they are visible at those lines.)
- Consumers: `fb_auth_url()` at app.py:717-727 (uses FB_APP_ID), `fb_callback()` at app.py:729-772 (uses both). No template references any `/fb-*` route (verified by grep), so the FB feature is backend-only today.
- `start.sh` sources `~/.hermes/.env` for `OPENAI_API_KEY`; the same file is the natural home for `FB_APP_ID` / `FB_APP_SECRET`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Tests | `venv/bin/python -m pytest tests/ -q` | all pass |
| Secret scan | `git grep -nE "[0-9a-f]{32}" -- app.py` | no output (after fix) |

## Scope

**In scope**:
- `app.py` lines 36-37 and, if needed, guards in `fb_auth_url` / `fb_callback`.
- `README.md` — one line documenting the two env vars (optional, only if README was already rewritten by plan 002).

**Out of scope**:
- Rotating the secret in the Meta console — **a human must do this**; the plan's job is to flag it loudly in the final report.
- Git history rewriting.
- The FB posting routes' logic (`fb_post`, `fb_pages`, `fb_token_test`).
- `~/.hermes/*` files on disk.

## Git workflow

- Commit style: `fix: read FB credentials from env only, no hardcoded fallback`.
- Do NOT push.

## Steps

### Step 1: Drop the hardcoded defaults

Change `app.py:36-37` to:

```python
FB_APP_ID     = os.environ.get("FB_APP_ID", "")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
```

### Step 2: Guard the OAuth routes

At the top of `fb_auth_url()` (app.py:717) and `fb_callback()` (app.py:729), add:

```python
if not FB_APP_ID or not FB_APP_SECRET:
    return jsonify(error="FB_APP_ID / FB_APP_SECRET not configured"), 503
```

(`fb_callback` returns HTML elsewhere, but a JSON 503 is acceptable for an unconfigured state.)

**Verify**: `venv/bin/python - <<'EOF'` style check:
```bash
venv/bin/python - <<'EOF'
import os, tempfile
os.environ.pop("FB_APP_ID", None); os.environ.pop("FB_APP_SECRET", None)
import app
app.DB_PATH = tempfile.mktemp(); app.init_db()
c = app.app.test_client()
r = c.get("/fb-auth-url")
assert r.status_code == 503, r.status_code
print("guard OK")
EOF
```
→ prints `guard OK`.

### Step 3: Add a regression test

In `tests/test_app.py` add `test_fb_auth_requires_config`: with `FB_APP_ID`/`FB_APP_SECRET` absent from env, `GET /fb-auth-url` → 503.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass (8 tests if plan 001's 7 exist).

### Step 4: Confirm no secret-shaped literals remain

**Verify**: `git grep -nE "[0-9a-f]{32}" -- app.py` → no output, and `grep -n "1509522633850909" app.py` → no output.

## Test plan

One new test (Step 3). Existing suite is the regression gate.

## Done criteria

- [ ] No hardcoded FB credentials in `app.py` (Step 4 greps empty)
- [ ] `/fb-auth-url` returns 503 when unconfigured
- [ ] Full test suite passes
- [ ] Final report to the operator states in bold: **the old App Secret must be rotated at developers.facebook.com → App Settings → Basic, because it remains in git history**
- [ ] `plans/README.md` status row updated

## STOP conditions

- `app.py:36-37` no longer matches the excerpt shape (drifted).
- You find the same credentials hardcoded anywhere else in the repo (`git grep` the app-id digits across all files) — report all locations rather than fixing only one.

## Maintenance notes

- If the FB posting feature gets a UI later (see plan 005), configuration docs for these env vars must ship with it.
- Consider the same env-only treatment for `SET_KEY_SECRET` (the `/set-key` route at app.py:882-898 is unauthenticated when that var is unset — acceptable for a localhost-only tool, but revisit before any non-local deployment).
