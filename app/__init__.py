"""
Protect Transcription Service
==============================
FastAPI application factory.  Import ``app`` to get the ASGI app instance.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import AUDIO_PATH
from app.database import init_database
from app.protect import close_protect_client, get_protect_client
from app.routes import health, settings, summaries, sync, transcriptions, webhook
from app.worker import transcription_worker

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent.parent / "static"
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Startup
    init_database()
    Path(AUDIO_PATH).mkdir(parents=True, exist_ok=True)

    try:
        await get_protect_client()
    except Exception as exc:
        logger.warning("Could not connect to Protect on startup: %s", exc)

    worker_task = asyncio.create_task(transcription_worker())
    logger.info("Transcription worker started")

    yield

    # Shutdown
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        logger.info("Transcription worker cancelled")

    await close_protect_client()


app = FastAPI(
    title="Protect Transcribe",
    description="Speech transcription service for UniFi Protect",
    lifespan=lifespan,
)

# Static files (JS, CSS assets)
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Routers
for _router in (
    health.router,
    webhook.router,
    transcriptions.router,
    settings.router,
    summaries.router,
    sync.router,
):
    app.include_router(_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")
