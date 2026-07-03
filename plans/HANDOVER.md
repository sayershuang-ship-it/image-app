# Handover — 2026-07-04

## What shipped

All 12 implementation plans (006–017) from the 2026-07-03 health-check audit are DONE.

### Core feature
- **Google Gemini image provider** (`gemini-3.1-flash-lite-image`) alongside OpenAI `gpt-image-2`. Both models coexist, selectable from the Studio and Templates UIs. Google key reads `GOOGLE_API_KEY` from `~/.image-studio.env` (same file as `OPENAI_API_KEY`).
- **Model config single source** — both frontends fetch `/api/models` dynamically; adding a model now means editing only `_MODELS` in `app.py` + `SIZE_LABELS` in studio.html.

### Bugs fixed
- FB posting UI (`/fb-pages` response unwrapping, page-name escaping)
- Gallery cards now load 256px `/api/thumb` instead of full `/download` (perf)
- Cost tracking: each image row stores per-image cost, not batch total (was inflated up to n²×)
- `/set-key` no longer wipes `GOOGLE_API_KEY` from `~/.image-studio.env`
- Community prompt XSS — all interpolations escaped; inline `onclick` replaced with delegation
- History: restore passes pid (avoids localStorage quota); Clear All deletes all rows via `DELETE /history`
- UTC timestamp parsing (`YYYY-MM-DD HH:MM:SS` → ISO UTC) for gallery/history display & filters
- `/health` now returns `api_key_set` (badge works)
- FTS search sanitized (quotes, hyphens, non-numeric limit no longer 500)
- `SET_KEY_SECRET` no longer rendered into page HTML; replaced with `prompt()` on demand
- Templates page now shows all n results (not just `results[0]`)

### Refactors
- Shared JS extracted into `static/app.js` (`$`, `escHtml`, `escAttr`, `parseTs`, `toast`, `sleep`, `pollJob`, `exportZip`). All four app pages now load it via `base_app.html`. `index.html` intentionally excluded (standalone landing page).
- 12 new tests (22 total): model validation, Gemini generation path (mocked), job lifecycle, `/api/models` contract, `/fb-pages` contract, per-image cost, `/set-key`, FTS special chars, `/health` contract, `/use-history` roundtrip, `DELETE /history`.

## Final state

```
62c46eb refactor: extract shared page JS into static/app.js          ← HEAD (main)
2017a77 fix: sanitize FTS query input
b5656ba fix: /health reports api_key_set
7a12a15 test: cover Gemini provider path, model validation, job lifecycle
ed6ec79 refactor: studio + templates pages load model config from /api/models
fd7c568 fix: parse prompt_ts as UTC in gallery/history (display + filters)
e200d62 fix: restore carries reference image via pid; DELETE /history clears all rows
97cdc86 fix: escape community-prompt data in templates page (stored XSS)
4fca67d fix: /set-key preserves other keys in ~/.image-studio.env
f351e30 fix: store per-image cost_usd instead of batch total on each row
edacc1e perf: gallery cards use /api/thumb, full image only in modal
ff7cbe2 fix: gallery reads /fb-pages payload shape; escape page names in fb_callback
5fefd12 chore: document Gemini model + /api/models in README
b9f2e21 feat: landing page showcase images + copy refresh
b9aaa6d feat: Google Gemini image provider with model selector
00bd8e2 fix: auto-kill process on port 5001 before starting          ← base
```

- **Tests**: `venv/bin/python -m pytest tests/ -q` → 22 passed
- **Tree**: clean except `plans/README.md` (modified) and untracked plan files under `plans/`

## What still needs doing

1. **Operator FB smoke test** — after Plan 007, `POST /fb-post` should now work with real page tokens. Do one manual round-trip.
2. **Operator decision** — commit the `plans/*.md` files into the repo. (They're currently untracked on purpose — the executor left them for human review.)
3. **Direction finding (from audit)** — Provider abstraction (fal.ai/Flux is a natural third model given the landing page already uses its outputs). Coarse effort: M. REST API symmetry (`/api/v1/history`, `/api/v1/models`). Coarse effort: S.

## How to add a new model

1. Add an entry to `_MODELS` in `app.py` (provider, qualities, sizes, cost_table)
2. If it's a new provider, add a `_generate_<provider>()` function and a branch in `_run_generation()`
3. Add a `SIZE_LABELS` entry in `studio.html` if the model uses a new size
4. Everything else (dropdowns, cost badge, validation) self-updates from `/api/models`

## Operator environment

- `OPENAI_API_KEY` and `GOOGLE_API_KEY` are read from `~/.image-studio.env` at startup
- Run: `./start.sh` or `OPENAI_API_KEY=sk-... GOOGLE_API_KEY=... venv/bin/python app.py`
- Port: `http://localhost:5001`
