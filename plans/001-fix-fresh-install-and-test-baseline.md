# Plan 001: Make a fresh clone installable and runnable, and add a pytest smoke-test baseline

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 149315b..HEAD -- requirements.txt app.py tests/`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `149315b`, 2026-06-11

## Why this matters

A fresh clone of this repo cannot start. Two independent breakages: (1) `app.py` does `import requests` but `requirements.txt` only lists `flask`, `openai`, `Pillow`, so `pip install -r requirements.txt && python app.py` fails with `ModuleNotFoundError: No module named 'requests'`. (2) `init_db()` in `app.py` runs `INSERT ... SELECT ... FROM community_prompts`, but no code in `app.py` creates the `community_prompts` table — it is only created by `sync_awesome_prompts.py`. On the developer's machine this works by accident because a populated `prompts.db` already exists; on any new machine `init_db()` raises `sqlite3.OperationalError: no such table: community_prompts`. The repo also has zero tests, so there is no one-command way to verify the app works — this plan establishes that baseline, which later plans (002–005) use as their verification gate.

## Current state

- `requirements.txt` — the whole file is:
  ```
  flask>=2.0
  openai>=1.0
  Pillow>=10.0
  ```
- `app.py:22` — `import requests` (used by the Facebook routes at app.py:737, 750, 799, 805, 848).
- `app.py:72-112` — `init_db()`. It creates the `prompts` table, runs column migrations, then at app.py:105-112 creates the FTS index and seeds it:
  ```python
  conn.execute("""
      CREATE VIRTUAL TABLE IF NOT EXISTS community_prompts_fts
      USING fts5(title, prompt, category, content='community_prompts', content_rowid='id')
  """)
  conn.execute("""
      INSERT OR IGNORE INTO community_prompts_fts(rowid, title, prompt, category)
      SELECT id, title, prompt, category FROM community_prompts
  """)
  ```
  `community_prompts` itself is created only in `sync_awesome_prompts.py:62-76` (`CREATE TABLE IF NOT EXISTS community_prompts (...)`).
- `app.py:976-982` — `init_db()` is called only under `if __name__ == "__main__":`.
- There is no `tests/` directory and no pytest config anywhere.
- A `venv/` exists at the repo root; the app runs with `python3 app.py` on port 5001 (`start.sh` documents this).
- DB path: `app.py:30` — `DB_PATH = os.path.join(os.path.dirname(__file__), "prompts.db")`. The live `prompts.db` is 273MB of real user data. **Never delete or overwrite it.** Tests must use a temporary DB path, not the real one.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Install | `venv/bin/pip install -r requirements.txt` | exit 0 |
| Install test dep | `venv/bin/pip install pytest` | exit 0 |
| Run tests | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |
| Syntax check | `venv/bin/python -m py_compile app.py` | exit 0, no output |

## Scope

**In scope** (the only files you should modify/create):
- `requirements.txt`
- `app.py` (only the `init_db()` function)
- `tests/test_app.py` (create)
- `tests/conftest.py` (create)

**Out of scope** (do NOT touch):
- `prompts.db`, `prompts.db-wal`, `prompts.db-shm` — live user data.
- `sync_awesome_prompts.py`, `import_fpd.py` — data import scripts, handled by plan 002.
- All templates.
- Any route function in `app.py`.

## Git workflow

- Branch: work directly on `main` is acceptable for this repo (single developer, no PR flow evident), or use `advisor/001-fresh-install` if instructed.
- Commit message style (match `git log`): `fix: <description>` / `feat: <description>`, e.g. `fix: fresh-install bootstrap + add pytest smoke tests`.
- Do NOT push.

## Steps

### Step 1: Add `requests` to requirements.txt

Append `requests>=2.28` to `requirements.txt` (keep the existing three lines unchanged).

**Verify**: `grep -c "requests" requirements.txt` → `1`

### Step 2: Make `init_db()` self-sufficient

In `app.py` `init_db()`, immediately BEFORE the `CREATE VIRTUAL TABLE ... community_prompts_fts` statement (app.py:105), insert the same `CREATE TABLE IF NOT EXISTS community_prompts` DDL used in `sync_awesome_prompts.py:62-76`:

```python
conn.execute("""
    CREATE TABLE IF NOT EXISTS community_prompts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        category TEXT,
        platform TEXT,
        author TEXT,
        image_url TEXT,
        source_url TEXT,
        prompt TEXT,
        score INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
```

This is idempotent (`IF NOT EXISTS`), so it is safe against the existing populated DB.

**Verify**: `venv/bin/python -c "import tempfile, os, app; app.DB_PATH = tempfile.mktemp(); import sqlite3; orig = app.DB_PATH"` is NOT a sufficient test — instead run:
```bash
venv/bin/python - <<'EOF'
import tempfile, os, importlib
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
import app
app.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")
app.init_db()
print("init_db OK on empty DB")
EOF
```
→ prints `init_db OK on empty DB`, exit 0.

Note: `init_db()` uses the module-level `DB_PATH` via `sqlite3.connect(DB_PATH)`. If reassigning `app.DB_PATH` after import does not take effect (it should, since `init_db` reads the global at call time), STOP and report.

### Step 3: Create the test baseline

Create `tests/conftest.py`:

```python
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module


@pytest.fixture()
def client():
    app_module.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")
    app_module.init_db()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
```

Create `tests/test_app.py` with these smoke tests (use the `client` fixture in each):

1. `test_health` — `GET /health` → status 200, JSON `{"status": "ok"}`.
2. `test_index_renders` — `GET /` → status 200.
3. `test_app_pages_render` — `GET` each of `/studio`, `/gallery`, `/history-view`, `/templates`, `/spark` → all status 200.
4. `test_history_empty` — `GET /history` → status 200, body is `[]` (empty JSON list, because the fixture DB is fresh).
5. `test_search_prompts_empty_query` — `GET /api/search-prompts?q=` → 200, `{"results": []}`.
6. `test_generate_requires_prompt` — `POST /generate` with JSON `{}` and env var `OPENAI_API_KEY=sk-test` set → 400 with an error message (no real API call happens because validation rejects first).
7. `test_db_file_blocked` — `GET /prompts.db` → 403.

**Verify**: `venv/bin/python -m pytest tests/ -q` → `7 passed`, exit 0.

### Step 4: Confirm nothing else changed

**Verify**: `git status --porcelain` shows only `requirements.txt`, `app.py`, `tests/` (plus the pre-existing dirty files `prompts.db-shm`, `prompts.db-wal`, `templates/history.html`, `sync_awesome_prompts.py`, `import_fpd.py`, `templates/fpd_styles.json` which were already modified/untracked before this plan — do not stage those).

## Test plan

Covered by Step 3 — the seven smoke tests above ARE the deliverable test baseline. No mocking of OpenAI is needed because no test triggers a real generation.

## Done criteria

- [ ] `venv/bin/pip install -r requirements.txt` exits 0 and installs `requests`
- [ ] The Step 2 empty-DB bootstrap script prints `init_db OK on empty DB`
- [ ] `venv/bin/python -m pytest tests/ -q` → 7 passed
- [ ] `git status` shows no modifications outside the in-scope list (beyond pre-existing dirt)
- [ ] `plans/README.md` status row updated

## STOP conditions

- `init_db()` no longer matches the excerpt (drifted code).
- Any test requires a real `OPENAI_API_KEY` to pass — that means a route is making a live API call on a validation path; report instead of stubbing.
- Reassigning `app.DB_PATH` doesn't redirect DB writes (would mean tests touch the real 273MB `prompts.db` — abort immediately).

## Maintenance notes

- Every later plan (002–005) should run `venv/bin/python -m pytest tests/ -q` as its regression gate.
- If routes are split out of `app.py` into modules later, `tests/conftest.py`'s import will need updating.
- Deferred: pinning exact dependency versions (`pip freeze`-style) — worth doing but a separate decision.
