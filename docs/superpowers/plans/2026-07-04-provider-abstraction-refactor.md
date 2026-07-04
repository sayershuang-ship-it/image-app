# Provider Abstraction Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OpenAI image-generation path in `app.py` a named function symmetric with `_generate_gemini`, dispatched through a small registry instead of an if/else, so a future provider (e.g. fal.ai/Flux) can be added by writing one function + one registry entry — no changes to dispatch logic.

**Architecture:** `_run_generation` stays the single orchestration entry point (called by `/generate`, `/batch-generate`, `/api/v1/generate`). Internally, provider-specific generation logic moves into two symmetric functions (`_generate_openai`, `_generate_gemini`) that take the same argument shape and return a `list[dict]` of results instead of writing directly to the shared `_jobs` dict. Two small shared helpers (`_finalize_job_success`, `_finalize_job_failure`) do the `_jobs` bookkeeping that both providers currently duplicate. Dispatch happens via a module-level `_PROVIDERS` dict keyed by the `provider` string already present in `_MODELS`.

**Tech Stack:** Python 3, Flask, pytest. Single file (`app.py`) — no new files, no new dependencies.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-04-provider-abstraction-refactor-design.md` — read it before starting if anything below is unclear.
- No new provider is added in this plan (no fal.ai integration) — out of scope per spec.
- No new tests are added. The existing 22 tests in `tests/test_app.py` are the regression net; every task must end with `venv/bin/python -m pytest tests/ -q` reporting `22 passed` (same count, same file, zero test-file edits).
- No class-based provider interface / registry framework — providers stay plain functions in a dict, matching the rest of `app.py`'s style (no classes today).
- External behavior must not change: `/generate`, `/batch-generate`, `/api/v1/generate`, and `/job-status/<job_id>` must behave identically before and after (same response shapes, same `_jobs` entries, same error behavior).
- Every task operates on `/Users/shouwan/projects/image-app/app.py` only.

---

### Task 1: Extract `_finalize_job_success` / `_finalize_job_failure`, update `_generate_gemini` to return results

**Files:**
- Modify: `app.py:448-579` (the `_generate_gemini` function and the `_run_generation` function, currently at these line numbers — verify with `grep -n "_generate_gemini\|_run_generation" app.py` first since line numbers may have shifted since this plan was written)

**Interfaces:**
- Consumes: nothing new — same globals already in scope (`_jobs`, `_jobs_lock`, `save_prompt`, `calc_cost`, `make_thumbnail`, `get_google_client`, `_GEMINI_ASPECT_MAP`).
- Produces:
  - `_generate_gemini(prompt: str, image_b64: str, quality: str, size: str, n: int, model: str, original_prompt: str = "") -> list[dict]` — note the signature drops `job_id` and adds `quality` (accepted, unused) compared to today.
  - `_finalize_job_success(job_id: str, results: list) -> None`
  - `_finalize_job_failure(job_id: str, prompt: str, image_b64: str, quality: str, size: str, model: str, exc: Exception) -> None`
  - These three names are consumed by Task 2.

- [ ] **Step 1: Replace `_generate_gemini` and `_run_generation` with the version below**

Find the current `_generate_gemini` function (starts with `def _generate_gemini(job_id: str, prompt: str, image_b64: str,`) through the end of `_run_generation` (ends with the `except Exception as exc:` block that sets `_jobs[job_id]["status"] = "failed"`). Replace that entire span with:

```python
def _generate_gemini(prompt: str, image_b64: str, quality: str,
                     size: str, n: int, model: str,
                     original_prompt: str = "") -> list:
    """Run Gemini image generation and return a list of result dicts."""
    from google.genai import types as genai_types

    client = get_google_client()
    if not client:
        raise Exception("GOOGLE_API_KEY not configured. Set GOOGLE_API_KEY in ~/.image-studio.env")

    aspect_ratio = _GEMINI_ASPECT_MAP.get(size, "1:1")

    if image_b64:
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        contents = [
            genai_types.Part.from_bytes(data=buf.read(), mime_type="image/png"),
            prompt,
        ]
    else:
        contents = [prompt]

    config = genai_types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        image_config=genai_types.ImageConfig(aspect_ratio=aspect_ratio),
    )

    unit_cost = calc_cost(model, "standard", size, 1)
    results = []
    for i in range(n):
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        for candidate in response.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                if part.inline_data and part.inline_data.mime_type and \
                   part.inline_data.mime_type.startswith("image/"):
                    b64 = base64.b64encode(part.inline_data.data).decode()
                    data_url = f"data:{part.inline_data.mime_type};base64,{b64}"
                    pid = save_prompt(prompt, make_thumbnail(image_b64) if image_b64 else None,
                                      None, "standard", size, model,
                                      None, b64, True,
                                      original_prompt=original_prompt or None, cost_usd=unit_cost)
                    results.append({"url": data_url, "revised_prompt": None,
                                     "cost_usd": unit_cost, "pid": pid})

    return results


def _finalize_job_success(job_id: str, results: list) -> None:
    with _jobs_lock:
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["results"] = results


def _finalize_job_failure(job_id: str, prompt: str, image_b64: str, quality: str,
                          size: str, model: str, exc: Exception) -> None:
    save_prompt(prompt, image_b64, None, quality, size,
                model, None, None, False, str(exc))
    with _jobs_lock:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(exc)


def _run_generation(job_id: str, prompt: str, image_b64: str,
                    quality: str, size: str, n: int, model: str,
                    original_prompt: str = "") -> None:
    """Run image generation in a background thread and store result in _jobs."""
    model_config = _MODELS.get(model, _MODELS["gpt-image-2"])
    provider = model_config["provider"]

    try:
        if provider == "google":
            results = _generate_gemini(prompt, image_b64, quality, size, n, model,
                                       original_prompt=original_prompt)
            _finalize_job_success(job_id, results)
            return

        # ── OpenAI path ──────────────────────────────────────────────────────────
        client = get_client()
        kwargs = dict(model=model, prompt=prompt, n=n,
                      quality=quality, size=size)

        if image_b64:
            img_bytes = base64.b64decode(image_b64)
            img = Image.open(io.BytesIO(img_bytes))
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            w, h = img.size
            if w > 2048 or h > 2048:
                ratio = min(2048 / w, 2048 / h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            buf.name = 'reference.png'
            kwargs["image"] = buf
            edit_sizes = {'256x256', '512x512', '1024x1024', '1536x1024', '1024x1536', 'auto'}
            if size not in edit_sizes:
                kwargs["size"] = 'auto'
            response = client.images.edit(**kwargs)
        else:
            response = client.images.generate(**kwargs)

        unit_cost = calc_cost(model, quality, size, 1)
        results = []
        for item in response.data:
            if item.url:
                try:
                    import urllib.request
                    with urllib.request.urlopen(item.url) as resp:
                        raw_b64 = base64.b64encode(resp.read()).decode()
                except Exception:
                    raw_b64 = None
                data_url = f"data:image/png;base64,{raw_b64}" if raw_b64 else item.url
                pid = save_prompt(prompt, image_b64, item.revised_prompt, quality, size,
                                  model, item.url, raw_b64, True,
                                  original_prompt=original_prompt or None, cost_usd=unit_cost)
                results.append({"url": data_url, "revised_prompt": item.revised_prompt,
                                 "cost_usd": unit_cost, "pid": pid})
            elif item.b64_json:
                data_url = f"data:image/png;base64,{item.b64_json}"
                pid = save_prompt(prompt, make_thumbnail(image_b64) if image_b64 else None,
                                  item.revised_prompt, quality, size,
                                  model, None, item.b64_json, True,
                                  original_prompt=original_prompt or None, cost_usd=unit_cost)
                results.append({"url": data_url, "revised_prompt": item.revised_prompt,
                                 "cost_usd": unit_cost, "pid": pid})

        _finalize_job_success(job_id, results)

    except Exception as exc:
        _finalize_job_failure(job_id, prompt, image_b64, quality, size, model, exc)
```

Note: this is a behavior-preserving intermediate step. The OpenAI path is still inline inside `_run_generation` — it gets extracted in Task 2. Do not skip ahead.

- [ ] **Step 2: Run the full test suite to verify no regression**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: `22 passed` (same count as before this change — if any test fails or the count differs, stop and investigate before proceeding; do not edit test files to make it pass).

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "refactor: extract shared job-finalize helpers; _generate_gemini returns results"
```

---

### Task 2: Extract `_generate_openai`, dispatch via `_PROVIDERS` registry

**Files:**
- Modify: `app.py` — the `_run_generation` function produced by Task 1 (locate with `grep -n "def _run_generation" app.py`, since Task 1 already changed line numbers from the original file).

**Interfaces:**
- Consumes: `_finalize_job_success`, `_finalize_job_failure`, `_generate_gemini` (all from Task 1) — signatures unchanged from Task 1.
- Produces:
  - `_generate_openai(prompt: str, image_b64: str, quality: str, size: str, n: int, model: str, original_prompt: str = "") -> list[dict]`
  - `_PROVIDERS: dict[str, Callable]` — `{"openai": _generate_openai, "google": _generate_gemini}`. This is the registry a future provider (e.g. fal.ai) would add an entry to.

- [ ] **Step 1: Replace the OpenAI inline branch and `_run_generation` with the version below**

Find the `_run_generation` function produced by Task 1 (starts with `def _run_generation(job_id: str, prompt: str, image_b64: str,`, ends with the `except Exception as exc:` block). Replace it with the following three definitions, inserted immediately after `_finalize_job_failure` and before the old `_run_generation`'s location:

```python
def _generate_openai(prompt: str, image_b64: str, quality: str,
                     size: str, n: int, model: str,
                     original_prompt: str = "") -> list:
    """Run OpenAI image generation and return a list of result dicts."""
    client = get_client()
    kwargs = dict(model=model, prompt=prompt, n=n,
                  quality=quality, size=size)

    if image_b64:
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        w, h = img.size
        if w > 2048 or h > 2048:
            ratio = min(2048 / w, 2048 / h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        buf.name = 'reference.png'
        kwargs["image"] = buf
        edit_sizes = {'256x256', '512x512', '1024x1024', '1536x1024', '1024x1536', 'auto'}
        if size not in edit_sizes:
            kwargs["size"] = 'auto'
        response = client.images.edit(**kwargs)
    else:
        response = client.images.generate(**kwargs)

    unit_cost = calc_cost(model, quality, size, 1)
    results = []
    for item in response.data:
        if item.url:
            try:
                import urllib.request
                with urllib.request.urlopen(item.url) as resp:
                    raw_b64 = base64.b64encode(resp.read()).decode()
            except Exception:
                raw_b64 = None
            data_url = f"data:image/png;base64,{raw_b64}" if raw_b64 else item.url
            pid = save_prompt(prompt, image_b64, item.revised_prompt, quality, size,
                              model, item.url, raw_b64, True,
                              original_prompt=original_prompt or None, cost_usd=unit_cost)
            results.append({"url": data_url, "revised_prompt": item.revised_prompt,
                             "cost_usd": unit_cost, "pid": pid})
        elif item.b64_json:
            data_url = f"data:image/png;base64,{item.b64_json}"
            pid = save_prompt(prompt, make_thumbnail(image_b64) if image_b64 else None,
                              item.revised_prompt, quality, size,
                              model, None, item.b64_json, True,
                              original_prompt=original_prompt or None, cost_usd=unit_cost)
            results.append({"url": data_url, "revised_prompt": item.revised_prompt,
                             "cost_usd": unit_cost, "pid": pid})

    return results


_PROVIDERS = {
    "openai": _generate_openai,
    "google": _generate_gemini,
}


def _run_generation(job_id: str, prompt: str, image_b64: str,
                    quality: str, size: str, n: int, model: str,
                    original_prompt: str = "") -> None:
    """Run image generation in a background thread and store result in _jobs."""
    model_config = _MODELS.get(model, _MODELS["gpt-image-2"])
    provider = model_config["provider"]
    generate_fn = _PROVIDERS.get(provider)

    try:
        if generate_fn is None:
            raise Exception(f"No provider registered for: {provider}")
        results = generate_fn(prompt, image_b64, quality, size, n, model,
                             original_prompt=original_prompt)
        _finalize_job_success(job_id, results)
    except Exception as exc:
        _finalize_job_failure(job_id, prompt, image_b64, quality, size, model, exc)
```

Double check after pasting: `_generate_gemini` (Task 1) and `_generate_openai` must both already be defined above this point in the file (they are — `_generate_gemini` is defined earlier in the file, `_generate_openai` is defined immediately above `_PROVIDERS` in this same edit), since `_PROVIDERS` references both by name at module load time.

- [ ] **Step 2: Run the full test suite to verify no regression**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: `22 passed` (identical to Task 1's result — if the count or outcome differs, stop and investigate; do not edit test files).

- [ ] **Step 3: Manually sanity-check both providers still work end to end**

Run: `./start.sh` (or `OPENAI_API_KEY=sk-... GOOGLE_API_KEY=... venv/bin/python app.py`), then in another terminal:

```bash
curl -s -X POST http://localhost:5001/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a red apple on a white table","model":"gpt-image-2","quality":"low","size":"1024x1024","n":1}'
```

Expected: JSON with a `job_id`. Poll `curl -s http://localhost:5001/job-status/<job_id>` until `status` is `done`, confirm `results` has one entry with a `url` and `cost_usd`. Repeat with `"model":"gemini-3.1-flash-lite-image","quality":"standard"`. Stop the server after (`Ctrl-C` or `kill` the process per `plans/HANDOVER.md`'s operator notes).

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "refactor: extract _generate_openai; dispatch providers via _PROVIDERS registry"
```

---

## Self-Review Notes (for whoever executes this plan)

- Task 1 and Task 2 together implement the entire spec (`_generate_gemini`/`_generate_openai` symmetry, `_finalize_job_success`/`_finalize_job_failure` extraction, `_PROVIDERS` dispatch) — no spec section is left uncovered.
- No task adds a new test file or new test function, per the spec's explicit non-goal — the two "run pytest" steps are verification, not new coverage.
- Signatures are identical between `_generate_openai` and `_generate_gemini` (`prompt, image_b64, quality, size, n, model, original_prompt=""`) — this is what makes `_PROVIDERS` dispatch work without per-provider special-casing in `_run_generation`.
- If Task 1's test run fails, do not proceed to Task 2 — an intermediate-state failure means the Task 1 edit was pasted incorrectly (most likely a missed line or indentation mismatch), not a design problem.
