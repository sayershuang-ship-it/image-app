# Plan 021: Store generated images as files on disk, with thumbnails made at write time

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat bb87099..HEAD -- app.py tests/ .gitignore`
> Changes from plans 018–020 are expected. Re-locate every function named
> below by name, not by line number, and confirm that its body matches the
> excerpt. On a mismatch, STOP.
>
> **This plan touches the operator's real data (`prompts.db`, ~514 MB). Step 6
> is a one-time migration and MUST NOT be run by the executor.** The executor
> writes and tests the script against a temp DB. The human runs it on the real DB.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED (data migration; mitigated by a backup, dry-run, and a read path that tolerates both storage modes)
- **Depends on**: plans/020-detect-image-format-on-export.md (uses `detect_image_format`)
- **Category**: perf / architecture
- **Planned at**: commit `bb87099`, 2026-09-26

## Why this matters

Every generated image is stored as a base64 TEXT column (`prompts.result_b64`)
in SQLite. The DB grew from 308 MB (2026-07-03) to 514 MB (2026-09-23), and
490 MB of that is the `prompts` table (270 rows). New gpt-image-2.5-flare images
average 2.96 MB each in base64, and the 2.5 models allow sizes up to
3840x2160, so growth is accelerating. `DELETE` never runs `VACUUM`, so deleting
images doesn't shrink the file. On top of that, `/api/thumb/<pid>` decodes the
full 2–3 MB PNG and resizes it on **every** request, so a cold gallery load
decodes N full images. And the failure path stores the **full-size** reference
upload (up to ~15 MB) instead of a thumbnail. After this plan: image bytes live
in `images/<pid>.<ext>`, thumbnails in `images/thumbs/<pid>.jpg` (made once at
write time), and the DB holds only paths.

## Current state

- `save_prompt(...)`, `app.py:385-396`: inserts `result_b64` (and `image_b64`,
  the reference upload) directly.
- Callers of `save_prompt` that pass result bytes:
  - `_generate_gemini`, `app.py:703`: `save_prompt(prompt, make_thumbnail(image_b64) if image_b64 else None, None, "standard", size, model, None, b64, True, ...)`
  - `_generate_openai` URL branch, `app.py:772`: passes the **raw** `image_b64` (not a thumbnail) and `raw_b64`.
  - `_generate_openai` b64 branch, `app.py:779`: passes `make_thumbnail(image_b64)` and `item.b64_json`.
  - `_finalize_job_failure`, `app.py:721`: passes the **raw** `image_b64` and no result.
- Readers of `result_b64` (every one must switch to a shared loader):
  - `/api/thumb/<pid>`, `app.py:428-450` (decodes + resizes to 256px on each call)
  - `/export-zip`, `app.py:998-1031`
  - `/fb-post`, `app.py:1316-…` (`SELECT result_b64, prompt …`, around line 1335)
  - `/download/<pid>`, `app.py:1467-1486`
  - `/save-to-pictures/<pid>`, `app.py:1505-1523`
  - `GET /history/<pid>`, `app.py:417-426` does `SELECT *` (it returns `result_b64` in JSON; no frontend code reads that field — confirmed by `grep -rn result_b64 templates static` returning nothing)
  - `GET /history`, `app.py:403-415` computes `has_result` from `result_b64 IS NOT NULL`
- `init_db()`, `app.py:131-205`: migrates columns with the `PRAGMA table_info` + `ALTER TABLE … ADD COLUMN` pattern (lines 154-162). Match it.
- Tests: `tests/conftest.py` sets `app_module.DB_PATH` to a temp dir per test.
  **Derive the image directory from `DB_PATH` at call time** (not as an
  import-time constant), so tests automatically write to their temp dir.
- `.gitignore` already ignores `prompts.db*`. `images/` must be added.

## Scope

**In scope**: `app.py`, `tests/test_app.py`, `.gitignore`, a new
`migrate_images_to_files.py` (repo root, next to the existing
`migrate_templates.py`), and a new test file `tests/test_migrate_images.py`.
**Out of scope**: templates and JS (URLs `/api/thumb/<pid>` and
`/download/<pid>` keep working unchanged), the `templates` and
`community_prompts` tables, and `image_b64` storage for **successful** rows
(it is already thumbnailed).

## Steps

### Step 1: Schema + path helpers
- In `init_db`, add columns `result_path TEXT` and `thumb_path TEXT` to
  `prompts` using the existing loop at lines 155-162.
- Add helpers:
  ```python
  def _images_dir() -> str:
      d = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "images")
      os.makedirs(os.path.join(d, "thumbs"), exist_ok=True)
      return d

  def _write_image_files(pid: int, img_bytes: bytes) -> tuple:
      """Write full image + 256px JPEG thumb. Return (result_path, thumb_path), relative to _images_dir()."""
  ```
  Use `detect_image_format` for the extension. The thumbnail uses the same
  resize code as the current `/api/thumb` (RGB convert, 256 max, JPEG q75).
  Write to a `.tmp` file, then `os.replace`, so a crash never leaves a partial file.
- Add `def _load_result_bytes(row) -> bytes | None`. It takes a row that has
  `result_path` and `result_b64`. It returns the file bytes if `result_path` is
  set and the file exists, else the decoded `result_b64`, else `None`.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass.

### Step 2: Write path
Change `save_prompt` so that when `result_b64` is given, it inserts the row
with `result_b64=NULL`, then calls `_write_image_files(pid, base64.b64decode(result_b64))`,
then does `UPDATE prompts SET result_path=?, thumb_path=? WHERE id=?`. If writing
the file raises, fall back to storing `result_b64` in the row (log with `print`,
matching the file's existing logging style) so that a generation is never lost.
Also, in `_finalize_job_failure` and the OpenAI URL branch (`app.py:772`), pass
`make_thumbnail(image_b64) if image_b64 else None` instead of the raw `image_b64`.

**Verify**: new test `test_save_prompt_writes_files` (see Test plan) passes.

### Step 3: Read paths
Switch every reader listed in "Current state" to select `result_path, result_b64`
and use `_load_result_bytes`. Specifically:
- `/api/thumb`: if `thumb_path` exists, `send_file` it directly. Otherwise
  keep the current on-the-fly resize (for legacy rows).
- `GET /history`: `has_result` = `result_path IS NOT NULL OR result_b64 IS NOT NULL`.
- `GET /history/<pid>`: replace `SELECT *` with an explicit column list that
  excludes `result_b64` and adds `result_path` (the response shrinks from MBs
  to bytes; no frontend reads the field).
- `DELETE /history/<pid>` and `DELETE /history`: also delete the files
  (ignore `FileNotFoundError`).

**Verify**: `grep -n "result_b64" app.py` → matches only in `init_db`, `save_prompt`, `_load_result_bytes`, the fallback branch, and SELECT column lists. None of them may pass `row["result_b64"]` straight to `base64.b64decode` outside `_load_result_bytes`.

### Step 4: `.gitignore`
Add `images/`.

**Verify**: `git check-ignore images/x.png` → prints the path.

### Step 5: Migration script
Create `migrate_images_to_files.py`, modeled on the CLI style of `migrate_templates.py`:
- `--db PATH` (default `prompts.db` next to the script), `--dry-run` (default **on**; `--apply` turns it off), `--vacuum`.
- Before `--apply`: refuse to run if `<db>.bak-before-images` does not exist,
  and print the exact `sqlite3 <db> ".backup '<db>.bak-before-images'"` command for the operator.
- For every row where `result_b64 IS NOT NULL AND result_path IS NULL`: write
  the files, then verify that the written file's bytes equal the decoded b64,
  **then** set the paths and `result_b64=NULL`. Commit every 20 rows.
- Also replace full-size `image_b64` on `success=0` rows with `make_thumbnail(...)`.
- It must be idempotent: a second run finds 0 rows.
- It prints: rows migrated, bytes moved, and DB size before/after (`--vacuum` runs `VACUUM` at the end).
Import helpers from `app` (`import app as app_module`, then set `app_module.DB_PATH = args.db`) instead of duplicating code.

**Verify**: `venv/bin/python migrate_images_to_files.py --db /tmp/does-not-exist.db` → a clean error, not a traceback. Do **not** run it against `prompts.db`.

### Step 6 (OPERATOR ONLY — executor must not run): real migration
Document these commands at the end of your report, then stop:
```
sqlite3 prompts.db ".backup 'prompts.db.bak-before-images'"
venv/bin/python migrate_images_to_files.py            # dry run
venv/bin/python migrate_images_to_files.py --apply --vacuum
```

## Test plan
In `tests/test_app.py` (use the `client` fixture; make real bytes with Pillow as in plan 020):
- `test_save_prompt_writes_files`: after `save_prompt(...)`, the row has `result_b64 IS NULL`, and both files exist under `os.path.dirname(app_module.DB_PATH)/images`.
- `test_thumb_served_from_file`: GET `/api/thumb/<pid>` → 200, `image/jpeg`, and the body equals the thumb file's bytes.
- `test_legacy_b64_row_still_readable`: insert a row with raw SQL (`result_b64` set, no path) → `/download` and `/api/thumb` both return 200.
- `test_delete_removes_files`.
- `test_history_item_excludes_result_b64`.
- `test_failure_row_stores_thumbnail_reference`: `_finalize_job_failure` with a 2000x2000 PNG base64 → the stored `image_b64` decodes to an image whose longest side is ≤ 512.
New `tests/test_migrate_images.py` (model it after `tests/test_migrate_templates.py`):
- dry-run changes nothing; `--apply` without a backup refuses; `--apply` with a backup migrates, and a second run migrates 0 rows.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass.

## Done criteria
- [ ] Tests exit 0 with ≥ 9 new tests
- [ ] `git check-ignore images/` succeeds
- [ ] `grep -n "base64.b64decode(row\[\"result_b64\"\])" app.py` → no matches
- [ ] `prompts.db` is untouched by the executor (`ls -l prompts.db` shows the same mtime as before starting)
- [ ] Only in-scope files are modified or created

## STOP conditions
- Any step would require writing to the real `prompts.db` or `images/` in the repo root.
- A frontend file turns out to read `result_b64` (`grep -rn result_b64 templates static` returns a match).
- `/fb-post` streams `result_b64` somewhere other than a multipart upload (re-read that function; if it is different from what "Current state" describes, STOP).

## Maintenance notes
- Backups of the app now need both `prompts.db` **and** `images/`. Tell the operator.
- The legacy b64 read fallback can be removed once `SELECT COUNT(*) FROM prompts WHERE result_b64 IS NOT NULL` is 0 on the operator's DB.
- Plan 024 adds another column to `prompts`; it must run after this plan to avoid merge conflicts in `init_db`.
- Reversed decision: the batch-1/2 index listed "move images out of SQLite" as rejected until the DB reached 1 GB. It was re-opened on 2026-09-26 because the growth rate and 4K sizes make the threshold a few months away, and the thumbnail cost is paid today.
