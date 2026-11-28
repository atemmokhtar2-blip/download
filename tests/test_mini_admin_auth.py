import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from services.mini_admin import validate_telegram_init_data


def _signed_init_data(token="test-token", auth_date=None, user_id=12345):
    auth_date = int(time.time()) if auth_date is None else int(auth_date)
    values = {
        "auth_date": str(auth_date),
        "query_id": "AAE-test",
        "user": json.dumps({"id": user_id, "first_name": "Admin"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_telegram_init_data_signature_is_validated():
    user = validate_telegram_init_data(_signed_init_data(), "test-token")
    assert user["id"] == 12345
    assert user["first_name"] == "Admin"


def test_telegram_init_data_rejects_tampering():
    data = _signed_init_data().replace("Admin", "Attacker")
    with pytest.raises(ValueError):
        validate_telegram_init_data(data, "test-token")


def test_telegram_init_data_rejects_expired_sessions():
    old = int(time.time()) - 60 * 60 * 25
    with pytest.raises(ValueError):
        validate_telegram_init_data(_signed_init_data(auth_date=old), "test-token")


def test_telegram_init_data_rejects_duplicate_fields():
    data = _signed_init_data()
    with pytest.raises(ValueError):
        validate_telegram_init_data(data + "&auth_date=1", "test-token")


def test_telegram_init_data_rejects_future_sessions():
    future = int(time.time()) + 600
    with pytest.raises(ValueError):
        validate_telegram_init_data(_signed_init_data(auth_date=future), "test-token")
