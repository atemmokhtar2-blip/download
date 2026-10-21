from __future__ import annotations  # py3.9 compat: `str | None` annotations

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from telegram import Update
from telegram.ext import (
    Application, ApplicationHandlerStop, CommandHandler, MessageHandler,
    CallbackQueryHandler, InlineQueryHandler, TypeHandler, filters
)

from config.settings import BOT_TOKEN
from database.db import init_db
from utils.logger import system_logger
from utils.helpers import summarize_error

from handlers.start import (
    start_command, language_callback,
    help_command,
)
from handlers.settings_menu import settings_command, settings_callback, try_handle_settings_text
from handlers.download import (
    handle_url, download_callback, handle_batch_urls, _extract_urls
)
from handlers.search import search_command, search_callback, inline_search
from handlers.admin import (
    admin_command, admin_callback, send_update_announcement,
    engine_status_command, update_ytdlp_command, health_command,
    callbacks_health_command, commands_command,
)
from handlers.referral import (
    referral_callback, try_handle_code_message, try_handle_token_message,
)
from handlers.owner_panel import owner_command, owner_callback, owner_text_handler, warroom_command
from services.subscription_gate import subscription_check_callback
from workers.cleanup import cleanup_temp_files, cleanup_old_cache, cleanup_old_history, clone_expiry_worker

# Arabic-only main menu. Matching is emoji-insensitive (see normalize_button_text)
# so the buttons work no matter how the client sends the label back.
MENU_BUTTONS = {"📥 تحميل", "📥 Download", "⚙️ الإعدادات", "⚙️ Settings",
                "🛠️ لوحة التحكم"}  # clone panel door — routed to clone_admin_command,
                # which re-gates server-side (clone + Pro + owner/admin only)


async def ping_command(update: Update, context):
    """Lightweight liveness check for every user."""
    await update.effective_message.reply_text("🏓 البوت يعمل حاليًا.")


async def noop_callback(update: Update, context):
    """Acknowledge decorative/disabled inline buttons without side effects."""
    if update.callback_query:
        await update.callback_query.answer()


async def unknown_callback(update: Update, context):
    """Final safety net: no inline button should ever spin forever."""
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    user_id = query.from_user.id if query.from_user else "unknown"
    system_logger.warning("Unknown callback received: user=%s data=%r", user_id, data)
    await query.answer("انتهت صلاحية الجلسة. استخدم /start مرة أخرى.", show_alert=True)


async def forced_subscription_guard(update: Update, context):
    """Global fail-closed gate before every command, callback, text and inline query.

    The only non-admin interaction allowed through while unsubscribed is the
    explicit subscription-check callback. The guard runs in group -1 and stops
    handler dispatch when verification fails, so individual handlers cannot
    accidentally bypass the subscription requirement.
    """
    user = update.effective_user
    if user is None:
        return

    query = getattr(update, "callback_query", None)

    # Repeated Download-button presses are guarded ONLY in the distributed
    # main bot. Clone instances keep their own policy and are never modified by
    # this main-bot anti-spam rule. The guard is persistent across restarts.
    if not context.application.bot_data.get("is_clone_instance", False):
        try:
            from middlewares.auth import is_admin
            if not is_admin(user.id):
                from database.interaction_abuse import active_ban_seconds
                active_ban = active_ban_seconds(user.id)
                if active_ban > 0:
                    if query:
                        try:
                            await query.answer(
                                f"استخدامك متوقف مؤقتًا. حاول بعد {max(1, (active_ban + 59) // 60)} دقيقة.",
                                show_alert=True,
                            )
                        except Exception:
                            pass
                    else:
                        message = update.effective_message
                        if message is not None:
                            try:
                                await message.reply_text(
                                    f"استخدامك متوقف مؤقتًا. حاول بعد {max(1, (active_ban + 59) // 60)} دقيقة."
                                )
                            except Exception:
                                pass
                    raise ApplicationHandlerStop
                reason = ""
                if query:
                    callback_action = str(query.data or "").split("|", 1)[0]
                    if (callback_action in {"dl_smart", "dl_audio", "dl_audio_320", "dl_image", "dl_album"}
                            or callback_action.startswith("dl_video_")):
                        reason = "download_callback"
                if not reason:
                    message = update.effective_message
                    button_text = (getattr(message, "text", "") or "").strip().casefold()
                    if button_text in {"📥 تحميل", "تحميل", "📥 download", "download"}:
                        reason = "download_menu"
                if reason:
                    from database.interaction_abuse import check_repeated_action
                    decision, remaining = check_repeated_action(user.id, reason)
                    if decision != "allow":
                        if query:
                            try:
                                if decision == "banned":
                                    await query.answer(
                                        f"تم إيقاف استخدامك مؤقتًا بسبب تكرار الضغطات. حاول بعد {max(1, (remaining + 59) // 60)} دقيقة.",
                                        show_alert=True,
                                    )
                                else:
                                    await query.answer()
                            except Exception:
                                pass
                        elif decision == "banned":
                            message = update.effective_message
                            if message is not None:
                                try:
                                    await message.reply_text(
                                        f"تم إيقاف استخدامك مؤقتًا بسبب تكرار الضغطات لمدة {max(1, (remaining + 59) // 60)} دقائق."
                                    )
                                except Exception:
                                    pass
                        raise ApplicationHandlerStop
        except ApplicationHandlerStop:
            raise
        except Exception:
            # Anti-abuse storage trouble must be visible to logs, but must not
            # accidentally take down the entire update dispatcher.
            system_logger.exception("Main-bot anti-spam guard failed for user=%s", user.id)

    if query and str(query.data or "").startswith("sub_check"):
        return

    # The main bot's subscription policy is also the source of truth for
    # commands, settings, admin callbacks, download callbacks and inline mode.
    try:
        from services.subscription_gate import enforce_subscription
        allowed = await enforce_subscription(update, context, user)
    except Exception:
        system_logger.exception(
            "Forced-subscription guard failed closed for user=%s", user.id
        )
        allowed = False
        message = update.effective_message
        if message is not None:
            try:
                await message.reply_text(
                    "تعذّر التحقق من الاشتراك حاليًا. لن يُسمح باستخدام البوت "
                    "حتى يعود التحقق للعمل. حاول بعد قليل."
                )
            except Exception:
                pass
        elif query:
            try:
                await query.answer(
                    "تعذّر التحقق من الاشتراك. حاول بعد قليل.", show_alert=True
                )
            except Exception:
                pass
        else:
            inline = getattr(update, "inline_query", None)
            if inline:
                try:
                    await inline.answer([], cache_time=0, is_personal=True)
                except Exception:
                    pass

    if not allowed:
        raise ApplicationHandlerStop


async def message_router(update: Update, context):
    """Route private/user text messages — ignore channel posts and non-user updates."""
    message = update.effective_message
    # filters.TEXT can also match channel_post updates. Those have no
    # update.message/from_user, and treating them like user input crashes with
    # "'NoneType' object has no attribute 'text'". Only route real user messages.
    if message is None or message.from_user is None or not message.text:
        return
    text = message.text.strip()

    # Secure clone-token capture runs FIRST: while a user is in the
    # awaiting-token state, any text they send is treated as the token,
    # deleted immediately, and never routed anywhere else. The redemption
    # code capture (awaiting-code state) is checked just before it.
    if await try_handle_code_message(update, context):
        return
    if await try_handle_token_message(update, context):
        return

    # Clone-admin capture flows (broadcast body, welcome message, add-admin
    # id, user search) — gated inside by plan + instance ownership, so it
    # never consumes anything on the main bot or for non-privileged users.
    from handlers.clone_admin import try_handle_capture
    if await try_handle_capture(update, context):
        return

    if await try_handle_settings_text(update, context):
        return

    # Emoji-insensitive matching: some clients send the reply-keyboard label
    # without emoji or with different variation selectors, so both the press
    # and the expected labels are normalized before comparison.
    from utils.button_text import normalize_button_text
    from locales import t
    norm = normalize_button_text(text)
    if norm in {normalize_button_text(b) for b in MENU_BUTTONS}:
        from database.users import get_user, update_user
        user_id = update.effective_user.id
        db_user = get_user(user_id)
        if db_user:
            update_user(user_id, language="ar")
        download_btn = {
            normalize_button_text(t("ar", "menu_download")),
            normalize_button_text(t("en", "menu_download")),
        }
        settings_btn = {
            normalize_button_text(t("ar", "menu_settings")),
            normalize_button_text(t("en", "menu_settings")),
        }
        admin_btn = {
            normalize_button_text(t("ar", "menu_admin")),
            normalize_button_text(t("en", "menu_admin")),
        }
        if norm in download_btn:
            # Reply-keyboard menu actions must pass the same forced-subscription
            # gate as direct URL messages and download callbacks. Without this,
            # tapping "تحميل" repeatedly leaks the next-step prompt to users
            # who have not joined the required channels.
            from services.subscription_gate import enforce_subscription
            if not await enforce_subscription(update, context, update.effective_user):
                return
            await update.message.reply_text(t("ar", "send_url_prompt"), parse_mode="HTML")
        elif norm in settings_btn:
            # Open the real settings menu (same screen as /settings).
            await settings_command(update, context)
        elif norm in admin_btn:
            # The clone's OWN panel — re-gated server-side inside (clone +
            # Pro + owner/admin); anywhere else it is a polite refusal.
            from handlers.clone_admin import clone_admin_command
            await clone_admin_command(update, context)
        return

    # Owner-panel text states run first (owner-gated in code inside the handler).
    if await owner_text_handler(update, context):
        return

    from handlers.admin import admin_text_handler
    if await admin_text_handler(update, context):
        return

    urls = _extract_urls(text)
    if len(urls) > 1:
        await handle_batch_urls(update, context)
    elif urls:
        await handle_url(update, context)


def _make_post_init(is_clone: bool):
    """post_init factory: workers + clone-network boot run ONLY in the main bot.

    Clone instances share this process and codebase; running cleanup workers
    or the instance spawner inside every clone would duplicate work and could
    recursively spawn instances. Clones just log that they are up.
    """
    async def post_init(application: Application):
        if is_clone:
            system_logger.info("Clone bot @%s started.", application.bot.username)
            return
        system_logger.info("Bot starting up...")
        try:
            from services import referral
            referral.set_notifier(application.bot, asyncio.get_running_loop())
            system_logger.info("Milestone notifier registered.")
        except Exception:
            system_logger.exception("Milestone notifier registration failed")
        asyncio.create_task(cleanup_temp_files())
        asyncio.create_task(cleanup_old_cache())
        asyncio.create_task(cleanup_old_history())
        # Clone-plan validity enforcement: auto-stop + expire + notify owners
        # when a clone's basic/pro window ends (every 15 min).
        asyncio.create_task(clone_expiry_worker())
        system_logger.info("Background workers started.")
        # Boot the clone network: start every ACTIVE earned instance inside
        # this process (referral schema is already initialized in main()).
        try:
            from services.instance_manager import start_active_instances
            asyncio.create_task(start_active_instances())
        except Exception:
            system_logger.exception("Clone instance bootstrap failed")
    return post_init


async def global_error_handler(update: object, context) -> None:
    """Catch unhandled exceptions so the bot never dies silently.

    The real root cause is always written to the server logs via
    ``system_logger.exception``, and now a short, safe version is also shown to
    the user so they can report/understand exactly what went wrong.
    """
    err = context.error
    system_logger.exception("Unhandled error: %s", err)
    try:
        from utils.helpers import summarize_error  # defensive: the handler must never crash itself
        reason = summarize_error(err)
    except Exception:
        reason = str(err or "Unknown error")[:220]
    try:
        if update and getattr(update, "effective_message", None):
            from locales import t
            await update.effective_message.reply_text(
                t("ar", "unexpected_error", reason=reason)
            )
    except Exception:
        pass


def build_application(token: str | None = None, clone: bool = False) -> Application:
    """Build one Application.

    ``token=None`` → the main bot (TELEGRAM_BOT_TOKEN). ``clone=True`` marks a
    referral-earned instance: identical handler wiring, but no duplicated
    background workers and no recursive instance spawning (see _make_post_init).
    """
    bot_token = (token or BOT_TOKEN or "").strip()
    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set.")

    app = Application.builder().token(bot_token).post_init(_make_post_init(clone)).build()
    app.bot_data["is_clone_instance"] = bool(clone)

    # Global subscription gate MUST run before all user-facing handlers.
    # Group -1 + ApplicationHandlerStop prevents commands/callbacks/inline mode
    # from bypassing forced subscription; only the verification callback passes.
    app.add_handler(TypeHandler(Update, forced_subscription_guard), group=-1)

    # No language-selection conversation: /start immediately opens the Arabic bot.
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("ping", ping_command))

    # 👑 Owner Control Center — owner-only, protected in code (see owner_panel).
    app.add_handler(CommandHandler("owner", owner_command))
    app.add_handler(CommandHandler("warroom", warroom_command))

    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", admin_command))
    app.add_handler(CommandHandler("broadcast", admin_command))
    app.add_handler(CommandHandler("ban", admin_command))
    app.add_handler(CommandHandler("unban", admin_command))
    app.add_handler(CommandHandler("users", admin_command))
    app.add_handler(CommandHandler("search", admin_command))
    app.add_handler(CommandHandler("system", admin_command))
    app.add_handler(CommandHandler("maintenance", admin_command))
    app.add_handler(CommandHandler("announce_update", send_update_announcement))
    app.add_handler(CommandHandler("engine_status", engine_status_command))
    app.add_handler(CommandHandler("update_ytdlp", update_ytdlp_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("callbacks_health", callbacks_health_command))
    app.add_handler(CommandHandler("commands", commands_command))
    app.add_handler(CommandHandler("find", search_command))

    app.add_handler(CallbackQueryHandler(noop_callback, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(subscription_check_callback, pattern="^sub_check"))
    # Owner callbacks are matched BEFORE admin ones and re-verify is_owner
    # in code on every single press (never trust a hidden button).
    app.add_handler(CallbackQueryHandler(owner_callback, pattern="^own_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    # 👑 Clone-admin panel (Pro clones): scoped to the RUNNING instance and
    # re-verified server-side on every press (see handlers/clone_admin.py).
    from handlers.clone_admin import clone_admin_callback
    app.add_handler(CallbackQueryHandler(clone_admin_callback, pattern="^ca:"))
    app.add_handler(CallbackQueryHandler(referral_callback, pattern="^ref:"))
    # Legacy buttons are handled without allowing a language switch.
    app.add_handler(CallbackQueryHandler(language_callback, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern="^settings"))
    app.add_handler(CallbackQueryHandler(search_callback, pattern="^search_"))
    app.add_handler(CallbackQueryHandler(download_callback, pattern="^dl_"))
    app.add_handler(CallbackQueryHandler(unknown_callback))
    app.add_handler(InlineQueryHandler(inline_search))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))
    app.add_error_handler(global_error_handler)
    return app


def main():
    init_db()
    system_logger.info("Database initialized.")
    # Referral/clone tables (PostgreSQL — the only database backend) must
    # exist before the first /start attribution or any claim attempt.
    try:
        from database.pg import init_referral_schema, backend_name
        init_referral_schema()
        system_logger.info("Referral schema ready (backend=%s).", backend_name())
    except Exception:
        system_logger.exception("Referral schema init failed — referral features degraded.")
    app = build_application()
    system_logger.info("Bot polling started.")
    app.run_polling(
        stop_signals=False,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
