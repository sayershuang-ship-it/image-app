# Plan 006: Commit the in-flight Gemini + landing work; restore repo hygiene

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git status --short`
> This plan was written when the working tree had ~310 lines of uncommitted
> changes across `app.py`, `requirements.txt`, `static/app.css`,
> `templates/index.html`, `templates/studio.html`, `templates/templates.html`,
> plus 4 untracked `static/image-fal-ai-flux-schnell-*.jpg` files. If the tree
> is already clean, Steps 1–3 are done — skip to Step 4.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none — **all other plans (007–017) depend on this one**
- **Category**: tech-debt
- **Planned at**: commit `00bd8e2`, 2026-07-03 (working tree had uncommitted changes)

## Why this matters

The working tree contains two unrelated in-flight features that exist ONLY as
uncommitted changes: (a) the Google Gemini provider integration in `app.py` /
`templates/studio.html` / `templates/templates.html` / `requirements.txt`, and
(b) a landing-page refresh in `templates/index.html` that references four
untracked images. Every other plan in this batch edits this code — an executor
working from a fresh checkout/worktree of `00bd8e2` would not even see it.
Additionally, committing `index.html` without `git add`-ing the four
`static/image-fal-ai-flux-schnell-*.jpg` files would break the landing page
(hero mockup and showcase grid reference them at `templates/index.html:814-818`
and `templates/index.html:897-912`).

**Note**: this plan is an exception to the usual "executor works in a
worktree" model — Steps 1–3 operate on the user's actual working tree and are
best performed by the operator or with the operator watching, because they
create commits on the user's branch. If you are an executor in an isolated
worktree without these uncommitted changes, STOP and report.

## Current state

- `git status` shows modified: `app.py`, `requirements.txt`, `static/app.css`,
  `templates/index.html`, `templates/studio.html`, `templates/templates.html`.
- Untracked: `static/image-fal-ai-flux-schnell-mqc0b2r7.jpg`, `-mqc0b7kd.jpg`,
  `-mqc0bbsi.jpg`, `-mqc0bfuu.jpg` (referenced by the modified `index.html`).
- Untracked stray directory `這個網站的風格有什麼建議的/` containing design
  handoff artifacts (`DESIGN-HANDOFF.md`, `DESIGN-MANIFEST.json`, an
  `index.html` copy, duplicate PNGs). It duplicates content and its name is a
  chat prompt — it is not part of the app.
- `.gitignore` currently lacks `.DS_Store` (a `.DS_Store` exists at repo root
  and in `templates/`, `tests/`).
- `README.md` says the app is GPT-only ("生成模型： OpenAI gpt-image-2 /
  gpt-4o", line 10) — stale now that Gemini is integrated; the route table
  (lines 35–52) is missing `/api/models`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |
| App imports | `venv/bin/python -c "import app"` | exit 0, no output |
| Tree state | `git status --short` | (see per-step) |

## Scope

**In scope**:
- Git staging/commits of the already-made changes (no code edits to them)
- `.gitignore`
- `README.md`
- Deletion of the stray directory `這個網站的風格有什麼建議的/` (Step 4 — ask first)

**Out of scope** (do NOT touch):
- Any behavioral change to `app.py` or the templates — this plan commits the
  work as-is; bugs in it are fixed by plans 007–017.
- `prompts.db*` (already gitignored), `venv/`, `awesome-prompts/`.

## Git workflow

- Work directly on `main` (this repo commits to main; see `git log`).
- Commit message style: conventional prefixes, e.g.
  `feat: async image generation with job queue and progress UI` (from log).

## Steps

### Step 1: Verify the in-flight code passes the test suite

Run: `venv/bin/python -m pytest tests/ -q`

**Verify**: all tests pass. If any fail, STOP — the in-flight work is broken
and the operator must decide before it is committed.

### Step 2: Commit the Gemini provider integration

Stage and commit ONLY: `app.py`, `requirements.txt`, `templates/studio.html`,
`templates/templates.html`, `static/app.css`.

Suggested message: `feat: Google Gemini image provider (gemini-3.1-flash-lite-image) with model selector`

**Verify**: `git status --short` no longer lists those five files as modified.

### Step 3: Commit the landing-page refresh WITH its images

Stage and commit: `templates/index.html` AND the four
`static/image-fal-ai-flux-schnell-*.jpg` files together.

Suggested message: `feat: landing page showcase images + copy refresh`

**Verify**: `git status --short` shows no modified/untracked files except the
stray directory and `.DS_Store` files.
**Verify**: `git show --stat HEAD` lists `templates/index.html` and all 4 jpg files.

### Step 4: Remove the stray design-artifact directory (ask first)

The directory `這個網站的風格有什麼建議的/` appears to be an accidental
export (design handoff files + duplicate images). **Ask the operator to
confirm deletion** (or, if running unattended, STOP after Step 5 and report
that this directory needs a decision). If confirmed: `rm -rf` it. It is
untracked, so no git change results.

**Verify**: `ls` no longer shows the directory (if confirmed).

### Step 5: Ignore .DS_Store and refresh README

1. Append `.DS_Store` on its own line to `.gitignore`.
2. In `README.md`:
   - Line 10 (技術棧 → 生成模型): change to
     `- **生成模型：** OpenAI gpt-image-2 / gpt-4o、Google gemini-3.1-flash-lite-image`
   - Route table: add a row `| /api/models | 模型清單（含價格表） |`
   - 啟動 section: add a line noting the optional
     `GOOGLE_API_KEY=...`（環境變數或 `~/.image-studio.env`）for Gemini.
3. Commit `.gitignore` + `README.md`:
   `chore: ignore .DS_Store, document Gemini model + /api/models in README`

**Verify**: `git status --short` → `.DS_Store` files no longer appear as
untracked; working tree clean apart from anything the operator deferred.

## Test plan

No new tests — Step 1 runs the existing suite as the gate.

## Done criteria

- [ ] `venv/bin/python -m pytest tests/ -q` exits 0
- [ ] `git status --short` shows a clean tree (except items the operator explicitly deferred)
- [ ] `git log --oneline -3` shows the two feature commits + the chore commit
- [ ] `README.md` mentions Gemini and `/api/models`
- [ ] `plans/README.md` status row updated

## STOP conditions

- You are running in an isolated worktree that does NOT contain the
  uncommitted changes described above (nothing to commit — report back).
- Step 1 test failures.
- The operator is unavailable to confirm Step 4 deletion — skip deletion,
  finish the rest, and note it in your report.

## Maintenance notes

- After this lands, plans 007–017 can be executed from clean checkouts.
- The four landing images are fal.ai flux-schnell outputs used as static
  marketing assets; if the operator later adds fal.ai as a real provider
  (see direction notes in the audit), consider regenerating them in-app.
