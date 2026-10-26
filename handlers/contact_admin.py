"""رسالة إلى الأدمن — confirmation-first contact flow.

Users send text or media to the bot; instead of silently ignoring it (the old
behavior) the bot now offers an explicit ✅ send / ❌ cancel confirmation, and
only after approval forwards the ORIGINAL content to the configured admin with
the sender's name + Telegram id. Download links keep their existing priority
and are never treated as contact messages (see the routing order in bot.py).

Security invariants implemented here:
  * The admin recipient is fixed from config (CONTACT_ADMIN_ID) — never from
    user input, so a user cannot pick or spoof an admin.
  * Content is NEVER forwarded implicitly; only the send button triggers it.
  * A per-request token marked ``done`` before the network call guarantees a
    double-tap cannot send the same confirmation twice.
  * Only the original sender can act on their own confirmation token.
  * Profanity is filtered before any offer/forward (handled by the caller via
    ``contains_profanity`` for text/captions).
"""

from __future__ import annotations

import logging
import secrets

from html import escape

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.button_text import inline_button
from utils.telegram_safe import safe_edit
from middlewares.auth import is_admin
from database.users import ban_user, get_user, create_user
from services.profanity_filter import contains_profanity
from config.settings import CONTACT_ADMIN_ID, CONTACT_ADMIN_ENABLED

logger = logging.getLogger(__name__)

# token -> pending confirmation request. In-memory is enough: a lost (post
# restart) token simply answers "session expired" instead of mis-forwarding.
_PENDING: dict[str, dict] = {}

# (admin_chat_id, message_id) -> original sender id, so an admin can reply to
# the forwarded header/content/hint and have their reply routed back to the user.
_REPLY_MAP: dict[tuple[int, int], int] = {}

_EMPTY_MARKUP = InlineKeyboardMarkup([])

_CONFIRM_TEXT = (
    "📩 <b>هل تريد إرسال هذه الرسالة إلى الأدمن؟</b>\n\n"
    "لن يُرسَل أي محتوى قبل موافقتك."
)


def _new_token() -> str:
    return secrets.token_hex(5)


def _display_name(user) -> str:
    parts = [getattr(user, "first_name", "") or "", getattr(user, "last_name", "") or ""]
    full = " ".join(p for p in parts if p).strip()
    return full or ("@" + (getattr(user, "username", "") or "")) or str(getattr(user, "id", "User"))


async def _edit_confirm(query, text: str) -> None:
    """Edit the confirmation message and drop its buttons (terminal state)."""
    await safe_edit(
        query.edit_message_text,
        text=text,
        parse_mode="HTML",
        reply_markup=_EMPTY_MARKUP,
    )


_PROFANITY_BAN_REASON = "إهانة/شتائم في رسالة للأدمن (حظر تلقائي)"


async def _ban_for_profanity(user_id: int) -> None:
    """Immediately ban a user who sent profanity toward the admin inbox.

    Persists the ban (users.ban_reason + banned_at) and writes an audit row so
    the owner sees auto-bans in the security audit screen. Admins/owner never
    reach this path — callers gate on ``is_admin`` before invoking it.
    """
    try:
        uid = int(user_id)
        if get_user(uid) is None:
            create_user(uid, "", "محظور", "", "ar")
        ban_user(uid, _PROFANITY_BAN_REASON)
    except Exception:
        logger.exception("auto-ban persist failed uid=%s", user_id)
        return
    try:
        from database.admin_audit import log_admin_action
        log_admin_action(0, "auto_ban_profanity", target_id=uid, detail=_PROFANITY_BAN_REASON)
    except Exception as exc:
        logger.warning("auto-ban audit failed uid=%s: %s", uid, exc)


async def ask_forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user, message) -> None:
    """Show the ✅ إرسال / ❌ إلغاء confirmation (content is NOT yet forwarded)."""
    token = _new_token()
    keyboard = InlineKeyboardMarkup([[
        inline_button("✅ إرسال", callback_data=f"contact_send|{token}"),
        inline_button("❌ إلغاء", callback_data=f"contact_cancel|{token}"),
    ]])
    confirm = await message.reply_text(_CONFIRM_TEXT, parse_mode="HTML", reply_markup=keyboard)
    _PENDING[token] = {
        "user_id": user.id,
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "confirm_chat_id": confirm.chat_id,
        "confirm_message_id": confirm.message_id,
        "done": False,
    }


async def _deliver_to_admin(context: ContextTypes.DEFAULT_TYPE, user, chat_id: int, message_id: int) -> None:
    """Forward the original content + sender identity to the configured admin."""
    bot = context.bot
    admin = int(CONTACT_ADMIN_ID)
    name = escape(_display_name(user))
    username = escape(getattr(user, "username", "") or "—")

    header = await bot.send_message(
        chat_id=admin,
        text=(
            "📩 <b>رسالة جديدة من مستخدم</b>\n"
            f"👤 <b>{name}</b>\n"
            f"🔖 @{username}\n"
            f"🆔 <code>{user.id}</code>"
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    _REPLY_MAP[(admin, header.message_id)] = user.id

    # copy_message forwards the ORIGINAL content (text/photo/video/file/…),
    # and Telegram attaches `forward_from` = the original sender. Returns a
    # MessageId (has .message_id) in python-telegram-bot.
    try:
        copied = await bot.copy_message(
            chat_id=admin, from_chat_id=chat_id, message_id=message_id,
        )
        copied_id = getattr(copied, "message_id", None)
        if copied_id:
            _REPLY_MAP[(admin, copied_id)] = user.id
    except Exception as exc:
        logger.warning("copy_message to admin failed user=%s: %s", user.id, exc)

    hint = await bot.send_message(
        chat_id=admin,
        text="↩️ للرد على المستخدم: اعمل <b>رد</b> على الرسالة المنسوخة ثم أرسل ردّك.",
        parse_mode="HTML",
    )
    _REPLY_MAP[(admin, hint.message_id)] = user.id


async def contact_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ✅ إرسال / ❌ إلغاء on a pending contact confirmation."""
    query = update.callback_query
    data = query.data or ""
    if "|" not in data:
        await query.answer("انتهت صلاحية الجلسة.", show_alert=True)
        return
    action, token = data.split("|", 1)
    pending = _PENDING.get(token)
    if not pending:
        await query.answer("انتهت صلاحية الجلسة. أرسل رسالتك مرة أخرى.", show_alert=True)
        return
    if query.from_user.id != pending["user_id"]:
        await query.answer("هذا الطلب لا يخصك.", show_alert=True)
        return

    if action == "contact_cancel":
        if pending.get("done"):
            await query.answer()
            return
        pending["done"] = True
        _PENDING.pop(token, None)
        await query.answer()
        await _edit_confirm(query, "🚫 <b>تم الإلغاء.</b> لم يُرسَل أي شيء للأدمن.")
        return

    if action == "contact_send":
        if pending.get("done"):
            await query.answer("✅ تم الإرسال بالفعل.", show_alert=True)
            return
        # Mark done BEFORE the network calls so a double-tap cannot re-send.
        pending["done"] = True
        await query.answer()
        try:
            await _deliver_to_admin(context, query.from_user, pending["chat_id"], pending["message_id"])
            await _edit_confirm(query, "✅ <b>تم إرسال رسالتك إلى الأدمن.</b>")
        except Exception as exc:
            logger.exception("contact deliver failed user=%s: %s", pending["user_id"], exc)
            await _edit_confirm(query, "⚠️ <b>تعذّر الإرسال حاليًا.</b> جرّب مرة أخرى بعد قليل.")
        finally:
            _PENDING.pop(token, None)
        return

    await query.answer("انتهت صلاحية الجلسة.", show_alert=True)


async def try_handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route an admin's reply to a forwarded contact message back to the user.

    Returns True when the update was consumed (so callers stop dispatching).
    """
    message = update.effective_message
    if message is None or not message.reply_to_message:
        return False
    user = update.effective_user
    if user is None or not is_admin(user.id):
        return False
    replied = message.reply_to_message
    target_user_id = _REPLY_MAP.get((message.chat_id, replied.message_id))
    if not target_user_id:
        return False
    try:
        await context.bot.copy_message(
            chat_id=target_user_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        await message.reply_text("✅ تم إرسال ردّك للمستخدم.", quote=False)
    except Exception as exc:
        logger.warning("contact reply fail admin=%s target=%s: %s", user.id, target_user_id, exc)
        try:
            await message.reply_text("⚠️ تعذّر إرسال الرد للمستخدم.", quote=False)
        except Exception:
            pass
    return True


async def contact_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE, user, message) -> None:
    """Final text fallback: normal (non-link) message → profanity gate → confirm."""
    if not CONTACT_ADMIN_ENABLED or is_admin(user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return
    if contains_profanity(text):
        await _ban_for_profanity(user.id)
        await message.reply_text(
            "🚫 <b>تم حظر حسابك</b> بسبب استخدام ألفاظ نابية أو سباب.",
            parse_mode="HTML",
        )
        return
    await ask_forward_to_admin(update, context, user, message)


async def media_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Media (photo/video/document/voice/audio/video-note) contact + caption routing."""
    message = update.effective_message
    if message is None or message.from_user is None:
        return

    if await try_handle_admin_reply(update, context):
        return

    user = update.effective_user
    caption = (message.caption or "").strip()

    # Download-link priority inside captions, mirroring the text router.
    from handlers.download import _extract_urls, process_url
    urls = _extract_urls(caption)
    if urls:
        # process_url enforces subscription / ban / rate limit internally.
        for url in urls:
            await process_url(update, context, user, url, message)
        return

    if not CONTACT_ADMIN_ENABLED or is_admin(user.id):
        return

    if caption and contains_profanity(caption):
        await _ban_for_profanity(user.id)
        await message.reply_text(
            "🚫 <b>تم حظر حسابك</b> بسبب استخدام ألفاظ نابية أو سباب.",
            parse_mode="HTML",
        )
        return

    await ask_forward_to_admin(update, context, user, message)
