"""Pydantic request/response models."""

from pydantic import BaseModel


class WebhookPayload(BaseModel):
    """UniFi Protect webhook payload structure."""

    alarm: dict
    timestamp: int


class TranscriptionResponse(BaseModel):
    id: int
    event_id: str
    camera_name: str
    timestamp: str
    transcription: str
    language: str | None
    duration_seconds: float | None
    status: str
    audio_file: str | None
