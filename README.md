# PayHub — Railway + PostgreSQL build

This build keeps the fixed Binance Order ID verification and webhook flow, and replaces SQLite with PostgreSQL for Railway persistence.

## Railway deployment

Create a Railway service from this project and add a Railway PostgreSQL service in the same project.

Set these Variables on the PayHub service:

```env
DATABASE_URL=${{Postgres.DATABASE_URL}}
PUBLIC_BASE_URL=https://YOUR-PAYHUB-RAILWAY-DOMAIN
ADMIN_PASSWORD=YOUR_ADMIN_PASSWORD
ADMIN_EMAIL=YOUR_EMAIL
SESSION_SECRET=LONG_RANDOM_SECRET
ADMIN_BOT_TOKEN=YOUR_ADMIN_TELEGRAM_BOT_TOKEN
ADMIN_TELEGRAM_ID=YOUR_TELEGRAM_ID
DEFAULT_COIN=USDT
INVOICE_EXPIRE_MINUTES=30
DB_POOL_MIN=1
DB_POOL_MAX=10
```

Optional global Binance fallback:

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
```

Normal verification uses each approved PayHub user's Binance API key/secret saved in the dashboard.

### Start command

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Telegram shop bot variables

```env
PAYMENT_BASE_URL=https://YOUR-PAYHUB-RAILWAY-DOMAIN
PAYMENT_API_KEY=pk_live_xxxx
PAYMENT_WEBHOOK_SECRET=whsec_xxxx
```

### Webhook URL

In the PayHub dashboard, set the payment bot webhook URL to:

```text
https://YOUR-TELEGRAM-BOT-RAILWAY-DOMAIN/webhook
```

The webhook secret shown by PayHub for that bot must exactly match the Telegram shop bot's `PAYMENT_WEBHOOK_SECRET`.

## Included fixes

- PostgreSQL persistence via `DATABASE_URL`; no SQLite/volume required.
- Threaded PostgreSQL connection pool.
- Atomic payment claim with row locking and unique transaction protection.
- `/api/v1/verify` accepts `order_id` plus legacy `txid` / `tx_id` aliases.
- Exact Binance Order/TX ID matching.
- Verification uses the approved PayHub user's saved Binance credentials.
- Invoice expiry enforced with `INVOICE_EXPIRE_MINUTES`.
- Webhook retries up to 3 times and sends `X-Webhook-Secret`.
- Existing bot webhook URL can be edited from the dashboard.
