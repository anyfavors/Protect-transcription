# Use a stable slim version of Python
FROM python:3.12-slim

# Environment variables for Python performance and security
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ="Europe/Copenhagen"

# Install system dependencies (ffmpeg for audio processing and curl for healthcheck)
# Commands are combined to minimize the number of image layers
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install 'uv' extremely fast from the official binary image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy requirements first to leverage Docker layer caching
# If requirements.txt is unchanged, this layer is reused in subsequent builds
COPY requirements.txt .

# Install Python packages directly into the system environment using uv (much faster than pip)
RUN uv pip install --system --no-cache -r requirements.txt

# Copy the rest of the application code
# Note: Ensure you rename your local "app (1).py" to "app.py" in your repository
COPY app.py .
COPY templates/ templates/

# Create data directories with appropriate permissions for persistent storage
RUN mkdir -p /data/audio && chmod 777 /data/audio

# Environment defaults (can be overridden at runtime via docker-compose or -e flags)
ENV PROTECT_HOST="argos.local" \
    PROTECT_PORT="443" \
    WHISPER_URL="http://whisper-server:8000" \
    DATABASE_PATH="/data/transcriptions.db" \
    AUDIO_PATH="/data/audio" \
    AUDIO_BUFFER_BEFORE="5" \
    AUDIO_BUFFER_AFTER="10"

EXPOSE 8080

# Improved Healthcheck using curl for reliability
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Start the application using uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
