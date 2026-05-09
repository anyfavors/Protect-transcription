"""
Authentication helpers.

* ``API_TOKEN`` (env var) — when set, all destructive / privacy-sensitive endpoints
  require a matching ``Authorization: Bearer <token>`` or ``X-API-Token`` header.
  When empty (default), auth is disabled — useful for local dev and existing
  deployments. A WARNING is logged at startup so the operator knows.

* ``WEBHOOK_SECRET`` (env var) — when set, the /api/webhook endpoint requires
  an HMAC-SHA256 signature in the ``X-Hub-Signature-256`` header (GitHub style)
  computed over the raw body.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

API_TOKEN: str = os.getenv("API_TOKEN", "")
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")


def auth_enabled() -> bool:
    return bool(API_TOKEN)


def webhook_auth_enabled() -> bool:
    return bool(WEBHOOK_SECRET)


async def require_api_token(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
) -> None:
    """
    FastAPI dependency: validate API token when configured.

    Tokens may be presented via:
    * ``Authorization: Bearer <token>`` (preferred)
    * ``X-API-Token: <token>``
    * ``?token=<token>`` query string (browser-friendly for ``<audio src>``)
    """
    if not API_TOKEN:
        return

    presented: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    elif x_api_token:
        presented = x_api_token.strip()
    else:
        q = request.query_params.get("token")
        if q:
            presented = q.strip()

    if not presented or not secrets.compare_digest(presented, API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing API token")


_MAX_WEBHOOK_BODY_BYTES = 1_048_576  # 1 MiB — plenty for an alarm payload


async def verify_webhook_signature(request: Request) -> bytes:
    """
    Verify webhook HMAC signature when WEBHOOK_SECRET is configured.
    Returns the raw request body (so the caller can re-parse without reading twice).
    Rejects bodies larger than _MAX_WEBHOOK_BODY_BYTES (DoS guard).
    """
    body = await request.body()
    if len(body) > _MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="webhook body too large")
    if not WEBHOOK_SECRET:
        return body

    sig_header = request.headers.get("x-hub-signature-256", "")
    if not sig_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="missing webhook signature")

    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    presented = sig_header[len("sha256=") :]
    if not hmac.compare_digest(expected, presented):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    return body
