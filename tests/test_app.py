import os


def test_health(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    assert rv.get_json() == {"status": "ok"}


def test_index_renders(client):
    rv = client.get("/")
    assert rv.status_code == 200


def test_app_pages_render(client):
    for route in ["/studio", "/gallery", "/history-view", "/templates", "/spark"]:
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
