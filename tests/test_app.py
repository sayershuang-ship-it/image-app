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
    assert rv.get_json() == {"status": "ok"}


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
        for key in ["id", "name", "provider", "qualities", "sizes", "max_n", "supports_edit", "cost_table"]:
            assert key in m, f"missing key {key} in model {m.get('id', '?')}"
        assert len(m["qualities"]) > 0
        for q in m["qualities"]:
            assert q in m["cost_table"], f"quality {q} not in cost_table for {m['id']}"
    # Gemini contract: both 1792x1024 and 1024x1792 in cost_table["standard"]
    gemini = [m for m in body["models"] if m["id"] == "gemini-3.1-flash-lite-image"][0]
    ct = gemini["cost_table"]["standard"]
    assert "1792x1024" in ct
    assert "1024x1792" in ct
