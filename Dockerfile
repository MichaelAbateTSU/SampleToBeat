FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS backend
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
WORKDIR /build/backend
COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN uv venv /opt/venv && uv pip install --compile-bytecode --python /opt/venv/bin/python ".[postgres,storage]" \
    && uv pip freeze --python /opt/venv/bin/python > /opt/requirements-installed.txt

FROM python:3.12-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg libsndfile1 libgomp1 libatomic1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 choplab && groupadd --gid 1000 render-secrets \
    && useradd --uid 10001 --gid choplab --groups render-secrets --create-home choplab \
    && mkdir -p /data && chown choplab:choplab /data
COPY --from=backend /opt/venv /opt/venv
COPY --from=backend /opt/requirements-installed.txt /opt/requirements-installed.txt
COPY --from=frontend --chown=choplab:choplab /build/frontend/dist /opt/frontend
ENV PATH="/opt/venv/bin:$PATH" CHOPLAB_DATA=/data CHOPLAB_FRONTEND=/opt/frontend \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NUMBA_CACHE_DIR=/tmp/numba NUMBA_CPU_NAME=generic \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1
USER choplab
WORKDIR /home/choplab
RUN NUMBA_CACHE_DIR=/home/choplab/.cache/numba python -c "from choplab import demo, analysis; analysis.analyze(demo.make(bars=2))"
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl --fail --silent --show-error --max-time 4.5 "http://127.0.0.1:$(cat /tmp/choplab-health-port)/api/health"
CMD ["sh", "-c", "mkdir -p /tmp/numba && cp -R /home/choplab/.cache/numba/. /tmp/numba/ && python -c 'from choplab.hosting import listening_port; print(listening_port())' > /tmp/choplab-health-port && exec choplab"]
