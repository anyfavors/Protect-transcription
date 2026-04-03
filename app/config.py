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

# Available Whisper models for selection in the UI.
# speaches uses faster-whisper which requires CTranslate2-format models.
AVAILABLE_MODELS: list[dict] = [
    # Danish-optimised (recommended)
    {
        "id": "syvai/faster-hviske-v3-conversation",
        "name": "Hviske V3 Danish (best Danish, CTranslate2)",
        "size": "~3GB",
        "danish": True,
    },
    {
        "id": "CoRal-project/roest-v3-whisper-1.5b",
        "name": "Røst V3 Danish — Alexandra Inst. (57% better CER)",
        "size": "~3GB",
        "danish": True,
    },
    # Generic Whisper
    {"id": "Systran/faster-whisper-large-v3", "name": "Whisper Large V3", "size": "~3GB"},
    {
        "id": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "name": "Whisper Large V3 Turbo (6× faster)",
        "size": "~1.6GB",
    },
    {"id": "Systran/faster-whisper-medium", "name": "Whisper Medium", "size": "~1.5GB"},
    {"id": "Systran/faster-whisper-small", "name": "Whisper Small (fastest)", "size": "~500MB"},
]

AVAILABLE_LANGUAGES: list[dict] = [
    {"code": "da", "name": "Danish"},
    {"code": "en", "name": "English"},
    {"code": "de", "name": "German"},
    {"code": "sv", "name": "Swedish"},
    {"code": "no", "name": "Norwegian"},
    {"code": "auto", "name": "Auto-detect"},
]
