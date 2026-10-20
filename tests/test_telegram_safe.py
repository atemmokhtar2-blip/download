import asyncio
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from utils.telegram_safe import safe_answer


def test_safe_answer_ignores_expired_callback_query():
    query = AsyncMock()
    query.answer.side_effect = BadRequest(
        "Query is too old and response timeout expired or query id is invalid"
    )

    result = asyncio.run(safe_answer(query, "OK", show_alert=True))

    assert result is None
    query.answer.assert_awaited_once_with("OK", show_alert=True)


def test_safe_answer_propagates_unrelated_telegram_errors():
    query = AsyncMock()
    query.answer.side_effect = BadRequest("Message is not modified")

    with pytest.raises(BadRequest, match="Message is not modified"):
        asyncio.run(safe_answer(query, "OK"))
