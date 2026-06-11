# Plan 004: Give the four app pages a shared Jinja base template and unified nav

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat 149315b..HEAD -- templates/ app.py`
> `templates/history.html` already has uncommitted modifications at planning
> time — read the live file, not git HEAD. For other in-scope files, on a
> mismatch with the "Current state" excerpts, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (visual regressions possible; mitigated by conservative extraction)
- **Depends on**: 001 (test suite as gate)
- **Category**: tech-debt
- **Planned at**: commit `149315b`, 2026-06-11

## Why this matters

All six templates are fully standalone HTML files with their own inline `<style>` and `<script>` blocks — no `base.html`, no `static/` directory, ~5,500 lines total. Four of them (`studio.html`, `gallery.html`, `history.html`, `templates.html`) are the same "app shell": same dark theme via CSS custom properties, same top nav using `.nav-btn` links, same toast pattern, and duplicated fetch helpers (e.g. history loading and `/export-zip` logic exist in both gallery.html and history.html). Every nav change today means editing four files; they have already drifted (studio's nav lacks the Templates link; gallery/history lack it too — only templates.html links to all four). This plan extracts a shared base template + static assets for those four pages so cross-page changes become single-file edits.

`index.html` (light-theme marketing landing) and `spark.html` (standalone experimental UI, currently linked from nowhere) are intentionally **left standalone** — see Scope.

## Current state

- `templates/` contains exactly: `gallery.html` (668 lines), `history.html` (818), `index.html` (580), `spark.html` (1388), `studio.html` (1230), `templates.html` (767). (If `fpd_styles.json` is still there, plan 002 hasn't run — that's fine, ignore it.)
- There is **no** `static/` directory and `app.py` never calls `url_for('static', ...)` — Flask's default static serving works once the directory exists; no app.py change needed.
- Nav blocks today (live line numbers may shift; locate by searching `nav-breadcrumb` / `nav-btn`):
  - `gallery.html:376-381` — breadcrumb "/ Gallery"; links: Gallery (active), History, Studio. **No Templates link.**
  - `history.html:436-441` — breadcrumb "/ History"; links: Gallery, History (active), Studio. **No Templates link.**
  - `templates.html:314-320` — breadcrumb "/ Templates"; links: Gallery, History, Studio, Templates (active).
  - `studio.html:591-593` — has a `Key` button (`id="api-key-btn"`) plus Gallery, History links. **Keep the Key button studio-only.**
- Each of the four files opens with `<html lang="en" data-theme="dark">` (studio) or similar, and a `:root { --bg: oklch(...); ... }` variable block. The variable palettes are *similar but not identical* across the four files — do NOT assume they are the same; diff them (Step 1).
- Routes rendering these templates: `app.py:634-653` (`/studio`, `/gallery`, `/history-view`, `/templates`). `/studio` passes `api_key_set` and `set_key_secret` template variables (app.py:636-638) — the base template must not break that.
- Repo conventions: vanilla JS, no build step, no framework. Keep it that way.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Tests | `venv/bin/python -m pytest tests/ -q` | all pass (incl. the 4 pages → 200) |
| Run app | `OPENAI_API_KEY=sk-test venv/bin/python app.py` | serves on http://localhost:5001 |
| Template syntax | covered by the page-render tests | 200, no Jinja errors |

## Suggested executor toolkit

- If a browser-preview tool is available (e.g. Claude Preview MCP / `verify` skill), screenshot each of the four pages before and after each extraction step and compare visually. If not available, rely on the render tests plus manual operator review — say so in your report.

## Scope

**In scope**:
- `templates/base_app.html` (create)
- `templates/studio.html`, `templates/gallery.html`, `templates/history.html`, `templates/templates.html`
- `static/app.css` (create), `static/app.js` (create, only if Step 4 applies)
- `tests/test_app.py` — extend render tests if needed

**Out of scope** (do NOT touch):
- `templates/index.html` — separate light-theme landing design; standalone by design.
- `templates/spark.html` — experimental orphaned UI; its fate (link it, merge it into studio, or delete) is an **operator decision** recorded in plans/README.md. Do not refactor or delete it.
- `app.py` — no route changes needed; static serving is automatic.
- Page-specific CSS/JS — this plan extracts only what is shared; do not "clean up" page-internal styles.

## Git workflow

- Commit per step (one page migration per commit), style: `refactor: extract shared app shell (studio)` etc.
- Do NOT push.

## Steps

### Step 1: Diff the shared surface

For the four files, extract each `:root {...}` block and each nav block into scratch files and diff them. Produce (in your working notes, not committed): the list of CSS custom properties that are identical across all four, those that differ, and the canonical nav markup. **Canonical nav** = templates.html's version (it is the most complete: Gallery, History, Studio, Templates), plus a Jinja block for page-specific extras (studio's Key button).

**Verify**: you can state for each of the 4 files which custom properties differ from studio.html's palette. If more than ~30% of the variable values differ across files, STOP — the "shared theme" assumption is wrong and the base should carry structure only, not the palette.

### Step 2: Create `static/app.css` and `templates/base_app.html`

`static/app.css`: the shared `:root` variables (use **studio.html's values as canonical** where they differ — studio is the most-developed page), the universal reset (`* { box-sizing... }`), body basics, and the `.nav`/`.nav-btn`/`.nav-breadcrumb` styles (copy from templates.html's nav styling).

`templates/base_app.html` skeleton:

```html
<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{% block title %}Image Studio{% endblock %}</title>
<link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}" />
<style>{% block page_css %}{% endblock %}</style>
</head>
<body>
<nav class="nav">
  <div class="nav-inner">
    <a href="/" class="nav-wordmark">Image Studio</a>
    <span class="nav-breadcrumb">{% block breadcrumb %}{% endblock %}</span>
    <div class="nav-actions">
      {% block nav_extra %}{% endblock %}
      <a href="/gallery"      class="nav-btn {% block nav_gallery %}{% endblock %}">Gallery</a>
      <a href="/history-view" class="nav-btn {% block nav_history %}{% endblock %}">History</a>
      <a href="/studio"       class="nav-btn {% block nav_studio %}{% endblock %}">Studio</a>
      <a href="/templates"    class="nav-btn {% block nav_templates %}{% endblock %}">Templates</a>
    </div>
  </div>
</nav>
{% block content %}{% endblock %}
{% block page_js %}{% endblock %}
</body>
</html>
```

Adapt class names/markup to what the four files actually use (Step 1 findings win over this sketch — match the existing rendered DOM as closely as possible so page CSS keeps working).

**Verify**: `venv/bin/python -m pytest tests/ -q` still passes (nothing uses the base yet).

### Step 3: Migrate one page at a time, templates.html first

Per page (order: `templates.html` → `gallery.html` → `history.html` → `studio.html`, easiest to hardest):

1. Replace the file's doctype/head/nav with `{% extends "base_app.html" %}` + blocks; move the page's entire remaining `<style>` content into `{% block page_css %}`, body content into `{% block content %}`, scripts into `{% block page_js %}`.
2. Delete from the page's CSS only the rules now provided by `app.css` (the `:root` vars, reset, nav rules). If a page's variable value differed from canonical, keep the override inside its `page_css` block — do not silently restyle the page.
3. For studio.html: put the Key button in `{% block nav_extra %}` and keep its `api_key_set`/`set_key_secret` usages intact.

**Verify after EACH page**: `venv/bin/python -m pytest tests/ -q` passes, and `curl -s localhost:5001/<route> | grep -c "nav-btn"` ≥ 4 with the app running (all four nav links present). Commit before starting the next page.

### Step 4 (optional, only if time permits): extract duplicated JS helpers

`gallery.html` and `history.html` both fetch `/history` and POST `/export-zip` with near-identical code. If—and only if—the implementations are actually near-identical (diff them first), move a shared `fetchHistory()` / `exportZip(ids)` into `static/app.js` and include it from `base_app.html`. If they differ meaningfully, skip this step and note it.

**Verify**: both pages still list images and the Export button still downloads a zip (manual check or browser tool).

## Test plan

- Plan 001's render tests already assert 200 for all four routes — they catch Jinja errors.
- Add one test: `test_nav_consistent` — GET each of the four pages, assert each response body contains all four hrefs `/gallery`, `/history-view`, `/studio`, `/templates`.

## Done criteria

- [ ] `templates/base_app.html` and `static/app.css` exist; four pages extend the base
- [ ] All four pages' nav contains all four links (test from Test plan passes)
- [ ] `venv/bin/python -m pytest tests/ -q` → all pass
- [ ] `index.html` and `spark.html` are byte-identical to their pre-plan state (`git diff --stat` shows no changes to them)
- [ ] Total line count of the four migrated templates decreased (`wc -l templates/{studio,gallery,history,templates}.html` < pre-plan 3,483)
- [ ] `plans/README.md` status row updated

## STOP conditions

- Step 1 reveals the four palettes are mostly different (>30% of vars) — report; base should then be structure-only.
- A migrated page visually breaks in a way you cannot attribute to a specific deleted rule — revert that page's commit and report which rule conflicts.
- Any change seems to require editing `app.py` routes — it shouldn't; stop and report.

## Maintenance notes

- New app pages should extend `base_app.html`; the landing (`index.html`) stays separate.
- Open operator decision recorded in the index: **spark.html** — 1,388 lines, reachable only by typing `/spark`, duplicates studio's generate/enhance/set-key flows. Options: (a) delete it, (b) link it from nav as "Spark (beta)", (c) port its best UI ideas into studio and then delete. Until decided, it stays frozen.
- After this lands, theming changes happen in `static/app.css` only.
