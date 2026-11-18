"""Private provisioning API mounted inside the existing Download service.

The current Railway process serves this API and the Gradio dashboard together.
The existing Telegram bot event loop owns all clone pollers; this API only
persists provisioning requests, and instance_sync_worker starts clones there.
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

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database import pg
from services import referral

log = logging.getLogger("download.factory_api")
app = FastAPI(title="Download Factory Adapter", docs_url=None, redoc_url=None)
router = APIRouter()

async def initialize_shared_schema() -> None:
    # The Factory API and Telegram clone pollers share this one Railway
    # process and the same PostgreSQL database. Initialise idempotently before
    # the API accepts provisioning requests.
    from database.db import init_db
    init_db()
    pg.init_referral_schema()
    _ensure_factory_schema()

class ProvisionRequest(BaseModel):
    contract_version: int = Field(default=1, ge=1, le=1)
    request_id: str = Field(min_length=36, max_length=36)
    owner_telegram_id: int = Field(gt=0)
    template: str
    bot_token: str = Field(min_length=30, max_length=200)


def _primary_admin_is_confirmed(instance_id: int, owner_id: int) -> bool:
    """Read the clone-local primary-admin record; never inspect global admins."""
    try:
        from services import plan_policy
        row = plan_policy.get_clone_admin(int(instance_id), int(owner_id))
        return bool(
            row
            and int(row.get("user_id") or 0) == int(owner_id)
            and row.get("can_ban")
            and row.get("can_broadcast")
            and row.get("can_stats")
        )
    except Exception:
        log.exception("factory_primary_admin_attestation_read_failed instance=%s", instance_id)
        return False


def _ensure_primary_admin(instance_id: int, owner_id: int) -> bool:
    """Idempotently assign and then verify all owner permissions in this clone."""
    try:
        from services import plan_policy
        if not plan_policy.ensure_owner_admin(int(instance_id), int(owner_id)):
            return False
        return _primary_admin_is_confirmed(instance_id, owner_id)
    except Exception:
        log.exception("factory_primary_admin_assignment_failed instance=%s", instance_id)
        return False

def _authenticate(timestamp: str, signature: str, method: str, path: str, body: bytes) -> None:
    secret = os.getenv("FACTORY_SERVICE_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "service_configuration_missing")
    if not re.fullmatch(r"\d{10,13}", timestamp or ""):
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

@router.get("/health")
async def health():
    """Safe liveness/readiness diagnostics; never return secret values."""
    checks = {
        "database": False,
        "factory_service_secret": len(os.getenv("FACTORY_SERVICE_SECRET", "")) >= 32,
        "clone_instances_enabled": os.getenv("ENABLE_CLONE_INSTANCES", "1") == "1",
        "main_bot_token": bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip()),
        "stable_clone_encryption_key": len(os.getenv("BOT_SECRETS_KEY", "")) >= 32,
    }
    try:
        checks["database"] = bool(pg.fetchone("SELECT 1 AS ok"))
    except Exception as exc:
        log.warning("factory_adapter_healthcheck_failed error_type=%s", type(exc).__name__)

    ready = all(checks.values())
    payload = {
        "ok": checks["database"],
        "ready": ready,
        "service": "download-factory-adapter",
        "checks": checks,
        "warnings": [key for key, value in checks.items() if not value],
    }
    if not checks["database"]:
        return JSONResponse(payload, status_code=503)
    return payload

@router.get("/internal/factory/status/{request_id}")
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
        "SELECT instance_id, owner_telegram_id, bot_id, bot_username, status, safe_error_code "
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
    raw_error_code = str(runtime.get("last_error") or row.get("safe_error_code") or "")
    error_code = raw_error_code if re.fullmatch(r"[a-zA-Z0-9_]{1,80}", raw_error_code) else None
    owner_id = int(row.get("owner_telegram_id") or 0)
    owner_admin_confirmed = _primary_admin_is_confirmed(int(row["instance_id"]), owner_id)
    return {
        "contract_version": 1,
        "request_id": request_uuid,
        "instance_id": str(row["instance_id"]),
        "bot_id": row.get("bot_id"),
        "bot_username": row.get("bot_username"),
        "status": mapped,
        "primary_admin_id": owner_id if owner_admin_confirmed else None,
        "owner_admin_confirmed": owner_admin_confirmed,
        "error_code": error_code,
    }


@router.post("/internal/factory/provision")
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
    if payload.contract_version != 1:
        raise HTTPException(400, "unsupported_contract_version")
    if payload.template != "download":
        raise HTTPException(400, "template_mismatch")
    if idempotency_key != request_uuid:
        raise HTTPException(400, "idempotency_key_mismatch")

    _ensure_factory_schema()
    prior = pg.fetchone(
        "SELECT request_id, owner_telegram_id, instance_id, bot_id, bot_username, status, safe_error_code "
        "FROM factory_provision_requests WHERE request_id = ?",
        (request_uuid,),
    )
    if prior and int(prior.get("owner_telegram_id") or 0) != payload.owner_telegram_id:
        raise HTTPException(409, "request_owner_mismatch")
    if prior and prior.get("status") in ("active", "provisioning") and prior.get("instance_id"):
        if not _ensure_primary_admin(
            int(prior["instance_id"]), payload.owner_telegram_id
        ):
            pg.execute(
                "UPDATE factory_provision_requests SET status='failed', safe_error_code='owner_admin_assignment_failed', updated_at=NOW() WHERE request_id=?",
                (request_uuid,),
            )
            raise HTTPException(503, "owner_admin_assignment_failed")
        runtime = pg.fetchone(
            "SELECT runtime_status FROM bot_instances WHERE id=? LIMIT 1",
            (int(prior["instance_id"]),),
        )
        runtime_state = str((runtime or {}).get("runtime_status") or "pending")
        if runtime_state in ("pending", "running"):
            return {
                "contract_version": 1,
                "request_id": request_uuid,
                "instance_id": str(prior.get("instance_id") or ""),
                "bot_id": prior.get("bot_id"),
                "bot_username": prior.get("bot_username"),
                "status": "active" if runtime_state == "running" else "provisioning",
                "primary_admin_id": payload.owner_telegram_id,
                "owner_admin_confirmed": True,
                "error_code": prior.get("safe_error_code"),
            }

    try:
        identity = referral._tg_get_me(payload.bot_token)
    except Exception:
        raise HTTPException(422, "invalid_bot_token")
    token_hash = referral.hash_token(payload.bot_token)
    instance_id = int(prior["instance_id"]) if prior and prior.get("instance_id") else None
    existing_token = pg.fetchone("SELECT instance_id FROM bot_secrets WHERE token_hash = ?", (token_hash,))
    if existing_token and (instance_id is None or int(existing_token["instance_id"]) != instance_id):
        raise HTTPException(409, "bot_token_already_linked")
    if existing_token and instance_id is not None:
        if not _ensure_primary_admin(instance_id, payload.owner_telegram_id):
            raise HTTPException(503, "owner_admin_assignment_failed")
        current = referral.get_instance(instance_id) or {}
        pg.execute("UPDATE bot_instances SET status='active', runtime_status='pending', last_error='' WHERE id=?", (instance_id,))
        pg.execute(
            "UPDATE factory_provision_requests SET bot_id=?, bot_username=?, status='provisioning', safe_error_code=NULL, updated_at=NOW() WHERE request_id=?",
            (current.get("bot_id"), current.get("bot_username") or "", request_uuid),
        )
        return {
            "contract_version": 1,
            "request_id": request_uuid, "instance_id": str(instance_id),
            "bot_id": current.get("bot_id"), "bot_username": current.get("bot_username"),
            "status": "provisioning",
            "primary_admin_id": payload.owner_telegram_id,
            "owner_admin_confirmed": True,
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
        if not _ensure_primary_admin(instance_id, payload.owner_telegram_id):
            pg.execute(
                "UPDATE bot_instances SET status='failed', last_error='owner_admin_assignment_failed' WHERE id=?",
                (instance_id,),
            )
            pg.execute(
                "UPDATE factory_provision_requests SET instance_id=?, status='failed', safe_error_code='owner_admin_assignment_failed', updated_at=NOW() WHERE request_id=?",
                (instance_id, request_uuid),
            )
            raise HTTPException(503, "owner_admin_assignment_failed")
        pg.execute(
            "UPDATE factory_provision_requests SET instance_id=?, bot_id=?, bot_username=?, status='provisioning', updated_at=NOW() WHERE request_id=?",
            (instance_id, result["bot_id"], result.get("bot_username") or "", request_uuid),
        )
        log.info("factory_instance_reserved owner=%s instance=%s bot=%s",
                 payload.owner_telegram_id, instance_id, result["bot_id"])
        return {
            "contract_version": 1,
            "request_id": request_uuid,
            "instance_id": str(instance_id),
            "bot_id": result["bot_id"],
            "bot_username": result.get("bot_username"),
            "status": "provisioning",
            "primary_admin_id": payload.owner_telegram_id,
            "owner_admin_confirmed": True,
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

# Keep a standalone FastAPI app available, while the production host can
# include the router directly before mounting Gradio at the root path.
app.include_router(router)
