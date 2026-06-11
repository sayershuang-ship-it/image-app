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
