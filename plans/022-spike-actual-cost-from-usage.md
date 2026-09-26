# Plan 022 (spike): Record provider-reported token usage; compute actual cost when prices are known

> **Executor instructions**: This is a **spike with a build tail**. Steps 1–3
> are always done. Step 4 happens only if its gate passes. Run every
> verification and honor the STOP conditions. When done, update
> `plans/README.md` and write your findings into the "Spike result" section at
> the bottom of this file.
>
> **Drift check (run first)**: `git diff --stat bb87099..HEAD -- app.py tests/test_app.py`
> Changes from plans 018–021 are expected. Locate `_generate_openai`,
> `_generate_gemini`, and `save_prompt` by name.

## Status

- **Priority**: P2
- **Effort**: S (spike) + S (tail)
- **Risk**: LOW
- **Depends on**: plans/019-shared-generation-request-parser.md (soft — only to avoid editing the same regions at the same time)
- **Category**: direction / correctness
- **Planned at**: commit `bb87099`, 2026-09-26

## Why this matters

All cost figures in the app (the per-image `cost_usd` column, `/api/stats`
total, and the Studio cost badge) come from a hand-written `cost_table` in
`_MODELS`. For `gpt-image-2.5-sunburst` and `gpt-image-2.5-flare`, that table
is openly a guess (`app.py:266-269`: "reuses gpt-image-2's per-size prices as
an ESTIMATE"; xhigh/max reuse the high row). The OpenAI SDK installed here
exposes token usage on image responses. So the app can **record what was
actually consumed** today, and compute real dollars once per-token prices are
entered. This also makes plan 023 (multi-model compare) trustworthy.

## Current state

- `venv/lib/python*/site-packages/openai/types/images_response.py` defines
  `ImagesResponse.usage: Optional[Usage]`, with `input_tokens`,
  `output_tokens`, `total_tokens`, `input_tokens_details.{image_tokens,text_tokens}`,
  and `output_tokens_details` (optional). The docstring says "For gpt-image-1
  only". **Whether gpt-image-2 / 2.5 populate it is unknown. That is what this spike finds out.**
- `_generate_openai` (`app.py:728-786`) receives `response` from
  `client.images.generate/edit(...)` and never reads `response.usage`.
- `_generate_gemini` (`app.py:657-710`) calls `client.models.generate_content`.
  The google-genai response has `usage_metadata` (check `prompt_token_count`,
  `candidates_token_count`, and `total_token_count` in
  `venv/lib/python*/site-packages/google/genai/types.py` — confirm by grep).
- `prompts` table columns: see `init_db` (`app.py:131-205`); column migration
  pattern at lines 154-162.
- `n` images share one response, so the usage is for the whole call. The
  per-image share is usage / number of results. This matches how plan 009
  split `cost_usd` per image.

## Scope
**In scope**: `app.py`, `tests/test_app.py`, and this plan file (the "Spike result" section).
**Out of scope**: `cost_table` values. **Never invent per-token prices.** Any
price must come from the operator or from an official OpenAI/Google pricing
page URL that is recorded in a code comment. Also out of scope: UI changes
beyond what Step 4 lists.

## Steps

### Step 1: Record raw usage (always)
- Add a `usage_json TEXT` column to `prompts` (same migration loop).
- Add a `usage_json=None` parameter to `save_prompt` and write it.
- In `_generate_openai`: `usage = getattr(response, "usage", None)`; if it is present,
  `usage_json = json.dumps(usage.model_dump())`, and store the **same whole-call
  JSON** on each row plus `"n_in_call": len(response.data)`.
- In `_generate_gemini`: do the same with `response.usage_metadata`
  (`model_dump()` if available). Since Gemini is called once per image, there is no split.
- Include `usage` (the dict) in each result dict that is returned to the job, so the UI can show it later.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass.

### Step 2: Tests for recording
- `test_openai_usage_recorded`: patch `app.get_client` so `images.generate`
  returns a `MagicMock` with `data=[item]` (`item.url=None`,
  `item.b64_json=<tiny real PNG b64>`, `item.revised_prompt=None`) and
  `usage.model_dump.return_value={"input_tokens":10,"output_tokens":100,"total_tokens":110}`.
  Assert that the row's `usage_json` parses and has `total_tokens == 110`.
- `test_openai_usage_absent_is_null`: `usage=None` → the column is NULL, and nothing crashes.
Use the existing `test_gemini_generation_saves_results` (around line 289) as the mocking pattern.

**Verify**: tests pass.

### Step 3: Find out whether the real models return usage (needs operator approval — costs about $0.01–0.05)
Ask the operator for permission first. If they approve, run one real `quality="low"`,
`size="1024x1024"` generation per model (`gpt-image-2`, `gpt-image-2.5-sunburst`,
`gpt-image-2.5-flare`, `gemini-3.1-flash-lite-image`) through the running app,
then run:
`sqlite3 prompts.db "select model, usage_json from prompts order by id desc limit 4"`.
Record the output (token counts only) in "Spike result" below. If the operator
declines, record "not measured" and skip Step 4.

### Step 4 (gate: usage is present for a model AND the operator provides official per-token prices with a source URL)
- Add an optional `"token_prices": {"input_text": ..., "input_image": ..., "output_image": ...}`
  (USD per 1M tokens) to that model's `_MODELS` entry, with the source URL in a comment.
- Add a `actual_cost_usd REAL` column. Compute it from `usage_json` ÷ `n_in_call` when `token_prices` exists.
- `/api/stats` gains `actual_cost` = `SUM(COALESCE(actual_cost_usd, cost_usd, 0))`.
  Keep `total_cost` unchanged for compatibility.
- Add a test for the arithmetic with fixed fake prices that are injected by monkeypatching `_MODELS`.

If the gate fails, stop after Step 3. The spike's value is the recorded data plus the finding.

## Done criteria
- [ ] Tests exit 0 with ≥ 2 new tests
- [ ] `grep -n usage_json app.py` shows the column, `save_prompt`, and both providers
- [ ] The "Spike result" section is filled in
- [ ] No per-token price appears in code without a source URL comment

## STOP conditions
- `response.usage` exists but has a shape that differs from the SDK type (record it and stop).
- Getting the prices would require guessing.

## Maintenance notes
- When the SDK updates, re-check `images_response.py`. The "gpt-image-1 only" docstring may change.
- Once real prices exist, the `cost_table` estimate comment for 2.5 should be replaced.

## Spike result
2026-09-26 — Steps 1–2 done on branch `advisor/022-usage-spike` (e3b4b7d, 93 tests). Steps 3–4 NOT run (awaiting operator approval for paid calls).

- The installed SDK types match the plan. So far this is confirmed only by reading SDK source; no real API calls were made.
  - openai 3.11.0: `ImagesResponse.usage: Optional[Usage]` (`openai/types/images_response.py:78`). Its docstring still says "gpt-image-1 only". `Usage` has `input_tokens`, `input_tokens_details{image_tokens,text_tokens}`, `output_tokens`, `total_tokens` and an optional `output_tokens_details`.
  - google-genai 2.10.0: `GenerateContentResponse.usage_metadata` (`google/genai/types.py:8069`) has `prompt_token_count`, `candidates_token_count`, `total_token_count`, `candidates_tokens_details` and `traffic_type`, among others.
- Usage is stored in `prompts.usage_json`. For OpenAI it is the whole call's usage plus `n_in_call`. Gemini rows have no `n_in_call`, so treat it as 1. Usage capture is best-effort and can never fail a generation (`_usage_dict` and `_usage_json` never raise).
- Still unknown: whether gpt-image-2, 2.5-sunburst, 2.5-flare and gemini-3.1-flash-lite-image actually fill these fields in. After merging, any normal generation records it, so no dedicated paid test is needed. Just check `select model, usage_json from prompts order by id desc limit 4`.
- No prices were obtained, so Step 4 is still gated.
