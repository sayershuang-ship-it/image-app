# Provider abstraction refactor — design

## Purpose

`_run_generation` in `app.py` dispatches image generation to a provider via an if/else branch.
The Gemini path is a named function (`_generate_gemini`); the OpenAI path is inlined directly in
the `else` branch with no corresponding named function. This asymmetry was flagged in
`plans/HANDOVER.md` as a blocker to adding a third provider (fal.ai/Flux is a natural candidate —
the landing page already ships static Flux Schnell showcase images, though there is no fal.ai API
integration today).

This refactor makes the two existing providers symmetric so a future provider can be added by
writing one function with a matching signature and registering it — no changes to dispatch logic.

**Explicitly out of scope:** adding fal.ai/Flux itself. That's a separate spec/plan/implementation
cycle, to be brainstormed on its own once this lands.

## Non-goals

- No new test proving pluggability with a fake third provider — the existing 22 tests (which
  exercise `_run_generation` end-to-end via `/generate`, `/api/v1/generate`, and directly) already
  form the regression net; if they pass unchanged, the refactor preserved behavior.
- No class-based `ImageProvider` interface / registry framework. `app.py` has no classes today;
  introducing one for two (soon three) providers would be over-engineering for the current scale.
- No forced extraction of the per-provider result-assembly logic (see "Architecture" below) — the
  two providers save different shapes to `save_prompt` (OpenAI: original image + `revised_prompt`;
  Gemini: thumbnail + `None`), so that part stays provider-specific.

## Architecture

`_run_generation` keeps its current external role: it's the single entry point called by
`/generate`, `/batch-generate`, and `/api/v1/generate` (verified — no other call site touches
provider logic directly). Internally it dispatches through a small registry instead of if/else:

```python
_PROVIDERS = {
    "openai": _generate_openai,
    "google": _generate_gemini,
}

def _run_generation(job_id, prompt, image_b64, quality, size, n, model, original_prompt=""):
    model_config = _MODELS.get(model, _MODELS["gpt-image-2"])
    provider = model_config["provider"]
    generate_fn = _PROVIDERS.get(provider)
    if generate_fn is None:
        raise Exception(f"No provider registered for: {provider}")

    try:
        results = generate_fn(prompt, image_b64, quality, size, n, model,
                               original_prompt=original_prompt)
        _finalize_job_success(job_id, results)
    except Exception as exc:
        _finalize_job_failure(job_id, prompt, image_b64, quality, size, model, exc)
```

## Components and data flow

**`_generate_openai(prompt, image_b64, quality, size, n, model, original_prompt="") -> list[dict]`**
New function. Body is the current lines ~519–571 of `app.py` (the `else` branch of
`_run_generation`), moved verbatim except the final step no longer writes to `_jobs` — it returns
the `results` list instead. Result dict shape unchanged: `{"url", "revised_prompt", "cost_usd",
"pid"}`.

**`_generate_gemini(prompt, image_b64, quality, size, n, model, original_prompt="") -> list[dict]`**
Existing function, two changes: signature gains an unused `quality` param (for symmetry with
`_generate_openai` — Gemini only has one quality tier, so it's accepted and ignored), and the tail
no longer writes to `_jobs` — it returns `results` instead.

**`_finalize_job_success(job_id, results)`**
```python
def _finalize_job_success(job_id, results):
    with _jobs_lock:
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["results"] = results
```
Extracted because both providers currently do this exact sequence identically — zero-risk
extraction (partial adoption of the "shared finalize helper" approach, scoped to only the parts
that are truly identical).

**`_finalize_job_failure(job_id, prompt, image_b64, quality, size, model, exc)`**
Mirrors the current `except` block content in `_run_generation` (the `save_prompt(..., False,
str(exc))` call plus writing `_jobs[job_id]["status"] = "failed"`), given a name, behavior
unchanged.

**Deliberately not extracted:** the per-result-item loop inside each provider (building the
`results.append(...)` entries and calling `save_prompt`) stays inside each provider function.
OpenAI and Gemini pass different arguments to `save_prompt` (original image vs. thumbnail,
`item.revised_prompt` vs. `None`) — forcing a shared helper here would need a parameter for every
point of difference and wouldn't actually reduce complexity.

## Error handling

No behavioral change. Exceptions raised inside `_generate_openai`/`_generate_gemini` (e.g. missing
API key, API errors) propagate up to `_run_generation`'s `try/except`, exactly as today — only the
except-block body is renamed to `_finalize_job_failure` instead of being inlined.

## Testing

No new tests added. Existing tests call `_run_generation` directly or via `/generate` /
`/api/v1/generate` (verified via `grep -n "_run_generation" tests/test_app.py` — no test touches
`_generate_gemini`/`_generate_openai`/`_PROVIDERS` directly, so the internal restructuring can't
break test wiring). Success criterion: `venv/bin/python -m pytest tests/ -q` still reports
22 passed with zero code changes to the test files.

## Adding the next provider (informational — not part of this refactor)

Once this lands, adding fal.ai/Flux (in its own future spec) becomes: write
`_generate_fal(prompt, image_b64, quality, size, n, model, original_prompt="") -> list[dict]`,
add `"fal": _generate_fal` to `_PROVIDERS`, add a `_MODELS` entry with `"provider": "fal"`. No
changes to `_run_generation`.
