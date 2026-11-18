# Factory provisioning adapter — existing Railway service

The Download Factory adapter runs inside the **existing** Railway service. It
does not need a second Railway service, a second worker, or a separate host.
The same `python3 app.py` process serves:
- the signed Factory API;
- the existing Gradio control center (when its password is configured);
- while the existing Telegram bot thread owns the main bot and all clone pollers.

Factory provisioning writes durable clone records to the same PostgreSQL
database used by Download. The main bot's existing `instance_sync_worker`
discovers new active records and starts clone pollers inside its own event
loop, normally within 20 seconds.

## Required variables on the one Download Railway service

- `NEON_DATABASE_URL` (or `POSTGRES_URL` / `DATABASE_URL`): the existing
  PostgreSQL database used by the main bot and clone instances.
- `TELEGRAM_BOT_TOKEN`: the main Download bot token. It prevents the main bot
  itself from being linked as a clone.
- `FACTORY_SERVICE_SECRET`: at least 32 random characters. It must be the
  **same exact secret** configured for the Factory Vercel project. Never log
  or expose this value.
- `ENABLE_CLONE_INSTANCES=1`: keeps the existing process responsible
  for starting factory-created clones.
- `BOT_SECRETS_KEY`: use a stable random value of at least 32 characters.
  This is strongly required for production durability: clone tokens are
  encrypted at rest, and this key must not change between restarts/deploys.

`DASHBOARD_PASSWORD` protects only the Gradio control center. When it is
missing, the dashboard displays a locked notice, but the signed internal
Factory API remains mounted and available. Configure `DASHBOARD_USERNAME`
and `DASHBOARD_PASSWORD` to use the control center.

The Factory bot performs channel subscription checks before it sends the
signed provisioning request. Download does not need a separate Factory bot
token or channel-admin permission.

## Internal contract

- `POST /internal/factory/provision` accepts the Factory's HMAC-SHA256
  signature, a five-minute timestamp window, and the matching idempotency key.
- `GET /internal/factory/status/{request_id}` reports the durable
  provisioning state and clone runtime status to the Factory.
- Bot tokens are encrypted by the existing Download Fernet layer. Raw tokens
  must never be written to logs or returned by the API.
- A user can have only one live Download clone, and the same Telegram bot
  token cannot be linked to multiple clone instances.
- The existing worker discovers active instances every 20 seconds. Do not
  start clone polling inside the API process.

## Health and readiness

Open `GET /health` on the existing Railway URL. It returns a safe
checklist without secret values:
- `ok` means the PostgreSQL liveness check passed.
- `ready` means the database, shared Factory secret, main Telegram
  token, clone runner, and stable clone encryption key are configured.
- `checks` identifies which requirement is missing; `warnings`
  contains only check names, never secret values.

The Factory endpoint URL configured in the Factory project must be the
existing Railway URL ending with `/internal/factory/provision`.
