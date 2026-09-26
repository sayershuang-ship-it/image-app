# Implementation Plans

Batch 1 generated 2026-06-11 at commit `149315b` (plans 001–005, all DONE).
Batch 2 generated 2026-07-03 at commit `00bd8e2` by a full health-check audit
(the working tree also contained ~310 lines of uncommitted Gemini + landing
changes — plan 006 commits them; all other batch-2 plans assume 006 is done).
Batch 3 generated 2026-09-26 at commit `bb87099` (plans 018–024; baseline
`venv/bin/python -m pytest tests/ -q` → 68 passed).

Execute in the order below unless dependencies say otherwise. Each executor:
read the plan fully before starting, honor its STOP conditions, and update
your row when done.

## Execution order & status

| Plan | Title | Priority | Effort | Depends on | Status |
|------|-------|----------|--------|------------|--------|
| 001 | Fix fresh install (requests dep, community_prompts bootstrap) + pytest baseline | P1 | S | — | DONE |
| 002 | Untrack db-wal/-shm, relocate fpd_styles.json, fix stale README/HEALTH_CHECK | P1 | S | 001 | DONE |
| 003 | Remove hardcoded Facebook App Secret (env-only) — **human must rotate secret** | P1 | S | 001 | DONE |
| 004 | Shared base template + static/ for studio/gallery/history/templates pages | P2 | M | 001 | DONE |
| 005 | Spike: Facebook posting UI in gallery | P3 | M | 003, (004) | DONE |
| 006 | Commit in-flight Gemini + landing work; repo hygiene (.DS_Store, README, stray dir) | P1 | S | — | DONE |
| 007 | Fix /fb-pages response-shape mismatch (FB posting UI is broken) | P1 | S | 006 | DONE |
| 008 | Gallery cards load /api/thumb instead of full-size /download | P1 | S | 006 | DONE |
| 009 | Store per-image cost_usd, not batch total (stats inflated up to n²×) | P1 | S | 006 | DONE |
| 010 | /set-key preserves other keys in ~/.image-studio.env (was wiping GOOGLE_API_KEY) | P1 | S | 006 | DONE |
| 011 | Escape community-prompt data in templates.html (stored XSS from scraped data) | P1 | S | 006 | DONE |
| 012 | Studio + templates pages read model config from /api/models (kills 3-way drift) | P2 | M | 006, after 009 | DONE |
| 013 | History: restore carries reference image via pid; Clear All truly clears | P2 | M | 006 | DONE |
| 014 | Parse SQLite UTC timestamps correctly in gallery/history (display + filters) | P2 | S | 006 | DONE |
| 015 | Tests: Gemini provider path, model validation, job lifecycle (all mocked) | P2 | M | 009, (012) | DONE |
| 016 | Consolidate duplicated page JS into static/app.js | P3 | M | 007, 008, 011, 013, 014 | DONE |
| 017 | Small-fix bundle: /health contract, FTS 500s, SET_KEY_SECRET exposure, n>1 display | P3 | S | 006, (012 for Fix C) | DONE |
| 018 | Generation routes check the selected model's provider key (Gemini works without OpenAI key) | P1 | S | — | DONE (merged c7dbb4c) |
| 019 | Shared `_parse_generation_request` for /generate, /api/v1/generate, /batch-generate | P2 | S | 018 | DONE (merged) |
| 020 | Download / ZIP export use real image format (no PNG-named-.jpg) | P3 | S | — | DONE (merged c7dbb4c) |
| 021 | Move images out of SQLite to `images/` + write-time thumbnails (migration run by operator) | P1 | M | 020 | DONE (merged e3b4b7d; migration NOT yet run) |
| 022 | Spike: record provider token usage; actual cost once official prices supplied | P2 | S | (019) | PARTIAL — Steps 1–2 DONE (merged e3b4b7d); Step 3/4 await operator |
| 023 | Studio "Compare" mode: one prompt → 2–4 models side-by-side with cost | P2 | M | 019, (022) | DONE (merged fb7de6e; UI verified in browser with stubbed API) |
| 024 | Semantic search over own history (reuse Ollama bge-m3) | P3 | M | 021 | DONE (merged fb7de6e; UI verified with stubbed API) |

Status values: TODO | IN PROGRESS | DONE | BLOCKED (with one-line reason) | REJECTED (with one-line rationale)

## Dependency notes

- **006 gates everything in batch 2**: the Gemini code the other plans edit
  exists only as uncommitted working-tree changes until 006 commits it.
  006 Steps 1–3 must run on the operator's working tree, not a fresh worktree.
- 012 should run after 009 (both edit the cost-related regions of
  `app.py`/`studio.html`); 015 asserts per-image costs, so it needs 009.
- 016 is a consolidation pass over helpers that 007/008/011/013/14 modify —
  run it last among the frontend plans or every earlier plan conflicts.
- 017 Fix C edits `studio.html` near regions 012 touches — do 017 after 012.
- 007–011 and 013–014 are mutually independent; parallel executors are fine
  as long as no two touch the same file (check each plan's Scope).

- **Batch 3 order**: 018 ∥ 020 → 019 → 021 → 022 → 023 → 024.
  019 needs 018's `_missing_key_for_model`; 021 reuses 020's
  `detect_image_format`; 023 must use 019's parser; 024 edits `init_db`
  after 021's columns.

## Open operator decisions

- 023/024 merged 2026-09-26 (fb7de6e); worktrees and advisor branches cleaned up. With Ollama + bge-m3 running, backfill: `venv/bin/python generate_history_embeddings.py --dry-run 5`, then without the flag.

- Note for executors: new worktrees start at `bb87099`, not local main — every dispatch must first `git reset --hard <current main>` on its fresh branch.

- Plan 021 Step 6 (after merging): **stop the app first**, then `sqlite3 prompts.db ".backup 'prompts.db.bak-before-images'"`, dry run `venv/bin/python migrate_images_to_files.py`, then `--apply --vacuum`. Afterwards back up `images/` along with `prompts.db`.
- Plan 022 Step 3: approve ~$0.05 of real low-quality calls; supply official per-token prices (with source URL) if wanted.
- Plan 006 Step 4: confirm deletion of the stray directory
  `這個網站的風格有什麼建議的/` (untracked design artifacts).
- After plan 007 lands: one manual FB post smoke test with real page tokens.

## Findings considered and rejected (do not re-audit)

- ~~**Move images out of SQLite into files on disk**~~: RE-OPENED in batch 3
  as plan 021 — DB 514 MB (2026-09-23), 2.5-flare images avg 2.96 MB b64,
  4K sizes now allowed; /api/thumb decodes full images per request.
- **Rewrite git history to purge committed WAL data**: unchanged verdict —
  only before publishing the repo.
- **CSRF/auth on state-changing routes**: unchanged verdict — app binds
  127.0.0.1; MUST be revisited before any non-localhost deployment. (Plan 013
  adds `DELETE /history`; its STOP condition re-checks this assumption.)
- **FTS index staleness** (no triggers; new imports unsearchable until
  restart): unchanged — imports are rare, restart fixes it.
- **Unbounded threads in /batch-generate**: unchanged — capped at 20 by the
  prompt cap.
- **Server-side sanitization of community prompts** (alternative to plan 011):
  rejected for now — single consumer; frontend escaping is smaller. Revisit if
  a second consumer of `/api/community-prompts` appears.
- **Pagination for /history beyond LIMIT 200**: not worth it for a
  single-user tool today; Clear All (plan 013) removes the main confusion the
  cap caused.
- **JS test tooling** (vitest/jest for template scripts): overhead outweighs
  value at this size; pytest render-smoke + grep gates suffice.

- **`/save-to-pictures` fetches arbitrary http URLs server-side** (batch 3):
  localhost-only single-user tool; negligible.
- **Templates semantic search is a full Python cosine scan** (batch 3): 1,095
  rows is fine; revisit past ~5k.

## Direction findings (options, not defects — operator's call)

- **Mask inpainting** (batch 3, not selected): `images.edit` supports `mask`;
  Studio already has "Variation" (`useAsReference`). Needs a canvas brush UI. Effort M.

- **Provider abstraction**: `_MODELS[..]["provider"]` + `_generate_gemini`
  is already half an interface; static assets named
  `image-fal-ai-flux-schnell-*` show fal.ai experiments happened outside the
  app. Formalizing a provider interface would make fal.ai/Flux a one-file
  addition. Coarse effort: M.
- **REST API symmetry**: `/api/v1/generate` exists but there is no
  `/api/v1/history` or `/api/v1/models`; external callers can generate but
  not list results. Coarse effort: S (internal routes already exist).
