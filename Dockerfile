# Use a stable slim version
FROM python:3.12-slim

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ="Europe/Copenhagen"

# Install uv binary directly from the official image (faster than pip install)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies (ffmpeg, curl)
# Cleaning up apt lists to keep image size down
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .

# Install Python packages using uv
# --system: Install into system python (no venv needed in container)
# --no-cache: We rely on Docker layer caching, not uv's internal cache here
RUN uv pip install --system --no-cache -r requirements.txt

# Copy the application code
# This is done AFTER installing requirements so code changes don't trigger re-installs
COPY app.py .
COPY templates/ templates/

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

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Start the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
