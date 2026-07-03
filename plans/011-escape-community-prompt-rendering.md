# Plan 011: Escape third-party community-prompt data in templates.html (stored XSS)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 00bd8e2..HEAD -- templates/templates.html`
> Changes from plan 006 are expected. Compare the "Current state" excerpts
> against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/006-commit-inflight-work-repo-hygiene.md
- **Category**: security
- **Planned at**: commit `00bd8e2`, 2026-07-03

## Why this matters

The 🌐 社群精選 tab renders rows from the `community_prompts` table — data
scraped from third-party sources (the awesome-prompts sync). Titles,
categories, image URLs and source URLs are concatenated into `innerHTML` and
into inline `onclick`/`title`/`href`/`src` attributes **without escaping**. A
crafted title like `"><img src=x onerror=alert(1)>` in the upstream dataset
executes script in the app. The app is localhost-only, which limits blast
radius, but the page also holds the ability to call `/set-key`,
`/save-to-pictures` (writes files), and `/fb-post` — script execution here is
not harmless. The fix is mechanical: escape all interpolations and replace the
string-built `onclick` with dataset-based event delegation.

## Current state

- `templates/templates.html:501-519` — `renderCommunityTemplates()` builds
  HTML by string concatenation. Key unescaped interpolations:

```js
      const imgTag = t.image_url ? '<img src="' + t.image_url + '" ...' : '';
      ...
      return '<div class="' + cls + '" onclick="selectCommunity(' + i + ', &quot;' + cat.replace(/"/g, '&quot;') + '&quot;)" style="..." title="' + t.title + '">' + imgTag + '<span ...>' + title + '</span></div>';
```

  and the category header: `'<span>' + cat + '</span>'`.

- `templates/templates.html:521-543` — `selectCommunity(idx, cat)` looks up
  the item, then for image-only items injects `item.image_url` and
  `item.source_url` unescaped into `#resultArea` innerHTML (lines 537-541).
- `templates/templates.html:654` — error path injects `e.message` as HTML:
  `` document.getElementById('resultArea').innerHTML = `<span class="empty" ...>${e.message}</span>` ``.
- There is **no escHtml helper in this file** (gallery.html:437-441 and
  history.html:480-484 each have one — copy that exact function).
- The built-in `TEMPLATES` constant (lines 296-456) is trusted local data —
  leave its rendering path (`renderTemplates`, `selectTemplate`) alone except
  where noted; the risk is the community data.
- The app-wide convention for events is `addEventListener` (see gallery.html
  and history.html); templates.html's inline `onclick` is the outlier.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Test suite | `venv/bin/python -m pytest tests/ -q` | all pass, exit 0 |

## Scope

**In scope**:
- `templates/templates.html` — `renderCommunityTemplates`, `selectCommunity`,
  the `generateImage` error path (line ~654), and adding `escHtml`/`escAttr`
  helpers.

**Out of scope**:
- `app.py` `/api/community-prompts` (server-side sanitization is a valid
  alternative but NOT this plan — don't do both).
- The static `TEMPLATES` rendering (`renderTemplates`/`selectTemplate`),
  except do NOT introduce new unescaped sinks.
- `sync_awesome_prompts.py` (data ingestion).

## Git workflow

- Commit style: `fix: escape community-prompt data in templates page (stored XSS)`

## Steps

### Step 1: Add escaping helpers

Copy `escHtml` from `templates/gallery.html:437-441` verbatim into the
templates.html script (top of the script block), and add:

```js
function escAttr(s) { return String(s || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
```

(same as `templates/studio.html:1196`).

### Step 2: Rewrite renderCommunityTemplates with data attributes

Replace the string-built `onclick` with `data-cat`/`data-idx` attributes and
escape every interpolation:

```js
    const itemHtml = items.map((t, i) => {
      const imgTag = t.image_url ? '<img src="' + escAttr(t.image_url) + '" style="width:40px;height:40px;object-fit:cover;border-radius:4px;margin-right:8px;flex-shrink:0" onerror="this.style.display=\'none\'" loading="lazy" />' : '';
      const hasPrompt = t.prompt && t.prompt.trim();
      const cls = hasPrompt ? 'tpl-item' : 'tpl-item no-prompt';
      const title = hasPrompt ? t.title : '🖼 ' + t.title + '（無文字，點擊看圖）';
      return '<div class="' + cls + '" data-cat="' + escAttr(cat) + '" data-idx="' + i + '" style="display:flex;align-items:center;gap:4px" title="' + escAttr(t.title) + '">' + imgTag + '<span style="overflow:hidden;text-overflow:ellipsis">' + escHtml(title) + '</span></div>';
    }).join('');
```

Category header: `'<span>' + escHtml(cat) + '</span>'`.

After setting `list.innerHTML`, attach delegation:

```js
    list.querySelectorAll('.tpl-item[data-cat]').forEach(el => {
      el.addEventListener('click', () => selectCommunity(parseInt(el.dataset.idx), el.dataset.cat, el));
    });
```

### Step 3: Fix selectCommunity

Change signature to `selectCommunity(idx, cat, el)`. Replace the brittle
"find by textContent" selection loop (lines 525-531) with:

```js
  document.querySelectorAll('.tpl-item').forEach(x => x.classList.remove('selected'));
  if (el) el.classList.add('selected');
```

In the image-only branch, escape the URL sinks:

- `<img src="' + escAttr(item.image_url) + '" ...`
- source link: only render it when the URL is http(s), and escape it:

```js
      const src = /^https?:\/\//.test(item.source_url || '') ? escAttr(item.source_url) : '';
      ... (src ? ' | <a href="' + src + '" target="_blank" rel="noopener" style="color:var(--accent)">原始來源</a>' : '')
```

### Step 4: Escape the error path

Line ~654: replace `${e.message}` with `${escHtml(e.message)}`.

**Verify (after steps 1–4)**: `venv/bin/python -m pytest tests/ -q` → all pass
(`test_app_pages_render` catches template syntax errors).

### Step 5: Manual XSS probe (if the app can be run)

Insert a hostile row and load the tab:

```bash
sqlite3 prompts.db "INSERT INTO community_prompts (title, category, prompt) VALUES ('\"><img src=x onerror=document.title=1>', 'XSSTEST\"><b>', 'p')"
```

Open `/templates` → 🌐 社群精選: the title must render as literal text, no
broken layout, devtools console shows no injected element. Then clean up:
`sqlite3 prompts.db "DELETE FROM community_prompts WHERE category LIKE 'XSSTEST%'"`.
If you cannot run the app, note the skip in your report.

## Test plan

- Template-syntax regression via existing `test_app_pages_render`.
- Manual probe in Step 5 (documented above so it's reproducible).
- (JS unit tests are not part of this repo's tooling — do not introduce a JS
  test framework for this.)

## Done criteria

- [ ] `grep -c "escAttr" templates/templates.html` → ≥ 4
- [ ] `grep -n "onclick=\"selectCommunity" templates/templates.html` → 0 matches
- [ ] `venv/bin/python -m pytest tests/ -q` exits 0
- [ ] No files outside the in-scope list modified (`git status`)
- [ ] `plans/README.md` status row updated

## STOP conditions

- The `renderCommunityTemplates` body differs materially from the excerpt
  (drift — e.g. plan 016 already consolidated helpers).
- You find yourself wanting to sanitize in `app.py` instead — that changes
  the architecture; report back first.

## Maintenance notes

- `sync_awesome_prompts.py` re-imports external data periodically; the
  frontend is now the enforcement point. If a second consumer of
  `/api/community-prompts` is added, consider moving sanitization server-side.
- Reviewer: check the studio suggestion box (`templates/studio.html:763-767`)
  as a reference — it already escapes the same data correctly.
