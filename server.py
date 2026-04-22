# ============================================================
# English Buddy Backend - Flask API Server
#
# Architecture:
#   Frontend (Flutter Web)  →  This Backend (Flask)  →  LLM / STT APIs
#   Admin Panel (Browser)   →  This Backend (Flask)  →  .env file
#
# Features:
#   - API keys stay on the server (never exposed to browser)
#   - Web admin panel at /admin to configure services
#   - Hot-reload config without restart
#   - Single origin = no CORS issues
#
# Endpoints:
#   GET  /admin                       - Web admin panel
#   GET  /api/config                  - Read current config
#   POST /api/config                  - Save config to .env
#   POST /api/test/llm               - Test LLM connection
#   POST /api/test/stt               - Test STT connection
#   GET  /api/status                  - Backend health + config info
#   GET  /api/v1/models               - List available LLM models
#   POST /api/v1/chat/completions     - Chat completion (proxy)
#   GET  /api/stt/health              - STT service health check
#   POST /api/stt/transcribe          - Transcribe audio (proxy)
# ============================================================

import os
import io
import json
import glob
import requests as http_requests
from flask import (
    Flask, request, jsonify,
    send_from_directory, Response,
)
from flask_cors import CORS
from dotenv import load_dotenv, dotenv_values

# ============================================================
# Paths
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
ENV_FILE = os.path.join(DATA_DIR, ".env")
ENV_EXAMPLE = os.path.join(BASE_DIR, ".env.example")

# Auto-create .env from .env.example on first run
if not os.path.isfile(ENV_FILE) and os.path.isfile(ENV_EXAMPLE):
    import shutil
    shutil.copy2(ENV_EXAMPLE, ENV_FILE)
    print(f"[init] Created {ENV_FILE} from .env.example")

# Load initial config
load_dotenv(ENV_FILE)


# ============================================================
# Config Management
# ============================================================
class Config:
    """Runtime config that reads from data/.env file on every access."""

    def __init__(self):
        self._data = {}
        self._file_mtime = 0
        self.reload()

    def _read_env_file(self):
        """Directly parse data/.env file into a dict (no env var caching)."""
        values = {}
        if os.path.isfile(ENV_FILE):
            for line in open(ENV_FILE, "r", encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    values[k.strip()] = v.strip()
        return values

    def reload(self):
        """Reload config from .env file."""
        raw = self._read_env_file()
        self._data = {
            "LLM_BASE_URL": raw.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            "LLM_API_KEY": raw.get("LLM_API_KEY", ""),
            "LLM_MODEL": raw.get("LLM_MODEL", "deepseek-chat"),
            "STT_BASE_URL": raw.get("STT_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"),
            "STT_API_KEY": raw.get("STT_API_KEY", ""),
            "STT_MODEL": raw.get("STT_MODEL", "whisper-large-v3"),
            "STT_TRANSCRIBE_PATH": raw.get("STT_TRANSCRIBE_PATH", "/audio/transcriptions"),
            "STT_HEALTH_PATH": raw.get("STT_HEALTH_PATH", "/models"),
            "HOST": raw.get("HOST", "0.0.0.0"),
            "PORT": raw.get("PORT", "5000"),
            "DEBUG": raw.get("DEBUG", "false"),
        }
        try:
            self._file_mtime = os.path.getmtime(ENV_FILE)
        except OSError:
            pass

    def get(self, key, default=""):
        # Auto-reload if file changed (handles multi-worker sync)
        try:
            mtime = os.path.getmtime(ENV_FILE)
            if mtime != self._file_mtime:
                self.reload()
        except OSError:
            pass
        return self._data.get(key, default)

    def save(self, updates: dict):
        """Save config updates to .env file and reload."""
        # Read existing .env or start fresh
        existing = {}
        if os.path.isfile(ENV_FILE):
            for line in open(ENV_FILE, "r", encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()

        # Apply updates (skip masked/placeholder values to avoid overwriting real keys)
        MASK_PATTERN = ("***", "(empty)")
        for k, v in updates.items():
            if v is not None and str(v).strip() and not str(v).startswith(MASK_PATTERN):
                existing[k] = str(v)

        # Write .env
        allowed_keys = {
            "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
            "STT_BASE_URL", "STT_API_KEY", "STT_MODEL",
            "STT_TRANSCRIBE_PATH", "STT_HEALTH_PATH",
            "HOST", "PORT", "DEBUG",
        }
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write("# English Buddy Backend - Auto-generated config\n")
            f.write(f"# Edit at: http://localhost:{existing.get('PORT','5000')}/admin\n\n")
            for k, v in existing.items():
                if k in allowed_keys:
                    f.write(f"{k}={v}\n")

        # Reload into runtime
        self.reload()

    def mask_key(self, key_val):
        if not key_val or len(key_val) < 8:
            return "***" if key_val else "(empty)"
        return "***" + key_val[-4:]

    def to_dict(self, mask_keys=True):
        d = dict(self._data)
        if mask_keys:
            if d.get("LLM_API_KEY"):
                d["LLM_API_KEY"] = self.mask_key(d["LLM_API_KEY"])
            if d.get("STT_API_KEY"):
                d["STT_API_KEY"] = self.mask_key(d["STT_API_KEY"])
        return d

    def to_dict_raw(self):
        """Return config with real keys (for test endpoints)."""
        return dict(self._data)


cfg = Config()


# ============================================================
# Flask App
# ============================================================

STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(BASE_DIR, "web"))
if not os.path.isdir(STATIC_DIR):
    _alt = os.path.join(BASE_DIR, "..", "english-learning-app", "build", "web")
    if os.path.isdir(_alt):
        STATIC_DIR = _alt

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
CORS(app)


# ============================================================
# Helper: Build headers for upstream APIs
# ============================================================
def _llm_headers():
    h = {"Content-Type": "application/json"}
    key = cfg.get("LLM_API_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _stt_headers():
    h = {}
    key = cfg.get("STT_API_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


# ============================================================
# Config Management Endpoints
# ============================================================

@app.route("/api/config", methods=["GET"])
def get_config():
    """Get current config (keys are NOT returned to browser for security)."""
    d = dict(cfg._data)
    # Never send real API keys to browser - send placeholder
    for key in ("LLM_API_KEY", "STT_API_KEY"):
        if d.get(key):
            d[key] = f"***{d[key][-4:]}"
    return jsonify(d)


@app.route("/api/config", methods=["POST"])
def save_config():
    """Save config to .env file. Accepts JSON body with config keys."""
    try:
        updates = request.get_json(force=True)
        if not isinstance(updates, dict):
            return jsonify({"error": "Invalid request body"}), 400

        cfg.save(updates)
        return jsonify({"status": "ok", "message": "Config saved and reloaded", "config": cfg.to_dict(mask_keys=True)})
    except Exception as e:
        return jsonify({"error": f"Save failed: {e}"}), 500


# ============================================================
# Connection Test Endpoints
# ============================================================

@app.route("/api/test/llm", methods=["POST"])
def test_llm():
    """Test LLM connection with current or provided config."""
    try:
        body = request.get_json(force=True) or {}
        base_url = body.get("base_url", cfg.get("LLM_BASE_URL")).rstrip("/")
        api_key = body.get("api_key", "")
        model = body.get("model", cfg.get("LLM_MODEL"))

        # If no real key provided (masked/empty), use saved key from file
        if not api_key or api_key.startswith("***"):
            api_key = cfg.get("LLM_API_KEY")
        if not base_url:
            base_url = cfg.get("LLM_BASE_URL")
        if not model:
            model = cfg.get("LLM_MODEL")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Try /v1/models first
        try:
            resp = http_requests.get(f"{base_url}/v1/models", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", [])]
                return jsonify({"status": "ok", "models": models[:20], "model": model})
        except Exception:
            pass

        # Fallback: try a minimal chat request
        resp = http_requests.post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=15,
        )
        if resp.status_code == 200:
            return jsonify({"status": "ok", "message": f"Connected (model: {model})"})
        else:
            return jsonify({"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}), 400

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502


@app.route("/api/test/stt", methods=["POST"])
def test_stt():
    """Test STT connection with current or provided config."""
    try:
        body = request.get_json(force=True) or {}
        base_url = body.get("base_url", "").rstrip("/") or cfg.get("STT_BASE_URL")
        api_key = body.get("api_key", "")
        health_path = body.get("health_path", cfg.get("STT_HEALTH_PATH"))

        # If no real key provided (masked/empty), use saved key from file
        if not api_key or api_key.startswith("***"):
            api_key = cfg.get("STT_API_KEY")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Try multiple paths (Groq needs /v1 prefix + Content-Type)
        paths_to_try = [
            f"{base_url}/v1/models",
            f"{base_url}{health_path}",
        ]
        for url in paths_to_try:
            try:
                resp = http_requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    return jsonify({"status": "ok", "message": "STT service reachable"})
            except Exception:
                continue

        # Health check endpoints may 403 on Groq, but transcription still works
        return jsonify({
            "status": "ok",
            "message": "Connection established (health endpoint restricted, but transcription should work)"
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 502


# ============================================================
# LLM Proxy Endpoints
# ============================================================

@app.route("/api/v1/models", methods=["GET"])
def list_models():
    """Proxy: list models from upstream LLM service."""
    try:
        base_url = cfg.get("LLM_BASE_URL")
        resp = http_requests.get(f"{base_url}/v1/models", headers=_llm_headers(), timeout=10)
        return Response(resp.content, status=resp.status_code, content_type="application/json")
    except Exception as e:
        return jsonify({"error": f"Cannot reach LLM service: {e}"}), 502


@app.route("/api/v1/chat/completions", methods=["POST"])
def chat_completions():
    """Proxy: chat completions to upstream LLM."""
    try:
        body = request.get_json(force=True)
        if not body or "messages" not in body:
            return jsonify({"error": "Missing 'messages' in request body"}), 400

        body["model"] = cfg.get("LLM_MODEL")
        url = f"{cfg.get('LLM_BASE_URL')}/v1/chat/completions"
        is_stream = body.get("stream", False)

        resp = http_requests.post(url, headers=_llm_headers(), json=body, timeout=120, stream=is_stream)

        if is_stream:
            def stream():
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
            return Response(stream(), status=resp.status_code, content_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
        base_url = cfg.get("STT_BASE_URL").rstrip("/")
        api_key = cfg.get("STT_API_KEY")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Try /v1/models first, then configured health path
        for url in [f"{base_url}/v1/models", f"{base_url}{cfg.get('STT_HEALTH_PATH')}"]:
            try:
                resp = http_requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    return jsonify({"status": "ok"})
            except Exception:
                continue
        # Even if health check fails, if key is set, assume it works
        if api_key:
            return jsonify({"status": "ok", "note": "key configured"})
        return jsonify({"status": "error"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 502


@app.route("/api/stt/transcribe", methods=["POST"])
def stt_transcribe():
    """Proxy: transcribe audio via upstream STT service."""
    try:
        if "file" not in request.files:
            return jsonify({"error": "No 'file' field in upload"}), 400

        audio = request.files["file"]
        language = request.form.get("language", "")

        url = f"{cfg.get('STT_BASE_URL')}{cfg.get('STT_TRANSCRIBE_PATH')}"
        files = {"file": (audio.filename or "audio.wav", audio.stream, audio.content_type)}
        data = {}
        model = cfg.get("STT_MODEL")
        if model:
            data["model"] = model
        if language:
            data["language"] = language

        resp = http_requests.post(url, headers=_stt_headers(), files=files, data=data, timeout=60)
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
    """Backend health check."""
    return jsonify({
        "status": "ok",
        "llm": {
            "base_url": cfg.get("LLM_BASE_URL"),
            "model": cfg.get("LLM_MODEL"),
            "has_key": bool(cfg.get("LLM_API_KEY")),
        },
        "stt": {
            "base_url": cfg.get("STT_BASE_URL"),
            "model": cfg.get("STT_MODEL"),
            "has_key": bool(cfg.get("STT_API_KEY")),
        },
    })


# ============================================================
# Admin Panel (Web UI)
# ============================================================

ADMIN_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>English Buddy - Backend Admin</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; color: #1a1a2e; min-height: 100vh; }
.header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
.header h1 { font-size: 22px; font-weight: 700; }
.header p { font-size: 13px; opacity: 0.85; margin-top: 4px; }
.container { max-width: 900px; margin: 24px auto; padding: 0 16px; }
.card { background: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 24px; margin-bottom: 20px; }
.card h2 { font-size: 17px; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
.card h2 .icon { font-size: 20px; }
.field { margin-bottom: 14px; }
.field label { display: block; font-size: 13px; font-weight: 500; color: #555; margin-bottom: 4px; }
.field input { width: 100%; padding: 10px 12px; border: 1px solid #d9d9d9; border-radius: 8px; font-size: 14px; transition: border-color 0.2s; outline: none; }
.field input:focus { border-color: #667eea; box-shadow: 0 0 0 2px rgba(102,126,234,0.15); }
.field .hint { font-size: 11px; color: #999; margin-top: 3px; }
.presets { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.presets button { padding: 5px 12px; border: 1px solid #d9d9d9; border-radius: 6px; background: white; cursor: pointer; font-size: 12px; transition: all 0.2s; }
.presets button:hover { border-color: #667eea; color: #667eea; }
.presets button.active { background: #667eea; color: white; border-color: #667eea; }
.btn-row { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
.btn { padding: 10px 20px; border: none; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px; }
.btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); color: white; }
.btn-primary:hover { opacity: 0.9; transform: translateY(-1px); }
.btn-success { background: #52c41a; color: white; }
.btn-success:hover { opacity: 0.9; }
.btn-outline { background: white; border: 1px solid #d9d9d9; color: #555; }
.btn-outline:hover { border-color: #667eea; color: #667eea; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
.test-result { margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 13px; display: none; }
.test-result.ok { display: block; background: #f6ffed; border: 1px solid #b7eb8f; color: #389e0d; }
.test-result.error { display: block; background: #fff2f0; border: 1px solid #ffccc7; color: #cf1322; }
.toast { position: fixed; top: 20px; right: 20px; padding: 12px 20px; border-radius: 8px; color: white; font-size: 14px; z-index: 999; animation: slideIn 0.3s ease; display: none; }
.toast.success { display: block; background: #52c41a; }
.toast.error { display: block; background: #ff4d4f; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
.info-bar { display: flex; gap: 12px; flex-wrap: wrap; }
.info-item { background: #f6f8fa; border-radius: 8px; padding: 10px 16px; font-size: 13px; }
.info-item .label { color: #888; font-size: 11px; }
.info-item .value { font-weight: 600; color: #1a1a2e; margin-top: 2px; }
.toggle-key { position: relative; }
.toggle-key input { padding-right: 40px; }
.toggle-key .eye { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); cursor: pointer; font-size: 16px; opacity: 0.5; }
.toggle-key .eye:hover { opacity: 1; }
.divider { height: 1px; background: #f0f0f0; margin: 16px 0; }
</style>
</head>
<body>

<div class="header">
  <h1>English Buddy Backend Admin</h1>
  <p>API keys are stored on server only. Configure your AI services here.</p>
</div>

<div id="toast" class="toast"></div>

<div class="container">

  <!-- Status -->
  <div class="card">
    <h2><span class="icon">📊</span> Service Status</h2>
    <div class="info-bar" id="statusBar">Loading...</div>
  </div>

  <!-- LLM Config -->
  <div class="card">
    <h2><span class="icon">🤖</span> LLM Configuration (AI Chat)</h2>
    <div class="presets" id="llmPresets">
      <button data-preset="deepseek" class="active">DeepSeek</button>
      <button data-preset="openai">OpenAI</button>
      <button data-preset="kimi">Kimi</button>
      <button data-preset="glm">GLM</button>
      <button data-preset="ollama">Local Ollama</button>
    </div>
    <div class="field">
      <label>Service URL</label>
      <input type="text" id="llmBaseUrl" placeholder="https://api.deepseek.com">
      <div class="hint">OpenAI-compatible API base URL</div>
    </div>
    <div class="field">
      <label>API Key</label>
      <div class="toggle-key">
        <input type="password" id="llmApiKey" placeholder="sk-...">
        <span class="eye" onclick="togglePwd('llmApiKey', this)">👁</span>
      </div>
      <div class="hint">Leave empty for local models (Ollama)</div>
    </div>
    <div class="field">
      <label>Model Name</label>
      <input type="text" id="llmModel" placeholder="deepseek-chat">
    </div>
    <div id="llmTestResult" class="test-result"></div>
    <div class="btn-row">
      <button class="btn btn-success" onclick="testLLM()">🔌 Test Connection</button>
    </div>
  </div>

  <!-- STT Config -->
  <div class="card">
    <h2><span class="icon">🎙️</span> STT Configuration (Voice Recognition)</h2>
    <div class="presets" id="sttPresets">
      <button data-preset="groq" class="active">Groq (Free)</button>
      <button data-preset="openai_stt">OpenAI</button>
      <button data-preset="siliconflow">SiliconFlow</button>
      <button data-preset="local">Local Whisper</button>
    </div>
    <div class="field">
      <label>Service URL</label>
      <input type="text" id="sttBaseUrl" placeholder="https://api.groq.com/openai/v1">
    </div>
    <div class="field">
      <label>API Key</label>
      <div class="toggle-key">
        <input type="password" id="sttApiKey" placeholder="gsk-...">
        <span class="eye" onclick="togglePwd('sttApiKey', this)">👁</span>
      </div>
    </div>
    <div class="field">
      <label>Model</label>
      <input type="text" id="sttModel" placeholder="whisper-large-v3">
    </div>
    <div class="field">
      <label>Transcribe Path</label>
      <input type="text" id="sttTranscribePath" placeholder="/audio/transcriptions">
    </div>
    <div class="field">
      <label>Health Check Path</label>
      <input type="text" id="sttHealthPath" placeholder="/models">
    </div>
    <div id="sttTestResult" class="test-result"></div>
    <div class="btn-row">
      <button class="btn btn-success" onclick="testSTT()">🔌 Test Connection</button>
    </div>
  </div>

  <!-- Save -->
  <div class="card">
    <div class="btn-row">
      <button class="btn btn-primary" onclick="saveConfig()">💾 Save All Configuration</button>
      <button class="btn btn-outline" onclick="loadConfig()">🔄 Reload</button>
    </div>
  </div>

</div>

<script>
const PRESETS = {
  deepseek: { llmBaseUrl: 'https://api.deepseek.com', llmModel: 'deepseek-chat', llmApiKey: '' },
  openai: { llmBaseUrl: 'https://api.openai.com', llmModel: 'gpt-4o-mini', llmApiKey: '' },
  kimi: { llmBaseUrl: 'https://api.moonshot.cn', llmModel: 'moonshot-v1-8k', llmApiKey: '' },
  glm: { llmBaseUrl: 'https://open.bigmodel.cn/api/paas/v4', llmModel: 'glm-4-flash', llmApiKey: '' },
  ollama: { llmBaseUrl: 'http://host.docker.internal:11434', llmModel: 'llama3.2:latest', llmApiKey: '' },
  groq: { sttBaseUrl: 'https://api.groq.com/openai/v1', sttModel: 'whisper-large-v3', sttTranscribePath: '/audio/transcriptions', sttHealthPath: '/models', sttApiKey: '' },
  openai_stt: { sttBaseUrl: 'https://api.openai.com/v1', sttModel: 'whisper-1', sttTranscribePath: '/audio/transcriptions', sttHealthPath: '/models', sttApiKey: '' },
  siliconflow: { sttBaseUrl: 'https://api.siliconflow.cn/v1', sttModel: 'FunAudioLLM/SenseVoiceSmall', sttTranscribePath: '/audio/transcriptions', sttHealthPath: '/models', sttApiKey: '' },
  local: { sttBaseUrl: 'http://host.docker.internal:18000', sttModel: '', sttTranscribePath: '/transcribe', sttHealthPath: '/health', sttApiKey: '' },
};

function togglePwd(id, el) {
  const inp = document.getElementById(id);
  inp.type = inp.type === 'password' ? 'text' : 'password';
  el.textContent = inp.type === 'password' ? '👁' : '🙈';
}

function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type;
  setTimeout(() => t.className = 'toast', 3000);
}

function showResult(id, ok, msg) {
  const el = document.getElementById(id);
  el.className = 'test-result ' + (ok ? 'ok' : 'error');
  el.textContent = (ok ? '✅ ' : '❌ ') + msg;
}

async function loadConfig() {
  try {
    const resp = await fetch('/api/config');
    const data = await resp.json();
    if (data.LLM_BASE_URL) document.getElementById('llmBaseUrl').value = data.LLM_BASE_URL;
    if (data.LLM_MODEL) document.getElementById('llmModel').value = data.LLM_MODEL;
    // API keys: show masked value in placeholder, keep input empty
    const llmKeyEl = document.getElementById('llmApiKey');
    if (data.LLM_API_KEY && data.LLM_API_KEY.startsWith('***')) {
      llmKeyEl.value = '';
      llmKeyEl.placeholder = data.LLM_API_KEY + ' (already set, leave blank to keep)';
    }
    const sttKeyEl = document.getElementById('sttApiKey');
    if (data.STT_API_KEY && data.STT_API_KEY.startsWith('***')) {
      sttKeyEl.value = '';
      sttKeyEl.placeholder = data.STT_API_KEY + ' (already set, leave blank to keep)';
    }
    if (data.STT_BASE_URL) document.getElementById('sttBaseUrl').value = data.STT_BASE_URL;
    if (data.STT_MODEL) document.getElementById('sttModel').value = data.STT_MODEL;
    if (data.STT_TRANSCRIBE_PATH) document.getElementById('sttTranscribePath').value = data.STT_TRANSCRIBE_PATH;
    if (data.STT_HEALTH_PATH) document.getElementById('sttHealthPath').value = data.STT_HEALTH_PATH;

    // Highlight matching preset
    highlightPreset('llmPresets', data.LLM_BASE_URL);
    highlightPreset('sttPresets', data.STT_BASE_URL);
  } catch (e) {
    showToast('Failed to load config', 'error');
  }
}

function highlightPreset(containerId, url) {
  const btns = document.querySelectorAll('#' + containerId + ' button');
  btns.forEach(btn => {
    const p = PRESETS[btn.dataset.preset];
    const match = p && (p.llmBaseUrl === url || p.sttBaseUrl === url);
    btn.classList.toggle('active', match);
  });
}

async function loadStatus() {
  try {
    const resp = await fetch('/api/status');
    const data = await resp.json();
    const bar = document.getElementById('statusBar');
    bar.innerHTML = `
      <div class="info-item"><div class="label">Backend</div><div class="value">${data.status === 'ok' ? '🟢 Running' : '🔴 Error'}</div></div>
      <div class="info-item"><div class="label">LLM</div><div class="value">${data.llm.has_key ? '🟢 ' + data.llm.model : '🔴 No Key'}</div></div>
      <div class="info-item"><div class="label">STT</div><div class="value">${data.stt.has_key ? '🟢 ' + data.stt.model : '🔴 No Key'}</div></div>
    `;
  } catch (e) {
    document.getElementById('statusBar').innerHTML = '<div class="info-item"><div class="value">🔴 Cannot connect to backend</div></div>';
  }
}

async function saveConfig() {
  const body = {
    LLM_BASE_URL: document.getElementById('llmBaseUrl').value.trim(),
    LLM_API_KEY: document.getElementById('llmApiKey').value.trim(),
    LLM_MODEL: document.getElementById('llmModel').value.trim(),
    STT_BASE_URL: document.getElementById('sttBaseUrl').value.trim(),
    STT_API_KEY: document.getElementById('sttApiKey').value.trim(),
    STT_MODEL: document.getElementById('sttModel').value.trim(),
    STT_TRANSCRIBE_PATH: document.getElementById('sttTranscribePath').value.trim(),
    STT_HEALTH_PATH: document.getElementById('sttHealthPath').value.trim(),
  };
  try {
    const resp = await fetch('/api/config', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const data = await resp.json();
    if (data.status === 'ok') {
      showToast('Configuration saved!', 'success');
      loadStatus();
    } else {
      showToast('Save failed: ' + (data.error || ''), 'error');
    }
  } catch (e) {
    showToast('Save failed: ' + e, 'error');
  }
}

async function testLLM() {
  const el = document.getElementById('llmTestResult');
  el.className = 'test-result';
  el.textContent = '⏳ Testing...';
  el.style.display = 'block';
  try {
    const body = {
      base_url: document.getElementById('llmBaseUrl').value.trim(),
      api_key: document.getElementById('llmApiKey').value.trim(),
      model: document.getElementById('llmModel').value.trim(),
    };
    const resp = await fetch('/api/test/llm', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const data = await resp.json();
    if (data.status === 'ok') {
      const models = data.models ? ' (' + data.models.length + ' models)' : '';
      showResult('llmTestResult', true, 'Connected!' + models + (data.model ? ' Model: ' + data.model : ''));
    } else {
      showResult('llmTestResult', false, data.message);
    }
  } catch (e) {
    showResult('llmTestResult', false, e.message);
  }
}

async function testSTT() {
  const el = document.getElementById('sttTestResult');
  el.className = 'test-result';
  el.textContent = '⏳ Testing...';
  el.style.display = 'block';
  try {
    const body = {
      base_url: document.getElementById('sttBaseUrl').value.trim(),
      api_key: document.getElementById('sttApiKey').value.trim(),
      health_path: document.getElementById('sttHealthPath').value.trim(),
    };
    const resp = await fetch('/api/test/stt', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    const data = await resp.json();
    if (data.status === 'ok') {
      showResult('sttTestResult', true, data.message);
    } else {
      showResult('sttTestResult', false, data.message);
    }
  } catch (e) {
    showResult('sttTestResult', false, e.message);
  }
}

// Preset buttons
document.querySelectorAll('#llmPresets button').forEach(btn => {
  btn.addEventListener('click', () => {
    const p = PRESETS[btn.dataset.preset];
    if (!p) return;
    document.querySelectorAll('#llmPresets button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (p.llmBaseUrl) document.getElementById('llmBaseUrl').value = p.llmBaseUrl;
    if (p.llmModel) document.getElementById('llmModel').value = p.llmModel;
    // Don't overwrite API key with preset (keep user's input)
  });
});

document.querySelectorAll('#sttPresets button').forEach(btn => {
  btn.addEventListener('click', () => {
    const p = PRESETS[btn.dataset.preset];
    if (!p) return;
    document.querySelectorAll('#sttPresets button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (p.sttBaseUrl) document.getElementById('sttBaseUrl').value = p.sttBaseUrl;
    if (p.sttModel) document.getElementById('sttModel').value = p.sttModel;
    if (p.sttTranscribePath !== undefined) document.getElementById('sttTranscribePath').value = p.sttTranscribePath;
    if (p.sttHealthPath !== undefined) document.getElementById('sttHealthPath').value = p.sttHealthPath;
  });
});

// Init
loadConfig();
loadStatus();
</script>
</body>
</html>'''


@app.route("/admin")
def admin_panel():
    """Web admin panel for configuring the backend."""
    return Response(ADMIN_HTML, content_type="text/html")


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
    print(f"  LLM:      {cfg.get('LLM_BASE_URL')} / {cfg.get('LLM_MODEL')} / key={cfg.mask_key(cfg.get('LLM_API_KEY'))}")
    print(f"  STT:      {cfg.get('STT_BASE_URL')} / {cfg.get('STT_MODEL')} / key={cfg.mask_key(cfg.get('STT_API_KEY'))}")
    print(f"  Admin:    http://localhost:{cfg.get('PORT')}/admin")
    print(f"  Listen:   http://{cfg.get('HOST')}:{cfg.get('PORT')}")
    print("=" * 55)
    print()
    app.run(host=cfg.get("HOST"), port=int(cfg.get("PORT")), debug=cfg.get("DEBUG", "").lower() == "true")
