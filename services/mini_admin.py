"""Authenticated Telegram Mini App API for the XELVOR admin center."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config.settings import BOT_TOKEN
from database.admin_audit import get_admin_audit, log_admin_action
from database.cache import get_cache_count, get_cache_hits
from database.downloads import (
    get_downloads_24h, get_downloads_today, get_downloads_week,
    get_platform_analytics, get_total_data_bytes, get_total_downloads,
)
from database.users import (
    ban_user, count_users, get_banned_users_count, get_new_users_today,
    get_users_page, search_users, unban_user,
)
from middlewares.auth import is_admin
from services.rbac import has_perm

logger = logging.getLogger(__name__)
router = APIRouter()
WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
INIT_DATA_MAX_AGE_SECONDS = 60 * 60 * 24


def validate_telegram_init_data(init_data: str, bot_token: str, *, now: int | None = None) -> dict:
    """Validate Telegram WebApp initData using Telegram's HMAC-SHA256 scheme."""
    if not init_data or not bot_token:
        raise ValueError("Missing Telegram init data or bot token")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Duplicate init data field")
        values[key] = value
    received_hash = values.pop("hash", "")
    if len(received_hash) != 64:
        raise ValueError("Invalid init data hash")
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Invalid init data signature")
    try:
        auth_date = int(values.get("auth_date", "0"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid auth date") from exc
    current = int(time.time()) if now is None else int(now)
    if auth_date <= 0 or auth_date > current + 60 or current - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise ValueError("Expired init data")
    try:
        user = json.loads(values["user"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Missing Telegram user") from exc
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise ValueError("Invalid Telegram user")
    return user


def require_admin(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Telegram bot authentication is not configured")
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(status_code=401, detail="Telegram authentication required")
    try:
        user = validate_telegram_init_data(authorization[4:].strip(), BOT_TOKEN)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid or expired Telegram session")
    user_id = user["id"]
    if not is_admin(user_id) or not has_perm(user_id, "admin.view"):
        raise HTTPException(status_code=403, detail="Admin access denied")
    user["_id"] = user_id
    return user


def require_permission(permission: str):
    def dependency(user: dict = Depends(require_admin)) -> dict:
        if not has_perm(user["_id"], permission):
            raise HTTPException(status_code=403, detail="Insufficient admin permissions")
        return user
    return dependency


class BanRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


@router.get("/mini-admin", include_in_schema=False)
def mini_admin_page():
    page = WEBAPP_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=503, detail="Mini App frontend is unavailable")
    return FileResponse(page, media_type="text/html; charset=utf-8")


@router.get("/api/mini-admin/me")
def mini_admin_me(user: dict = Depends(require_admin)):
    return {
        "id": user["_id"],
        "first_name": str(user.get("first_name", ""))[:80],
        "username": str(user.get("username", ""))[:80],
    }


@router.get("/api/mini-admin/dashboard")
def mini_admin_dashboard(user: dict = Depends(require_admin)):
    from database.users import get_total_users
    from services.runtime_diagnostics import render_diagnostics_html

    return {
        "metrics": {
            "users": int(get_total_users()),
            "new_users_today": int(get_new_users_today()),
            "banned_users": int(get_banned_users_count()),
            "downloads_today": int(get_downloads_today()),
            "downloads_24h": int(get_downloads_24h()),
            "downloads_week": int(get_downloads_week()),
            "downloads_total": int(get_total_downloads()),
            "data_bytes": int(get_total_data_bytes()),
            "cache_items": int(get_cache_count()),
            "cache_hits": int(get_cache_hits()),
        },
        "platforms": get_platform_analytics(7),
        "audit": get_admin_audit(10),
        "health": "available",
    }


@router.get("/api/mini-admin/users")
def mini_admin_users(
    q: str = Query(default="", max_length=80),
    status: str = Query(default="all", pattern="^(all|active|banned)$"),
    offset: int = Query(default=0, ge=0, le=1000000),
    limit: int = Query(default=20, ge=1, le=50),
    user: dict = Depends(require_admin),
):
    if q.strip():
        rows = search_users(q.strip())[:limit]
        if status == "active":
            rows = [row for row in rows if not row.get("is_banned")]
        elif status == "banned":
            rows = [row for row in rows if row.get("is_banned")]
        return {"items": rows, "total": len(rows), "offset": 0, "limit": limit}
    return {
        "items": get_users_page(offset=offset, limit=limit, status=None if status == "all" else status),
        "total": count_users(None if status == "all" else status),
        "offset": offset,
        "limit": limit,
    }


@router.post("/api/mini-admin/users/{target_id}/ban")
def mini_admin_ban(target_id: int, payload: BanRequest, user: dict = Depends(require_permission("admin.users"))):
    if target_id <= 0 or target_id == user["_id"]:
        raise HTTPException(status_code=400, detail="Invalid target user")
    ban_user(target_id, payload.reason)
    log_admin_action(user["_id"], "mini_app_ban_user", target_id, payload.reason)
    return {"ok": True, "user_id": target_id, "is_banned": True}


@router.post("/api/mini-admin/users/{target_id}/unban")
def mini_admin_unban(target_id: int, user: dict = Depends(require_permission("admin.users"))):
    if target_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid target user")
    unban_user(target_id)
    log_admin_action(user["_id"], "mini_app_unban_user", target_id, "Telegram Mini App")
    return {"ok": True, "user_id": target_id, "is_banned": False}
