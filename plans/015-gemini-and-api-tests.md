# Plan 015: Test coverage for the Gemini provider path and generation contracts

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- app.py tests/`
> Changes from plans 006–014 are expected (009 and 012 add tests to the same
> file). Compare the "Current state" excerpts against the live code before
> proceeding; on a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW (test-only, plus no production code changes)
- **Depends on**: plans/009-fix-cost-double-counting.md (asserts per-image cost); ideally after 012 (its `/api/models` test may already exist — do not duplicate)
- **Category**: tests
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

The Gemini provider integration (`_generate_gemini`, provider dispatch in
`_run_generation`, model validation in `/generate`) shipped with zero tests.
The audit found real contract-drift bugs elsewhere (`/fb-pages` shape, studio
cost-table clone) that a thin contract-test layer would have caught. This plan
locks the generation contracts: provider dispatch, model/quality validation,
job lifecycle, and the Gemini result path — all with mocked SDKs, no network.

## Current state

- `app.py:426-481` — `_generate_gemini(job_id, prompt, image_b64, size, n,
  model, original_prompt)`: lazy-imports `google.genai.types`, gets client via
  `get_google_client()` (`app.py:63-68`, returns `None` without
  `GOOGLE_API_KEY`), raises if no client; iterates
  `response.candidates[*].content.parts[*].inline_data` for images; saves rows
  via `save_prompt`; sets `_jobs[job_id]` to done with `results`.
- `app.py:484-557` — `_run_generation`: dispatches on
  `_MODELS[model]["provider"]`; exception handler saves a failed row and marks
  the job failed.
- `app.py:560-604` — `/generate`: rejects unknown models with 400
  (`if model not in _MODELS`), coerces invalid quality to the model's first
  quality, spawns a thread.
- `app.py:607-613` — `/job-status/<job_id>`: 404 for unknown; returns the job
  dict otherwise.
- Existing test style: `tests/test_app.py` — `patch("app.requests.post")`
  with `MagicMock` in `test_fb_post_requires_token_or_pid`; fixture `client`
  in `tests/conftest.py` gives a temp DB. `api_key_required` checks
  `os.environ["OPENAI_API_KEY"]` — tests that hit `/generate` must set it
  (e.g. `monkeypatch.setenv("OPENAI_API_KEY", "sk-test")`).
- Note: `google-genai` is in `requirements.txt`; `from google.genai import
  types` happens lazily inside `_generate_gemini`. If the package is missing
  in the venv, mock strategy below avoids importing it for the dispatch tests
  but `test_gemini_generation_saves_results` needs the real types import —
  check `venv/bin/python -c "from google.genai import types"` first; if it
  fails, `venv/bin/pip install google-genai` (it's a declared dependency).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |
| Dep check | `venv/bin/python -c "from google.genai import types"` | exit 0 |

## Scope

**In scope**:
- `tests/test_app.py` (new tests only)

**Out of scope**:
- ANY change to `app.py`. If a test reveals a production bug not already
  covered by plans 007–014, STOP and report it — do not fix inline.
- Live API calls (everything mocked).

## Git workflow

- Commit style: `test: cover Gemini provider path, model validation, job lifecycle`

## Steps

### Step 1: Model validation tests

Add:

- `test_generate_rejects_unknown_model(client, monkeypatch)`:
  `monkeypatch.setenv("OPENAI_API_KEY", "sk-test")`; POST `/generate` with
  `{"prompt": "x", "model": "nope"}` → 400, body error contains
  `Unknown model`.
- `test_generate_coerces_invalid_quality(client, monkeypatch)`: patch
  `app.threading.Thread` with a MagicMock (so no real generation runs);
  POST `/generate` with `{"prompt": "x", "model":
  "gemini-3.1-flash-lite-image", "quality": "high", "size": "1024x1024"}` →
  200 with a `job_id`; assert the Thread was constructed with
  `args` containing quality `"standard"` (inspect
  `mock_thread.call_args.kwargs["args"]`).

**Verify**: `venv/bin/python -m pytest tests/ -q -k model` → pass.

### Step 2: Gemini generation path test

`test_gemini_generation_saves_results(client)` (no HTTP needed — call the
worker directly, like plan 009's cost test):

1. Build a fake response:

```python
part = MagicMock()
part.inline_data.mime_type = "image/png"
part.inline_data.data = b"\x89PNG fake"
cand = MagicMock(); cand.content.parts = [part]
fake_resp = MagicMock(candidates=[cand])
fake_client = MagicMock()
fake_client.models.generate_content.return_value = fake_resp
```

2. `with patch("app.get_google_client", return_value=fake_client):`
   seed `app_module._jobs["gj"] = {"status": "running", "created_at": 0}` and
   call `app_module._run_generation("gj", "a cat", "", "standard",
   "1792x1024", 2, "gemini-3.1-flash-lite-image")`.
3. Assert: job status `done`; `len(results) == 2`; each result's `url` starts
   with `data:image/png;base64,`; each `cost_usd ==
   app_module.calc_cost("gemini-3.1-flash-lite-image", "standard",
   "1792x1024", 1)`; DB has 2 rows with `model =
   'gemini-3.1-flash-lite-image'` and `success = 1`.
4. Assert the SDK was called with `model="gemini-3.1-flash-lite-image"` and
   the config's aspect ratio for 1792x1024 (`"16:9"`) — inspect
   `fake_client.models.generate_content.call_args.kwargs["config"]`; if
   introspecting the genai config object is brittle, assert call count == 2
   and skip the aspect-ratio assertion with a comment.

**Verify**: `venv/bin/python -m pytest tests/ -q -k gemini` → pass.

### Step 3: Failure path + job lifecycle tests

- `test_gemini_without_key_fails_job(client)`: with
  `patch("app.get_google_client", return_value=None)`, seed a job, run
  `_run_generation(...)` for the Gemini model → job status `failed`, error
  mentions `GOOGLE_API_KEY`; DB has one row with `success = 0`.
- `test_job_status_unknown(client)`: `GET /job-status/doesnotexist` → 404.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass.

### Step 4: /api/models contract test — only if absent

`grep -n "api_models\|api/models" tests/test_app.py` — if plan 012 already
added `test_api_models_shape`, skip this step. Otherwise add it as specified
in plan 012 Step 1 (same file, `plans/012-model-config-single-source.md`).

## Test plan

This plan IS the test plan. Final state: ≥ 4 new tests, all mocked, suite
runtime still a few seconds.

## Done criteria

- [ ] `venv/bin/python -m pytest tests/ -q` exits 0
- [ ] `venv/bin/python -m pytest tests/ -q --collect-only | grep -c test_` shows ≥ 4 more tests than before this plan
- [ ] `git diff --stat` touches only `tests/test_app.py`
- [ ] No network access during tests (mocks only — no `sk-test` calls leave the process)
- [ ] `plans/README.md` status row updated

## STOP conditions

- A test exposes a production bug beyond those already planned (007–014):
  report it; do not patch `app.py`.
- `google-genai` cannot be installed in the venv (network-restricted
  environment): implement Steps 1 and 3 (which don't need it) and report
  Step 2 as blocked.
- `_generate_gemini` / `_run_generation` signatures differ from the excerpts.

## Maintenance notes

- When a third provider is added (fal.ai is a likely candidate — see the
  audit's direction notes), clone `test_gemini_generation_saves_results` as
  the template for its worker test.
- Reviewer: check mocks assert on call *arguments*, not just call counts —
  argument assertions are what catch contract drift.
