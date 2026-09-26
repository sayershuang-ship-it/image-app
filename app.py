"""
GPT Image 2 Generator with Prompt Memory
Features:
  - Upload image → GPT-4o vision analyzes → enhanced prompt
  - Prompt history stored in SQLite
  - Modern dark UI
Usage: OPENAI_API_KEY=... python app.py
"""

import os
import io
import re
import sqlite3
import base64
import time
import json
import uuid
import threading
from datetime import datetime
from functools import wraps

import requests
from flask import Flask, render_template, request, jsonify, send_file, abort, make_response
from markupsafe import escape
from PIL import Image
from openai import OpenAI

# ── Config ──────────────────────────────────────────────────────────────────────
API_KEY    = os.environ.get("OPENAI_API_KEY", "")
PORT       = 5001
DB_PATH    = os.path.join(os.path.dirname(__file__), "prompts.db")
ENHANCE_MODEL = os.environ.get("ENHANCE_MODEL", "gpt-4o")  # LLM used by /enhance-prompt

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))

# ── Facebook Config ────────────────────────────────────────────────────────────
FB_APP_ID     = os.environ.get("FB_APP_ID", "")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
FB_REDIRECT   = "http://localhost:5001/fb-callback"
FB_PAGE_TOKEN_FILE = os.path.expanduser("~/.hermes/fb_page_token")
KEY_FILE   = os.path.expanduser("~/.image-studio.env")

# Load persisted API key on startup if not already set
if not API_KEY and os.path.exists(KEY_FILE):
    with open(KEY_FILE) as f:
        for line in f:
            if line.startswith("OPENAI_API_KEY="):
                API_KEY = line.strip().split("=", 1)[1].strip('"').strip("'")
                os.environ["OPENAI_API_KEY"] = API_KEY
                break

# ── Google Gemini Config ──────────────────────────────────────────────────────────
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not GOOGLE_API_KEY and os.path.exists(KEY_FILE):
    with open(KEY_FILE) as f:
        for line in f:
            if line.startswith("GOOGLE_API_KEY="):
                GOOGLE_API_KEY = line.strip().split("=", 1)[1].strip('"').strip("'")
                os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
                break

_google_client = None

def get_google_client():
    global _google_client
    if _google_client is None and GOOGLE_API_KEY:
        from google import genai as _genai
        _google_client = _genai.Client(api_key=GOOGLE_API_KEY)
    return _google_client


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


def _upsert_key_file(name: str, value: str) -> None:
    """Update or append NAME=value in KEY_FILE, preserving other lines."""
    lines = []
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            lines = [l.rstrip("\n") for l in f]
    prefix = f"{name}="
    lines = [l for l in lines if not l.startswith(prefix)]
    lines.append(f"{name}={value}")
    with open(KEY_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")

# ── Job Queue (async generation) ──────────────────────────────────────────────
_jobs: dict = {}          # job_id → {status, results, error, created_at}
_jobs_lock = threading.Lock()
JOB_TTL = 3600            # seconds before a finished job is eligible for cleanup

def _cleanup_old_jobs():
    cutoff = time.time() - JOB_TTL
    with _jobs_lock:
        expired = [jid for jid, j in _jobs.items()
                   if j.get("created_at", 0) < cutoff
                   and j["status"] in ("done", "failed")]
        for jid in expired:
            del _jobs[jid]

# ── Block direct db file access ─────────────────────────────────────────────────
@app.before_request
def block_db_access():
    if request.path == "/prompts.db":
        abort(403)

# ── DB Setup ───────────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt         TEXT    NOT NULL,
                original_prompt TEXT,
                image_b64      TEXT,
                revised_prompt TEXT,
                quality        TEXT,
                size           TEXT,
                model          TEXT,
                prompt_ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                result_url     TEXT,
                result_b64     TEXT,
                success        INTEGER DEFAULT 1,
                error_msg      TEXT,
                cost_usd       REAL,
                starred        INTEGER DEFAULT 0,
                tags           TEXT
            )
        """)
        # Migrate existing tables: add new columns if absent
        existing = {r[1] for r in conn.execute("PRAGMA table_info(prompts)").fetchall()}
        for col, definition in [
            ("original_prompt", "TEXT"),
            ("cost_usd",        "REAL"),
            ("starred",         "INTEGER DEFAULT 0"),
            ("tags",            "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE prompts ADD COLUMN {col} {definition}")
        # Ensure community_prompts table exists (also created by sync_awesome_prompts.py)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS community_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                category TEXT,
                platform TEXT,
                author TEXT,
                image_url TEXT,
                source_url TEXT,
                prompt TEXT,
                score INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # FTS index for community_prompts
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS community_prompts_fts
            USING fts5(title, prompt, category, content='community_prompts', content_rowid='id')
        """)
        conn.execute("""
            INSERT OR IGNORE INTO community_prompts_fts(rowid, title, prompt, category)
            SELECT id, title, prompt, category FROM community_prompts
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL CHECK(source IN ('official', 'community')),
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL,
                thumbnail_prompt TEXT NOT NULL DEFAULT '',
                thumbnail_path TEXT,
                platform TEXT,
                author TEXT,
                source_url TEXT,
                score INTEGER,
                community_prompt_id INTEGER
            )
        """)
        existing_template_cols = {r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()}
        if "embedding" not in existing_template_cols:
            conn.execute("ALTER TABLE templates ADD COLUMN embedding BLOB")

# ── Helpers ────────────────────────────────────────────────────────────────────
THUMB_MAX = 512   # max dimension for stored thumbnails
THUMB_QUALITY = 75

_FORMAT_EXT = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
               "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}

def detect_image_format(img_bytes: bytes) -> tuple:
    """Return (extension, mimetype) by sniffing the bytes; default PNG."""
    try:
        fmt = Image.open(io.BytesIO(img_bytes)).format
    except Exception:
        fmt = None
    return _FORMAT_EXT.get(fmt, ("png", "image/png"))

def make_thumbnail(b64_data: str) -> str:
    """Resize base64 image to max THUMB_MAX px, return smaller base64 JPEG."""
    if not b64_data:
        return b64_data
    try:
        img_bytes = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        w, h = img.size
        if w > THUMB_MAX or h > THUMB_MAX:
            ratio = min(THUMB_MAX / w, THUMB_MAX / h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=THUMB_QUALITY)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return b64_data   # fallback: keep original

def get_client():
    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def api_key_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not os.environ.get("OPENAI_API_KEY"):
            return jsonify(error="OPENAI_API_KEY not set"), 500
        return f(*args, **kwargs)
    return decorated

_PROVIDER_KEY_ENV = {"openai": "OPENAI_API_KEY", "google": "GOOGLE_API_KEY"}

def _missing_key_for_model(model: str):
    """Return the env-var name of the missing key for this model's provider, or None."""
    provider = _MODELS.get(model, {}).get("provider", "openai")
    env = _PROVIDER_KEY_ENV.get(provider, "OPENAI_API_KEY")
    value = os.environ.get(env) or (GOOGLE_API_KEY if env == "GOOGLE_API_KEY" else "")
    return None if value else env

# ── Model definitions & pricing ──────────────────────────────────────────────────
_MODELS = {
    "gpt-image-2": {
        "name": "GPT Image 2",
        "provider": "openai",
        "qualities": ["low", "medium", "high"],
        "sizes": ["1024x1024", "1792x1024", "1024x1792", "1536x1024", "1024x1536", "2048x2048"],
        "max_n": 4,
        "supports_edit": True,
        "supports_custom_size": True,  # OpenAI SDK >=3.10: arbitrary WxH also works for gpt-image-2
        "cost_table": {
            "low":    {"1024x1024": 0.011, "1024x1792": 0.016, "1792x1024": 0.016},
            "medium": {"1024x1024": 0.042, "1024x1792": 0.063, "1792x1024": 0.063},
            "high":   {"1024x1024": 0.167, "1024x1792": 0.250, "1792x1024": 0.250,
                       "2048x2048": 0.167},
        },
    },
    "gpt-image-2.5-sunburst": {
        "name": "GPT Image 2.5 Sunburst",
        "provider": "openai",
        "qualities": ["low", "medium", "high", "xhigh", "max"],
        "sizes": ["1024x1024", "1792x1024", "1024x1792", "1536x1024", "1024x1536", "2048x2048"],
        "max_n": 4,
        "supports_edit": True,
        "supports_custom_size": True,
        # NOTE: cost_table reuses gpt-image-2's per-size prices as an ESTIMATE —
        # OpenAI has not published gpt-image-2.5 pricing as of 2026-09-10. xhigh/max
        # reuse the 'high' row (no basis to guess a specific multiplier); replace
        # once OpenAI publishes real numbers.
        "cost_table": {
            "low":    {"1024x1024": 0.011, "1024x1792": 0.016, "1792x1024": 0.016},
            "medium": {"1024x1024": 0.042, "1024x1792": 0.063, "1792x1024": 0.063},
            "high":   {"1024x1024": 0.167, "1024x1792": 0.250, "1792x1024": 0.250,
                       "2048x2048": 0.167},
            "xhigh":  {"1024x1024": 0.167, "1024x1792": 0.250, "1792x1024": 0.250,
                       "2048x2048": 0.167},
            "max":    {"1024x1024": 0.167, "1024x1792": 0.250, "1792x1024": 0.250,
                       "2048x2048": 0.167},
        },
    },
    "gpt-image-2.5-flare": {
        "name": "GPT Image 2.5 Flare",
        "provider": "openai",
        "qualities": ["low", "medium", "high", "xhigh", "max"],
        "sizes": ["1024x1024", "1792x1024", "1024x1792", "1536x1024", "1024x1536", "2048x2048"],
        "max_n": 4,
        "supports_edit": True,
        "supports_custom_size": True,
        # NOTE: same estimate caveat as gpt-image-2.5-sunburst above.
        "cost_table": {
            "low":    {"1024x1024": 0.011, "1024x1792": 0.016, "1792x1024": 0.016},
            "medium": {"1024x1024": 0.042, "1024x1792": 0.063, "1792x1024": 0.063},
            "high":   {"1024x1024": 0.167, "1024x1792": 0.250, "1792x1024": 0.250,
                       "2048x2048": 0.167},
            "xhigh":  {"1024x1024": 0.167, "1024x1792": 0.250, "1792x1024": 0.250,
                       "2048x2048": 0.167},
            "max":    {"1024x1024": 0.167, "1024x1792": 0.250, "1792x1024": 0.250,
                       "2048x2048": 0.167},
        },
    },
    "gemini-3.1-flash-lite-image": {
        "name": "Gemini 3.1 Flash Lite Image",
        "provider": "google",
        "qualities": ["standard"],
        "sizes": ["1024x1024", "1792x1024", "1024x1792", "1536x1024", "1024x1536"],
        "max_n": 4,
        "supports_edit": False,
        "cost_table": {
            "standard": {"1024x1024": 0.001, "1792x1024": 0.001, "1024x1792": 0.001,
                         "1536x1024": 0.001, "1024x1536": 0.001},
        },
    },
}

# Size → Gemini aspect ratio mapping
_GEMINI_ASPECT_MAP = {
    "1024x1024": "1:1",
    "1792x1024": "16:9",
    "1024x1792": "9:16",
    "1536x1024": "4:3",
    "1024x1536": "3:4",
}

_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")

def validate_custom_size(size: str) -> tuple:
    """Validate a WIDTHxHEIGHT string against OpenAI's gpt-image-2.5 rules
    (openai-python v3.10.0 / openai-node v7.12.0 release notes):
      - both dimensions divisible by 16
      - aspect ratio between 1:3 and 3:1
      - max 3840x2160
      - anything above 2560x1440 is flagged "experimental" but still allowed

    Returns (is_valid, error, is_experimental).
    """
    m = _SIZE_RE.match(size or "")
    if not m:
        return False, f"Invalid size format: {size!r} (expected WIDTHxHEIGHT)", False
    w, h = int(m.group(1)), int(m.group(2))
    if w == 0 or h == 0:
        return False, "Width and height must be positive", False
    if w % 16 != 0 or h % 16 != 0:
        return False, f"Width and height must each be a multiple of 16 (got {w}x{h})", False
    ratio = w / h
    if ratio < (1 / 3) or ratio > 3:
        return False, f"Aspect ratio must be between 1:3 and 3:1 (got {w}:{h})", False
    if w > 3840 or h > 2160:
        return False, f"Size exceeds the maximum of 3840x2160 (got {w}x{h})", False
    return True, None, (w > 2560 or h > 1440)

def validate_size_for_model(model_config: dict, size: str) -> tuple:
    """size is OK if it's one of the model's presets, or — when the model has
    supports_custom_size — a valid custom WIDTHxHEIGHT. Returns (ok, error)."""
    if size in model_config["sizes"]:
        return True, None
    if not model_config.get("supports_custom_size"):
        return False, f"Size {size!r} is not supported by this model"
    ok, err, _is_experimental = validate_custom_size(size)
    return (True, None) if ok else (False, err)

def _pixel_count(size: str):
    m = _SIZE_RE.match(size or "")
    return int(m.group(1)) * int(m.group(2)) if m else None

def calc_cost(model: str, quality: str, size: str, n: int = 1) -> float:
    model_table = _MODELS.get(model, {}).get("cost_table", {})
    quality_table = model_table.get(quality, {})
    default = 0.042
    if size in quality_table:
        return round(quality_table[size] * n, 4)
    # Custom/unknown size: scale the nearest known size's price (same model +
    # quality) by pixel-count ratio, instead of reporting a flat default that
    # would badly under-estimate a large custom request (e.g. 3840x2160).
    px = _pixel_count(size)
    if px and quality_table:
        best_size, best_price = min(
            quality_table.items(),
            key=lambda kv: abs((_pixel_count(kv[0]) or 0) - px)
        )
        best_px = _pixel_count(best_size)
        if best_px:
            return round(best_price * (px / best_px) * n, 4)
    return round(default * n, 4)

def save_prompt(prompt, image_b64, revised, quality, size, model,
                result_url, result_b64, success, error_msg="",
                original_prompt=None, cost_usd=None) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("""
            INSERT INTO prompts
                (prompt, original_prompt, image_b64, revised_prompt, quality, size, model,
                 result_url, result_b64, success, error_msg, cost_usd)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (prompt, original_prompt, image_b64 or None, revised, quality, size, model,
              result_url, result_b64, int(success), error_msg, cost_usd))
        return cur.lastrowid

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/history", methods=["GET"])
def history():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, prompt, original_prompt, revised_prompt, quality, size, model,
                   prompt_ts, result_url, success, error_msg, cost_usd,
                   COALESCE(starred, 0) as starred, tags,
                   CASE WHEN image_b64 IS NOT NULL THEN 1 ELSE 0 END as has_image,
                   CASE WHEN result_b64 IS NOT NULL THEN 1 ELSE 0 END as has_result
            FROM prompts ORDER BY id DESC LIMIT 200
        """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/history/<int:pid>", methods=["GET"])
def get_history_item(pid):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM prompts WHERE id=?", (pid,)
        ).fetchone()
    if not row:
        return jsonify(error="Not found"), 404
    return jsonify(dict(row))

@app.route("/api/thumb/<int:pid>")
def thumbnail(pid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT result_b64 FROM prompts WHERE id=?", (pid,)
        ).fetchone()
    if not row or not row[0]:
        return '', 404
    try:
        img_bytes = base64.b64decode(row[0])
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        w, h = img.size
        if w > 256 or h > 256:
            ratio = min(256 / w, 256 / h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=75)
        return buf.getvalue(), 200, {'Content-Type': 'image/jpeg',
                                     'Cache-Control': 'private, max-age=86400'}
    except Exception:
        return '', 500

@app.route("/history/<int:pid>", methods=["DELETE"])
def delete_history(pid):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM prompts WHERE id=?", (pid,))
    return jsonify(status="ok")


@app.route("/history", methods=["DELETE"])
def delete_all_history():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("DELETE FROM prompts")
    return jsonify(status="ok", deleted=cur.rowcount)


@app.route("/use-history/<int:pid>", methods=["POST"])
def use_history(pid):
    """Load a history item's prompt into the editor"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT prompt, image_b64 FROM prompts WHERE id=?", (pid,)
        ).fetchone()
    if not row:
        return jsonify(error="Not found"), 404
    return jsonify({
        "prompt": row["prompt"],
        "image_b64": row["image_b64"]
    })

# Output rules for gpt-image-2.5-* targets, distilled from OpenAI's GPT Image
# prompting guide (gpt-image-skill/skills/gpt-image/references/openai-cookbook.md §2).
_ENHANCE_RULES_25 = (
    "Output format — plain text, one short labeled line each, in this order "
    "(omit a line only if it truly does not apply):\n"
    "Use: <intended use, e.g. poster, product photo, infographic, UI mockup>\n"
    "Scene: <background / environment>\n"
    "Subject: <main subject(s); for people: body framing, pose, gaze, interaction with objects>\n"
    "Details: <materials, textures, visual medium, style>\n"
    "Composition: <framing, viewpoint, angle, placement of elements>\n"
    "Lighting & mood: <lighting, color palette, atmosphere>\n"
    "Text in image: <only if the image must contain text>\n"
    "Constraints: <exclusions and invariants, e.g. no watermark, no extra text>\n\n"
    "Rules:\n"
    "- Write everything in English EXCEPT literal text that must appear in the image: "
    "keep that exactly as the user wrote it (do not translate), wrapped in double quotes, "
    "and state its typography, color and placement.\n"
    "- If a photo is wanted, include the word \"photorealistic\"; keep camera specs high-level.\n"
    "- Be concrete, but do not invent requirements the user did not imply.\n"
    "- Output ONLY the prompt — no markdown, no explanations."
)

@app.route("/enhance-prompt", methods=["POST"])
@api_key_required
def enhance_prompt():
    data = request.get_json()
    if not data:
        return jsonify(error="Invalid JSON body"), 400
    prompt     = (data.get("prompt") or "").strip()
    image_b64  = data.get("image_b64") or ""
    is_25      = (data.get("model") or "").startswith("gpt-image-2.5")

    if not prompt and not image_b64:
        return jsonify(error="Prompt or image required"), 400

    try:
        client = get_client()
        messages = []

        if is_25:
            if image_b64 and prompt:
                task = (
                    "You are a creative image prompt writer for GPT Image 2.5.\n\n"
                    "Use the attached reference photo only as a visual style reference "
                    "(general appearance, clothing style, pose, expression, colors, lighting, "
                    "composition) — describe it in terms of visual style, not biometric details, "
                    "and do not identify anyone. Blend that aesthetic with the concept below. "
                    "In Constraints, list what should be preserved from the reference.\n\n"
                    f"CONCEPT:\n{prompt}\n\n"
                )
            elif image_b64:
                task = (
                    "You are a creative image prompt writer for GPT Image 2.5.\n\n"
                    "Write a prompt that recreates the visual style of the attached image, "
                    "describing it in artistic terms.\n\n"
                )
            else:
                task = (
                    "You are a creative image prompt writer for GPT Image 2.5.\n\n"
                    "Enhance the following image generation prompt to be more detailed "
                    "and visually precise while keeping the user's intent.\n\n"
                    f"Original prompt: {prompt}\n\n"
                )
            text = task + _ENHANCE_RULES_25
            if image_b64:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    ]
                })
            else:
                messages.append({"role": "user", "content": text})
        elif image_b64 and prompt:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "You are a creative image prompt writer for AI image generation.\n\n"
                            "Look at this reference photo and describe its overall visual style — "
                            "the subject's general appearance, clothing style, pose, expression, "
                            "color scheme, lighting, and composition. Think of it as a style "
                            "reference for creating new artistic images, not as identifying "
                            "a specific individual.\n\n"
                            "Then write a detailed English prompt for AI image generation "
                            "that combines this visual reference with the concept below.\n\n"
                            f"CONCEPT:\n{prompt}\n\n"
                            "Guidelines:\n"
                            "- Describe the subject in terms of visual style, not biometric details\n"
                            "- Blend the reference photo's aesthetic with the concept\n"
                            "- Include scene, lighting, color palette, mood, composition\n"
                            "- For 'mini characters' or multi-subject scenes: describe each "
                            "subject's appearance, pose, position, and relative size\n"
                            "- Output ONLY the English prompt, one paragraph, no markdown."
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    }
                ]
            })
        elif image_b64:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Look at this image and write a detailed English prompt for "
                            "AI image generation that captures its visual style. "
                            "Describe the overall scene, composition, lighting, color palette "
                            "and mood in artistic terms. "
                            "Output ONLY the English prompt, one paragraph, no explanations."
                        )
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    }
                ]
            })
        else:
            messages.append({
                "role": "user",
                "content": (
                    "Enhance the following image generation prompt to be more detailed "
                    "and visually precise. Add detail about composition, lighting, "
                    "color palette, mood, and artistic style. "
                    "Output ONLY the English prompt, one paragraph, no explanations.\n\n"
                    f"Original prompt: {prompt}"
                )
            })

        params = dict(
            model=ENHANCE_MODEL,
            messages=messages,
            max_completion_tokens=800 if is_25 else 500,
        )
        # Newer reasoning models reject a non-default temperature
        if ENHANCE_MODEL.startswith("gpt-4"):
            params["temperature"] = 0.7
        response = client.chat.completions.create(**params)
        enhanced = response.choices[0].message.content.strip()
        # Remove quotes only if the model wrapped the whole output in them —
        # a bare strip would eat the closing quote of in-image text like "SALE"
        if len(enhanced) >= 2 and enhanced[0] == enhanced[-1] and enhanced[0] in "\"'":
            enhanced = enhanced[1:-1].strip()
        # Detect content policy refusal
        refusal_patterns = [
            "I'm sorry", "I can't assist", "I cannot assist",
            "I can't help", "I cannot help", "I apologize",
            "I can't comply", "I cannot comply"
        ]
        if any(enhanced.lower().startswith(p.lower()) for p in refusal_patterns):
            return jsonify(error="Content policy restriction — try a different prompt"), 422
        return jsonify(enhanced=enhanced, enhance_model=ENHANCE_MODEL)

    except Exception as e:
        return jsonify(error=str(e)), 500

def _resolve_vars(text: str, variables: dict) -> str:
    """Substitute {argument name="x" default="y"} placeholders."""
    def _sub(m):
        return variables.get(m.group(1), m.group(2) or "")
    return re.sub(r'\{argument\s+name="([^"]+)"(?:\s+default="([^"]*)")?\s*\}', _sub, text)


def _generate_gemini(prompt: str, image_b64: str, quality: str,
                     size: str, n: int, model: str,
                     original_prompt: str = "") -> list:
    """Run Gemini image generation and return a list of result dicts."""
    from google.genai import types as genai_types

    client = get_google_client()
    if not client:
        raise Exception("GOOGLE_API_KEY not configured. Set GOOGLE_API_KEY in ~/.image-studio.env")

    aspect_ratio = _GEMINI_ASPECT_MAP.get(size, "1:1")

    if image_b64:
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        contents = [
            genai_types.Part.from_bytes(data=buf.read(), mime_type="image/png"),
            prompt,
        ]
    else:
        contents = [prompt]

    config = genai_types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=genai_types.ImageConfig(aspect_ratio=aspect_ratio),
    )

    unit_cost = calc_cost(model, "standard", size, 1)
    results = []
    for i in range(n):
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        for candidate in response.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                if part.inline_data and part.inline_data.mime_type and \
                   part.inline_data.mime_type.startswith("image/"):
                    b64 = base64.b64encode(part.inline_data.data).decode()
                    data_url = f"data:{part.inline_data.mime_type};base64,{b64}"
                    pid = save_prompt(prompt, make_thumbnail(image_b64) if image_b64 else None,
                                      None, "standard", size, model,
                                      None, b64, True,
                                      original_prompt=original_prompt or None, cost_usd=unit_cost)
                    results.append({"url": data_url, "revised_prompt": None,
                                     "cost_usd": unit_cost, "pid": pid})

    return results


def _finalize_job_success(job_id: str, results: list) -> None:
    with _jobs_lock:
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["results"] = results


def _finalize_job_failure(job_id: str, prompt: str, image_b64: str, quality: str,
                          size: str, model: str, exc: Exception) -> None:
    save_prompt(prompt, image_b64, None, quality, size,
                model, None, None, False, str(exc))
    with _jobs_lock:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(exc)


def _generate_openai(prompt: str, image_b64: str, quality: str,
                     size: str, n: int, model: str,
                     original_prompt: str = "") -> list:
    """Run OpenAI image generation and return a list of result dicts."""
    client = get_client()
    kwargs = dict(model=model, prompt=prompt, n=n,
                  quality=quality, size=size)

    if image_b64:
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        w, h = img.size
        if w > 2048 or h > 2048:
            ratio = min(2048 / w, 2048 / h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        buf.name = 'reference.png'
        kwargs["image"] = buf
        # NOTE: edit_sizes intentionally stays a fixed allowlist even for the
        # 2.5 models' custom-size support — non-preset sizes fall back to
        # 'auto' on the edit (images.edit) path only. Generation (images.generate)
        # supports the full custom-size range validated by validate_size_for_model().
        edit_sizes = {'256x256', '512x512', '1024x1024', '1536x1024', '1024x1536', 'auto'}
        if size not in edit_sizes:
            kwargs["size"] = 'auto'
        response = client.images.edit(**kwargs)
    else:
        response = client.images.generate(**kwargs)

    unit_cost = calc_cost(model, quality, size, 1)
    results = []
    for item in response.data:
        if item.url:
            try:
                import urllib.request
                with urllib.request.urlopen(item.url) as resp:
                    raw_b64 = base64.b64encode(resp.read()).decode()
            except Exception:
                raw_b64 = None
            data_url = f"data:image/png;base64,{raw_b64}" if raw_b64 else item.url
            pid = save_prompt(prompt, image_b64, item.revised_prompt, quality, size,
                              model, item.url, raw_b64, True,
                              original_prompt=original_prompt or None, cost_usd=unit_cost)
            results.append({"url": data_url, "revised_prompt": item.revised_prompt,
                             "cost_usd": unit_cost, "pid": pid})
        elif item.b64_json:
            data_url = f"data:image/png;base64,{item.b64_json}"
            pid = save_prompt(prompt, make_thumbnail(image_b64) if image_b64 else None,
                              item.revised_prompt, quality, size,
                              model, None, item.b64_json, True,
                              original_prompt=original_prompt or None, cost_usd=unit_cost)
            results.append({"url": data_url, "revised_prompt": item.revised_prompt,
                             "cost_usd": unit_cost, "pid": pid})

    return results


_PROVIDERS = {
    "openai": _generate_openai,
    "google": _generate_gemini,
}


def _run_generation(job_id: str, prompt: str, image_b64: str,
                    quality: str, size: str, n: int, model: str,
                    original_prompt: str = "") -> None:
    """Run image generation in a background thread and store result in _jobs."""
    model_config = _MODELS.get(model, _MODELS["gpt-image-2"])
    provider = model_config["provider"]
    generate_fn = _PROVIDERS.get(provider)

    try:
        if generate_fn is None:
            raise Exception(f"No provider registered for: {provider}")
        results = generate_fn(prompt, image_b64, quality, size, n, model,
                             original_prompt=original_prompt)
        _finalize_job_success(job_id, results)
    except Exception as exc:
        _finalize_job_failure(job_id, prompt, image_b64, quality, size, model, exc)


@app.route("/generate", methods=["POST"])
def generate():
    data             = request.get_json()
    original_prompt  = (data.get("prompt") or "").strip()
    prompt           = original_prompt
    image_b64        = data.get("image_b64") or ""
    model            = data.get("model", "gpt-image-2")
    quality          = data.get("quality", "medium")
    size             = data.get("size", "1024x1024")
    n                = max(min(int(data.get("n", 1)), 4), 1)
    negative         = (data.get("negative_prompt") or "").strip()
    variables        = data.get("variables") or {}

    if model not in _MODELS:
        return jsonify(error=f"Unknown model: {model}"), 400
    missing = _missing_key_for_model(model)
    if missing:
        return jsonify(error=f"{missing} not set"), 500
    model_config = _MODELS[model]
    if quality not in model_config["qualities"]:
        quality = model_config["qualities"][0]
    size_ok, size_err = validate_size_for_model(model_config, size)
    if not size_ok:
        return jsonify(error=size_err), 400

    if not prompt:
        return jsonify(error="Prompt is required"), 400
    if image_b64 and len(image_b64) > 15 * 1024 * 1024:
        return jsonify(error="Image too large (max ~10MB raw)"), 413

    prompt = _resolve_vars(prompt, variables)
    if negative:
        prompt = f"{prompt}\n\nNegative Prompt:\n{_resolve_vars(negative, variables)}"

    estimated_cost = calc_cost(model, quality, size, n)
    _cleanup_old_jobs()

    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "created_at": time.time(),
                         "estimated_cost": estimated_cost}

    thread = threading.Thread(
        target=_run_generation,
        args=(job_id, prompt, image_b64, quality, size, n, model, original_prompt),
        daemon=True,
    )
    thread.start()

    return jsonify(job_id=job_id, estimated_cost=estimated_cost)


@app.route("/job-status/<job_id>")
def job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify(error="Job not found"), 404
    return jsonify(job)


# ── Stats & cost summary ──────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as successes,
                   ROUND(SUM(COALESCE(cost_usd,0)),4) as total_cost,
                   SUM(starred) as starred_count
            FROM prompts
        """).fetchone()
    return jsonify(total=row[0], successes=row[1],
                   total_cost=row[2] or 0, starred_count=row[3] or 0)


# ── Model list endpoint ────────────────────────────────────────────────────────
@app.route("/api/models")
def api_models():
    models = []
    for mid, cfg in _MODELS.items():
        models.append({
            "id": mid,
            "name": cfg["name"],
            "provider": cfg["provider"],
            "qualities": cfg["qualities"],
            "sizes": cfg["sizes"],
            "max_n": cfg["max_n"],
            "supports_edit": cfg["supports_edit"],
            "supports_custom_size": cfg.get("supports_custom_size", False),
            "cost_table": cfg["cost_table"],
        })
    return jsonify(models=models, google_key_set=bool(GOOGLE_API_KEY))


# ── Tag & star endpoints ──────────────────────────────────────────────────────
@app.route("/api/prompts/<int:pid>/star", methods=["POST"])
def toggle_star(pid):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE prompts SET starred = 1 - COALESCE(starred,0) WHERE id=?", (pid,))
        row = conn.execute("SELECT starred FROM prompts WHERE id=?", (pid,)).fetchone()
    if not row:
        return jsonify(error="Not found"), 404
    return jsonify(starred=bool(row[0]))

@app.route("/api/prompts/<int:pid>/tags", methods=["POST"])
def set_tags(pid):
    tags = (request.get_json() or {}).get("tags", "")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE prompts SET tags=? WHERE id=?", (tags.strip(), pid))
    return jsonify(tags=tags.strip())


# ── Prompt search (FTS5) ──────────────────────────────────────────────────────
@app.route("/api/search-prompts")
def search_prompts():
    q = request.args.get("q", "").strip()
    try:
        limit = min(int(request.args.get("limit", 8)), 20)
    except ValueError:
        limit = 8
    if not q:
        return jsonify(results=[])
    sanitized = q.replace('"', ' ').strip()
    if not sanitized:
        return jsonify(results=[])
    match = " ".join(f'"{tok}"' for tok in sanitized.split())
    if not q.endswith(" "):
        match += "*"
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT c.id, c.title, c.category, c.prompt, c.image_url
                FROM community_prompts_fts f
                JOIN community_prompts c ON c.id = f.rowid
                WHERE community_prompts_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return jsonify(results=[])
    return jsonify(results=[dict(r) for r in rows])


# ── Batch generation ──────────────────────────────────────────────────────────
@app.route("/batch-generate", methods=["POST"])
def batch_generate():
    data     = request.get_json() or {}
    prompts  = data.get("prompts") or []       # list of strings
    model    = data.get("model", "gpt-image-2")
    quality  = data.get("quality", "medium")
    size     = data.get("size", "1024x1024")

    if model not in _MODELS:
        return jsonify(error=f"Unknown model: {model}"), 400
    missing = _missing_key_for_model(model)
    if missing:
        return jsonify(error=f"{missing} not set"), 500
    model_config = _MODELS[model]
    if quality not in model_config["qualities"]:
        quality = model_config["qualities"][0]
    size_ok, size_err = validate_size_for_model(model_config, size)
    if not size_ok:
        return jsonify(error=size_err), 400

    if not prompts or not isinstance(prompts, list):
        return jsonify(error="prompts must be a non-empty list"), 400
    prompts = [p.strip() for p in prompts[:20] if str(p).strip()]  # max 20
    if not prompts:
        return jsonify(error="No valid prompts"), 400

    batch_id = uuid.uuid4().hex[:12]
    job_ids  = []
    for p in prompts:
        job_id = uuid.uuid4().hex[:12]
        with _jobs_lock:
            _jobs[job_id] = {"status": "running", "created_at": time.time(),
                             "batch_id": batch_id}
        t = threading.Thread(target=_run_generation,
                             args=(job_id, p, "", quality, size, 1, model, p), daemon=True)
        t.start()
        job_ids.append({"job_id": job_id, "prompt": p})

    return jsonify(batch_id=batch_id, jobs=job_ids,
                   estimated_cost=calc_cost(model, quality, size, len(prompts)))


# ── ZIP export ────────────────────────────────────────────────────────────────
@app.route("/export-zip", methods=["POST"])
def export_zip():
    import zipfile as _zip
    ids = (request.get_json() or {}).get("ids") or []
    if not ids:
        return jsonify(error="ids required"), 400

    buf = io.BytesIO()
    manifest = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as zf:
            for pid in ids[:50]:
                row = conn.execute(
                    "SELECT id, prompt, revised_prompt, result_b64, quality, size, prompt_ts "
                    "FROM prompts WHERE id=?", (pid,)
                ).fetchone()
                if not row:
                    continue
                filename = None
                if row["result_b64"]:
                    img_data = base64.b64decode(row["result_b64"])
                    ext, _ = detect_image_format(img_data)
                    filename = f"img_{row['id']:04d}.{ext}"
                    zf.writestr(filename, img_data)
                manifest.append({
                    "id": row["id"], "filename": filename,
                    "prompt": row["prompt"], "revised_prompt": row["revised_prompt"],
                    "quality": row["quality"], "size": row["size"],
                    "generated_at": row["prompt_ts"],
                })
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    buf.seek(0)
    filename = f"image-studio-export-{datetime.now():%Y%m%d-%H%M%S}.zip"
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name=filename)


# ── REST API v1 ───────────────────────────────────────────────────────────────
@app.route("/api/v1/generate", methods=["POST"])
def api_v1_generate():
    """External REST API — same as /generate but returns polling URL."""
    data          = request.get_json() or {}
    original_prompt = (data.get("prompt") or "").strip()
    if not original_prompt:
        return jsonify(error="prompt is required"), 400
    prompt    = _resolve_vars(original_prompt, data.get("variables") or {})
    image_b64 = data.get("image_b64") or ""
    model     = data.get("model", "gpt-image-2")
    quality   = data.get("quality", "medium")
    size      = data.get("size", "1024x1024")
    n         = max(min(int(data.get("n", 1)), 4), 1)
    negative  = (data.get("negative_prompt") or "").strip()

    if model not in _MODELS:
        return jsonify(error=f"Unknown model: {model}"), 400
    missing = _missing_key_for_model(model)
    if missing:
        return jsonify(error=f"{missing} not set"), 500
    model_config = _MODELS[model]
    if quality not in model_config["qualities"]:
        quality = model_config["qualities"][0]
    size_ok, size_err = validate_size_for_model(model_config, size)
    if not size_ok:
        return jsonify(error=size_err), 400

    if negative:
        prompt = f"{prompt}\n\nNegative Prompt:\n{negative}"

    cost = calc_cost(model, quality, size, n)
    _cleanup_old_jobs()
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "created_at": time.time()}

    threading.Thread(target=_run_generation,
                     args=(job_id, prompt, image_b64, quality, size, n, model, original_prompt),
                     daemon=True).start()

    base = request.host_url.rstrip("/")
    return jsonify(job_id=job_id, estimated_cost=cost,
                   poll_url=f"{base}/job-status/{job_id}"), 202

@app.route("/studio")
def studio():
    return render_template("studio.html",
                           api_key_set=bool(os.environ.get("OPENAI_API_KEY")),
                           google_key_set=bool(GOOGLE_API_KEY),
                           set_key_secret_required=bool(os.environ.get("SET_KEY_SECRET")))


@app.route("/gallery")
def gallery():
    return render_template("gallery.html")


@app.route("/history-view")
def history_view():
    return render_template("history.html")


@app.route("/templates")
def prompt_templates():
    return render_template("templates.html")

@app.route("/api/community-prompts")
def community_prompts_api():
    """Return community_prompts grouped by category for the templates page."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, title, category, platform, author, image_url,
                   source_url, prompt, score
            FROM community_prompts
            ORDER BY category, id
        """).fetchall()
    grouped = {}
    for r in rows:
        cat = r["category"] or "其他"
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append({
            "id": r["id"],
            "title": r["title"],
            "platform": r["platform"],
            "author": r["author"],
            "image_url": r["image_url"],
            "source_url": r["source_url"],
            "prompt": r["prompt"],
            "score": r["score"]
        })
    return jsonify(grouped)


@app.route("/api/templates")
def templates_api():
    """Return the unified templates table grouped by category."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, source, title, category, prompt, thumbnail_path,
                   platform, author, source_url, score
            FROM templates
            ORDER BY category, id
        """).fetchall()
    grouped = {}
    for r in rows:
        cat = r["category"] or "其他／創意混搭 Comparisons, Mashups & Other"
        if cat not in grouped:
            grouped[cat] = []
        thumb_path = r["thumbnail_path"]
        thumbnail_url = f"/{thumb_path}" if thumb_path else None
        grouped[cat].append({
            "id": r["id"],
            "title": r["title"],
            "source": r["source"],
            "prompt": r["prompt"],
            "thumbnail_url": thumbnail_url,
            "platform": r["platform"],
            "author": r["author"],
            "source_url": r["source_url"],
            "score": r["score"],
        })
    return jsonify(grouped)


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
        try:
            vector = json.loads(r["embedding"])
        except (json.JSONDecodeError, TypeError):
            print(f"Skipping template id={r['id']}: malformed embedding JSON")
            continue
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


# ── Facebook Routes ──────────────────────────────────────────────────────────
def _load_page_token(page_id=""):
    """Load page token. If page_id provided, returns that page's token.
    Lines format: pid:name:token (new) or pid:token (old, backward-compatible).
    """
    try:
        if os.path.exists(FB_PAGE_TOKEN_FILE):
            with open(FB_PAGE_TOKEN_FILE) as f:
                for line in f:
                    line = line.strip()
                    if ":" in line:
                        parts = line.split(":", 2)  # pid, name?, token
                        if len(parts) == 3:
                            pid, _, ptoken = parts
                        else:
                            pid, ptoken = parts[0], parts[1]
                        if page_id and pid == page_id:
                            return ptoken
                        elif not page_id:
                            return ptoken  # return first one
    except: pass
    return ""

def _save_page_token(token, page_id="", name=""):
    """Save page token with optional page name. Appends to file."""
    existing = {}
    try:
        if os.path.exists(FB_PAGE_TOKEN_FILE):
            with open(FB_PAGE_TOKEN_FILE) as f:
                for line in f:
                    line = line.strip()
                    if ":" in line:
                        parts = line.split(":", 2)
                        if len(parts) == 3:
                            existing[parts[0]] = (parts[1], parts[2])
                        else:
                            existing[parts[0]] = ("", parts[1])
    except: pass
    existing[page_id] = (name, token)
    with open(FB_PAGE_TOKEN_FILE, "w") as f:
        for pid, (pname, ptoken) in existing.items():
            if pname:
                f.write(f"{pid}:{pname}:{ptoken}\n")
            else:
                f.write(f"{pid}:{ptoken}\n")

@app.route("/fb-auth-url")
def fb_auth_url():
    if not FB_APP_ID or not FB_APP_SECRET:
        return jsonify(error="FB_APP_ID / FB_APP_SECRET not configured"), 503
    from urllib.parse import quote
    url = (
        "https://www.facebook.com/v22.0/dialog/oauth"
        f"?client_id={FB_APP_ID}"
        f"&redirect_uri={quote(FB_REDIRECT, safe='')}"
        "&scope=pages_manage_posts,pages_read_engagement,pages_show_list"
        "&response_type=code"
    )
    return jsonify(url=url)

@app.route("/fb-callback")
def fb_callback():
    if not FB_APP_ID or not FB_APP_SECRET:
        return jsonify(error="FB_APP_ID / FB_APP_SECRET not configured"), 503
    code = request.args.get("code", "")
    if not code:
        return "<h1>Error: No code</h1>", 400

    try:
        # Exchange code for User Access Token
        r = requests.get("https://graph.facebook.com/v22.0/oauth/access_token", params={
            "client_id": FB_APP_ID,
            "client_secret": FB_APP_SECRET,
            "redirect_uri": FB_REDIRECT,
            "code": code
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
        user_token = data.get("access_token", "")
        if not user_token:
            return f"<h1>Failed to get token</h1><pre>{json.dumps(data, indent=2)}</pre>", 400

        # Get pages
        r2 = requests.get("https://graph.facebook.com/v22.0/me/accounts", params={
            "access_token": user_token
        }, timeout=10)
        r2.raise_for_status()
        pages_data = r2.json()
        pages = pages_data.get("data", [])

        if not pages:
            return "<h1>No Pages found</h1><p>This account doesn't manage any Facebook Pages.</p>", 400

        # Save all page tokens with names
        for p in pages:
            _save_page_token(p["access_token"], p["id"], p.get("name", ""))

        # Build response
        html = "<h1>✅ Authorized!</h1><ul>"
        for p in pages:
            html += f"<li><b>{escape(p.get('name', 'Unknown'))}</b> — ID: {escape(p['id'])}</li>"
        html += "</ul><p>Page tokens saved. You can now close this page.</p>"
        return html

    except Exception as e:
        return f"<h1>Error</h1><pre>{str(e)}</pre>", 500

@app.route("/fb-post", methods=["POST"])
def fb_post():
    """Post an image to a Facebook page.
    Body: {page_id, message, image_path (local) or image_url, or pid}
    If pid is given, loads result_b64 from prompts table and uploads as multipart.
    """
    data = request.get_json() or {}
    page_id = data.get("page_id", "")
    message = data.get("message", "")
    image_path = data.get("image_path", "")
    image_url  = data.get("image_url", "")
    pid = data.get("pid")           # prompt history id

    # Resolve pid → base64 image bytes from DB
    pid_bytes = None
    if pid is not None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result_b64, prompt FROM prompts WHERE id=?", (pid,)
        ).fetchone()
        conn.close()
        if not row or not row["result_b64"]:
            return jsonify(error=f"No image found for pid={pid}"), 404
        pid_bytes = base64.b64decode(row["result_b64"])
        # Default message = prompt text, truncated
        if not message and row["prompt"]:
            message = row["prompt"][:500]

    # Load page token
    token = _load_page_token(page_id)
    if not token:
        return jsonify(error="No page token. Run /fb-auth-url first."), 401

    try:
        params = {"access_token": token, "message": message} if message else {"access_token": token}
        import mimetypes

        if pid_bytes:
            files = {"source": (f"generated_{pid}.png", pid_bytes, "image/png")}
            r = requests.post(
                f"https://graph.facebook.com/v22.0/{page_id}/photos",
                data=params, files=files, timeout=30
            )
        elif image_path and os.path.exists(image_path):
            mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
            with open(image_path, "rb") as img:
                files = {"source": (os.path.basename(image_path), img, mime_type)}
                r = requests.post(
                    f"https://graph.facebook.com/v22.0/{page_id}/photos",
                    data=params, files=files, timeout=30
                )
        elif image_url:
            params["url"] = image_url
            r = requests.post(
                f"https://graph.facebook.com/v22.0/{page_id}/photos",
                data=params, timeout=30
            )
        else:
            return jsonify(error="image_path, image_url, or pid required"), 400

        result = r.json()
        if "id" in result:
            return jsonify(status="ok", post_id=result["id"], page_id=page_id)
        else:
            return jsonify(error=result.get("error", {}).get("message", str(result))), 400

    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/fb-pages")
def fb_pages():
    """List pages with saved tokens and names."""
    pages = []
    found = False
    try:
        with open(FB_PAGE_TOKEN_FILE) as f:
            for line in f:
                line = line.strip()
                if ":" in line:
                    parts = line.split(":", 2)
                    if len(parts) == 3:
                        pages.append({"id": parts[0], "name": parts[1]})
                    else:
                        pages.append({"id": parts[0], "name": ""})
                    found = True
    except FileNotFoundError:
        pass
    if not found:
        return jsonify(error="No Facebook token configured. Visit /fb-auth-url to authorize."), 400
    return jsonify(pages=pages)

@app.route("/fb-token-test")
def fb_token_test():
    """Test if a saved page token is still valid."""
    token = _load_page_token()
    if not token:
        return jsonify(valid=False, error="No token configured")
    try:
        url = f"https://graph.facebook.com/v22.0/me?access_token={token}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if r.status_code == 200:
            return jsonify(valid=True, page_name=data.get("name", "Unknown"), page_id=data.get("id", ""))
        else:
            return jsonify(valid=False, error=data.get("error", {}).get("message", "Unknown error"))
    except Exception as e:
        return jsonify(valid=False, error=str(e))

@app.route("/health")
def health():
    return jsonify(status="ok", api_key_set=bool(os.environ.get("OPENAI_API_KEY")))

@app.route("/favicon.ico")
def favicon():
    # Simple 1x1 transparent PNG favicon
    import struct, zlib
    def create_png():
        # 16x16 blue square favicon
        width, height = 16, 16
        raw = b''
        for y in range(height):
            raw += b'\x00'  # filter byte
            for x in range(width):
                raw += b'\x1a\x56\xd8\xff'  # RGBA blue
        def chunk(ctype, data):
            c = ctype + data
            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
        idat = zlib.compress(raw)
        return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')
    return send_file(io.BytesIO(create_png()), mimetype='image/png')


@app.route("/set-key", methods=["POST"])
def set_key():
    """Set API key at runtime. Requires SET_KEY_SECRET header to prevent abuse."""
    secret = os.environ.get("SET_KEY_SECRET", "")
    if secret and request.headers.get("X-Set-Key-Secret") != secret:
        return jsonify(status="error", message="Forbidden"), 403
    data = request.get_json()
    key = (data.get("api_key") or "").strip()
    if not key or not key.startswith("sk-"):
        return jsonify(status="error", message="Invalid API key"), 400
    os.environ["OPENAI_API_KEY"] = key
    global API_KEY
    API_KEY = key
    # Persist to disk so it survives restarts
    _upsert_key_file("OPENAI_API_KEY", key)
    return jsonify(status="ok")

@app.route("/download/<int:pid>", methods=["GET"])
def download(pid):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result_b64, result_url FROM prompts WHERE id=?", (pid,)
        ).fetchone()
    if not row:
        return jsonify(error="Not found"), 404
    if row["result_b64"]:
        img_data = base64.b64decode(row["result_b64"])
        ext, mime = detect_image_format(img_data)
        return send_file(
            io.BytesIO(img_data),
            mimetype=mime,
            as_attachment=True,
            download_name=f"img_{pid}.{ext}"
        )
    elif row["result_url"]:
        return jsonify(url=row["result_url"])
    return jsonify(error="No image data"), 404

PICTURES_DIR = os.path.expanduser("~/Pictures/OPENAI image 2.0")

def _make_topic_slug(prompt: str) -> str:
    import re
    slug = re.sub(r'[^\w\s一-鿿]', '', prompt or '')
    slug = re.sub(r'\s+', ' ', slug).strip()
    return slug[:40]

def _save_img_to_pictures(img_data: bytes, prompt: str) -> str:
    os.makedirs(PICTURES_DIR, exist_ok=True)
    topic = _make_topic_slug(prompt)
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    ext, _ = detect_image_format(img_data)
    filename = f"{ts} {topic}.{ext}" if topic else f"{ts}.{ext}"
    with open(os.path.join(PICTURES_DIR, filename), "wb") as f:
        f.write(img_data)
    return filename

@app.route("/save-to-pictures/<int:pid>", methods=["POST"])
def save_to_pictures(pid):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT result_b64, result_url, prompt FROM prompts WHERE id=?", (pid,)
        ).fetchone()
    if not row:
        return jsonify(error="Not found"), 404
    if row["result_b64"]:
        img_data = base64.b64decode(row["result_b64"])
    elif row["result_url"]:
        import urllib.request
        with urllib.request.urlopen(row["result_url"]) as resp:
            img_data = resp.read()
    else:
        return jsonify(error="No image data"), 404
    filename = _save_img_to_pictures(img_data, row["prompt"] or "")
    return jsonify(filename=filename)

@app.route("/save-to-pictures", methods=["POST"])
def save_to_pictures_dataurl():
    data = request.get_json() or {}
    data_url = data.get("data_url", "")
    prompt = data.get("prompt", "")
    if data_url.startswith("data:"):
        _, encoded = data_url.split(",", 1)
        img_data = base64.b64decode(encoded)
    elif data_url.startswith("http"):
        import urllib.request
        with urllib.request.urlopen(data_url) as resp:
            img_data = resp.read()
    else:
        return jsonify(error="No image data"), 400
    filename = _save_img_to_pictures(img_data, prompt)
    return jsonify(filename=filename)


if __name__ == "__main__":
    init_db()  # only on direct execution, not on module import
    if not os.environ.get("OPENAI_API_KEY"):
        print("WARNING: OPENAI_API_KEY environment variable is not set.")
        print("Set it with:  export OPENAI_API_KEY=sk-...")
    print(f"Open http://localhost:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
