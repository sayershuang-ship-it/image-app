import json
import os

JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "official_templates.json")


def test_official_templates_json_exists_and_has_99_items():
    assert os.path.exists(JSON_PATH), "run: node extract_official_templates.js"
    with open(JSON_PATH) as f:
        data = json.load(f)
    assert len(data) == 99
    for item in data:
        assert set(item.keys()) == {"tab", "subcategory", "title", "prompt"}
        assert item["prompt"].strip() != ""


def test_known_template_present():
    with open(JSON_PATH) as f:
        data = json.load(f)
    titles = {item["title"] for item in data}
    assert "35mm Film Portrait" in titles
    assert "Photo to Designer Toy" in titles  # appears twice (Character Design tab + Gallery Examples tab) — both kept, that's fine
