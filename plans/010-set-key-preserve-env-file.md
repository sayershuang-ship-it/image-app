# Plan 010: /set-key must not wipe other keys in ~/.image-studio.env

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- app.py tests/test_app.py`
> Changes from plans 006–009 are expected. Compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md
- **Category**: bug
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

The Gemini integration reads `GOOGLE_API_KEY` from `~/.image-studio.env`
(`app.py:52-59`). But the `/set-key` endpoint persists the OpenAI key by
opening that same file in `"w"` mode and writing ONLY the
`OPENAI_API_KEY=` line (`app.py:1108-1109`). The first time the operator sets
their OpenAI key via the studio UI, the Google key line is silently destroyed
— Gemini keeps working until the next restart, then fails with "GOOGLE_API_KEY
not configured" with no obvious cause. Classic delayed-detonation bug.

## Current state

- `app.py:40` — `KEY_FILE = os.path.expanduser("~/.image-studio.env")`.
- Readers: `app.py:43-49` (OPENAI_API_KEY) and `app.py:52-59`
  (GOOGLE_API_KEY) both parse `NAME=value` lines from `KEY_FILE`.
- The writer, `app.py:1094-1110` (`/set-key`):

```python
    os.environ["OPENAI_API_KEY"] = key
    global API_KEY
    API_KEY = key
    # Persist to disk so it survives restarts
    with open(KEY_FILE, "w") as f:
        f.write(f"OPENAI_API_KEY={key}\n")
    return jsonify(status="ok")
```

- Test conventions: `tests/test_app.py`, fixture `client` in
  `tests/conftest.py`. Tests that redirect module-level file paths (e.g.
  `FB_PAGE_TOKEN_FILE`) save and restore them in `try/finally` — match that
  pattern for `KEY_FILE`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |
| App imports | `venv/bin/python -c "import app"` | exit 0 |

## Scope

**In scope**:
- `app.py` — a small read-modify-write helper + the `/set-key` persistence block
- `tests/test_app.py` (add test)

**Out of scope**:
- The startup readers (lines 43–59) — their line-parsing format is the contract; keep it.
- `/set-key`'s auth logic (`SET_KEY_SECRET` header check) — plan 017 touches that.
- `start.sh` and `~/.hermes/.env` handling.

## Git workflow

- Commit style: `fix: /set-key preserves other keys in ~/.image-studio.env`

## Steps

### Step 1: Add an upsert helper and use it

In `app.py`, near the KEY_FILE config (below line 40), add:

```python
def _upsert_key_file(name: str, value: str) -> None:
    """Update or append NAME=value in KEY_FILE, preserving other lines."""
    lines = []
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            lines = [l.rstrip("\n") for l in f]
    prefix = f"{name}="
    lines = [l for l in lines if not l.startswith(prefix)]
    lines.append(f"{name}={value}")
    with open(KEY_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
```

Then in `/set-key`, replace the two-line `with open(KEY_FILE, "w")` block with
`_upsert_key_file("OPENAI_API_KEY", key)` (keep the comment).

**Verify**: `venv/bin/python -c "import app"` → exit 0.

### Step 2: Regression test

In `tests/test_app.py`, add `test_set_key_preserves_other_keys(client)`:

1. Create a temp file containing two lines:
   `GOOGLE_API_KEY=g-test\nOPENAI_API_KEY=sk-old\n`.
2. Save `app_module.KEY_FILE`, point it at the temp file (try/finally restore).
3. POST `/set-key` with json `{"api_key": "sk-newkey123"}`
   (no `SET_KEY_SECRET` env set in tests, so the header check is skipped;
   if the environment does define it, `monkeypatch.delenv("SET_KEY_SECRET", raising=False)`).
4. Assert response 200 and file content contains BOTH `GOOGLE_API_KEY=g-test`
   and `OPENAI_API_KEY=sk-newkey123`, and does NOT contain `sk-old`.
5. In the finally block, also restore `os.environ["OPENAI_API_KEY"]` to its
   previous value (the endpoint mutates it globally).

**Verify**: `venv/bin/python -m pytest tests/ -q -k set_key` → passes.

## Test plan

- New: `test_set_key_preserves_other_keys` (above); also covers the update
  (not just append) path since the file starts with an old OPENAI line.
- Full run: `venv/bin/python -m pytest tests/ -q` → all pass.

## Done criteria

- [ ] `venv/bin/python -m pytest tests/ -q` exits 0, including the new test
- [ ] `grep -n '_upsert_key_file' app.py` → 2+ matches (def + call)
- [ ] `grep -n 'open(KEY_FILE, "w")' app.py` → matches only inside `_upsert_key_file`
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- The `/set-key` block differs from the excerpt (drift).
- The test cannot restore global env state cleanly — report rather than
  leaving `OPENAI_API_KEY` polluted for other tests.

## Maintenance notes

- If a UI for setting `GOOGLE_API_KEY` is added later, reuse
  `_upsert_key_file("GOOGLE_API_KEY", ...)` — that's why it's parameterized.
- Reviewer: confirm file still ends with exactly one trailing newline (the
  startup parser doesn't care, but keep it tidy).
