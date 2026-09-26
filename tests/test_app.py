import os
import base64
import json
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

import app as app_module


def test_health(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    d = rv.get_json()
    assert d["status"] == "ok"
    assert isinstance(d["api_key_set"], bool)


def test_index_renders(client):
    rv = client.get("/")
    assert rv.status_code == 200


def test_app_pages_render(client):
    for route in ["/studio", "/gallery", "/history-view", "/templates"]:
        rv = client.get(route)
        assert rv.status_code == 200, f"{route} returned {rv.status_code}"


def test_history_empty(client):
    rv = client.get("/history")
    assert rv.status_code == 200
    assert rv.get_json() == []


def test_search_prompts_empty_query(client):
    rv = client.get("/api/search-prompts?q=")
    assert rv.status_code == 200
    assert rv.get_json() == {"results": []}


def test_search_prompts_special_chars(client):
    """/api/search-prompts handles quotes and non-numeric limit."""
    rv = client.get("/api/search-prompts?q=%22quoted%22")
    assert rv.status_code == 200
    assert "results" in rv.get_json()
    rv = client.get("/api/search-prompts?q=cat&limit=abc")
    assert rv.status_code == 200


def test_generate_requires_prompt(client):
    rv = client.post("/generate", json={}, environ_base={"OPENAI_API_KEY": "sk-test"})
    assert rv.status_code == 400
    data = rv.get_json()
    assert "error" in data


def test_db_file_blocked(client):
    rv = client.get("/prompts.db")
    assert rv.status_code == 403


def test_fb_auth_requires_config(client):
    rv = client.get("/fb-auth-url")
    assert rv.status_code == 503


def test_nav_consistent(client):
    """All four app pages share the same nav links."""
    for route in ["/studio", "/gallery", "/history-view", "/templates"]:
        rv = client.get(route)
        assert rv.status_code == 200
        body = rv.data.decode()
        for href in ["/gallery", "/history-view", "/studio", "/templates"]:
            assert f'href="{href}"' in body, f"{route} missing nav link: {href}"


def test_fb_post_requires_token_or_pid(client):
    """fb-post: missing token → 401; pid with image → reaches FB API."""
    # 1. No token, no pid → 401 (token check comes first)
    rv = client.post("/fb-post", json={})
    assert rv.status_code == 401

    # 2. pid that doesn't exist → 404
    rv = client.post("/fb-post", json={"pid": 99999, "page_id": "test"})
    assert rv.status_code == 404

    # 3. Insert a prompt with result_b64, then test with a mock page token
    small_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+P+/HgAF3wIM4+Rc7AAAAABJRU5ErkJggg=="
    )
    with sqlite3.connect(app_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO prompts (prompt, result_b64, success) VALUES (?, ?, 1)",
            ("test prompt", small_png),
        )
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Create a mock page token file
    token_file = tempfile.mktemp()
    with open(token_file, "w") as f:
        f.write("test_page:Test Page:a-test-token\n")
    old_token_file = app_module.FB_PAGE_TOKEN_FILE
    app_module.FB_PAGE_TOKEN_FILE = token_file

    try:
        with patch("app.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"id": "fb-post-123"}
            mock_post.return_value = mock_resp

            rv = client.post(
                "/fb-post",
                json={"pid": pid, "page_id": "test_page", "message": "hello"},
            )
            assert rv.status_code == 200
            data = rv.get_json()
            assert data["status"] == "ok"
            assert data["post_id"] == "fb-post-123"

            # Verify the request was made with multipart
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "files" in call_args[1], "fb_post should use multipart upload"
    finally:
        app_module.FB_PAGE_TOKEN_FILE = old_token_file


def test_fb_pages_shape(client):
    """/fb-pages returns {pages: [{id, name}, ...]}."""
    token_file = tempfile.mktemp()
    with open(token_file, "w") as f:
        f.write("pid1:Page One:tok1\n")
    old = app_module.FB_PAGE_TOKEN_FILE
    app_module.FB_PAGE_TOKEN_FILE = token_file
    try:
        rv = client.get("/fb-pages")
        assert rv.status_code == 200
        assert rv.get_json() == {"pages": [{"id": "pid1", "name": "Page One"}]}
    finally:
        app_module.FB_PAGE_TOKEN_FILE = old

    # Missing token file → 400
    app_module.FB_PAGE_TOKEN_FILE = "/nonexistent/path"
    try:
        rv = client.get("/fb-pages")
        assert rv.status_code == 400
        assert "error" in rv.get_json()
    finally:
        app_module.FB_PAGE_TOKEN_FILE = old


def test_per_image_cost_recorded(client):
    """Each generated image row stores per-image cost, not batch total."""
    small_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+P+/HgAF3wIM4+Rc7AAAAABJRU5ErkJggg=="
    )
    item = MagicMock(url=None, b64_json=small_png, revised_prompt=None)
    fake_resp = MagicMock(data=[item, item])  # n=2

    with patch("app.get_client") as gc:
        gc.return_value.images.generate.return_value = fake_resp
        app_module._jobs["j1"] = {"status": "running", "created_at": 0}
        app_module._run_generation("j1", "p", "", "medium", "1024x1024", 2, "gpt-image-2")

    per_image = app_module.calc_cost("gpt-image-2", "medium", "1024x1024", 1)
    batch_total = app_module.calc_cost("gpt-image-2", "medium", "1024x1024", 2)

    with sqlite3.connect(app_module.DB_PATH) as conn:
        rows = conn.execute("SELECT cost_usd FROM prompts ORDER BY id").fetchall()
    assert len(rows) == 2
    for r in rows:
        assert abs(r[0] - per_image) < 1e-6, f"expected per-image {per_image}, got {r[0]}"

    results = app_module._jobs["j1"]["results"]
    assert len(results) == 2
    for res in results:
        assert abs(res["cost_usd"] - per_image) < 1e-6
    assert abs(sum(r["cost_usd"] for r in results) - batch_total) < 1e-6


def test_set_key_preserves_other_keys(client):
    """/set-key updates OPENAI_API_KEY without destroying other keys."""
    token_file = tempfile.mktemp()
    with open(token_file, "w") as f:
        f.write("GOOGLE_API_KEY=g-test\nOPENAI_API_KEY=sk-old\n")

    old_key_file = app_module.KEY_FILE
    old_env = os.environ.get("OPENAI_API_KEY")
    app_module.KEY_FILE = token_file

    try:
        rv = client.post("/set-key", json={"api_key": "sk-newkey123"})
        assert rv.status_code == 200
        with open(token_file) as f:
            content = f.read()
        assert "GOOGLE_API_KEY=g-test" in content
        assert "OPENAI_API_KEY=sk-newkey123" in content
        assert "sk-old" not in content
    finally:
        app_module.KEY_FILE = old_key_file
        if old_env is not None:
            os.environ["OPENAI_API_KEY"] = old_env
        elif "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]


def test_delete_all_history(client):
    """DELETE /history removes all rows and returns count."""
    with sqlite3.connect(app_module.DB_PATH) as conn:
        for i in range(3):
            conn.execute("INSERT INTO prompts (prompt) VALUES (?)", (f"test {i}",))
    rv = client.delete("/history")
    assert rv.status_code == 200
    assert rv.get_json() == {"status": "ok", "deleted": 3}
    rv = client.get("/history")
    assert rv.get_json() == []


def test_use_history_roundtrip(client):
    """POST /use-history/<pid> returns prompt and image_b64."""
    small_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+P+/HgAF3wIM4+Rc7AAAAABJRU5ErkJggg=="
    )
    with sqlite3.connect(app_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO prompts (prompt, image_b64) VALUES (?, ?)",
            ("test prompt", small_png),
        )
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    rv = client.post(f"/use-history/{pid}")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["prompt"] == "test prompt"
    assert "image_b64" in data
    rv = client.post("/use-history/999999")
    assert rv.status_code == 404


def test_api_models_shape(client):
    """GET /api/models returns well-formed model list."""
    rv = client.get("/api/models")
    assert rv.status_code == 200
    body = rv.get_json()
    assert "models" in body
    assert isinstance(body["models"], list)
    assert len(body["models"]) >= 2
    assert isinstance(body["google_key_set"], bool)
    for m in body["models"]:
        for key in ["id", "name", "provider", "qualities", "sizes", "max_n", "supports_edit",
                    "supports_custom_size", "cost_table"]:
            assert key in m, f"missing key {key} in model {m.get('id', '?')}"
        assert len(m["qualities"]) > 0
        for q in m["qualities"]:
            assert q in m["cost_table"], f"quality {q} not in cost_table for {m['id']}"
    # Gemini contract: both 1792x1024 and 1024x1792 in cost_table["standard"]
    gemini = [m for m in body["models"] if m["id"] == "gemini-3.1-flash-lite-image"][0]
    ct = gemini["cost_table"]["standard"]
    assert "1792x1024" in ct
    assert "1024x1792" in ct


def test_generate_rejects_unknown_model(client, monkeypatch):
    """/generate returns 400 for unknown model."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    rv = client.post("/generate", json={"prompt": "x", "model": "nope"})
    assert rv.status_code == 400
    assert "Unknown model" in rv.get_json()["error"]


def test_generate_coerces_invalid_quality(client, monkeypatch):
    """/generate coerces invalid quality to model's first quality."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with patch("app.threading.Thread") as mock_thread:
        rv = client.post("/generate", json={
            "prompt": "x", "model": "gemini-3.1-flash-lite-image",
            "quality": "high", "size": "1024x1024"
        })
        assert rv.status_code == 200
        assert "job_id" in rv.get_json()
        args = mock_thread.call_args.kwargs["args"]
        # args: (job_id, prompt, image_b64, quality, size, n, model, original_prompt)
        assert args[3] == "standard"


def test_gemini_generation_saves_results(client):
    """Gemini generation path saves results correctly."""
    part = MagicMock()
    part.inline_data.mime_type = "image/png"
    part.inline_data.data = b"\x89PNG fake"
    cand = MagicMock()
    cand.content.parts = [part]
    fake_resp = MagicMock(candidates=[cand])
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_resp

    with patch("app.get_google_client", return_value=fake_client):
        app_module._jobs["gj"] = {"status": "running", "created_at": 0}
        app_module._run_generation("gj", "a cat", "", "standard",
                                    "1792x1024", 2, "gemini-3.1-flash-lite-image")

    job = app_module._jobs["gj"]
    assert job["status"] == "done"
    assert len(job["results"]) == 2
    for r in job["results"]:
        assert r["url"].startswith("data:image/png;base64,")
        per_img = app_module.calc_cost("gemini-3.1-flash-lite-image", "standard", "1792x1024", 1)
        assert abs(r["cost_usd"] - per_img) < 1e-6

    with sqlite3.connect(app_module.DB_PATH) as conn:
        rows = conn.execute("SELECT model, success FROM prompts").fetchall()
    assert len(rows) == 2
    for r in rows:
        assert r[0] == "gemini-3.1-flash-lite-image"
        assert r[1] == 1


def test_gemini_without_key_fails_job(client):
    """Gemini generation without GOOGLE_API_KEY marks job failed."""
    with patch("app.get_google_client", return_value=None):
        app_module._jobs["gf"] = {"status": "running", "created_at": 0}
        app_module._run_generation("gf", "p", "", "standard",
                                    "1024x1024", 1, "gemini-3.1-flash-lite-image")
    job = app_module._jobs["gf"]
    assert job["status"] == "failed"
    assert "GOOGLE_API_KEY" in job["error"]
    with sqlite3.connect(app_module.DB_PATH) as conn:
        row = conn.execute("SELECT success FROM prompts").fetchone()
    assert row[0] == 0


def test_job_status_unknown(client):
    """GET /job-status/<unknown> returns 404."""
    rv = client.get("/job-status/doesnotexist")
    assert rv.status_code == 404


def test_templates_table_created(client):
    """init_db() creates the templates table with the expected columns."""
    with sqlite3.connect(app_module.DB_PATH) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()}
    assert cols == {
        "id", "source", "title", "category", "prompt", "thumbnail_prompt",
        "thumbnail_path", "platform", "author", "source_url", "score",
        "community_prompt_id", "embedding",
    }


def test_api_templates_groups_by_category_and_nulls_missing_thumbnail(client):
    with sqlite3.connect(app_module.DB_PATH) as conn:
        conn.execute(
            """INSERT INTO templates
                   (source, title, category, prompt, thumbnail_prompt, thumbnail_path)
               VALUES ('official', 'T1', 'UI 與介面設計 UI / App Interfaces', 'p1', 'tp1', NULL)"""
        )
        conn.execute(
            """INSERT INTO templates
                   (source, title, category, prompt, thumbnail_prompt, thumbnail_path,
                    platform, author, source_url, score)
               VALUES ('community', 'T2', '人像攝影 Portrait & Fashion Photography', 'p2', 'tp2',
                       'static/template_thumbs/2.jpg', 'Instagram', 'someone', 'https://x.test', 5)"""
        )

    rv = client.get("/api/templates")
    assert rv.status_code == 200
    data = rv.get_json()

    assert "UI 與介面設計 UI / App Interfaces" in data
    assert data["UI 與介面設計 UI / App Interfaces"][0]["thumbnail_url"] is None

    portrait_items = data["人像攝影 Portrait & Fashion Photography"]
    assert portrait_items[0]["thumbnail_url"] == "/static/template_thumbs/2.jpg"
    assert portrait_items[0]["source"] == "community"
    assert portrait_items[0]["platform"] == "Instagram"


def test_templates_page_has_no_hardcoded_templates_object(client):
    """The old hardcoded TEMPLATES JS object must be gone — page now fetches
    everything from /api/templates."""
    rv = client.get("/templates")
    body = rv.data.decode()
    assert "const TEMPLATES = {" not in body
    assert "/api/templates" in body


def test_templates_table_has_embedding_column(client):
    with sqlite3.connect(app_module.DB_PATH) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()}
    assert "embedding" in cols


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


def test_api_templates_search_skips_row_with_malformed_embedding(client):
    with sqlite3.connect(app_module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO templates (id, source, title, category, prompt, "
            "thumbnail_prompt, thumbnail_path, embedding) VALUES "
            "(1, 'official', 'Good Match', 'cat', 'p1', 'tp1', NULL, ?)",
            (json.dumps([1.0, 0.0]).encode(),),
        )
        conn.execute(
            "INSERT INTO templates (id, source, title, category, prompt, "
            "thumbnail_prompt, thumbnail_path, embedding) VALUES "
            "(2, 'community', 'Bad Match', 'cat', 'p2', 'tp2', NULL, ?)",
            (b"not valid json",),
        )

    with patch("app.embed_text", return_value=[0.9, 0.1]):
        rv = client.get("/api/templates/search?q=test+query")

    assert rv.status_code == 200
    data = rv.get_json()
    titles = [item["title"] for item in data]
    assert "Good Match" in titles
    assert "Bad Match" not in titles


def test_api_templates_search_requires_q(client):
    rv = client.get("/api/templates/search")
    assert rv.status_code == 400


def test_api_templates_search_returns_503_when_ollama_unreachable(client):
    with patch("app.embed_text", side_effect=ConnectionError("refused")):
        rv = client.get("/api/templates/search?q=anything")
    assert rv.status_code == 503
    assert "Ollama" in rv.get_json()["error"]


def test_templates_page_has_search_input(client):
    rv = client.get("/templates")
    body = rv.data.decode()
    assert 'id="templateSearchInput"' in body
    assert "/api/templates/search" in body


def test_templates_page_has_no_leftover_collapsible_category_list(client):
    """The old always-visible 14-category collapsible sidebar list is gone —
    replaced by the category-card grid landing view."""
    rv = client.get("/templates")
    body = rv.data.decode()
    assert "renderTemplateList" not in body
    assert "toggleCat" not in body


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


# ── GPT Image 2.5 (sunburst / flare) ────────────────────────────────────────

def test_new_models_config_well_formed():
    for mid in ("gpt-image-2.5-sunburst", "gpt-image-2.5-flare"):
        cfg = app_module._MODELS[mid]
        assert cfg["provider"] == "openai"
        assert set(["low", "medium", "high", "xhigh", "max"]).issubset(cfg["qualities"])
        assert cfg["supports_custom_size"] is True
        for q in cfg["qualities"]:
            assert q in cfg["cost_table"], f"{mid} missing cost_table entry for {q}"


def test_gpt_image_2_supports_custom_size():
    assert app_module._MODELS["gpt-image-2"]["supports_custom_size"] is True


def test_calc_cost_for_new_model_preset_size():
    c = app_module.calc_cost("gpt-image-2.5-sunburst", "medium", "1024x1024", 1)
    assert c == app_module.calc_cost("gpt-image-2", "medium", "1024x1024", 1)


def test_calc_cost_custom_size_scales_with_pixels():
    small = app_module.calc_cost("gpt-image-2.5-sunburst", "high", "1024x1024", 1)
    large = app_module.calc_cost("gpt-image-2.5-sunburst", "high", "2048x1152", 1)
    assert large > small
    assert large != 0.042


def test_run_generation_for_sunburst_model():
    small_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "+P+/HgAF3wIM4+Rc7AAAAABJRU5ErkJggg=="
    )
    item = MagicMock(url=None, b64_json=small_png, revised_prompt=None)
    fake_resp = MagicMock(data=[item])
    with patch("app.get_client") as gc:
        gc.return_value.images.generate.return_value = fake_resp
        app_module._jobs["j2"] = {"status": "running", "created_at": 0}
        app_module._run_generation("j2", "p", "", "high", "1024x1024", 1, "gpt-image-2.5-sunburst")
    assert app_module._jobs["j2"]["status"] == "done"


def test_validate_custom_size_valid_preset_shape():
    ok, err, exp = app_module.validate_custom_size("1024x1024")
    assert ok and err is None and exp is False


def test_validate_custom_size_valid_custom_multiple_of_16():
    ok, err, exp = app_module.validate_custom_size("1600x1200")
    assert ok and err is None and exp is False


def test_validate_custom_size_rejects_non_multiple_of_16():
    ok, err, _ = app_module.validate_custom_size("1023x1023")
    assert not ok and "16" in err


def test_validate_custom_size_rejects_bad_aspect_ratio():
    ok, err, _ = app_module.validate_custom_size("3008x512")  # ~5.9:1
    assert not ok and "ratio" in err.lower()


def test_validate_custom_size_rejects_over_max():
    ok, err, _ = app_module.validate_custom_size("4096x2160")
    assert not ok and "3840x2160" in err


def test_validate_custom_size_flags_experimental_but_allows():
    ok, err, exp = app_module.validate_custom_size("3840x2160")
    assert ok and err is None and exp is True


def test_validate_size_for_model_rejects_custom_for_unsupported_model():
    cfg = app_module._MODELS["gemini-3.1-flash-lite-image"]
    ok, err = app_module.validate_size_for_model(cfg, "1600x1200")
    assert not ok and err


def test_generate_rejects_invalid_custom_size(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    rv = client.post("/generate", json={
        "prompt": "a cat", "model": "gpt-image-2.5-sunburst",
        "quality": "high", "size": "1023x1023",
    })
    assert rv.status_code == 400
    assert "16" in rv.get_json()["error"]


def _mock_enhance_client(content):
    fake = MagicMock()
    fake.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=content))
    ]
    return fake


def _sent_text(fake):
    content = fake.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    return content if isinstance(content, str) else content[0]["text"]


def test_enhance_prompt_uses_structured_rules_for_25(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = _mock_enhance_client('Use: poster\nText in image: "秋季限定"')
    with patch("app.get_client", return_value=fake):
        rv = client.post("/enhance-prompt", json={
            "prompt": "咖啡店海報，上面寫「秋季限定」", "model": "gpt-image-2.5-sunburst"})
    assert rv.status_code == 200
    kwargs = fake.chat.completions.create.call_args.kwargs
    assert "Constraints:" in _sent_text(fake)
    assert kwargs["max_completion_tokens"] == 800
    # closing quote of in-image text must survive
    assert rv.get_json()["enhanced"].endswith('"秋季限定"')


def test_enhance_prompt_25_with_image_sends_rules_and_image(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = _mock_enhance_client("Use: portrait")
    with patch("app.get_client", return_value=fake):
        rv = client.post("/enhance-prompt", json={
            "prompt": "astronaut", "image_b64": "aGVsbG8=", "model": "gpt-image-2.5-flare"})
    assert rv.status_code == 200
    content = fake.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "Constraints:" in content[0]["text"]
    assert content[1]["type"] == "image_url"


def test_enhance_prompt_keeps_legacy_rules_for_other_models(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for body in ({"prompt": "a cat", "model": "gpt-image-2"}, {"prompt": "a cat"}):
        fake = _mock_enhance_client('"A fluffy cat"')
        with patch("app.get_client", return_value=fake):
            rv = client.post("/enhance-prompt", json=body)
        assert rv.status_code == 200
        assert "one paragraph" in _sent_text(fake)
        assert "Constraints:" not in _sent_text(fake)
        assert fake.chat.completions.create.call_args.kwargs["max_completion_tokens"] == 500
        # whole-output wrapping quotes are still removed
        assert rv.get_json()["enhanced"] == "A fluffy cat"


def test_enhance_prompt_model_is_configurable(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = _mock_enhance_client("a cat")
    with patch("app.get_client", return_value=fake):
        rv = client.post("/enhance-prompt", json={"prompt": "a cat"})
    kwargs = fake.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4o" and kwargs["temperature"] == 0.7
    assert rv.get_json()["enhance_model"] == "gpt-4o"

    monkeypatch.setattr(app_module, "ENHANCE_MODEL", "gpt-5.5")
    fake = _mock_enhance_client("a cat")
    with patch("app.get_client", return_value=fake):
        rv = client.post("/enhance-prompt", json={"prompt": "a cat"})
    kwargs = fake.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.5" and "temperature" not in kwargs
    assert rv.get_json()["enhance_model"] == "gpt-5.5"


def test_enhance_prompt_refusal_returns_422(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake = _mock_enhance_client("I'm sorry, I can't help with that.")
    with patch("app.get_client", return_value=fake):
        rv = client.post("/enhance-prompt", json={
            "prompt": "x", "model": "gpt-image-2.5-sunburst"})
    assert rv.status_code == 422


# ── Per-provider API key check ────────────────────────────────────────────────
_GEMINI = "gemini-3.1-flash-lite-image"


def test_generate_gemini_without_openai_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    with patch("app.threading.Thread"):
        rv = client.post("/generate", json={
            "prompt": "x", "model": _GEMINI, "size": "1024x1024"})
    assert rv.status_code == 200
    assert "job_id" in rv.get_json()


def test_generate_openai_without_key_500(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rv = client.post("/generate", json={
        "prompt": "x", "model": "gpt-image-2", "size": "1024x1024"})
    assert rv.status_code == 500
    assert "OPENAI_API_KEY" in rv.get_json()["error"]


def test_generate_gemini_without_google_key_500(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "GOOGLE_API_KEY", "")
    rv = client.post("/generate", json={
        "prompt": "x", "model": _GEMINI, "size": "1024x1024"})
    assert rv.status_code == 500
    assert "GOOGLE_API_KEY" in rv.get_json()["error"]


def test_api_v1_generate_gemini_without_openai_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    with patch("app.threading.Thread"):
        rv = client.post("/api/v1/generate", json={
            "prompt": "x", "model": _GEMINI, "size": "1024x1024"})
    assert rv.status_code == 202
    assert "job_id" in rv.get_json()


def test_batch_generate_gemini_without_openai_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    with patch("app.threading.Thread"):
        rv = client.post("/batch-generate", json={
            "prompts": ["a"], "model": _GEMINI, "size": "1024x1024"})
    assert rv.status_code == 200
