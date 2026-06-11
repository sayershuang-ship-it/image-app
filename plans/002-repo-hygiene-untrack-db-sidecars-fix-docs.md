# Plan 002: Untrack SQLite WAL/SHM sidecars, relocate misplaced data files, and fix stale docs

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 149315b..HEAD -- .gitignore README.md HEALTH_CHECK.md import_fpd.py templates/fpd_styles.json`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (run plan 001 first so tests exist as a gate)
- **Category**: tech-debt
- **Planned at**: commit `149315b`, 2026-06-11

## Why this matters

`prompts.db` is correctly gitignored, but its SQLite sidecars `prompts.db-wal` (6.6MB of live write-ahead data) and `prompts.db-shm` ARE tracked in git. This means: the working tree is permanently dirty (they change on every app write), recent user data gets committed to version control, and a checkout on another machine would pair stale sidecars with a missing main DB — which can corrupt SQLite recovery. Separately, `templates/fpd_styles.json` is a data file living in the Flask templates directory and is a near-duplicate of the `STYLES` list hardcoded inside `import_fpd.py` (two sources of truth); and `README.md`/`HEALTH_CHECK.md` are actively wrong (README describes a one-page app with 53/162 prompts — reality is 6 pages and 177/489 rows; HEALTH_CHECK.md counted `venv/` files as source code).

## Current state

- `.gitignore` currently contains: `.DS_Store`, `venv/`, `__pycache__/`, `*.pyc`, `.hermes/`, `.claude/`, `dogfood-output/`, `image-studio-redesign.html`, `image-studio-redesign.md`, `Design/`, `prompts.db` — note it lacks `prompts.db-wal`, `prompts.db-shm`, and `tests/__pycache__` patterns are covered by `__pycache__/`.
- `git ls-files` includes `prompts.db-shm` and `prompts.db-wal` (tracked).
- `templates/fpd_styles.json` — untracked data file (14 portrait style routes, JSON). It is referenced ONLY in the docstrings of `import_fpd.py` and `sync_awesome_prompts.py`; no Python code reads it. `import_fpd.py` has the same 14 styles hardcoded in its `STYLES` list (import_fpd.py:16 onward).
- `import_fpd.py` — untracked script; upserts 14 styles into `community_prompts` keyed by `route_id` (adds a `route_id` column + unique index at import_fpd.py:135-136).
- `README.md` — stale: says frontend is `index.html` (root, not templates/), lists DB as 53 own + 162 community prompts (actual: 177 prompts, 489 community_prompts), does not mention `/studio`, `/gallery`, `/history-view`, `/templates`, `/spark`, the job queue, or the REST API at `/api/v1/generate`.
- `HEALTH_CHECK.md` — auto-generated 2026-05-21 by a script that counted `venv/` (claims 6596 files, 2636 source files, 2 test files — all wrong for this repo).
- `awesome-prompts/` — appears in `git ls-files` as a single entry (embedded repo/submodule-like). Leave it alone.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Tests (from plan 001) | `venv/bin/python -m pytest tests/ -q` | all pass |
| Tracked-file check | `git ls-files \| grep prompts.db` | no output (after fix) |

## Scope

**In scope**:
- `.gitignore`
- `prompts.db-wal`, `prompts.db-shm` — git index only (`git rm --cached`); never touch the files on disk
- `templates/fpd_styles.json` → move to `data/fpd_styles.json`
- `import_fpd.py` — update its docstring path reference; track the file in git
- `sync_awesome_prompts.py` — update its docstring path reference only
- `README.md`
- `HEALTH_CHECK.md` (delete)

**Out of scope**:
- The actual bytes of `prompts.db*` on disk — `git rm --cached` only, NEVER plain `git rm` or `rm`.
- Rewriting git history to purge the previously committed WAL data (data is low-sensitivity generated images; history rewrite not worth the disruption — recorded as rejected in plans/README.md).
- `awesome-prompts/` directory.
- All templates and `app.py`.

## Git workflow

- Commit style: `chore: untrack db sidecars, relocate fpd_styles, refresh README`.
- Do NOT push.

## Steps

### Step 1: Untrack the SQLite sidecars

```bash
git rm --cached prompts.db-shm prompts.db-wal
```
Then append to `.gitignore`:
```
prompts.db-wal
prompts.db-shm
```

**Verify**: `git ls-files | grep prompts` → no output. `ls prompts.db prompts.db-wal prompts.db-shm` → all three still exist on disk.

### Step 2: Relocate fpd_styles.json out of templates/

```bash
mkdir -p data
git mv 2>/dev/null || true   # file is untracked; use plain mv
mv templates/fpd_styles.json data/fpd_styles.json
```
Update the docstring in `import_fpd.py` (line ~7: `templates/fpd_styles.json` → `data/fpd_styles.json`) and the same reference in `sync_awesome_prompts.py`'s docstring.

Because `import_fpd.py` hardcodes the styles and never reads the JSON, also add one line to the top of `data/fpd_styles.json`'s sibling — no. Instead, add a note in `import_fpd.py`'s docstring: `NOTE: data/fpd_styles.json is the reference copy; the STYLES list below is the authoritative import source.` (Resolving the duplication properly — making the script read the JSON — is optional; if you do it, the JSON's `styles[*]` fields map as `route_id`, `style_name_zh`, `category`, and the prompt text lives in the script only, so full deduplication is NOT possible without merging fields into the JSON. Default: docstring note only.)

**Verify**: `ls templates/*.json` → no matches; `grep -n "data/fpd_styles" import_fpd.py sync_awesome_prompts.py` → 2 hits.

### Step 3: Track the import script

```bash
git add import_fpd.py data/fpd_styles.json
```

**Verify**: `git status --short` shows them staged as `A`.

### Step 4: Delete HEALTH_CHECK.md and rewrite README.md

`git rm HEALTH_CHECK.md`.

Rewrite `README.md` keeping its language (繁體中文) and structure, correcting:
- 技術棧: Flask + SQLite + 6 Jinja templates (no static dir yet), OpenAI gpt-image-2 / gpt-4o.
- 目錄結構: `app.py`, `prompts.db` (gitignored, prompts + community_prompts tables), `templates/` (index 首頁, studio 生成器, gallery, history, templates 提示詞庫, spark 實驗性介面), `sync_awesome_prompts.py`, `import_fpd.py`, `data/fpd_styles.json`, `tests/`.
- 啟動: `./start.sh` 或 `OPENAI_API_KEY=sk-... venv/bin/python app.py` → http://localhost:5001 .
- 主要路由表: `/` `/studio` `/gallery` `/history-view` `/templates` `/spark` `/generate` `/batch-generate` `/api/v1/generate` `/api/search-prompts` `/api/stats` `/export-zip` `/health`.
- 測試: `venv/bin/python -m pytest tests/ -q`.
- Do not invent features; only document what the routes above actually do (read `app.py` to confirm wording).

**Verify**: `grep -c "53筆\|162筆\|index.html             # 前端頁面" README.md` → 0.

### Step 5: Regression gate

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass (templates page still renders; nothing imports the moved JSON).

## Test plan

No new tests; plan 001's suite is the gate. The key regression risk is something secretly reading `templates/fpd_styles.json` — Step 2's grep plus the test suite covers it.

## Done criteria

- [ ] `git ls-files | grep prompts` → empty
- [ ] `prompts.db`, `-wal`, `-shm` still present on disk, byte-identical content (no app downtime needed)
- [ ] `templates/` contains only `.html` files
- [ ] `HEALTH_CHECK.md` deleted; `README.md` has no stale counts
- [ ] `venv/bin/python -m pytest tests/ -q` passes
- [ ] `plans/README.md` status row updated

## STOP conditions

- Any Python or template code (not docstring) actually reads `templates/fpd_styles.json` — the grep in "Current state" says only docstrings reference it; if that's wrong, stop.
- `git rm --cached` reports the sidecar files as untracked already (drift — someone fixed it).
- The Flask app is mid-write and `prompts.db-wal` is locked — fine for `mv`-free steps, but if any step errors with "database is locked", stop the dev server first and report.

## Maintenance notes

- The historical WAL blobs remain in git history (~6MB); if the repo is ever published, run `git filter-repo --path prompts.db-wal --path prompts.db-shm --invert-paths` first.
- If `import_fpd.py` grows more styles, decide then whether the JSON becomes the single source (script reads JSON) — currently the script is authoritative.
