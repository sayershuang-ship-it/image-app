# Templates Category-Card Grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/templates`'s always-visible 14-category collapsible
sidebar with a full-width landing grid of category cards (cover image +
name + count), transitioning to the existing two-pane editor workspace only
after a category is picked or a search is run.

**Architecture:** One page, two client-side view states (`#gridView` shown
first, `#workspaceView` — the existing `.workspace` — shown after a card
click or search), toggled by plain `display` changes, no page reload, no
new backend endpoints. A new shared `renderFlatItemList()` replaces the
category-list-specific and search-specific item-rendering code paths that
currently duplicate the same markup.

**Tech Stack:** Plain JS/HTML/CSS in `templates/templates.html` (no build
step, no framework — matches the rest of this project).

## Global Constraints

- Design source: `docs/superpowers/specs/2026-07-05-templates-category-grid-design.md`.
- No backend changes. `GET /api/templates` and `GET /api/templates/search`
  are consumed exactly as today — no new routes, no new response fields.
- `templateSearchInput` keeps its exact id — only its markup location moves
  (sidebar → grid view header) — so its existing debounce/fetch logic is
  reused unchanged.
- Thumbnail click-to-lightbox (`openThumbLightbox`/`closeThumbLightbox`,
  `.tpl-thumb` class + `data-full` attribute) and click-to-load-prompt
  behavior must work identically in the new single-list view as they did in
  the old grouped view.
- Reloading `/templates` always lands on the grid view (no deep-linking to a
  specific category in this pass — out of scope per the spec's Non-goals).

---

### Task 1: Grid view scaffolding — category cards, no click-through yet

**Files:**
- Modify: `templates/templates.html`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `TEMPLATE_DATA` (existing global, populated by `loadTemplates()`,
  currently at `templates.html:391-401`), `CAT_COVERS` (existing global,
  `templates.html:343-358`).
- Produces:
  - `#gridView`, `#categoryGrid`, `#workspaceView` — DOM ids later tasks
    and tests depend on.
  - `renderCategoryGrid()` — renders one `.category-card` per
    `Object.entries(TEMPLATE_DATA)` entry into `#categoryGrid`.
  - `showGridView()` / `showWorkspaceView()` — toggle which of the two
    containers is visible.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_templates_page_has_grid_view_scaffolding(client):
    """The templates page must render the new grid-view containers, with
    the workspace view hidden by default (grid is the landing view)."""
    rv = client.get("/templates")
    body = rv.data.decode()
    assert 'id="gridView"' in body
    assert 'id="categoryGrid"' in body
    assert 'id="workspaceView"' in body
    # Workspace must be hidden on initial render — the grid is what's seen first.
    workspace_pos = body.index('id="workspaceView"')
    # The workspaceView opening tag must carry a hidden style within the
    # next 200 characters (i.e. on the same opening tag).
    assert 'display:none' in body[workspace_pos:workspace_pos + 200]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_page_has_grid_view_scaffolding -v`
Expected: FAIL — none of `gridView`/`categoryGrid`/`workspaceView` exist yet.

- [ ] **Step 3: Add the CSS**

In `templates/templates.html`'s `{% block page_css %}`, immediately before
the closing `{% endblock %}` (currently right after `.tpl-thumb { cursor:
zoom-in; }`, around line 266), add:

```css
.grid-view-search {
  padding: 24px 48px 0;
}
.grid-view-search input {
  width: 100%;
  max-width: 640px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--fg);
  padding: 10px 14px;
  font-size: 15px;
  font-family: var(--font-body);
  border-radius: var(--radius-sm);
}
.grid-view-search input:focus { outline: none; border-color: var(--accent); }
.category-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 20px;
  padding: 24px 48px 48px;
}
.category-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  cursor: pointer;
  transition: border-color var(--ease);
}
.category-card:hover { border-color: var(--accent); }
.category-card img, .category-card-cover-placeholder {
  width: 100%;
  aspect-ratio: 3 / 2;
  object-fit: cover;
  display: block;
  background: var(--border);
}
.category-card-body { padding: 14px 16px; }
.category-card-body h3 {
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 590;
  letter-spacing: -0.01em;
}
.category-card-body p {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
  font-family: var(--font-mono);
}
```

- [ ] **Step 4: Add the grid-view markup**

In `templates/templates.html`'s `{% block content %}`, the current structure
is:

```html
{% block content %}
  <div class="workspace">
    <aside class="sidebar">
      ...
    </aside>
    <main class="main">
      ...
    </main>
  </div>
  <div class="thumb-lightbox" id="thumbLightbox" onclick="closeThumbLightbox()">
    <img id="thumbLightboxImg" src="" alt="" />
  </div>
{% endblock %}
```

Replace the opening `<div class="workspace">` line with a new `#gridView`
block placed before it, and give the `.workspace` div `id="workspaceView"`
plus an inline hidden style:

```html
{% block content %}
  <div class="grid-view" id="gridView">
    <div class="grid-view-search">
      <input type="text" id="templateSearchInput" placeholder="語意搜尋模板…" />
    </div>
    <div class="category-grid" id="categoryGrid"></div>
  </div>
  <div class="workspace" id="workspaceView" style="display:none">
    <aside class="sidebar">
      ...
    </aside>
    <main class="main">
      ...
    </main>
  </div>
  <div class="thumb-lightbox" id="thumbLightbox" onclick="closeThumbLightbox()">
    <img id="thumbLightboxImg" src="" alt="" />
  </div>
{% endblock %}
```

Do not touch the `<aside class="sidebar">` or `<main class="main">` internals
in this task — that happens in Task 2. For now, remove the OLD search input
block that currently sits inside `<aside class="sidebar">`
(`templates.html:275-278`, the `<div style="padding:10px 20px;...">` wrapping
the old `templateSearchInput`) since the input now lives in `#gridView`
instead — there must be only one element with `id="templateSearchInput"` on
the page.

- [ ] **Step 5: Add `renderCategoryGrid()`, `showGridView()`, `showWorkspaceView()`**

Add these three functions to the `<script>` block, near the existing
`renderTemplateList()` (`templates.html:403-434` — leave that function
in place for now, Task 2 removes it):

```javascript
function renderCategoryGrid() {
  const grid = document.getElementById('categoryGrid');
  if (!TEMPLATE_DATA) return;
  grid.innerHTML = Object.entries(TEMPLATE_DATA).map(([cat, items]) => {
    const cover = CAT_COVERS[cat] || '';
    const coverTag = cover
      ? '<img src="' + escAttr(cover) + '" loading="lazy" alt="" />'
      : '<div class="category-card-cover-placeholder"></div>';
    return '<div class="category-card" data-cat="' + escAttr(cat) + '">' +
           coverTag +
           '<div class="category-card-body">' +
           '<h3>' + escHtml(cat) + '</h3>' +
           '<p>' + items.length + ' 個範本</p>' +
           '</div></div>';
  }).join('');
}

function showGridView() {
  document.getElementById('gridView').style.display = '';
  document.getElementById('workspaceView').style.display = 'none';
}

function showWorkspaceView() {
  document.getElementById('gridView').style.display = 'none';
  document.getElementById('workspaceView').style.display = '';
}
```

Update `loadTemplates()` (`templates.html:391-401`) to call
`renderCategoryGrid()` instead of `renderTemplateList()`:

```javascript
async function loadTemplates() {
  const grid = document.getElementById('categoryGrid');
  grid.innerHTML = '<p style="padding:24px 48px;color:var(--muted)">Loading...</p>';
  try {
    const res = await fetch('/api/templates');
    TEMPLATE_DATA = await res.json();
    renderCategoryGrid();
  } catch (e) {
    grid.innerHTML = '<p style="padding:24px 48px;color:var(--danger)">Failed: ' + escHtml(e.message) + '</p>';
  }
}
```

Leave `renderTemplateList()` itself untouched for now (still defined, just
no longer called from `loadTemplates()` — Task 2 deletes it once nothing
references it).

- [ ] **Step 6: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_app.py -v`
Expected: PASS (all tests, including the new one)

- [ ] **Step 7: Manual check (grid renders, cards show data)**

Since automated tests here only check static HTML (this project has no JS
test harness), do a quick manual sanity check: start the app
(`venv/bin/python app.py`), open `/templates`, confirm 14 cards render with
cover images, category names, and counts, and that the search input appears
above the grid. Clicking a card does nothing yet — that's Task 2.

- [ ] **Step 8: Commit**

```bash
git add templates/templates.html tests/test_app.py
git commit -m "feat: add category-card grid view scaffolding to templates page"
```

---

### Task 2: Click-through wiring, shared flat-list rendering, remove old sidebar list

**Files:**
- Modify: `templates/templates.html`

**Interfaces:**
- Consumes: `showGridView()`, `showWorkspaceView()`, `renderCategoryGrid()`
  (Task 1), `openThumbLightbox()`/`closeThumbLightbox()` (existing,
  `templates.html:436-443`), `TEMPLATE_DATA` (existing global).
- Produces:
  - `renderFlatItemList(items, headerLabel)` — shared renderer for both a
    single category's items and search results. Renders into
    `#templateList`, with `headerLabel` shown as a static (non-collapsible)
    heading. Wires each item's click behavior (thumbnail → lightbox, text →
    load prompt into `#promptInput`).
  - `showCategoryInWorkspace(cat)` — looks up `TEMPLATE_DATA[cat]`, calls
    `renderFlatItemList`, then `showWorkspaceView()`.
  - `selectFlatItem(items, idx, el)` — replaces both `selectTemplateItem`
    and `selectSearchResult` with one function taking the items array
    directly (no more category-name indirection, since the sidebar only
    ever shows one list at a time now).

- [ ] **Step 1: Add the sidebar's back button, remove the old collapsible-list styling dependency**

The current sidebar markup (inside `<aside class="sidebar">`, now inside
`#workspaceView`) is:

```html
    <aside class="sidebar">
      <div class="sidebar-header">
        <h2>Prompt Templates</h2><span class="badge">Templates</span>
      </div>
      <div class="template-list" id="templateList"></div>
    </aside>
```

(Task 1 already removed the old search-input block that used to sit between
the header and `.template-list`.) Replace the `.sidebar-header` contents
with a back button:

```html
    <aside class="sidebar">
      <div class="sidebar-header">
        <button class="btn-back" onclick="showGridView()">← 回分類網格</button>
      </div>
      <div class="template-list" id="templateList"></div>
    </aside>
```

Add its style in `{% block page_css %}`, near `.sidebar-header` (around
line 29-36):

```css
.btn-back {
  background: none;
  border: none;
  color: var(--muted);
  font-size: 13px;
  font-family: var(--font-body);
  cursor: pointer;
  padding: 0;
  transition: color var(--ease);
}
.btn-back:hover { color: var(--accent); }
```

- [ ] **Step 2: Add `renderFlatItemList` and `selectFlatItem`**

Add to the `<script>` block, replacing nothing yet (additions only):

```javascript
function renderFlatItemList(items, headerLabel) {
  const list = document.getElementById('templateList');
  const itemHtml = items.map((t, i) => {
    const thumbTag = t.thumbnail_url
      ? '<img src="' + escAttr(t.thumbnail_url) + '" class="tpl-thumb" data-full="' + escAttr(t.thumbnail_url) + '" style="width:88px;height:88px;object-fit:cover;border-radius:8px;margin-right:10px;flex-shrink:0" onerror="this.src=\'/static/template_thumb_placeholder.svg\'" loading="lazy" />'
      : '<div style="width:88px;height:88px;border-radius:8px;margin-right:10px;flex-shrink:0;background:var(--border)"></div>';
    return '<div class="tpl-item" data-idx="' + i +
           '" style="display:flex;align-items:center;gap:4px" title="' + escAttr(t.title) + '">' +
           thumbTag + '<span style="overflow:hidden;text-overflow:ellipsis">' + escHtml(t.title) + '</span></div>';
  }).join('');
  list.innerHTML =
    '<div style="padding:10px 12px;font-size:12px;font-weight:510;text-transform:uppercase;color:var(--muted);letter-spacing:0.06em;font-family:var(--font-mono)">' +
    escHtml(headerLabel) + ' · ' + items.length + '</div>' +
    (itemHtml || '<div class="tpl-item" style="color:var(--muted)">No matches</div>');
  list.querySelectorAll('.tpl-item[data-idx]').forEach(el => {
    el.addEventListener('click', (e) => {
      const thumb = e.target.closest('.tpl-thumb');
      if (thumb) { openThumbLightbox(thumb.dataset.full); return; }
      selectFlatItem(items, parseInt(el.dataset.idx), el);
    });
  });
}

function selectFlatItem(items, idx, el) {
  document.querySelectorAll('.tpl-item').forEach(x => x.classList.remove('selected'));
  if (el) el.classList.add('selected');
  document.getElementById('promptInput').value = items[idx].prompt;
}

function showCategoryInWorkspace(cat) {
  const items = TEMPLATE_DATA[cat];
  renderFlatItemList(items, cat);
  showWorkspaceView();
}
```

- [ ] **Step 3: Wire category-card clicks**

Update `renderCategoryGrid()` (added in Task 1) to attach click handlers
after setting `innerHTML`:

```javascript
function renderCategoryGrid() {
  const grid = document.getElementById('categoryGrid');
  if (!TEMPLATE_DATA) return;
  grid.innerHTML = Object.entries(TEMPLATE_DATA).map(([cat, items]) => {
    const cover = CAT_COVERS[cat] || '';
    const coverTag = cover
      ? '<img src="' + escAttr(cover) + '" loading="lazy" alt="" />'
      : '<div class="category-card-cover-placeholder"></div>';
    return '<div class="category-card" data-cat="' + escAttr(cat) + '">' +
           coverTag +
           '<div class="category-card-body">' +
           '<h3>' + escHtml(cat) + '</h3>' +
           '<p>' + items.length + ' 個範本</p>' +
           '</div></div>';
  }).join('');
  grid.querySelectorAll('.category-card').forEach(el => {
    el.addEventListener('click', () => showCategoryInWorkspace(el.dataset.cat));
  });
}
```

(This replaces the Task-1 version of `renderCategoryGrid` — same body, with
the click-wiring loop added at the end.)

- [ ] **Step 4: Rewire search to use the shared renderer and the two view states**

Replace the search-input listener (`templates.html:454-463` before this
task's edits) — its empty-input branch currently calls `renderTemplateList()`:

```javascript
document.getElementById('templateSearchInput').addEventListener('input', (e) => {
  clearTimeout(searchDebounceTimer);
  const query = e.target.value.trim();
  if (!query) {
    showGridView();
    return;
  }
  searchDebounceTimer = setTimeout(() => runTemplateSearch(query), 300);
});
```

Replace `runTemplateSearch`'s body (`templates.html:464-497` before this
task's edits) — keep the fetch/error-handling exactly as-is, only change
what happens with a successful result:

```javascript
async function runTemplateSearch(query) {
  const list = document.getElementById('templateList');
  showWorkspaceView();
  list.innerHTML = '<div class="tpl-item" style="color:var(--muted)">Searching...</div>';
  let results;
  try {
    const res = await fetch('/api/templates/search?q=' + encodeURIComponent(query));
    const data = await res.json();
    if (!res.ok) {
      list.innerHTML = '<div class="tpl-item" style="color:var(--danger)">' + escHtml(data.error || 'Search failed') + '</div>';
      return;
    }
    results = data;
  } catch (e) {
    list.innerHTML = '<div class="tpl-item" style="color:var(--danger)">Search failed: ' + escHtml(e.message) + '</div>';
    return;
  }
  renderFlatItemList(results, '搜尋結果');
}
```

(`showWorkspaceView()` is called immediately so the "Searching..." state is
visible in the workspace view rather than flashing in the still-visible grid
view.)

- [ ] **Step 5: Delete now-dead functions and CSS**

Delete these functions entirely (nothing calls them anymore after Steps
1-4): `renderTemplateList()`, `selectTemplateItem()`, `selectSearchResult()`,
`toggleCat()`.

Delete their now-unused CSS rules from `{% block page_css %}`:
`.tab-bar`, `.tab-btn`, `.tab-btn.active`, `.tab-btn:hover` (dead since the
tab bar was already removed by a prior feature — verify these are indeed
unreferenced before deleting, via `grep -n "tab-bar\|tab-btn"
templates/templates.html`), `.category`, `.cat-header` and its `:hover`/
`.arrow`/`.collapsed` variants, `.cat-cover`, `.cat-items` and its `.hidden`
variant. Keep `.tpl-item`, `.tpl-item span`, `.tpl-item:hover`,
`.tpl-item.selected`, `.tpl-thumb`, `.template-list` — these are still used
by `renderFlatItemList`.

- [ ] **Step 6: Run the full test suite**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all tests pass (no test in this project exercises the deleted JS
functions directly — they're only reachable through browser interaction,
which Task 3 covers manually).

- [ ] **Step 7: Manual verification**

Start the app, open `/templates`:
- Click a category card → confirm the workspace view appears showing that
  category's flat item list with a header reading `"<category name> · N"`.
- Click a thumbnail inside that list → confirm the lightbox opens with the
  full-size image (this exercises the fix this task incidentally makes:
  search results previously didn't carry the `tpl-thumb`/`data-full`
  markup needed for the lightbox to work — `renderFlatItemList` now gives
  both category and search paths the same markup).
- Click an item's title → confirm the prompt loads into the editor.
- Click "← 回分類網格" → confirm it returns to the grid view.
- Type a query into the grid view's search box → confirm it transitions to
  the workspace view with ranked results.
- Clear the search box → confirm it returns to the grid view.

- [ ] **Step 8: Commit**

```bash
git add templates/templates.html
git commit -m "feat: wire category-card clicks and search to shared flat-list workspace view"
```

---

### Task 3: Test coverage cleanup and final verification

**Files:**
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: everything from Tasks 1-2. No new interfaces produced — this is
  the leaf of the chain.

- [ ] **Step 1: Update the stale search-input test**

The existing `test_templates_page_has_search_input` test (added by a prior
feature, currently asserting `'id="templateSearchInput"'` and
`"/api/templates/search"` appear in the page) remains valid as-is — the
input still has that id and the page still references that endpoint, just
from a different markup location. Run it to confirm it still passes:

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_page_has_search_input -v`
Expected: PASS (no code change needed — this step is verification only)

- [ ] **Step 2: Add a regression test for the old sidebar list being gone**

Add to `tests/test_app.py`:

```python
def test_templates_page_has_no_leftover_collapsible_category_list(client):
    """The old always-visible 14-category collapsible sidebar list is gone —
    replaced by the category-card grid landing view."""
    rv = client.get("/templates")
    body = rv.data.decode()
    assert "renderTemplateList" not in body
    assert "toggleCat" not in body
```

- [ ] **Step 3: Run it to verify it fails, then passes**

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_page_has_no_leftover_collapsible_category_list -v`

If Task 2 was completed correctly, this should already PASS (the functions
were deleted in Task 2, Step 5). If it FAILS, that means Task 2's cleanup
step was incomplete — go back and delete the leftover `renderTemplateList`/
`toggleCat` references before proceeding.

- [ ] **Step 4: Run the full suite one more time**

Run: `venv/bin/python -m pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 5: Final manual pass**

Repeat the full manual checklist from Task 2 Step 7, plus: confirm
Generate still works end-to-end for at least one template picked via a
category card and one picked via search (select a template, click Generate,
confirm an image comes back) — this exercises that the editor pane
(untouched by this plan) still receives prompts correctly from the new
click-through paths.

- [ ] **Step 6: Commit**

```bash
git add tests/test_app.py
git commit -m "test: add regression coverage for templates category-grid view"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1 = grid view + card rendering (spec sections
  "Grid view markup and rendering", "View-state toggle functions"), Task 2 =
  click-through + shared renderer + back button + cleanup (spec sections
  "Single-category rendering", "Search integration", "Back button"), Task 3 =
  testing (spec's "Testing" section). All spec sections covered.
- **Incidental bug fix noted:** the spec's Task 2 equivalent
  (`renderFlatItemList`) unifies markup that today exists in two
  slightly-diverged copies — `renderTemplateList`'s item markup has
  `class="tpl-thumb" data-full="..."` for lightbox support, but
  `runTemplateSearch`'s copy is missing both, meaning search-result
  thumbnails currently don't open the lightbox. This plan's refactor fixes
  that as a natural side effect of sharing one render function; Task 2 Step 7
  explicitly calls out verifying this works, so it doesn't go unnoticed if
  the refactor is done incorrectly.
- **Type/name consistency check:** `showGridView`/`showWorkspaceView`
  (Task 1) are called by name-identical functions in Task 2
  (`showCategoryInWorkspace`, the search empty-clear handler);
  `renderCategoryGrid` (Task 1) is redefined once in Task 2 with the same
  signature (no params, reads global `TEMPLATE_DATA`) plus added click
  wiring — no signature drift. `renderFlatItemList(items, headerLabel)` is
  used identically by both `showCategoryInWorkspace` and
  `runTemplateSearch`. `selectFlatItem(items, idx, el)` replaces both
  deleted single-purpose selectors with one shared one, consistent with the
  spec's stated intent to drop the two-key category+index lookup now that
  only one list is ever shown at a time.
