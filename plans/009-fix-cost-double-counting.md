# Plan 009: Record per-image cost, not the whole batch cost, on every row

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- app.py templates/studio.html tests/test_app.py`
> Changes from plans 006–008 are expected. Compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md
- **Category**: bug
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

`calc_cost(model, quality, size, n)` returns the cost of the **whole batch of
n images**, but the generation code stores that batch total in **each**
image's `cost_usd` row. Generating 4 images at $0.042 each writes $0.168 into
4 rows → the DB records $0.672. `/api/stats` (`SUM(cost_usd)`) and the studio
UI (which additionally sums the per-result `cost_usd` values client-side, up
to n× again in the success toast) both over-report spend by up to n²×. The
operator's cost tracking is the point of this feature; it's currently wrong
whenever n > 1.

## Current state

- `app.py:221-224` — `calc_cost`:

```python
def calc_cost(model: str, quality: str, size: str, n: int = 1) -> float:
    model_table = _MODELS.get(model, {}).get("cost_table", {})
    default = 0.042
    return round(model_table.get(quality, {}).get(size, default) * n, 4)
```

- OpenAI path, `app.py:523` — `cost = calc_cost(model, quality, size, n)`;
  that `cost` is then passed as `cost_usd=cost` for **each** item at
  `app.py:534-536` and `app.py:541-544`, and into each result dict
  (`"cost_usd": cost`) at `app.py:537` and `app.py:545`.
- Gemini path, `app.py:456` — `cost = calc_cost(model, "standard", size, n)`;
  same per-row reuse at `app.py:472-477`.
- Estimated cost (correctly the batch total) at `app.py:589`
  (`estimated_cost = calc_cost(model, quality, size, n)`) and `app.py:788`,
  `app.py:722` — these are fine and must keep batch semantics.
- Frontend sum: `templates/studio.html:1023` —
  `const cost = results.reduce((s, x) => s + (x.cost_usd || 0), 0);` — correct
  once each result carries its own per-image cost.
- Studio's own JS cost table also exists (`templates/studio.html:693-696`) —
  display-only, out of scope here (plan 012 consolidates it).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |
| App imports | `venv/bin/python -c "import app"` | exit 0 |

## Scope

**In scope**:
- `app.py` — `_run_generation` (OpenAI path) and `_generate_gemini` only
- `tests/test_app.py` (add tests)

**Out of scope**:
- `calc_cost` signature and the batch-total semantics of `estimated_cost`
  in `/generate`, `/api/v1/generate`, `/batch-generate` responses.
- `templates/studio.html` — its reduce() becomes correct automatically.
- Historical rows already in the DB (no data migration — see maintenance).

## Git workflow

- Commit style: `fix: store per-image cost_usd instead of batch total on each row`

## Steps

### Step 1: Compute a per-image cost in both provider paths

In `app.py`:

- OpenAI path: after `cost = calc_cost(model, quality, size, n)` (line ~523),
  add `unit_cost = calc_cost(model, quality, size, 1)` and use `unit_cost`
  for every `save_prompt(..., cost_usd=...)` call and every
  `results.append({... "cost_usd": ...})` in that loop (4 places).
- Gemini path: same substitution around lines 456–477 (2 places:
  `save_prompt` and `results.append`).

Do NOT change the `estimated_cost` calculations in the route handlers.

**Verify**: `venv/bin/python -c "import app"` → exit 0.

### Step 2: Add a regression test

In `tests/test_app.py`, add `test_per_image_cost_recorded(client)`:

1. `from unittest.mock import patch, MagicMock` (already imported at top).
2. Build a fake OpenAI response object: `item = MagicMock(url=None, b64_json="<tiny png b64>", revised_prompt=None)` — reuse the `small_png` base64 constant pattern from `test_fb_post_requires_token_or_pid`; `fake_resp = MagicMock(data=[item, item])` (n=2).
3. `with patch("app.get_client") as gc:` make
   `gc.return_value.images.generate.return_value = fake_resp`.
4. Call `app_module._run_generation("job1", "p", "", "medium", "1024x1024", 2, "gpt-image-2")` directly after seeding `app_module._jobs["job1"] = {"status": "running", "created_at": 0}`.
5. Assert both saved rows have
   `cost_usd == app_module.calc_cost("gpt-image-2", "medium", "1024x1024", 1)`
   (query the test DB: `SELECT cost_usd FROM prompts ORDER BY id`), and that
   `sum(r["cost_usd"] for r in app_module._jobs["job1"]["results"])` equals
   `calc_cost(..., 2)` within 1e-6.

**Verify**: `venv/bin/python -m pytest tests/ -q -k cost` → passes.

## Test plan

- New: `test_per_image_cost_recorded` (above).
- Pattern: mock style of `test_fb_post_requires_token_or_pid`
  (`patch("app.requests.post")` → here `patch("app.get_client")`).
- Full run: `venv/bin/python -m pytest tests/ -q` → all pass.

## Done criteria

- [ ] `venv/bin/python -m pytest tests/ -q` exits 0, including the new test
- [ ] In `app.py`, every `save_prompt(..., cost_usd=...)` inside the two
      generation loops passes a per-image (n=1) cost — verify by reading the
      two functions
- [ ] `estimated_cost` lines (`app.py` routes) unchanged (`git diff` shows no
      edits to the three route handlers)
- [ ] `plans/README.md` status row updated

## STOP conditions

- The cost code has already been restructured (drift vs. excerpts).
- You find consumers that rely on `cost_usd` being the batch total (search:
  `grep -rn cost_usd templates/ app.py` and read each hit before editing —
  today the only consumers are `/api/stats` SUM, `/history` passthrough,
  gallery/history display, and the studio reduce, all of which want per-image).

## Maintenance notes

- Historical rows keep inflated values; `/api/stats` total remains slightly
  overstated for old data. Deliberately not migrated (cannot reconstruct n
  per row reliably). If the operator wants, a one-off heuristic script could
  be a follow-up — out of scope here.
- Reviewer: check the Gemini path too, not just OpenAI (both were wrong).
