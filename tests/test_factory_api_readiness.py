from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException

from services import factory_api


def test_health_reports_factory_readiness_without_exposing_secret(monkeypatch):
    secret = "s" * 48
    monkeypatch.setenv("FACTORY_SERVICE_SECRET", secret)
    monkeypatch.setenv("ENABLE_CLONE_INSTANCES", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "main-bot-token")
    monkeypatch.setenv("BOT_SECRETS_KEY", "stable-encryption-key")
    monkeypatch.setattr(factory_api.pg, "fetchone", lambda *_args, **_kwargs: {"ok": 1})

    result = asyncio.run(factory_api.health())

    assert result["ok"] is True
    assert result["ready"] is True
    assert result["checks"] == {
        "database": True,
        "factory_service_secret": True,
        "clone_instances_enabled": True,
        "main_bot_token": True,
        "stable_clone_encryption_key": True,
    }
    assert secret not in json.dumps(result)


def test_health_exposes_missing_factory_configuration_as_readiness_warning(monkeypatch):
    monkeypatch.delenv("FACTORY_SERVICE_SECRET", raising=False)
    monkeypatch.setenv("ENABLE_CLONE_INSTANCES", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "main-bot-token")
    monkeypatch.setenv("BOT_SECRETS_KEY", "stable-encryption-key")
    monkeypatch.setattr(factory_api.pg, "fetchone", lambda *_args, **_kwargs: {"ok": 1})

    result = asyncio.run(factory_api.health())

    assert result["ok"] is True
    assert result["ready"] is False
    assert result["checks"]["factory_service_secret"] is False


def test_authentication_reports_missing_service_secret_as_configuration_error(monkeypatch):
    monkeypatch.delenv("FACTORY_SERVICE_SECRET", raising=False)

    with pytest.raises(HTTPException) as exc:
        factory_api._authenticate("", "", "POST", "/internal/factory/provision", b"{}")

    assert exc.value.status_code == 503
    assert exc.value.detail == "service_configuration_missing"


def test_authentication_accepts_factory_hmac_signature(monkeypatch):
    secret = "factory-secret-for-tests-" + ("x" * 32)
    timestamp = str(int(time.time() * 1000))
    method = "POST"
    path = "/internal/factory/provision"
    body = b'{"request_id":"test"}'
    monkeypatch.setenv("FACTORY_SERVICE_SECRET", secret)
    signature = hmac.new(
        secret.encode(),
        timestamp.encode() + b"\\n" + method.encode() + b"\\n" +
        path.encode() + b"\\n" + body,
        hashlib.sha256,
    ).hexdigest()

    factory_api._authenticate(timestamp, signature, method, path, body)
