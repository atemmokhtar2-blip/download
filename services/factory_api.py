"""Private provisioning API for the Download template.

Run as a separate Railway service from the same repository:
    uvicorn services.factory_api:app --host 0.0.0.0 --port $PORT

The long-running Download bot process owns the clone pollers. This API only
creates durable instances; the bot process discovers and starts them through
the periodic instance-manager sync worker.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database import pg
from services import referral

log = logging.getLogger("download.factory_api")
app = FastAPI(title="Download Factory Adapter", docs_url=None, redoc_url=None)

@app.on_event("startup")
async def initialize_shared_schema() -> None:
    # The adapter is a separate Railway service, so it cannot assume the
    # primary polling service initialized the shared PostgreSQL schema first.
    from database.db import init_db
    init_db()
    pg.init_referral_schema()
    _ensure_factory_schema()

class ProvisionRequest(BaseModel):
    request_id: str = Field(min_length=36, max_length=36)
    owner_telegram_id: int = Field(gt=0)
    template: str
    bot_token: str = Field(min_length=30, max_length=200)

def _authenticate(timestamp: str, signature: str, method: str, path: str, body: bytes) -> None:
    secret = os.getenv("FACTORY_SERVICE_SECRET", "")
    if len(secret) < 32 or not re.fullmatch(r"\d{10,13}", timestamp or ""):
        raise HTTPException(401, "unauthorized")
    millis = int(timestamp) if len(timestamp) == 13 else int(timestamp) * 1000
    if abs(int(time.time() * 1000) - millis) > 300_000:
        raise HTTPException(401, "expired_signature")
    expected = hmac.new(
        secret.encode(),
        timestamp.encode() + b"\n" + method.upper().encode() + b"\n" +
        path.encode() + b"\n" + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise HTTPException(401, "unauthorized")

def _require_primary_channel_admin(token: str, bot_id: int) -> bool:
    """A clone must be an admin of the factory channel to verify user membership."""
    import urllib.parse
    import urllib.request
    url = "https://api.telegram.org/bot" + token + "/getChatMember?" + urllib.parse.urlencode({
        "chat_id": "@K_J6k", "user_id": int(bot_id),
    })
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "download-factory/1.0"})
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        status = (payload.get("result") or {}).get("status")
        return bool(payload.get("ok") and status in ("administrator", "creator"))
    except Exception:
        return False


def _ensure_factory_schema() -> None:
    pg.execute("""
        CREATE TABLE IF NOT EXISTS factory_provision_requests (
            request_id UUID PRIMARY KEY,
            owner_telegram_id BIGINT NOT NULL,
            template_key TEXT NOT NULL,
            instance_id BIGINT,
            bot_id BIGINT,
            bot_username TEXT,
            status TEXT NOT NULL,
            safe_error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    pg.execute("ALTER TABLE bot_instances ADD COLUMN IF NOT EXISTS runtime_status TEXT DEFAULT 'pending'")

@app.get("/health")
async def health():
    try:
        pg.fetchone("SELECT 1 AS ok")
        return {"ok": True, "service": "download-factory-adapter"}
    except Exception:
        return JSONResponse({"ok": False}, status_code=503)

@app.get("/internal/factory/status/{request_id}")
async def status(
    request_id: str,
    request: Request,
    x_factory_timestamp: str = Header(default=""),
    x_factory_signature: str = Header(default=""),
):
    _authenticate(x_factory_timestamp, x_factory_signature, "GET", request.url.path, b"")
    try:
        request_uuid = str(uuid.UUID(request_id))
    except Exception:
        raise HTTPException(400, "invalid_request")
    _ensure_factory_schema()
    row = pg.fetchone(
        "SELECT instance_id, bot_id, bot_username, status, safe_error_code "
        "FROM factory_provision_requests WHERE request_id=? LIMIT 1",
        (request_uuid,),
    )
    if not row or not row.get("instance_id"):
        raise HTTPException(404, "instance_not_found")
    runtime = pg.fetchone(
        "SELECT runtime_status, last_error, status FROM bot_instances WHERE id=? LIMIT 1",
        (int(row["instance_id"]),),
    ) or {}
    runtime_status = str(runtime.get("runtime_status") or "pending")
    mapped = "active" if runtime_status == "running" else "failed" if runtime_status == "failed" else "provisioning"
    error_code = str(runtime.get("last_error") or row.get("safe_error_code") or "")[:80] or None
    return {
        "request_id": request_uuid,
        "instance_id": str(row["instance_id"]),
        "bot_id": row.get("bot_id"),
        "bot_username": row.get("bot_username"),
        "status": mapped,
        "error_code": error_code,
    }


@app.post("/internal/factory/provision")
async def provision(
    request: Request,
    x_factory_timestamp: str = Header(default=""),
    x_factory_signature: str = Header(default=""),
    idempotency_key: str = Header(default=""),
):
    raw = await request.body()
    _authenticate(x_factory_timestamp, x_factory_signature, "POST", request.url.path, raw)
    try:
        payload = ProvisionRequest.model_validate_json(raw)
        request_uuid = str(uuid.UUID(payload.request_id))
    except Exception:
        raise HTTPException(400, "invalid_request")
    if payload.template != "download":
        raise HTTPException(400, "template_mismatch")
    if idempotency_key != request_uuid:
        raise HTTPException(400, "idempotency_key_mismatch")

    _ensure_factory_schema()
    prior = pg.fetchone(
        "SELECT request_id, instance_id, bot_id, bot_username, status, safe_error_code "
        "FROM factory_provision_requests WHERE request_id = ?",
        (request_uuid,),
    )
    if prior and int(prior.get("owner_telegram_id") or 0) != payload.owner_telegram_id:
        raise HTTPException(409, "request_owner_mismatch")
    if prior and prior.get("status") in ("active", "provisioning"):
        runtime = pg.fetchone(
            "SELECT runtime_status FROM bot_instances WHERE id=? LIMIT 1",
            (int(prior["instance_id"]),),
        ) if prior.get("instance_id") else None
        runtime_state = str((runtime or {}).get("runtime_status") or "pending")
        if prior.get("status") == "active" or runtime_state in ("pending", "running"):
            return {
                "request_id": request_uuid,
                "instance_id": str(prior.get("instance_id") or ""),
                "bot_id": prior.get("bot_id"),
                "bot_username": prior.get("bot_username"),
                "status": "active" if runtime_state == "running" else "provisioning",
                "error_code": prior.get("safe_error_code"),
            }

    try:
        identity = referral._tg_get_me(payload.bot_token)
    except Exception:
        raise HTTPException(422, "invalid_bot_token")
    if not _require_primary_channel_admin(payload.bot_token, int(identity["id"])):
        raise HTTPException(422, "download_bot_must_be_admin_in_required_channel")

    token_hash = referral.hash_token(payload.bot_token)
    instance_id = int(prior["instance_id"]) if prior and prior.get("instance_id") else None
    existing_token = pg.fetchone("SELECT instance_id FROM bot_secrets WHERE token_hash = ?", (token_hash,))
    if existing_token and (instance_id is None or int(existing_token["instance_id"]) != instance_id):
        raise HTTPException(409, "bot_token_already_linked")
    if existing_token and instance_id is not None:
        current = referral.get_instance(instance_id) or {}
        pg.execute("UPDATE bot_instances SET status='active', runtime_status='pending', last_error='' WHERE id=?", (instance_id,))
        pg.execute(
            "UPDATE factory_provision_requests SET bot_id=?, bot_username=?, status='provisioning', safe_error_code=NULL, updated_at=NOW() WHERE request_id=?",
            (current.get("bot_id"), current.get("bot_username") or "", request_uuid),
        )
        return {
            "request_id": request_uuid, "instance_id": str(instance_id),
            "bot_id": current.get("bot_id"), "bot_username": current.get("bot_username"),
            "status": "provisioning",
        }

    existing = referral.get_instance_by_owner(payload.owner_telegram_id)
    if existing and existing.get("status") in ("pending_token", "active"):
        if instance_id is None or int(existing.get("id") or 0) != instance_id:
            raise HTTPException(409, "owner_already_has_download_instance")

    if prior:
        pg.execute(
            "UPDATE factory_provision_requests SET status='provisioning', safe_error_code=NULL, updated_at=NOW() WHERE request_id=?",
            (request_uuid,),
        )
    else:
        pg.execute(
            "INSERT INTO factory_provision_requests(request_id, owner_telegram_id, template_key, status) "
            "VALUES (?, ?, 'download', 'provisioning')",
            (request_uuid, payload.owner_telegram_id),
        )
    try:
        if instance_id is None:
            instance_id = pg.insert_returning_id(
                "INSERT INTO bot_instances(owner_id, status, plan) VALUES (?, 'pending_token', 'basic')",
                (payload.owner_telegram_id,),
            )
        else:
            pg.execute("UPDATE bot_instances SET status='pending_token', last_error='' WHERE id=?", (instance_id,))
        result = referral.verify_and_link_token(
            payload.owner_telegram_id,
            payload.bot_token,
            instance_id=instance_id,
            plan="basic",
            days=30,
        )
        if not result.get("ok"):
            pg.execute("UPDATE bot_instances SET status='failed', last_error=? WHERE id=?",
                       (str(result.get("reason") or "provision_failed")[:100], instance_id))
            pg.execute(
                "UPDATE factory_provision_requests SET instance_id=?, status='failed', safe_error_code=?, updated_at=NOW() WHERE request_id=?",
                (instance_id, str(result.get("reason") or "provision_failed")[:80], request_uuid),
            )
            raise HTTPException(422, "download_instance_rejected")
        pg.execute(
            "UPDATE factory_provision_requests SET instance_id=?, bot_id=?, bot_username=?, status='provisioning', updated_at=NOW() WHERE request_id=?",
            (instance_id, result["bot_id"], result.get("bot_username") or "", request_uuid),
        )
        log.info("factory_instance_reserved owner=%s instance=%s bot=%s",
                 payload.owner_telegram_id, instance_id, result["bot_id"])
        return {
            "request_id": request_uuid,
            "instance_id": str(instance_id),
            "bot_id": result["bot_id"],
            "bot_username": result.get("bot_username"),
            "status": "provisioning",
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.error("factory_provision_failed error_type=%s", type(exc).__name__)
        if instance_id:
            try:
                pg.execute("UPDATE bot_instances SET status='failed', last_error='factory_provision_failed' WHERE id=?", (instance_id,))
            except Exception:
                pass
        pg.execute(
            "UPDATE factory_provision_requests SET instance_id=?, status='failed', safe_error_code='factory_provision_failed', updated_at=NOW() WHERE request_id=?",
            (instance_id, request_uuid),
        )
        raise HTTPException(503, "provisioning_unavailable")
