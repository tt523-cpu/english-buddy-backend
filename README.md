# English Buddy Backend

> AI API 代理后端，支持 Web 管理页面配置，一键 Docker 部署。

## 一键部署

```bash
git clone https://github.com/tt523-cpu/english-buddy-backend.git
cd english-buddy-backend
docker compose up -d
```

然后打开 **http://服务器IP:5000/admin** 在网页里配置 API Key。

## 支持的 AI 服务

| 服务 | 类型 | 说明 |
|------|------|------|
| DeepSeek | LLM | 推荐使用，性价比高 |
| OpenAI | LLM | GPT-4o / GPT-4o-mini |
| Kimi (月之暗面) | LLM | Moonshot-v1 系列 |
| GLM (智谱) | LLM | GLM-4 系列 |
| Ollama | LLM | 本地部署模型 |
| Groq | STT | **免费**语音识别 |
| OpenAI | STT | Whisper |
| SiliconFlow | STT | SenseVoice |

## 目录结构

```
english-buddy-backend/
├── server.py          # 后端服务（Flask）
├── requirements.txt   # Python 依赖
├── Dockerfile         # Docker 镜像
├── docker-compose.yml # Docker Compose
├── .env.example       # 配置模板
├── data/              # 持久化数据（Docker 卷挂载）
│   └── .env           # 运行时配置（自动生成）
└── web/               # 前端静态文件（放 Flutter 构建产物）
```

## API 接口

| Endpoint | 说明 |
|----------|------|
| `GET /admin` | Web 管理页面 |
| `GET /api/config` | 读取配置（Key 脱敏） |
| `POST /api/config` | 保存配置 |
| `POST /api/test/llm` | 测试 LLM 连接 |
| `POST /api/test/stt` | 测试 STT 连接 |
| `GET /api/status` | 健康检查 |
| `POST /api/v1/chat/completions` | LLM 对话代理 |
| `GET /api/v1/models` | 模型列表 |
| `POST /api/stt/transcribe` | 语音转文字代理 |

## 常用命令

```bash
# 查看日志
docker compose logs -f

# 重启
docker compose restart

# 更新代码
git pull && docker compose up -d --build
```
