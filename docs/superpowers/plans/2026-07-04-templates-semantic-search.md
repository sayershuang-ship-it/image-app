# Templates Semantic Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a search box to `/templates` that does semantic (intent) matching
across all 977 templates using local Ollama embeddings, with zero external
API calls and zero per-query cost.

**Architecture:** An offline batch script embeds every `templates` row once
via Ollama's local `bge-m3` model and stores the vector in a new
`templates.embedding` column. A new `GET /api/templates/search?q=` endpoint
embeds only the query string at request time and ranks all stored vectors by
cosine similarity in Python. The frontend adds a debounced search box that
replaces the category list with a flat top-30 ranked list while a query is
active.

**Tech Stack:** Python 3, Flask, sqlite3, `requests` (already a dependency,
`app.py:22`), local Ollama daemon (`http://localhost:11434`) with the
`bge-m3` model.

## Global Constraints

- Design source: `docs/superpowers/specs/2026-07-04-templates-semantic-search-design.md`.
- Builds on the `templates` table from
  `docs/superpowers/plans/2026-07-04-unified-templates-page.md` (977 rows:
  99 official + 878 community).
- **Verified API shape** (checked live against the actual installed Ollama,
  version 0.31.1, with `bge-m3:latest` pulled): `POST /api/embed` with body
  `{"model": "bge-m3", "input": "<text>"}` returns
  `{"model": ..., "embeddings": [[<1024 floats>]], ...}` — note plural
  `embeddings`, a **list of vectors** even for a single input string; take
  `embeddings[0]` for a single-string call. Vector dimension is **1024**.
- No new Python dependencies — `requests` is already imported in `app.py:22`.
- `bge-m3` must be pulled once per machine before the batch script or search
  endpoint will work: `ollama pull bge-m3` (~1.2GB). This is a manual,
  environment-level setup step, not something any script automates (same
  category as setting `OPENAI_API_KEY`/`GOOGLE_API_KEY` was for the prior
  migration).
- No similarity threshold — always return the top 30 by score, even if the
  30th-best match is weak (spec, "Error handling" section).

---

### Task 1: `templates.embedding` column

**Files:**
- Modify: `app.py:105-160` (inside `init_db()`, alongside the existing
  `prompts` table column-migration pattern at `app.py:127-136`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `templates.embedding` column (BLOB, nullable), which Task 2's
  `embed_text()`/storage code and Task 4's search endpoint both read/write.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_templates_table_has_embedding_column(client):
    with sqlite3.connect(app_module.DB_PATH) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()}
    assert "embedding" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_table_has_embedding_column -v`
Expected: FAIL — `embedding` not in the column set.

- [ ] **Step 3: Add the column migration**

In `app.py`, immediately after the existing `templates` table's
`CREATE TABLE IF NOT EXISTS` block (added by the prior plan, right after the
`community_prompts_fts` block), add a column-migration check matching the
existing `prompts`-table pattern at `app.py:127-136`:

```python
        existing_template_cols = {r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()}
        if "embedding" not in existing_template_cols:
            conn.execute("ALTER TABLE templates ADD COLUMN embedding BLOB")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_table_has_embedding_column -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: add embedding column to templates table"
```

---

### Task 2: `embed_text()` and `cosine_similarity()` in `app.py`

**Files:**
- Modify: `app.py` (add near `get_google_client()`, `app.py:64-69`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces:
  - `OLLAMA_URL = "http://localhost:11434"` module constant.
  - `embed_text(text: str, model: str = "bge-m3") -> list` — returns a list
    of floats (length 1024 for `bge-m3`). Raises `requests.RequestException`
    (or subclasses, e.g. `ConnectionError`) if Ollama is unreachable; raises
    `KeyError`/similar if the response is malformed. Callers (Task 3's batch
    script, Task 4's search route) catch these.
  - `cosine_similarity(a: list, b: list) -> float` — pure function, no I/O.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
from unittest.mock import patch, MagicMock


def test_cosine_similarity_identical_vectors_is_one():
    assert app_module.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert app_module.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_embed_text_posts_to_ollama_and_returns_first_vector():
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "model": "bge-m3",
        "embeddings": [[0.1, 0.2, 0.3]],
    }
    fake_response.raise_for_status.return_value = None
    with patch("app.requests.post", return_value=fake_response) as mock_post:
        vec = app_module.embed_text("hello world")
    assert vec == [0.1, 0.2, 0.3]
    mock_post.assert_called_once_with(
        "http://localhost:11434/api/embed",
        json={"model": "bge-m3", "input": "hello world"},
        timeout=30,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_app.py -k "cosine_similarity or embed_text" -v`
Expected: FAIL with `AttributeError: module 'app' has no attribute 'cosine_similarity'`

- [ ] **Step 3: Add the functions to `app.py`**

Immediately after `get_google_client()` (`app.py:64-69`), add:

```python
OLLAMA_URL = "http://localhost:11434"


def embed_text(text: str, model: str = "bge-m3") -> list:
    """Embed text via the local Ollama daemon. Raises on any failure —
    callers (batch script, search endpoint) decide how to handle it."""
    response = requests.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": model, "input": text},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data["embeddings"][0]


def cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_app.py -k "cosine_similarity or embed_text" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: add embed_text() and cosine_similarity() helpers"
```

---

### Task 3: `generate_template_embeddings.py` batch script

**Files:**
- Create: `generate_template_embeddings.py`
- Test: `tests/test_generate_template_embeddings.py`

**Interfaces:**
- Consumes: `app.embed_text` (Task 2), `templates` table with `embedding`
  column (Task 1).
- Produces:
  - `get_rows_without_embedding(conn, limit=None) -> list[sqlite3.Row]`
  - `run_embedding_batch(conn, limit=None) -> tuple[int, int]` — `(succeeded, failed)`
  - CLI: `venv/bin/python generate_template_embeddings.py [--dry-run N]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_generate_template_embeddings.py`:

```python
import json
import os
import sqlite3
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import app as app_module
import generate_template_embeddings as gte


def _fresh_db():
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    app_module.DB_PATH = db_path
    app_module.init_db()
    return db_path


def test_get_rows_without_embedding_excludes_already_embedded():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt) "
            "VALUES (1, 'official', 'T1', 'p1', 'c', 'tp1')"
        )
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt, embedding) "
            "VALUES (2, 'official', 'T2', 'p2', 'c', 'tp2', ?)",
            (json.dumps([0.1, 0.2]).encode(),),
        )
        conn.row_factory = sqlite3.Row
        rows = gte.get_rows_without_embedding(conn)
    assert [r["id"] for r in rows] == [1]


def test_run_embedding_batch_stores_vector_and_skips_done_rows():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt) "
            "VALUES (1, 'official', 'T1', 'some prompt', 'UI', 'a concrete prompt')"
        )
        conn.commit()

        with patch("generate_template_embeddings.embed_text", return_value=[0.1, 0.2, 0.3]):
            succeeded, failed = gte.run_embedding_batch(conn)

        assert (succeeded, failed) == (1, 0)
        row = conn.execute("SELECT embedding FROM templates WHERE id=1").fetchone()
        assert json.loads(row[0]) == [0.1, 0.2, 0.3]

        # Re-running must not re-embed the same row.
        with patch("generate_template_embeddings.embed_text") as mock_embed:
            succeeded2, failed2 = gte.run_embedding_batch(conn)
        assert (succeeded2, failed2) == (0, 0)
        mock_embed.assert_not_called()


def test_run_embedding_batch_continues_past_a_failure():
    db_path = _fresh_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt) "
            "VALUES (1, 'official', 'T1', 'p1', 'c', 'tp1')"
        )
        conn.execute(
            "INSERT INTO templates (id, source, title, prompt, category, thumbnail_prompt) "
            "VALUES (2, 'official', 'T2', 'p2', 'c', 'tp2')"
        )
        conn.commit()

        def flaky_embed(text, model="bge-m3"):
            if "tp1" in text or "T1" in text:
                raise ConnectionError("ollama down")
            return [0.5, 0.5]

        with patch("generate_template_embeddings.embed_text", side_effect=flaky_embed):
            succeeded, failed = gte.run_embedding_batch(conn)

        assert (succeeded, failed) == (1, 1)
        row2 = conn.execute("SELECT embedding FROM templates WHERE id=2").fetchone()
        assert json.loads(row2[0]) == [0.5, 0.5]
        row1 = conn.execute("SELECT embedding FROM templates WHERE id=1").fetchone()
        assert row1[0] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_generate_template_embeddings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'generate_template_embeddings'`

- [ ] **Step 3: Write `generate_template_embeddings.py`**

```python
#!/usr/bin/env python3
"""
generate_template_embeddings.py — one-time (and re-runnable) batch job that
embeds every templates row via local Ollama (bge-m3) and stores the vector
in templates.embedding, for /api/templates/search to rank against.

Prerequisite: `ollama pull bge-m3` (once per machine) and the Ollama daemon
running locally (http://localhost:11434).

Run: venv/bin/python generate_template_embeddings.py [--dry-run N]

Safe to re-run: rows that already have an embedding are skipped.
"""

import argparse
import json
import os
import sqlite3

from app import embed_text

DB_PATH = os.path.join(os.path.dirname(__file__), "prompts.db")


def get_rows_without_embedding(conn: sqlite3.Connection, limit=None) -> list:
    query = ("SELECT id, title, category, prompt FROM templates "
             "WHERE embedding IS NULL")
    if limit is not None:
        query += f" LIMIT {int(limit)}"
    return conn.execute(query).fetchall()


def run_embedding_batch(conn: sqlite3.Connection, limit=None) -> tuple:
    rows = get_rows_without_embedding(conn, limit=limit)
    succeeded, failed = 0, 0
    for row in rows:
        text = f"{row['title']} {row['category']} {row['prompt']}"
        try:
            vector = embed_text(text)
        except Exception as exc:
            print(f"  [embedding failed] id={row['id']} — {exc}")
            failed += 1
            continue
        conn.execute(
            "UPDATE templates SET embedding = ? WHERE id = ?",
            (json.dumps(vector).encode(), row["id"]),
        )
        conn.commit()
        succeeded += 1
    return succeeded, failed


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", type=int, default=None,
                         help="Only embed the first N rows missing an embedding")
    args = parser.parse_args(argv)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        succeeded, failed = run_embedding_batch(conn, limit=args.dry_run)
        total = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        still_missing = conn.execute(
            "SELECT COUNT(*) FROM templates WHERE embedding IS NULL").fetchone()[0]
        print(f"Embedded {succeeded} rows, {failed} failed. "
              f"{total} total rows, {still_missing} still missing an embedding. "
              f"Re-run this script to retry those.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_generate_template_embeddings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add generate_template_embeddings.py tests/test_generate_template_embeddings.py
git commit -m "feat: add generate_template_embeddings.py batch script"
```

---

### Task 4: `GET /api/templates/search` endpoint

**Files:**
- Modify: `app.py` (add route near `/api/templates`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `app.embed_text`, `app.cosine_similarity` (Task 2), `templates`
  table with populated `embedding` column (Task 3's output).
- Produces: `GET /api/templates/search?q=<query>` →
  - `400 {"error": "q is required"}` if `q` missing/empty.
  - `503 {"error": "本地 Ollama 未啟動或缺少 bge-m3 模型，請執行 ollama pull bge-m3 並確認 ollama serve 正在執行"}`
    if `embed_text` raises.
  - `200` with a **flat JSON array** (not grouped by category), each item
    `{"id", "title", "source", "prompt", "thumbnail_url", "platform",
    "author", "source_url", "similarity"}`, sorted by `similarity` descending,
    top 30.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_api_templates_search_returns_top_matches_sorted_by_similarity(client):
    with sqlite3.connect(app_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO templates (id, source, title, category, prompt, "
            "thumbnail_prompt, thumbnail_path, embedding) VALUES "
            "(1, 'official', 'Close Match', 'cat', 'p1', 'tp1', NULL, ?)",
            (json.dumps([1.0, 0.0]).encode(),),
        )
        conn.execute(
            "INSERT INTO templates (id, source, title, category, prompt, "
            "thumbnail_prompt, thumbnail_path, embedding) VALUES "
            "(2, 'community', 'Far Match', 'cat', 'p2', 'tp2', 'static/template_thumbs/2.jpg', ?)",
            (json.dumps([0.0, 1.0]).encode(),),
        )

    with patch("app.embed_text", return_value=[0.9, 0.1]):
        rv = client.get("/api/templates/search?q=test+query")

    assert rv.status_code == 200
    data = rv.get_json()
    assert [item["title"] for item in data] == ["Close Match", "Far Match"]
    assert data[0]["similarity"] > data[1]["similarity"]
    assert data[1]["thumbnail_url"] == "/static/template_thumbs/2.jpg"


def test_api_templates_search_requires_q(client):
    rv = client.get("/api/templates/search")
    assert rv.status_code == 400


def test_api_templates_search_returns_503_when_ollama_unreachable(client):
    with patch("app.embed_text", side_effect=ConnectionError("refused")):
        rv = client.get("/api/templates/search?q=anything")
    assert rv.status_code == 503
    assert "Ollama" in rv.get_json()["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_app.py -k "api_templates_search" -v`
Expected: FAIL with 404 (`/api/templates/search` doesn't exist yet)

- [ ] **Step 3: Add the route to `app.py`**

Immediately after the `/api/templates` route (added by the prior plan), add:

```python
@app.route("/api/templates/search")
def templates_search_api():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify(error="q is required"), 400

    try:
        query_vector = embed_text(query)
    except Exception:
        return jsonify(error="本地 Ollama 未啟動或缺少 bge-m3 模型，"
                             "請執行 ollama pull bge-m3 並確認 ollama serve 正在執行"), 503

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, source, title, prompt, thumbnail_path,
                   platform, author, source_url, score, embedding
            FROM templates
            WHERE embedding IS NOT NULL
        """).fetchall()

    scored = []
    for r in rows:
        vector = json.loads(r["embedding"])
        similarity = cosine_similarity(query_vector, vector)
        scored.append((similarity, r))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    results = []
    for similarity, r in scored[:30]:
        thumb_path = r["thumbnail_path"]
        results.append({
            "id": r["id"],
            "title": r["title"],
            "source": r["source"],
            "prompt": r["prompt"],
            "thumbnail_url": f"/{thumb_path}" if thumb_path else None,
            "platform": r["platform"],
            "author": r["author"],
            "source_url": r["source_url"],
            "similarity": similarity,
        })
    return jsonify(results)
```

`json` is already imported at the top of `app.py` (`app.py:16`), so no new
import is needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_app.py -v`
Expected: PASS (all tests, including the new ones)

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: add GET /api/templates/search semantic search endpoint"
```

---

### Task 5: Frontend — search box on `/templates`

**Files:**
- Modify: `templates/templates.html`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `GET /api/templates/search` (Task 4).
- Produces: no new interfaces — leaf of the chain.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_templates_page_has_search_input(client):
    rv = client.get("/templates")
    body = rv.data.decode()
    assert 'id="templateSearchInput"' in body
    assert "/api/templates/search" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_app.py::test_templates_page_has_search_input -v`
Expected: FAIL — no such element/reference yet.

- [ ] **Step 3: Add the search box markup**

In `templates/templates.html`, inside `.sidebar-header` (the block containing
the `<h2>Prompt Templates</h2>` / badge — from the unified-templates-page
plan, this now reads `<span class="badge">Templates</span>`), add a search
input right after it, still inside `<aside class="sidebar">` but before
`<div class="template-list" id="templateList">`:

```html
      <div class="sidebar-header">
        <h2>Prompt Templates</h2><span class="badge">Templates</span>
      </div>
      <div style="padding:10px 20px;border-bottom:1px solid var(--border)">
        <input type="text" id="templateSearchInput" placeholder="語意搜尋模板…"
               style="width:100%;background:var(--surface);border:1px solid var(--border);color:var(--fg);padding:8px 10px;font-size:13px;font-family:var(--font-body)" />
      </div>
      <div class="template-list" id="templateList"></div>
```

(This replaces the existing `<div class="sidebar-header">...</div>` block —
add the new `<div>` immediately after it, don't duplicate the header.)

- [ ] **Step 4: Add the search logic to the page script**

In the `<script>` block, after `renderTemplateList()` (added by the prior
unified-templates-page plan), add:

```javascript
let searchDebounceTimer = null;

document.getElementById('templateSearchInput').addEventListener('input', (e) => {
  clearTimeout(searchDebounceTimer);
  const query = e.target.value.trim();
  if (!query) {
    renderTemplateList();
    return;
  }
  searchDebounceTimer = setTimeout(() => runTemplateSearch(query), 300);
});

async function runTemplateSearch(query) {
  const list = document.getElementById('templateList');
  list.innerHTML = '<div class="tpl-item" style="color:var(--muted)">Searching...</div>';
  let results;
  try {
    const res = await fetch('/api/templates/search?q=' + encodeURIComponent(query));
    const data = await res.json();
    if (!res.ok) {
      list.innerHTML = '<div class="tpl-item" style="color:oklch(55% 0.17 25)">' + escHtml(data.error || 'Search failed') + '</div>';
      return;
    }
    results = data;
  } catch (e) {
    list.innerHTML = '<div class="tpl-item" style="color:oklch(55% 0.17 25)">Search failed: ' + escHtml(e.message) + '</div>';
    return;
  }
  window.SEARCH_RESULTS = results;
  const itemHtml = results.map((t, i) => {
    const thumbTag = t.thumbnail_url
      ? '<img src="' + escAttr(t.thumbnail_url) + '" style="width:88px;height:88px;object-fit:cover;border-radius:8px;margin-right:10px;flex-shrink:0" onerror="this.src=\'/static/template_thumb_placeholder.svg\'" loading="lazy" />'
      : '<div style="width:88px;height:88px;border-radius:8px;margin-right:10px;flex-shrink:0;background:var(--border)"></div>';
    return '<div class="tpl-item" data-search-idx="' + i +
           '" style="display:flex;align-items:center;gap:4px" title="' + escAttr(t.title) + '">' +
           thumbTag + '<span style="overflow:hidden;text-overflow:ellipsis">' + escHtml(t.title) + '</span></div>';
  }).join('');
  list.innerHTML = itemHtml || '<div class="tpl-item" style="color:var(--muted)">No matches</div>';
  list.querySelectorAll('.tpl-item[data-search-idx]').forEach(el => {
    el.addEventListener('click', () => selectSearchResult(parseInt(el.dataset.searchIdx), el));
  });
}

function selectSearchResult(idx, el) {
  document.querySelectorAll('.tpl-item').forEach(x => x.classList.remove('selected'));
  if (el) el.classList.add('selected');
  const item = window.SEARCH_RESULTS[idx];
  document.getElementById('promptInput').value = item.prompt;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_app.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add templates/templates.html tests/test_app.py
git commit -m "feat: add semantic search box to templates page"
```

---

### Task 6: Run the real embedding batch and manually verify

No automated test — this is the actual one-time data job, same shape as
Task 9 in the unified-templates-page plan.

**Files:** none (operational task only)

- [ ] **Step 1: Confirm prerequisites**

```bash
ollama list | grep bge-m3
```
Expected: `bge-m3:latest` present (already pulled at plan-writing time on this
machine — a fresh machine would need `ollama pull bge-m3` first).

- [ ] **Step 2: Sanity-check on a small sample**

```bash
venv/bin/python generate_template_embeddings.py --dry-run 10
```
Expected: `Embedded 10 rows, 0 failed. 977 total rows, 967 still missing an
embedding...`

- [ ] **Step 3: Run the full batch**

```bash
venv/bin/python generate_template_embeddings.py
```
This runs entirely locally (no external API, no quota) — expect it to be
much faster than the Gemini thumbnail batch. Check the summary line for
`still missing an embedding`; if non-zero, re-run (idempotent).

- [ ] **Step 4: Manual browser verification**

Start the server, open `/templates`, type a Chinese query with no literal
keyword overlap with any prompt text (e.g. "生日海報") into the new search
box. Confirm:
- The category list is replaced by a flat ranked list within ~300ms of
  typing.
- At least one plausible match appears (e.g. a poster/event template) even
  though "生日海報" doesn't literally appear in its prompt.
- Clearing the search box restores the normal category view.
- Clicking a search result loads its prompt into the editor.

- [ ] **Step 5: Run the full test suite**

```bash
venv/bin/python -m pytest tests/ -q
```
Expected: all tests pass.

---

## Self-Review Notes

- **Spec coverage:** Task 1 = schema, Task 2 = embedding/similarity
  primitives, Task 3 = batch embedding script, Task 4 = search endpoint,
  Task 5 = frontend search box, Task 6 = the real batch run + manual
  verification the spec's "Testing" section calls for. All spec sections
  covered.
- **Verified against the real system, not assumed:** the `/api/embed`
  request/response shape (plural `embeddings`, list-of-lists, 1024 dims) was
  confirmed with a live `curl` against the actual installed Ollama + `bge-m3`
  before writing Task 2/3's code, rather than guessed from documentation.
- **Type/name consistency check:** `embed_text` (Task 2, defined in `app.py`)
  is imported by `generate_template_embeddings.py` (Task 3) and used directly
  in the `/api/templates/search` route (Task 4) — same function, one
  definition, matching the existing `migrate_templates.py` /
  `get_client()`/`get_google_client()` reuse pattern. `cosine_similarity`
  (Task 2) is used only in Task 4's endpoint. `thumbnail_url` field naming in
  the search endpoint (Task 4) matches `/api/templates`'s existing field name
  exactly, so Task 5's frontend can reuse the same thumbnail-rendering
  snippet from the unified-templates-page plan without any shape mismatch.
