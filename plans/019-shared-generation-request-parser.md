# Plan 019: One shared parser/validator for all generation request bodies

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat bb87099..HEAD -- app.py tests/test_app.py`
> Plan 018's changes are expected. Compare any other differences against the
> excerpts below; on a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/018-per-provider-key-check.md
- **Category**: tech-debt (+ two small bugs)
- **Planned at**: commit `bb87099`, 2026-09-26

## Why this matters

`/generate`, `/api/v1/generate`, and `/batch-generate` each parse and validate
model, quality, size, n, negative prompt, and variables with copy-pasted code,
and the copies have already drifted:
(a) `/api/v1/generate` does **not** run `_resolve_vars` on the negative
prompt, but `/generate` does;
(b) `int(data.get("n", 1))` raises `ValueError` on a body like `"n": "abc"`,
which becomes a 500 instead of a 400;
(c) `/generate` calls `request.get_json()` without `or {}`.
Plan 023 is about to add a fourth entry point, so the logic should live in one
place first.

## Current state

- `/generate`, `app.py:813-860`. The parsing block:
  ```python
  data             = request.get_json()
  original_prompt  = (data.get("prompt") or "").strip()
  prompt           = original_prompt
  image_b64        = data.get("image_b64") or ""
  model            = data.get("model", "gpt-image-2")
  quality          = data.get("quality", "medium")
  size             = data.get("size", "1024x1024")
  n                = max(min(int(data.get("n", 1)), 4), 1)
  negative         = (data.get("negative_prompt") or "").strip()
  variables        = data.get("variables") or {}
  if model not in _MODELS: return jsonify(error=f"Unknown model: {model}"), 400
  model_config = _MODELS[model]
  if quality not in model_config["qualities"]: quality = model_config["qualities"][0]
  size_ok, size_err = validate_size_for_model(model_config, size)
  if not size_ok: return jsonify(error=size_err), 400
  if not prompt: return jsonify(error="Prompt is required"), 400
  if image_b64 and len(image_b64) > 15 * 1024 * 1024: return ..., 413
  prompt = _resolve_vars(prompt, variables)
  if negative:
      prompt = f"{prompt}\n\nNegative Prompt:\n{_resolve_vars(negative, variables)}"
  ```
- `/api/v1/generate`, `app.py:1035-1075`. It is the same, except that the prompt
  check happens first (error text `"prompt is required"`), there is no 413
  check, and the negative prompt is not var-resolved.
- `/batch-generate`, `app.py:957-994`. It validates model, quality, and size the
  same way, then processes a `prompts` list (max 20); there is no n,
  image, or negative prompt.
- After plan 018, each route also calls `_missing_key_for_model(model)` right
  after the unknown-model check.
- `max_n` is stored per model in `_MODELS` (currently 4 for all models); the
  routes hard-code 4.

## Scope

**In scope**: `app.py`, `tests/test_app.py`.
**Out of scope**: response shapes (`job_id`, `estimated_cost`, `poll_url`,
the 202 status on v1), `_run_generation`, and all templates. Error **status codes**
must stay the same for every input that already worked. The only intended
changes are the 400 for a non-integer `n`, the var-resolved negative prompt on
v1, and the 413 check now also applying to v1.

## Steps

### Step 1: Add the parser
Above `@app.route("/generate")`, add:
```python
class _BadRequest(Exception):
    def __init__(self, message, status=400):
        super().__init__(message); self.message = message; self.status = status

def _parse_model_settings(data: dict) -> tuple:
    """Validate model/quality/size (+ provider key). Returns (model, quality, size)."""
    model = data.get("model", "gpt-image-2")
    if model not in _MODELS:
        raise _BadRequest(f"Unknown model: {model}")
    missing = _missing_key_for_model(model)
    if missing:
        raise _BadRequest(f"{missing} not set", 500)
    cfg = _MODELS[model]
    quality = data.get("quality", "medium")
    if quality not in cfg["qualities"]:
        quality = cfg["qualities"][0]
    size = data.get("size", "1024x1024")
    ok, err = validate_size_for_model(cfg, size)
    if not ok:
        raise _BadRequest(err)
    return model, quality, size

def _parse_generation_request(data: dict, prompt_error="Prompt is required") -> dict:
    model, quality, size = _parse_model_settings(data)
    original_prompt = (data.get("prompt") or "").strip()
    if not original_prompt:
        raise _BadRequest(prompt_error)
    try:
        n = int(data.get("n", 1))
    except (TypeError, ValueError):
        raise _BadRequest("n must be an integer")
    n = max(min(n, _MODELS[model]["max_n"]), 1)
    image_b64 = data.get("image_b64") or ""
    if image_b64 and len(image_b64) > 15 * 1024 * 1024:
        raise _BadRequest("Image too large (max ~10MB raw)", 413)
    variables = data.get("variables") or {}
    prompt = _resolve_vars(original_prompt, variables)
    negative = (data.get("negative_prompt") or "").strip()
    if negative:
        prompt = f"{prompt}\n\nNegative Prompt:\n{_resolve_vars(negative, variables)}"
    return dict(original_prompt=original_prompt, prompt=prompt, image_b64=image_b64,
                model=model, quality=quality, size=size, n=n)

@app.errorhandler(_BadRequest)
def _handle_bad_request(e):
    return jsonify(error=e.message), e.status
```
Note on ordering: in the current `/generate` code, an unknown model is reported
before a missing prompt. The parser keeps that order.

**Verify**: `venv/bin/python -m pytest tests/ -q` → still all pass (nothing calls it yet).

### Step 2: Switch `/generate`
Replace the parsing block with `req = _parse_generation_request(request.get_json(silent=True) or {})`,
then use `req["..."]` in the rest of the function. Keep the job-creation code as it is.

**Verify**: tests pass.

### Step 3: Switch `/api/v1/generate`
Use `_parse_generation_request(request.get_json(silent=True) or {}, prompt_error="prompt is required")`.
Keep the `poll_url` and the 202 status.

**Verify**: tests pass.

### Step 4: Switch `/batch-generate`
Replace only its model/quality/size block with
`model, quality, size = _parse_model_settings(data)`. Leave the prompts-list handling unchanged.

**Verify**: tests pass; `grep -n 'data.get("model", "gpt-image-2")' app.py` → exactly 1 match (inside `_parse_model_settings`).

### Step 5: Tests
Add:
- `test_generate_non_integer_n_returns_400` (`"n": "abc"` → 400).
- `test_v1_negative_prompt_resolves_variables`: patch `app.threading.Thread`,
  then POST to v1 with `negative_prompt: 'no {argument name="x" default="d"}'` and
  `variables: {"x": "blur"}`. Assert that `mock_thread.call_args.kwargs["args"][1]` ends with `no blur`.
- `test_v1_image_too_large_413`.
- `test_generate_empty_body_returns_400` (POST with `data="x"`, `content_type="text/plain"` → 400, not 500).

Also make two existing tests hermetic by adding `monkeypatch` to their
signatures. `test_generate_requires_prompt` needs
`monkeypatch.setenv("OPENAI_API_KEY", "sk-test")`.
`test_generate_coerces_invalid_quality` needs
`monkeypatch.setenv("GOOGLE_API_KEY", "g-test")` too, because it posts a Gemini
model and plan 018 made Gemini require the Google key. Both tests currently
fail when the suite runs with no `~/.image-studio.env`. Check with:
`env -u OPENAI_API_KEY -u GOOGLE_API_KEY HOME="$(mktemp -d)" venv/bin/python -m pytest tests/ -q` → all pass.

In every new test, call `monkeypatch.setenv("OPENAI_API_KEY", "sk-test")`. The
existing `test_generate_requires_prompt` passes `environ_base=...`, which does
**not** set `os.environ`. It only passes today because `app.py` loads the
operator's real `~/.image-studio.env` at import time. Do not copy that pattern.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass, including the 4 new tests.

## Done criteria
- [ ] Tests exit 0 with ≥ 4 new tests
- [ ] `grep -c "max(min(int(data.get" app.py` → `0`
- [ ] Only `app.py`, `tests/test_app.py`, and `plans/README.md` are modified

## STOP conditions
- An existing test asserts on the exact old ordering of errors or on error text
  that the parser changes, in a way that cannot be preserved.
- Plan 018 has not landed (`grep -n _missing_key_for_model app.py` returns nothing).
  Do 018 first.

## Maintenance notes
- Any new generation entry point (plan 023's `/compare-generate`) must call
  `_parse_generation_request` / `_parse_model_settings` rather than parsing the body itself.
- `_BadRequest` is app-wide, so other routes can reuse it.
