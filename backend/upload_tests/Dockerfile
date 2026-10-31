FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 libatomic1 \
    && rm -rf /var/lib/apt/lists/*
COPY backend /backend
WORKDIR /backend
RUN pip install --no-cache-dir ".[postgres,storage]" \
    && pip install --no-cache-dir fastapi==0.157.0 starlette==1.9.0 python-multipart==0.0.32 \
       uvicorn==0.54.0 pytest==9.1.1 httpx==0.28.1
RUN useradd --uid 10001 --create-home choplab && mkdir /data && chown choplab /data
ENV CHOPLAB_MODE=hosted CHOPLAB_PUBLIC_URL=https://localhost CHOPLAB_DATA=/data \
    CHOPLAB_MAX_UPLOAD_MB=1024 CHOPLAB_MIN_FREE_MB=2048 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NUMBA_CPU_NAME=generic \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1
USER choplab
CMD ["python", "/backend/upload_tests/server.py"]
