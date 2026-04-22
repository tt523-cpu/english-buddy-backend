# ============================================================
# English Buddy Backend - Flask API Server
#
# Architecture:
#   Frontend (Flutter Web)  →  This Backend (Flask)  →  LLM / STT APIs
#
# Why:
#   - API keys stay on the server (never exposed to browser)
#   - Single origin = no CORS issues
#   - One place to configure all AI services
#   - Can swap LLM/STT providers without changing frontend
#
# Endpoints:
#   GET  /api/status                  - Backend health + config info
#   GET  /api/v1/models               - List available LLM models
#   POST /api/v1/chat/completions     - Chat completion (proxy)
#   GET  /api/stt/health              - STT service health check
#   POST /api/stt/transcribe          - Transcribe audio (proxy)
#   GET  /                             - Serve frontend static files
#   GET  /<path>                       - SPA fallback
# ============================================================

import os
import json
import requests as http_requests
from flask import (
    Flask, request, jsonify,
    send_from_directory, Response,
)
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# Config
# ============================================================
def env(key, default=""):
    return os.environ.get(key, default)


# LLM
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_API_KEY  = env("LLM_API_KEY", "")
LLM_MODEL    = env("LLM_MODEL", "deepseek-chat")

# STT
STT_BASE_URL        = env("STT_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
STT_API_KEY         = env("STT_API_KEY", "")
STT_MODEL           = env("STT_MODEL", "whisper-large-v3")
STT_TRANSCRIBE_PATH = env("STT_TRANSCRIBE_PATH", "/audio/transcriptions")
STT_HEALTH_PATH     = env("STT_HEALTH_PATH", "/models")

# Server
HOST  = env("HOST", "0.0.0.0")
PORT  = int(env("PORT", "5000"))
DEBUG = env("DEBUG", "false").lower() == "true"

# Frontend static files
# Docker: /app/web
# Local dev: ../english-learning-app/build/web or ./web
STATIC_DIR = os.environ.get(
    "STATIC_DIR",
    os.path.join(os.path.dirname(__file__), "web"),
)
if not os.path.isdir(STATIC_DIR):
    # Fallback: look for build/web in parent project
    _alt = os.path.join(os.path.dirname(__file__), "..", "english-learning-app", "build", "web")
    if os.path.isdir(_alt):
        STATIC_DIR = _alt


# ============================================================
# Flask App
# ============================================================
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
CORS(app)


def _mask_key(key):
    """Show only last 4 chars of a key for logging."""
    if not key or len(key) < 8:
        return "***" if key else "(empty)"
    return "***" + key[-4:]


def _llm_headers():
    h = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        h["Authorization"] = f"Bearer {LLM_API_KEY}"
    return h


def _stt_headers():
    h = {}
    if STT_API_KEY:
        h["Authorization"] = f"Bearer {STT_API_KEY}"
    return h


# ============================================================
# LLM Proxy Endpoints
# ============================================================

@app.route("/api/v1/models", methods=["GET"])
def list_models():
    """Proxy: list models from upstream LLM service."""
    try:
        url = f"{LLM_BASE_URL}/v1/models"
        resp = http_requests.get(url, headers=_llm_headers(), timeout=10)
        return Response(resp.content, status=resp.status_code, content_type="application/json")
    except Exception as e:
        return jsonify({"error": f"Cannot reach LLM service: {e}"}), 502


@app.route("/api/v1/chat/completions", methods=["POST"])
def chat_completions():
    """Proxy: chat completions to upstream LLM.
    
    The server injects model name and API key.
    Frontend only sends messages + parameters.
    """
    try:
        body = request.get_json(force=True)
        if not body or "messages" not in body:
            return jsonify({"error": "Missing 'messages' in request body"}), 400

        # Server controls the model
        body["model"] = LLM_MODEL

        url = f"{LLM_BASE_URL}/v1/chat/completions"
        is_stream = body.get("stream", False)

        resp = http_requests.post(
            url,
            headers=_llm_headers(),
            json=body,
            timeout=120,
            stream=is_stream,
        )

        if is_stream:
            def stream():
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
            return Response(
                stream(),
                status=resp.status_code,
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return Response(resp.content, status=resp.status_code, content_type="application/json")

    except http_requests.exceptions.Timeout:
        return jsonify({"error": "LLM request timed out"}), 504
    except Exception as e:
        return jsonify({"error": f"LLM proxy error: {e}"}), 502


# ============================================================
# STT Proxy Endpoints
# ============================================================

@app.route("/api/stt/health", methods=["GET"])
def stt_health():
    """Check if upstream STT service is reachable."""
    try:
        url = f"{STT_BASE_URL}{STT_HEALTH_PATH}"
        resp = http_requests.get(url, headers=_stt_headers(), timeout=10)
        ok = resp.status_code == 200
        return jsonify({"status": "ok" if ok else "error"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 502


@app.route("/api/stt/transcribe", methods=["POST"])
def stt_transcribe():
    """Proxy: transcribe audio via upstream STT service.
    
    Accepts multipart/form-data with a 'file' field.
    Server injects model name and API key.
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "No 'file' field in upload"}), 400

        audio = request.files["file"]
        language = request.form.get("language", "")

        url = f"{STT_BASE_URL}{STT_TRANSCRIBE_PATH}"
        files = {"file": (audio.filename or "audio.wav", audio.stream, audio.content_type)}
        data = {}
        if STT_MODEL:
            data["model"] = STT_MODEL
        if language:
            data["language"] = language

        resp = http_requests.post(
            url,
            headers=_stt_headers(),
            files=files,
            data=data,
            timeout=60,
        )
        return Response(resp.content, status=resp.status_code, content_type="application/json")

    except http_requests.exceptions.Timeout:
        return jsonify({"error": "STT request timed out"}), 504
    except Exception as e:
        return jsonify({"error": f"STT proxy error: {e}"}), 502


# ============================================================
# Status Endpoint
# ============================================================

@app.route("/api/status", methods=["GET"])
def api_status():
    """Backend health check. Frontend uses this to verify connectivity."""
    return jsonify({
        "status": "ok",
        "llm": {
            "base_url": LLM_BASE_URL,
            "model": LLM_MODEL,
            "has_key": bool(LLM_API_KEY),
        },
        "stt": {
            "base_url": STT_BASE_URL,
            "model": STT_MODEL,
            "has_key": bool(STT_API_KEY),
        },
    })


# ============================================================
# Frontend Static Files (SPA)
# ============================================================

@app.route("/")
def serve_index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    file_path = os.path.join(app.static_folder, path)
    if os.path.isfile(file_path):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")


# ============================================================
# Startup
# ============================================================
if __name__ == "__main__":
    print()
    print("=" * 55)
    print("  English Buddy Backend")
    print(f"  Frontend: {STATIC_DIR} {'(found)' if os.path.isdir(STATIC_DIR) else '(NOT FOUND - only API will work)'}")
    print(f"  LLM:      {LLM_BASE_URL} / {LLM_MODEL} / key={_mask_key(LLM_API_KEY)}")
    print(f"  STT:      {STT_BASE_URL} / {STT_MODEL} / key={_mask_key(STT_API_KEY)}")
    print(f"  Listen:   http://{HOST}:{PORT}")
    print("=" * 55)
    print()
    app.run(host=HOST, port=PORT, debug=DEBUG)
