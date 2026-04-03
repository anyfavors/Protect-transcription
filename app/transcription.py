"""
Audio fetching, ffmpeg extraction, and Whisper transcription.
"""

import logging
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import httpx

from app.config import AUDIO_PATH, WHISPER_URL
from app.database import get_settings
from app.protect import get_protect_client

logger = logging.getLogger(__name__)


async def fetch_audio_clip(
    camera_id: str,
    start_time: datetime,
    end_time: datetime,
) -> bytes | None:
    """
    Fetch a video clip from Protect and extract 16 kHz mono WAV audio via ffmpeg.
    camera_id can be a UUID or a MAC address.
    """
    try:
        client = await get_protect_client()

        # Try direct UUID lookup first, then MAC address lookup
        camera = client.bootstrap.cameras.get(camera_id)
        if not camera:
            normalized_mac = camera_id.upper().replace(":", "").replace("-", "")
            for cam in client.bootstrap.cameras.values():
                if cam.mac.upper().replace(":", "").replace("-", "") == normalized_mac:
                    camera = cam
                    logger.info("Found camera by MAC: %s (%s)", cam.name, cam.id)
                    break

        if not camera:
            logger.error("Camera %s not found (tried UUID and MAC)", camera_id)
            logger.info(
                "Available cameras: %s",
                [(c.name, c.mac, c.id) for c in client.bootstrap.cameras.values()],
            )
            return None

        logger.info(
            "Fetching clip from %s (%s to %s)",
            camera.name,
            start_time.isoformat(),
            end_time.isoformat(),
        )

        video_data: bytes | None = None
        try:
            if hasattr(camera, "get_video"):
                video_data = await camera.get_video(start_time, end_time)
            elif hasattr(camera, "export_video"):
                video_data = await camera.export_video(start_time, end_time)
            else:
                video_methods = [
                    m for m in dir(camera) if "video" in m.lower() or "export" in m.lower()
                ]
                logger.error("No video export method found. Available: %s", video_methods)
                return None
        except TypeError as exc:
            logger.warning("Video method TypeError (%s), trying output_file fallback", exc)
            try:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                if hasattr(camera, "get_video"):
                    await camera.get_video(start_time, end_time, output_file=tmp_path)
                    video_data = tmp_path.read_bytes()
                    tmp_path.unlink(missing_ok=True)
            except Exception as exc2:
                logger.error("Fallback also failed: %s", exc2)
                raise

        if not video_data:
            logger.error("No video data received from Protect")
            return None

        logger.info("Received %d bytes of video data", len(video_data))
        return _extract_audio(video_data)

    except Exception:
        logger.exception("Error fetching audio clip for camera %s", camera_id)
        return None


def _extract_audio(video_data: bytes) -> bytes | None:
    """Run ffmpeg to extract 16 kHz mono WAV from raw video bytes."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
        vf.write(video_data)
        video_path = Path(vf.name)

    audio_path = video_path.with_suffix(".wav")

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-af",
                "highpass=f=200,loudnorm",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            logger.error("ffmpeg error (rc=%d): %s", result.returncode, result.stderr)
            return None

        if not audio_path.exists() or audio_path.stat().st_size == 0:
            logger.error("ffmpeg produced empty audio file")
            return None

        audio_bytes = audio_path.read_bytes()
        logger.info("Extracted %d bytes of audio", len(audio_bytes))
        return audio_bytes

    finally:
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)


def _is_hallucination(text: str) -> bool:
    """
    Detect Whisper hallucination: the same 2-5 word n-gram repeating 4+ times
    consecutively (e.g. "tak tak tak tak tak tak").
    """
    if not text or len(text) < 20:
        return False
    words = text.lower().split()
    if len(words) < 6:
        return False
    for n in range(2, 6):
        for i in range(len(words) - n * 3):
            phrase = tuple(words[i : i + n])
            repeats = 1
            j = i + n
            while j + n <= len(words) and tuple(words[j : j + n]) == phrase:
                repeats += 1
                j += n
            if repeats >= 4:
                return True
    return False


async def transcribe_audio(audio_data: bytes) -> dict:
    """
    Submit audio to the Whisper (speaches) server and return the parsed JSON.
    Returns a dict with an 'error' key on failure.
    """
    settings = get_settings()
    model = settings.get("whisper_model", "Systran/faster-whisper-large-v3")
    language = settings.get("language", "da")
    vad_filter = settings.get("vad_filter", "true").lower() == "true"
    condition_on_previous = settings.get("condition_on_previous_text", "false").lower() == "true"
    no_speech_threshold = settings.get("no_speech_threshold", "0.6")
    compression_ratio_threshold = settings.get("compression_ratio_threshold", "2.4")

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            files = {"file": ("audio.wav", audio_data, "audio/wav")}
            data: dict = {
                "model": model,
                "language": language,
                "response_format": "verbose_json",
                "temperature": "0.0",
                "initial_prompt": (
                    "Dette er en optagelse fra et overvågningskamera i et privat hjem. "
                    "Samtalen er på dansk."
                ),
                "condition_on_previous_text": str(condition_on_previous).lower(),
                "no_speech_threshold": no_speech_threshold,
                "compression_ratio_threshold": compression_ratio_threshold,
            }
            if vad_filter:
                data["vad_filter"] = "true"

            logger.info(
                "Transcribing model=%s lang=%s vad=%s condition_on_previous=%s",
                model,
                language,
                vad_filter,
                condition_on_previous,
            )

            response = await client.post(
                f"{WHISPER_URL}/v1/audio/transcriptions",
                files=files,
                data=data,
            )

            if response.status_code != 200:
                logger.error("Whisper API error %d: %s", response.status_code, response.text)
                return {"error": response.text}

            result = response.json()
            text = result.get("text", "")
            if _is_hallucination(text):
                logger.warning("Hallucination detected, discarding: %r", text[:120])
                return {"error": "hallucination_detected", "raw_text": text}

            logger.info("Transcription: %s...", text[:100])
            return result

    except Exception as exc:
        logger.exception("Error calling Whisper API: %s", exc)
        return {"error": str(exc)}


def save_audio_file(audio_data: bytes, event_time: datetime, camera_name: str) -> str:
    """Persist audio bytes to AUDIO_PATH and return the filename."""
    import hashlib

    Path(AUDIO_PATH).mkdir(parents=True, exist_ok=True)
    audio_hash = hashlib.md5(audio_data).hexdigest()[:8]
    filename = f"{event_time.strftime('%Y%m%d_%H%M%S')}_{camera_name}_{audio_hash}.wav"
    (Path(AUDIO_PATH) / filename).write_bytes(audio_data)
    return filename
