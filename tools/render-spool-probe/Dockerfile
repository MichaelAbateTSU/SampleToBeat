FROM python:3.12-slim-bookworm
ARG SOURCE_SHA=fd53e2af147f69d6ab44b66be479067164267d02
RUN apt-get update && apt-get install -y --no-install-recommends git ffmpeg libsndfile1 libgomp1 libatomic1 \
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q https://github.com/MichaelAbateTSU/SampleToBeat.git /source \
    && cd /source && git checkout -q "$SOURCE_SHA" \
    && test "$(git rev-parse HEAD)" = "$SOURCE_SHA" \
    && git rev-parse HEAD > /source-sha
WORKDIR /source/backend
RUN pip install --no-cache-dir ".[postgres,storage]" \
    && pip install --no-cache-dir fastapi==0.157.0 starlette==1.9.0 python-multipart==0.0.32 uvicorn==0.54.0 \
    && pip freeze > /installed.txt
RUN groupadd --gid 10001 choplab && useradd --uid 10001 --gid choplab --create-home choplab \
    && mkdir /data /probe && chown choplab:choplab /data /probe
COPY server.py /probe/server.py
ENV CHOPLAB_MODE=hosted CHOPLAB_PUBLIC_URL=https://spool-probe.invalid CHOPLAB_DATA=/data \
    CHOPLAB_MAX_UPLOAD_MB=1024 CHOPLAB_MIN_FREE_MB=2048 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NUMBA_CACHE_DIR=/tmp/numba NUMBA_CPU_NAME=generic \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1
USER choplab
WORKDIR /probe
CMD ["python", "server.py"]
