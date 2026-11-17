# Factory provisioning adapter — Railway deployment

The existing Download Railway service remains the only runtime. The factory
API is mounted inside the existing Gradio/ASGI web process; it writes durable
clone records, and the existing Download bot process discovers them from
PostgreSQL and starts each clone in its own event loop. No second Railway
service, worker, or host is required.

## Existing Railway service

Keep the current Railway service and its existing deployment. Do not create a
second service and do not change the bot's Telegram polling startup. The
existing `python3 app.py` entry point now serves the Gradio dashboard and the
factory provisioning API in the same ASGI process. The bot and all clone
pollers continue to run inside the existing long-running bot thread/event loop.

## Shared environment

Keep the existing service's PostgreSQL DSN and stable clone encryption key.
The following values must be available to the one Download service:

- `NEON_DATABASE_URL` (or `POSTGRES_URL` / `DATABASE_URL`): same PostgreSQL database.
- `BOT_SECRETS_KEY`: exactly the same stable encryption key used by the main worker.
- `TELEGRAM_BOT_TOKEN`: main Download bot token, used only to reject linking the main bot as a clone.
- `FACTORY_SERVICE_SECRET`: a new random secret of at least 32 bytes, also configured on the factory Vercel project.

Keep `ENABLE_CLONE_INSTANCES=1` on the existing long-running Download service.
The factory API is part of this same process and does not start a second Telegram poller.

## Operational behavior

- `POST /internal/factory/provision` accepts only signed requests with a
  five-minute timestamp window and an idempotency key.
- Bot tokens are stored by the existing Download Fernet encryption layer;
  raw tokens are never logged.
- Each owner can have one live Download clone. Telegram bot tokens cannot be
  linked to more than one instance.
- The bot token must already have administrator rights in `@K_J6k`. This is
  required because each Download clone checks membership using its own bot
  identity. Provisioning fails closed if that permission is missing.
- The existing Download worker scans PostgreSQL every 20 seconds and starts
  newly provisioned active instances in its own process. Do not run clone
  polling in the API service.

## Health

After the existing Railway service redeploys, check `GET /health` on that same service. The endpoint must return `{"ok": true}` before the factory adapter URL is configured.
