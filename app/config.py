"""
Central configuration loaded from environment variables.
All other modules import constants from here instead of calling os.getenv directly.
"""

import logging
import os
from zoneinfo import ZoneInfo

PROTECT_HOST: str = os.getenv("PROTECT_HOST", "argos.local")
PROTECT_PORT: int = int(os.getenv("PROTECT_PORT", "443"))
PROTECT_USERNAME: str = os.getenv("PROTECT_USERNAME", "")
PROTECT_PASSWORD: str = os.getenv("PROTECT_PASSWORD", "")

WHISPER_URL: str = os.getenv("WHISPER_URL", "http://whisper-server:8000")

OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")

DATABASE_PATH: str = os.getenv("DATABASE_PATH", "/data/transcriptions.db")
AUDIO_PATH: str = os.getenv("AUDIO_PATH", "/data/audio")

AUDIO_BUFFER_BEFORE: int = int(os.getenv("AUDIO_BUFFER_BEFORE", "5"))
AUDIO_BUFFER_AFTER: int = int(os.getenv("AUDIO_BUFFER_AFTER", "10"))

LOCAL_TZ: ZoneInfo = ZoneInfo(os.getenv("TZ", "Europe/Copenhagen"))

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

AVAILABLE_LANGUAGES: list[dict] = [
    {"code": "da", "name": "Danish"},
    {"code": "en", "name": "English"},
    {"code": "de", "name": "German"},
    {"code": "sv", "name": "Swedish"},
    {"code": "no", "name": "Norwegian"},
    {"code": "auto", "name": "Auto-detect"},
]
