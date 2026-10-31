# Temporary, test-only upload-boundary probe. Not part of the ChopLab app.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 probe
WORKDIR /app
COPY server.py .
USER probe
EXPOSE 10000
CMD ["python", "server.py"]
