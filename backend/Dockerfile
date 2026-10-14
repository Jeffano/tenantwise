# Multi stage build: dependencies compile in a builder stage, the final image only gets
# the installed packages. Smaller image means faster pulls and faster Cloud Run cold starts.
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_CACHE_DIR=/models
WORKDIR /app
COPY --from=builder /install /usr/local

# Bake the embedding model into the image so cold starts never download it.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')"

COPY app ./app

# Never run as root inside the container.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app /models
USER appuser

# Cloud Run injects PORT. One worker per instance; Cloud Run scales instances instead.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips="*"
