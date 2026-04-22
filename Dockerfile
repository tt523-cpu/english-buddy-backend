# ============================================================
# English Buddy Backend - Dockerfile
# Python backend + optional frontend static files
# ============================================================

FROM python:3.11-slim

LABEL maintainer="English Buddy"
LABEL description="Backend proxy for English Buddy - LLM & STT API gateway"

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY server.py .
COPY .env.example .

# Create data dir for persistent config
RUN mkdir -p /app/data /app/web

# Copy frontend if it exists
# Before building, run: flutter build web --release && cp -r build/web/ backend/web/
COPY web* /app/web/

# Environment
ENV PYTHONUNBUFFERED=1
ENV STATIC_DIR=/app/web
ENV DATA_DIR=/app/data

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import requests; r = requests.get('http://localhost:5000/api/status'); r.raise_for_status()"

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "server:app"]
