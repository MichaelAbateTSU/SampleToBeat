# ---- web app
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_SUPPORT_EMAIL
ARG VITE_LEGAL_ENTITY
RUN npm run build

# ---- API + engine
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/requirements.lock.txt backend/.python-version ./
COPY backend/src ./src
RUN uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache . 
COPY --from=web /web/dist /app/frontend/dist
RUN useradd --create-home choplab && mkdir /data && chown choplab /data
USER choplab
ENV PATH=/opt/venv/bin:$PATH \
    CHOPLAB_HOST=0.0.0.0 \
    CHOPLAB_DATA=/data \
    CHOPLAB_FRONTEND=/app/frontend/dist \
    NUMBA_CACHE_DIR=/tmp/numba
VOLUME /data
EXPOSE 8765
# listens on $CHOPLAB_PORT, else the host's $PORT (Render, Fly, Railway), else 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;e=os.environ;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % (e.get('CHOPLAB_PORT') or e.get('PORT') or '8765'))"
CMD ["choplab"]
