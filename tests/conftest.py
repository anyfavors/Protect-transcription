"""
Shared pytest fixtures.

Design: patch `app.database.get_connection` to return connections to a
temporary SQLite file.  Every module that does DB work imports get_connection
from app.database, so one patch covers everything.
"""

import os
import sqlite3
from collections.abc import Generator
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch) -> Generator[str, None, None]:
    """
    Redirect all DB access to a fresh temporary SQLite file by patching
    app.database.get_connection.  Also patches AUDIO_PATH.
    """
    db_file = str(tmp_path / "test.db")
    audio_dir = str(tmp_path / "audio")
    os.makedirs(audio_dir, exist_ok=True)

    import app.config as cfg

    monkeypatch.setattr(cfg, "AUDIO_PATH", audio_dir)

    # Patch the single connection factory used by every module
    import app.database as db_mod

    monkeypatch.setattr(db_mod, "DATABASE_PATH", db_file)

    # Also patch AUDIO_PATH used by transcription routes
    import app.routes.transcriptions as trans_routes

    monkeypatch.setattr(trans_routes, "AUDIO_PATH", audio_dir)

    def _make_connection():
        conn = sqlite3.connect(db_file, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    monkeypatch.setattr(db_mod, "get_connection", _make_connection)
    # Also patch the _connect alias used inside database.py itself
    monkeypatch.setattr(db_mod, "_connect", _make_connection)

    # Patch get_connection in every module that imported it at module load time
    import app.routes.sync as _rs
    import app.routes.transcriptions as _rt
    import app.summaries as _sm
    import app.worker as _wk

    for mod in (_rt, _rs, _sm, _wk):
        if hasattr(mod, "get_connection"):
            monkeypatch.setattr(mod, "get_connection", _make_connection)

    # Also fix DATABASE_PATH used directly in worker.py and summaries.py
    monkeypatch.setattr(_wk, "DATABASE_PATH", db_file)

    from app.database import init_database

    init_database()

    yield db_file


@pytest.fixture()
def client(tmp_db) -> Generator[TestClient, None, None]:
    """
    TestClient with fresh DB and lifespan's network calls suppressed.
    """

    @asynccontextmanager
    async def _stub_lifespan(application):
        yield

    import app as app_module

    original_lifespan = app_module.app.router.lifespan_context
    app_module.app.router.lifespan_context = _stub_lifespan

    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        yield c

    app_module.app.router.lifespan_context = original_lifespan
