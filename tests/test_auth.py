"""Tests for app.auth (API token + webhook HMAC)."""

import hashlib
import hmac

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth as auth_mod


def _build_app() -> FastAPI:
    """Mini-app exercising the auth helpers without dragging in lifespan."""
    from fastapi import Depends, Request

    a = FastAPI()

    @a.get("/private", dependencies=[Depends(auth_mod.require_api_token)])
    async def private():
        return {"ok": True}

    @a.post("/hook")
    async def hook(request: Request):
        body = await auth_mod.verify_webhook_signature(request)
        return {"len": len(body)}

    return a


def test_api_token_disabled_when_unset(monkeypatch):
    monkeypatch.setattr(auth_mod, "API_TOKEN", "")
    with TestClient(_build_app()) as c:
        r = c.get("/private")
        assert r.status_code == 200


def test_api_token_required_when_set(monkeypatch):
    monkeypatch.setattr(auth_mod, "API_TOKEN", "sekret")
    with TestClient(_build_app()) as c:
        r = c.get("/private")
        assert r.status_code == 401
        r = c.get("/private", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        r = c.get("/private", headers={"Authorization": "Bearer sekret"})
        assert r.status_code == 200
        r = c.get("/private", headers={"X-API-Token": "sekret"})
        assert r.status_code == 200


def test_webhook_signature_disabled_when_unset(monkeypatch):
    monkeypatch.setattr(auth_mod, "WEBHOOK_SECRET", "")
    with TestClient(_build_app()) as c:
        r = c.post("/hook", content=b"{}")
        assert r.status_code == 200


def test_webhook_signature_required_when_set(monkeypatch):
    monkeypatch.setattr(auth_mod, "WEBHOOK_SECRET", "topsecret")
    with TestClient(_build_app()) as c:
        body = b'{"alarm":"x"}'
        r = c.post("/hook", content=body)
        assert r.status_code == 401  # no header
        r = c.post("/hook", content=body, headers={"X-Hub-Signature-256": "sha256=deadbeef"})
        assert r.status_code == 401  # wrong sig

        good = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
        r = c.post("/hook", content=body, headers={"X-Hub-Signature-256": f"sha256={good}"})
        assert r.status_code == 200
        assert r.json()["len"] == len(body)
