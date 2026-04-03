# Use a stable slim version
FROM python:3.12-slim

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ="Europe/Copenhagen"

# Install uv binary directly from the official image (faster than pip install)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .

# Install Python packages using uv
RUN uv pip install --system --no-cache -r requirements.txt

# Download Tailwind v4 standalone CLI and compile CSS
COPY app.css.src .
COPY static/ static/
COPY templates/ templates/
RUN ARCH=$(dpkg --print-architecture) && \
    case "$ARCH" in \
      amd64) TW_ARCH="linux-x64" ;; \
      arm64) TW_ARCH="linux-arm64" ;; \
      arm*)  TW_ARCH="linux-armv7" ;; \
      *) echo "Unsupported arch: $ARCH" && exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-${TW_ARCH}" \
         -o /usr/local/bin/tailwindcss && \
    chmod +x /usr/local/bin/tailwindcss && \
    tailwindcss -i app.css.src -o static/app.css --minify && \
    rm /usr/local/bin/tailwindcss

# Copy application source
COPY app/ app/

# Create data directories
RUN mkdir -p /data/audio && chmod 777 /data/audio

# Environment defaults
ENV PROTECT_HOST="argos.local" \
    PROTECT_PORT="443" \
    WHISPER_URL="http://whisper-server:8000" \
    DATABASE_PATH="/data/transcriptions.db" \
    AUDIO_PATH="/data/audio" \
    AUDIO_BUFFER_BEFORE="5" \
    AUDIO_BUFFER_AFTER="10"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# app package exposes the FastAPI instance as app:app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
