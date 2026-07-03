# Implementation Plans

Batch 1 generated 2026-06-11 at commit `149315b` (plans 001–005, all DONE).
Batch 2 generated 2026-07-03 at commit `00bd8e2` by a full health-check audit
(the working tree also contained ~310 lines of uncommitted Gemini + landing
changes — plan 006 commits them; all other batch-2 plans assume 006 is done).

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
| 016 | Consolidate duplicated page JS into static/app.js | P3 | M | 007, 008, 011, 013, 014 | TODO |
| 017 | Small-fix bundle: /health contract, FTS 500s, SET_KEY_SECRET exposure, n>1 display | P3 | S | 006, (012 for Fix C) | TODO |

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

## Open operator decisions

- Plan 006 Step 4: confirm deletion of the stray directory
  `這個網站的風格有什麼建議的/` (untracked design artifacts).
- After plan 007 lands: one manual FB post smoke test with real page tokens.

## Findings considered and rejected (do not re-audit)

- **Move images out of SQLite into files on disk**: DB is now 308 MB
  (273 MB at last audit) — still under the ~1 GB revisit threshold set in
  batch 1. Trajectory says revisit in a few months.
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

## Direction findings (options, not defects — operator's call)

- **Provider abstraction**: `_MODELS[..]["provider"]` + `_generate_gemini`
  is already half an interface; static assets named
  `image-fal-ai-flux-schnell-*` show fal.ai experiments happened outside the
  app. Formalizing a provider interface would make fal.ai/Flux a one-file
  addition. Coarse effort: M.
- **REST API symmetry**: `/api/v1/generate` exists but there is no
  `/api/v1/history` or `/api/v1/models`; external callers can generate but
  not list results. Coarse effort: S (internal routes already exist).
