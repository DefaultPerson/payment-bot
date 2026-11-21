# Payment Bot

Telegram bot that sells subscriptions to private channels and chats: it takes the
payment, issues access links and revokes access when the subscription ends.

## Features

- Payments: CryptoBot, xRocket, Telegram Stars, Tribute (cards, via digital products).
  Signed webhooks with pollers as a fallback; idempotent settlement.
- Plans with per-plan chats, 1/3/6-month durations, carried days on plan change.
- Loyalty tiers, promo codes, one-time free trial.
- Referrals with a balance that can pay for a subscription.
- Access by join request: approved only for users whose plan covers the chat.
- Expiry reminders, a service channel with payment events, broadcasts.
- Inline admin panel: grant access, promo codes, referral settings, stats.
- Languages: ru, ua, en. Prometheus metrics at `/metrics`.

## Stack

Python 3.13, aiogram 3, SQLAlchemy + asyncpg, Alembic, PostgreSQL, Redis, uv.

## Quick start

Requires `uv`, PostgreSQL and Redis.

```bash
make init-env            # creates .env and config.toml from the examples
# set BOT_TOKEN, DATABASE_URL, REDIS_URL and ADMIN_IDS in .env
uv run alembic upgrade head
uv run python -m bot
```

## Configuration

- `.env` — secrets and infrastructure: bot token, provider tokens, database, Redis,
  admin ids. See `.env.example`.
- `config.toml` — business settings: plans, prices, chats, loyalty, trial, providers.
  See `config.example.toml`.
- Any `config.toml` value can be overridden by an env variable with `__` as the
  section separator, e.g. `PAYMENTS__TEST_MODE=true`. Nested values are JSON.

The bot must be an admin of every managed chat with the rights to invite users and
ban members.

## Testing payments

`payments.test_mode = true` routes CryptoBot and xRocket to their test networks
(`CRYPTOBOT_TESTNET_TOKEN`, `XROCKET_TESTNET_TOKEN`) and shows a cheap `test`
tariff. Telegram Stars and Tribute have no sandbox: pay the test tariff for 1 ⭐
and refund it:

```bash
uv run python -m bot.tools.refund_star --invoice-id <id>
```

## HTTP

The built-in server listens on `127.0.0.1:8080` and serves `/health`, `/metrics`
and `/webhooks/{cryptobot,xrocket,tribute}`. Webhooks without a valid provider
signature get `401`. To receive them, set `HTTP__HOST=0.0.0.0` and route a public
HTTPS endpoint to port 8080.

## Deployment

`Dockerfile` builds the image; `docker-compose.prod.yml` is a Compose file for
Coolify or any Docker host with external PostgreSQL and Redis. Run
`alembic upgrade head` before starting a new release.

## Development

```bash
make test    # pytest
make lint    # ruff
uv run python -m bot.tools.preview_messages --dry-run   # render every bot screen
```

## License

[MIT](LICENSE)
