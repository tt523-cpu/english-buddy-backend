# English Buddy Backend

后端 API 代理服务器，为 English Buddy 英语学习 App 提供 AI 服务。

**核心功能**：API Key 全部存在服务端，前端无需配置，开箱即用。

## Quick Deploy

```bash
git clone https://github.com/YOUR_USERNAME/english-buddy-backend.git
cd english-buddy-backend

cp .env.example .env
# 编辑 .env 填入你的 API Key

docker compose up -d
# 打开 http://服务器IP:5000
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | 后端健康检查 + 配置信息 |
| GET | `/api/v1/models` | 获取可用 LLM 模型列表 |
| POST | `/api/v1/chat/completions` | AI 对话代理 |
| GET | `/api/stt/health` | 语音识别健康检查 |
| POST | `/api/stt/transcribe` | 语音转文字代理 |

## 配置 AI 服务

编辑 `.env` 文件：

### LLM（AI 对话 / 翻译）

| 服务商 | LLM_BASE_URL | LLM_MODEL | 费用 |
|--------|-------------|-----------|------|
| **DeepSeek** | `https://api.deepseek.com` | `deepseek-chat` | ~¥1/百万token |
| OpenAI | `https://api.openai.com` | `gpt-4o-mini` | 较贵 |
| Kimi | `https://api.moonshot.cn` | `moonshot-v1-8k` | 有免费额度 |
| GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | 有免费额度 |
| 本地 Ollama | `http://host.docker.internal:11434` | `llama3.2:latest` | 免费 |

### STT（语音识别）

| 服务商 | STT_BASE_URL | STT_MODEL | 费用 |
|--------|-------------|-----------|------|
| **Groq** | `https://api.groq.com/openai/v1` | `whisper-large-v3` | **免费** |
| OpenAI | `https://api.openai.com/v1` | `whisper-1` | $0.006/分钟 |
| SiliconFlow | `https://api.siliconflow.cn/v1` | `FunAudioLLM/SenseVoiceSmall` | 每天100次免费 |
| 本地 Whisper | `http://host.docker.internal:18000` | _(空)_ | 免费 |

## 挂载前端

把 Flutter Web 构建产物放到 `web/` 目录：

```bash
# Flutter 项目里构建
flutter build web --release

# 复制到后端
cp -r build/web/* /path/to/english-buddy-backend/web/

# 重启
docker compose restart
```

或者用 volume 挂载（修改 docker-compose.yml）：

```yaml
volumes:
  - ./web:/app/web:ro
```

## 测试

```bash
# 检查后端
curl http://localhost:5000/api/status

# 测试 LLM
curl -X POST http://localhost:5000/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello!"}],"max_tokens":50}'

# 检查 STT
curl http://localhost:5000/api/stt/health
```

## 常用命令

```bash
docker compose logs -f       # 查看日志
docker compose restart       # 重启
docker compose down          # 停止
docker compose up -d --build # 代码更新后重建
```

## Architecture

```
Browser / App
    │
    ▼
┌──────────────────────────┐
│   English Buddy Backend   │  ← This server (Flask + Docker)
│   Port 5000               │
│                            │
│  /api/v1/chat/completions │──→ DeepSeek / OpenAI / Ollama
│  /api/stt/transcribe      │──→ Groq / OpenAI / Whisper
│  /                         │──→ Flutter Web (static files)
└──────────────────────────┘
```
