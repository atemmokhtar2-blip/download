"""Forced channel subscription gate (main bot) — owner-approved policy.

  * Main-bot-wide: applies to every non-admin, non-banned user.
  * Flexible channel list (per ``instance_id``; 0 = global main bot).
  * FAIL-CLOSED verification: on ANY Telegram error we retry, alert the
    owner, and NEVER assume the subscription is confirmed. There is no
    bypass path — a broken verification only blocks access, it never grants it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from html import escape

from telegram import InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from utils.button_text import inline_button

logger = logging.getLogger(__name__)

_RETRIES = 2
_JOINED_STATUSES = {"creator", "administrator", "member"}
# One owner alert per channel every 5 minutes max (avoid request-storm spam).
_OWNER_ALERT_COOLDOWN: dict[str, float] = {}


def resolve_chat_id(channel_ref: str):
    """Return an int for numeric refs, the @username string otherwise.

    Telegram REJECTS negative numeric chat ids sent as JSON strings (it treats
    the string as a @username and fails with "chat not found"), so numeric
    ``channel_ref`` values MUST be converted to int before any get_chat /
    get_chat_member call. This was silently breaking membership checks for
    private channels added by numeric ID.
    """
    ref = str(channel_ref or "").strip()
    if ref.startswith("@"):
        return ref
    if ref.lstrip("-").isdigit():
        try:
            return int(ref)
        except ValueError:
            pass
    return ref


def _join_url(ch: dict) -> str:
    link = (ch.get("invite_link") or "").strip()
    if link.startswith(("http://", "https://")):
        return link
    ref = (ch.get("channel_ref") or "").strip()
    if ref.startswith("@"):
        return f"https://t.me/{ref[1:].strip()}"
    return ""


async def _alert_owner(bot, channel_ref: str, error: str) -> None:
    from config.settings import OWNER_ID
    from database.admin_audit import log_admin_action

    key = str(channel_ref or "").strip()
    now = time.monotonic()
    if now - _OWNER_ALERT_COOLDOWN.get(key, 0.0) < 300:
        return
    _OWNER_ALERT_COOLDOWN[key] = now

    try:
        log_admin_action(0, "subscription_verify_error",
                         detail=f"channel={channel_ref[:60]} err={error[:120]}")
    except Exception:
        pass

    try:
        await bot.send_message(
            int(OWNER_ID),
            "⚠️ <b>تعذّر التحقق من قناة الاشتراك الإجباري</b>\n\n"
            f"• القناة: <code>{escape(channel_ref[:80])}</code>\n"
            f"• الخطأ: <code>{escape(error[:160])}</code>\n\n"
            "🛡 <b>الوضع الآمن مفعّل:</b> الاشتراك لا يُعتبر مؤكدًا، ولن يتم "
            "تجاوز الشرط حتى يعود التحقق للعمل. تحقق أن البوت مشرف/عضو في القناة "
            "وأن التوكن سليم.",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("owner subscription alert failed: %s", exc)


async def _check_member(bot, channel_ref: str, user_id: int) -> bool:
    """True when the user has joined; raises after retries on Telegram errors."""
    last_exc: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            member = await bot.get_chat_member(chat_id=resolve_chat_id(channel_ref), user_id=user_id)
            status = getattr(member, "status", "")
            # A restricted user is only a member when Telegram explicitly says so.
            # Treating every "restricted" status as joined lets non-members bypass the gate.
            if status == "restricted":
                return bool(getattr(member, "is_member", False))
            return status in _JOINED_STATUSES
        except TelegramError as exc:
            last_exc = exc
            if attempt < _RETRIES:
                await asyncio.sleep(1 + attempt)
                continue
    raise last_exc if last_exc else RuntimeError("subscription verification failed")


async def _verify(bot, user_id: int, channels: list[dict]):
    """Return (missing, errored). ``errored`` entries are (channel, error_text)."""
    missing: list[dict] = []
    errored: list[tuple[dict, str]] = []
    for ch in channels:
        ref = ch.get("channel_ref") or ""
        try:
            if not await _check_member(bot, ref, user_id):
                missing.append(ch)
        except Exception as exc:
            err = str(exc)[:160]
            errored.append((ch, err))
            await _alert_owner(bot, ref, err)
    return missing, errored


async def _send_prompt(message, channels, verification_error: bool) -> None:
    rows: list = []
    listed: list[str] = []
    for ch in channels:
        label = (ch.get("title") or ch.get("channel_ref") or "قناة").strip()
        listed.append(f"• {escape(label)}")
        url = _join_url(ch)
        if url:
            try:
                rows.append([inline_button(f"➕ {label}", url=url)])
            except Exception:
                pass
    rows.append([inline_button("✅ انضميت — تحقق الآن", callback_data="sub_check")])

    text = "🔒 <b>اشترك في القناة لاستخدام البوت</b>\n\n"
    if verification_error:
        text += (
            "⚠️ تعذّر التحقق من اشتراكك مؤقتًا. أعد المحاولة بعد قليل.\n"
            "لن يتم تجاوز الشرط حتى ينجح التحقق.\n\n"
        )
    text += (
        "اشترك في القنوات التالية، ثم اضغط <b>«انضميت — تحقق الآن»</b>:\n"
        + "\n".join(listed)
    )

    try:
        await message.reply_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.warning("subscription prompt failed: %s", exc)


def _active_global_channels() -> list[dict]:
    from database import subscription as db
    return db.list_channels(0, enabled_only=True)


async def enforce_subscription(update, context, user) -> bool:
    """Enforce forced channel subscription for one action.

    Returns True when the action may proceed; False when a prompt was sent and
    the caller MUST stop. Admins, banned users, and disabled/empty config are
    passed through untouched (banned users get their normal ban message).
    """
    from middlewares.auth import is_admin, is_banned

    # The policy applies to the MAIN bot. Clone instances are out of scope for
    # now — per-instance channel lists are layered on later via instance_id
    # (the storage schema already carries it, so no migration will be needed).
    from services.instance_manager import is_clone_bot
    if is_clone_bot(getattr(getattr(context, "bot", None), "username", None)):
        return True

    if is_admin(user.id) or is_banned(user.id):
        return True

    from database import subscription as db
    if not db.subscription_enabled():
        logger.info("force-sub: rule disabled — uid=%s passes", user.id)
        return True

    channels = _active_global_channels()
    if not channels:
        logger.info("force-sub: no active channels — uid=%s passes", user.id)
        return True

    missing, errored = await _verify(context.bot, user.id, channels)
    if not missing and not errored:
        return True

    logger.info(
        "force-sub: uid=%s blocked (missing=%s, verify_errors=%s)",
        user.id,
        [str(c.get("channel_ref")) for c in missing],
        [str(c.get("channel_ref")) for c, _ in errored],
    )

    # A failed callback check should show an alert, not another copy of the prompt.
    if getattr(update, "callback_query", None):
        try:
            await update.callback_query.answer(
                "تعذّر التحقق من الاشتراك. حاول مرة أخرى بعد قليل."
                if errored else
                "أنت لم تشترك في القناة بعد. اشترك أولًا ثم اضغط تحقق الآن.",
                show_alert=True,
            )
        except Exception:
            pass
        return False

    message = update.effective_message
    if message is None:
        return False
    # Show ALL configured channels (the user may have missed joining one even
    # when another channel errored during verification).
    await _send_prompt(message, channels, verification_error=bool(errored))
    return False


async def subscription_check_callback(update, context: ContextTypes.DEFAULT_TYPE):
    """'✅ انضميت — تحقق الآن' — re-verify and either confirm or re-prompt."""
    query = update.callback_query
    user = query.from_user
    # Same scope as enforce_subscription: the main bot only.
    from services.instance_manager import is_clone_bot
    if is_clone_bot(getattr(getattr(context, "bot", None), "username", None)):
        await query.answer("✅ لا يوجد اشتراك إجباري في هذه النسخة.")
        return
    channels = _active_global_channels()
    if not channels:
        await query.answer("✅ لا يوجد اشتراك إجباري مفعل.")
        return

    missing, errored = await _verify(context.bot, user.id, channels)
    if not missing and not errored:
        await query.answer("✅ تم التحقق بنجاح!")
        try:
            await query.edit_message_text(
                "✅ <b>تم التحقق من اشتراكك!</b>\n\nأرسل رابط التحميل أو استخدم /search.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    # Keep the original prompt in place; report failure in a Telegram alert.
    if errored:
        await query.answer(
            "تعذّر التحقق من الاشتراك حاليًا. حاول مرة أخرى بعد قليل.",
            show_alert=True,
        )
    else:
        await query.answer(
            "❌ لم يتم العثور على اشتراكك. اشترك في القناة ثم اضغط تحقق الآن.",
            show_alert=True,
        )
