FROM python:3.12-slim-bookworm
RUN pip install --no-cache-dir boto3==1.43.118 pytest==9.1.1
COPY backend/src/choplab/object_storage.py /opt/choplab/object_storage.py
COPY backend/storage_tests /tests
ENV PYTHONPATH=/opt PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "/tests/linux_s3_boundary.py"]
