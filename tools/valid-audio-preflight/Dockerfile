FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy==2.5.4 soundfile==0.14.0 scipy==1.18.1 imageio-ffmpeg==0.6.0
COPY backend/src/choplab/audio_io.py backend/src/choplab/core.py /opt/backend/src/choplab/
COPY tools/valid-audio-preflight /tests
ENV PYTHONPATH=/opt/backend/src PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1
WORKDIR /tests
