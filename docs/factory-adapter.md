# Factory provisioning adapter — Railway deployment

The Download runtime remains the long-running owner of clone pollers. The
factory API is a separate Railway service from this same repository; it writes
durable clone records, and the existing Download worker discovers them from
PostgreSQL.

## Add a Railway service

Create a second service from this repository after merging this branch. Set its
start command to:

```bash
uvicorn services.factory_api:app --host 0.0.0.0 --port $PORT
```

Do not replace the existing Download service command (`python3 app.py`).

## Shared environment

Copy the existing service's PostgreSQL DSN and stable clone encryption key to
the new API service. The following values must match the main Download worker:

- `NEON_DATABASE_URL` (or `POSTGRES_URL` / `DATABASE_URL`): same PostgreSQL database.
- `BOT_SECRETS_KEY`: exactly the same stable encryption key used by the main worker.
- `TELEGRAM_BOT_TOKEN`: main Download bot token, used only to reject linking the main bot as a clone.
- `FACTORY_SERVICE_SECRET`: a new random secret of at least 32 bytes, also configured on the factory Vercel project.

Keep `ENABLE_CLONE_INSTANCES=1` on the existing long-running Download service.
The factory API service does not need to run the main Telegram polling process.

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

After Railway deploys the new service, check `GET /health`. The endpoint must
return `{"ok": true}` before the factory adapter URL is configured.
