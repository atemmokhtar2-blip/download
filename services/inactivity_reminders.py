"""Gentle, randomized reminders for users inactive for 3, 7, or 14 days."""

import asyncio
import logging
import random

from telegram.error import Forbidden, TelegramError

from database.onboarding import (
    get_due_reengagement_users,
    mark_reengagement_stage,
)

logger = logging.getLogger(__name__)

REMINDER_MESSAGES = {
    1: (
        "مختفي فين؟ أنا موجود لو احتجت تنزّل حاجة.",
        "لو عندك رابط حابب تحفظه، ابعتهولي في أي وقت.",
        "بس بفكّرك إنك تقدر تبعت الرابط هنا وقت ما تحتاج.",
        "أهلًا، لو احتجت تنزيل فيديو أو صوت أنا موجود.",
        "لسه موجودين أهو. ابعت أي رابط وقت ما تحتاج.",
    ),
    2: (
        "عامل إيه؟ لو احتجت البوت، ابعت الرابط وخلاص.",
        "افتكرتك أقولك إن البوت جاهز وقت ما تحتاجه.",
        "لو لقيت فيديو عجبك، ابعت رابطه وأنا أساعدك.",
        "مجرد تذكير صغير: تقدر تستخدم البوت في أي وقت.",
        "لو محتاج تحفظ حاجة من الإنترنت، ابعت الرابط هنا.",
    ),
    3: (
        "لسه تقدر ترجع تستخدم البوت وقت ما تحب.",
        "أنا موجود لو احتجتني في أي وقت.",
        "لو احتجت تنزيل أي محتوى، ابعت الرابط هنا.",
        "مش هزعجك تاني بالتذكير ده. أنا موجود وقت ما تحتاج.",
        "خلي البوت عندك، يمكن تحتاجه في أي وقت.",
    ),
}

POLL_INTERVAL_SECONDS = 6 * 60 * 60
BATCH_SIZE = 200


async def inactivity_reminder_worker(bot) -> None:
    """Check every six hours and send at most one catch-up reminder per user."""
    while True:
        try:
            due_users = await asyncio.to_thread(get_due_reengagement_users, BATCH_SIZE)
            for item in due_users:
                user_id = int(item["user_id"])
                stage = int(item["due_stage"])
                messages = REMINDER_MESSAGES.get(stage)
                if not messages:
                    continue
                message = random.choice(messages)
                try:
                    await bot.send_message(chat_id=user_id, text=message)
                except Forbidden:
                    # User blocked the bot or chat is no longer reachable.
                    # Persist the stage so this user is not retried forever.
                    try:
                        await asyncio.to_thread(mark_reengagement_stage, user_id, stage)
                    except Exception:
                        logger.exception("Could not persist blocked reminder uid=%s", user_id)
                    continue
                except TelegramError:
                    # A transient failure should be retried on the next pass.
                    logger.warning("Inactivity reminder failed uid=%s", user_id, exc_info=True)
                    continue
                try:
                    await asyncio.to_thread(mark_reengagement_stage, user_id, stage)
                except Exception:
                    # A send may have succeeded; log this ambiguity rather than
                    # crashing the worker. The activity table is persistent.
                    logger.exception("Could not persist reminder stage uid=%s", user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Inactivity reminder worker pass failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
