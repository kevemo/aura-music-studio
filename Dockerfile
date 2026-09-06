FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AURA_PROJECTS_ROOT=/app/projects \
    LSS_DB_PATH=/app/data/live_sound_studio.sqlite3 \
    LSS_BACKUP_DIR=/app/backups

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 curl git miniupnpc age \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY aura_music_studio ./aura_music_studio
COPY app.py ./app.py
COPY worker.py ./worker.py
COPY projects ./projects

RUN pip install --upgrade pip \
    && pip install .

RUN mkdir -p /app/data /app/projects /app/backups

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# Forwarded client identity is authenticated inside the application with the Caddy-to-app shared
# proxy token. Disable Uvicorn's automatic X-Forwarded-* processing so untrusted headers can never
# rewrite the ASGI peer before that application security boundary runs.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
