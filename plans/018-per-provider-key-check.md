# Plan 018: Generation routes check the API key of the selected model's provider

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat bb87099..HEAD -- app.py tests/test_app.py`
> If either file changed, compare the "Current state" excerpts against the
> live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `bb87099`, 2026-09-26

## Why this matters

The app supports two providers: OpenAI (`gpt-image-*`) and Google
(`gemini-3.1-flash-lite-image`). All three generation routes are guarded by
`@api_key_required`, which checks only `OPENAI_API_KEY`. A user who has only a
`GOOGLE_API_KEY` configured gets `500 OPENAI_API_KEY not set` when they try to
generate with Gemini, even though the OpenAI key is not needed. After this
plan, each request is checked against the key of the provider that serves the
requested model.

## Current state

- `app.py` — the Flask app (single file).
- Decorator, `app.py:233-239`:
  ```python
  def api_key_required(f):
      @wraps(f)
      def decorated(*args, **kwargs):
          if not os.environ.get("OPENAI_API_KEY"):
              return jsonify(error="OPENAI_API_KEY not set"), 500
          return f(*args, **kwargs)
      return decorated
  ```
- It is used on: `/enhance-prompt` (`app.py:504`, **correctly** — enhance
  always calls OpenAI), `/generate` (`:814`), `/batch-generate` (`:958`),
  `/api/v1/generate` (`:1036`).
- Model → provider mapping: `_MODELS[model]["provider"]` is `"openai"` or
  `"google"` (`app.py:242-313`).
- Google key: a module-level global, `GOOGLE_API_KEY` (`app.py:54-61`), which is
  also mirrored into `os.environ["GOOGLE_API_KEY"]` when it is loaded from
  `~/.image-studio.env`. Read the key with
  `os.environ.get("GOOGLE_API_KEY") or GOOGLE_API_KEY` so that both the tests
  (which use `monkeypatch.setenv`) and the file-loaded case work.
- In each route, the model is read from the JSON body
  (`data.get("model", "gpt-image-2")`) and validated with
  `if model not in _MODELS: return 400`.
- Test convention: `tests/test_app.py` uses the `client` fixture from
  `tests/conftest.py`, uses `monkeypatch.setenv/delenv` for keys, and patches
  `app.threading.Thread` so that no real generation runs (see
  `test_generate_coerces_invalid_quality`, around line 274).

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Tests | `venv/bin/python -m pytest tests/ -q` | all pass (68 before this plan) |

## Scope

**In scope**: `app.py`, `tests/test_app.py`.
**Out of scope**: `/enhance-prompt` keeps `@api_key_required` unchanged. Do not
change any template or the error-message wording of the existing decorator.

## Git workflow

- Branch `advisor/018-per-provider-key-check`. Conventional commits, e.g.
  `fix: gemini generation no longer requires OPENAI_API_KEY`. Do not push.

## Steps

### Step 1: Add the helper
Below `api_key_required` in `app.py`, add:
```python
_PROVIDER_KEY_ENV = {"openai": "OPENAI_API_KEY", "google": "GOOGLE_API_KEY"}

def _missing_key_for_model(model: str):
    """Return the env-var name of the missing key for this model's provider, or None."""
    provider = _MODELS.get(model, {}).get("provider", "openai")
    env = _PROVIDER_KEY_ENV.get(provider, "OPENAI_API_KEY")
    value = os.environ.get(env) or (GOOGLE_API_KEY if env == "GOOGLE_API_KEY" else "")
    return None if value else env
```
`_MODELS` is defined later in the file than the decorator. That is fine,
because the function is only called at request time.

**Verify**: `venv/bin/python -c "import app"` → exit 0.

### Step 2: Swap the guard on the three generation routes
Remove `@api_key_required` from `/generate`, `/batch-generate`, and
`/api/v1/generate`. In each one, immediately **after** the existing
`if model not in _MODELS: return ... 400` check, add:
```python
missing = _missing_key_for_model(model)
if missing:
    return jsonify(error=f"{missing} not set"), 500
```
Keep the 500 status code so that existing clients behave the same way.

**Verify**: `grep -n "@api_key_required" app.py` → exactly one match (on `/enhance-prompt`).

### Step 3: Tests
Add these to `tests/test_app.py`:
1. `test_generate_gemini_without_openai_key` — `monkeypatch.delenv("OPENAI_API_KEY", raising=False)`,
   `monkeypatch.setenv("GOOGLE_API_KEY", "g-test")`, patch `app.threading.Thread`,
   POST `/generate` with `{"prompt":"x","model":"gemini-3.1-flash-lite-image","size":"1024x1024"}` → 200 with `job_id`.
2. `test_generate_openai_without_key_500` — delete `OPENAI_API_KEY`, POST with
   `model: gpt-image-2` → 500, and the error contains `OPENAI_API_KEY`.
3. `test_generate_gemini_without_google_key_500` — delete both keys and also
   `monkeypatch.setattr(app_module, "GOOGLE_API_KEY", "")` → 500, and the error contains `GOOGLE_API_KEY`.
4. The same as test 1, but for `/api/v1/generate` (expect 202) and for
   `/batch-generate` with `{"prompts":["a"], "model": "gemini-3.1-flash-lite-image", "size":"1024x1024"}` (expect 200).

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass, 5 new tests.

## Done criteria
- [ ] `venv/bin/python -m pytest tests/ -q` exits 0 (≥ 73 passed)
- [ ] `grep -c "@api_key_required" app.py` → `1`
- [ ] `git status` shows only `app.py`, `tests/test_app.py` (and `plans/README.md`) modified

## STOP conditions
- An existing test fails because it relied on the old OpenAI-only 500 for a
  Gemini model (report which test; do not edit it to pass).
- `_MODELS` has a provider other than `openai` or `google` (a new provider was added since this plan was written).

## Maintenance notes
- Adding a provider (for example fal.ai) now requires adding it to `_PROVIDER_KEY_ENV`.
- Plan 019 folds this check into the shared request parser. If 019 lands later,
  it moves this code; it does not duplicate it.
