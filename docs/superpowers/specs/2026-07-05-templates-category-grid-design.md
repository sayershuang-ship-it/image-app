# Templates category-card grid landing view — design

## Purpose

`/templates` currently opens straight into a two-pane workspace: a 300px
sidebar listing all 14 categories as collapsible text rows
(`templates/templates.html:403-434`, `renderTemplateList()`), and a prompt
editor/generate pane on the right. Nothing on first load communicates what's
actually in each category beyond a text label and item count — a user has to
expand a category and scroll before they know if it has what they want.

`ImageStudio-Design-Package/index.html` (an already-generated design
prototype, not previously wired into the app) shows a different shape: 14
large cards (cover image + category name + template count) in a responsive
grid, no sidebar. This redesign adopts that as `/templates`'s landing view,
with the existing two-pane workspace becoming a second screen reached by
picking a category or searching.

## Non-goals

- No backend changes. `GET /api/templates` and `GET /api/templates/search`
  (both already shipped) are unchanged — the grid view's per-category counts
  are computed client-side from `/api/templates`'s existing grouped response,
  the same way `renderTemplateList()` already does today.
- No change to the right-hand editor/generate pane (`templates.html:281-298`)
  or to `/generate`, `/enhance-prompt`, `/save-to-pictures` — this is a
  navigation/browsing change only.
- No change to thumbnail click-to-lightbox behavior
  (`openThumbLightbox`/`closeThumbLightbox`, `templates.html:436-443`) or to
  click-to-load-prompt behavior (`selectTemplateItem`,
  `selectSearchResult`) — both are reused as-is inside the category/search
  view.
- No server-side routing change — this is one page (`GET /templates`) with
  two client-side view states, not two URLs. Reloading the page always
  returns to the grid landing view (no deep-linking to a specific category in
  this pass).

## Architecture

Two view states inside the same page, toggled by showing/hiding two
top-level containers with plain `display` changes (no client-side router,
no page reload):

- **Grid view** (`#gridView`, shown on load): full-width, hides the sidebar
  + editor `.workspace` entirely. Contains the search input (moved here from
  the sidebar) above a responsive grid of 14 category cards.
- **Workspace view** (`#workspaceView` — the existing `.workspace` div,
  `templates.html:270-300`, unchanged internally except the sidebar's top
  region): hidden on load, shown after a card click or a search submission.
  Its sidebar gains a "← 回分類網格" back button and loses the search input
  (moved to the grid view) and the always-visible 14-category collapsible
  list (replaced by a single category's flat item list, or a flat search
  results list — both already exist as `renderTemplateList` /
  `runTemplateSearch` output shapes, just no longer wrapped in per-category
  collapse sections when reached from a card click).

```
┌─ #gridView (shown first) ──────────────────┐     ┌─ #workspaceView (existing .workspace) ─┐
│ [search box]                               │ --> │ [← 回分類網格]                          │
│ [card] [card] [card] [card]                │     │ <single category or search results>    │
│ [card] [card] [card] [card]        ...     │     │                    │  <editor pane>     │
└─────────────────────────────────────────────┘     └─────────────────────────────────────────┘
```

Transition triggers:
1. Click a category card → show workspace view with that category's items
   (flat list, no collapse header — see "Components" below).
2. Type in the grid view's search box (same 300ms debounce as today) → on
   results, show workspace view with the flat search-results list.
3. Click "← 回分類網格" in the workspace view → show grid view again,
   clearing whatever list was in the sidebar.

## Components and data flow

### 1. Grid view markup and rendering

New container in `{% block content %}`, sibling to the existing
`.workspace` div, e.g.:

```html
<div class="grid-view" id="gridView">
  <div class="grid-view-search">
    <input type="text" id="templateSearchInput" placeholder="語意搜尋模板…" />
  </div>
  <div class="category-grid" id="categoryGrid"></div>
</div>
```

`templateSearchInput` keeps its existing id and the exact same debounce/fetch
logic already in `templates.html:454-463` — only its markup location moves
(sidebar → grid view header). No JS behavior change to the search call
itself.

`renderCategoryGrid()` (new function): given the already-fetched
`TEMPLATE_DATA` (from the existing `loadTemplates()`,
`templates.html:391-401` — unchanged), builds one card per category using
the existing `CAT_COVERS` mapping (`templates.html:343-357`, unchanged) for
the cover image and `TEMPLATE_DATA[cat].length` for the count:

```javascript
function renderCategoryGrid() {
  const grid = document.getElementById('categoryGrid');
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

Called once, right after `TEMPLATE_DATA` is populated in `loadTemplates()`.

### 2. View-state toggle functions (new)

```javascript
function showGridView() {
  document.getElementById('gridView').style.display = '';
  document.getElementById('workspaceView').style.display = 'none';
}

function showWorkspaceView() {
  document.getElementById('gridView').style.display = 'none';
  document.getElementById('workspaceView').style.display = '';
}
```

The existing `.workspace` div gets `id="workspaceView"` added (its class
stays `workspace` for existing CSS to keep applying) and an inline
`style="display:none"` as its initial state, so the grid view is what
renders first without a flash of the old sidebar.

### 3. Single-category rendering (modified from `renderTemplateList`)

Today's `renderTemplateList()` (`templates.html:403-434`) renders ALL 14
categories as collapsible sections. That function is kept for no one else to
call, but the card-click path uses a new, simpler function that renders only
one category's items flat (no collapse header, matching the existing
per-item markup exactly — thumbnail, click-to-lightbox, click-to-load-prompt
all identical):

```javascript
function showCategoryInWorkspace(cat) {
  const items = TEMPLATE_DATA[cat];
  renderFlatItemList(items, cat);
  showWorkspaceView();
}
```

`renderFlatItemList(items, headerLabel)` (new, shared by both the
card-click path and the search-result path — see below) factors out the
per-item markup block that's currently duplicated between
`renderTemplateList` (`templates.html:405-410`) and `runTemplateSearch`
(`templates.html:466-471`), and renders it under a static header showing
`headerLabel` (the category name, or `"搜尋結果"` for search) instead of a
collapsible `.cat-header`. It attaches the same click handlers
`selectTemplateItem`-equivalent behavior — since there's no longer a
"category" concept distinguishing rows once inside a single list, this
reuses `selectSearchResult`'s simpler index-based lookup pattern
(`templates.html:499-503`) against whatever `items` array was passed in,
rather than `selectTemplateItem`'s two-key
(`TEMPLATE_DATA[cat][idx]`) lookup — one fewer parameter, same effect.

### 4. Search integration

`runTemplateSearch(query)` (`templates.html:464-498`) keeps its fetch logic
unchanged. Its rendering tail (`templates.html:483-498`, currently building
`itemHtml` inline) is replaced with a call to the same
`renderFlatItemList(results, "搜尋結果")` from #3, then `showWorkspaceView()`.
The debounced listener on `templateSearchInput`
(`templates.html:454-463`)'s empty-input branch, which currently calls
`renderTemplateList()` to restore the grouped view, instead calls
`showGridView()` — since there's no more always-visible category list to
"restore", clearing the search just means going back to the grid.

### 5. Back button

Sidebar's top region (`templates.html:271-279`, `.sidebar-header` +
containing search input) becomes:

```html
<div class="sidebar-header">
  <button class="btn-back" onclick="showGridView()">← 回分類網格</button>
</div>
<div class="template-list" id="templateList"></div>
```

The `.badge` "Templates" span and the search input both move out (search to
the grid view per #1; the badge was purely decorative and is dropped since
the workspace view no longer represents "all templates," just one
category/search result at a time).

## Error handling

No new failure modes. `runTemplateSearch`'s existing 400/503 handling
(`templates.html:472-481`) is unchanged — errors render inside
`#templateList` exactly as today, still inside the workspace view (a failed
search still transitions to the workspace view to show the error message,
consistent with today's behavior of showing errors in place of results
rather than silently staying on the previous screen).

## Testing

- `tests/test_app.py`: replace the existing
  `test_templates_page_has_search_input` assertion (which currently checks
  for `templateSearchInput` inside the page — still true, just relocated)
  with an additional assertion that `id="gridView"` and `id="categoryGrid"`
  are present in the rendered `/templates` HTML, and that `id="workspaceView"`
  is present with an inline `display:none` (or equivalent hidden-by-default
  marker) so the grid is confirmed to be the default visible state at
  render time.
- Manual verification (no JS test harness in this project): load `/templates`,
  confirm the grid of 14 cards renders with correct cover images and counts;
  click a card, confirm the workspace view shows that category's flat item
  list and the back button returns to the grid; type a search query from the
  grid view, confirm it transitions to the workspace view with ranked flat
  results; click a thumbnail inside the workspace view to confirm the
  lightbox still works; click an item's title to confirm the prompt still
  loads into the editor and Generate still works end-to-end for at least one
  template.
