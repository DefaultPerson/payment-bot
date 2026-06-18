# Hydra Payment Bot

Telegram payment bot for Hydra Monitors subscriptions.

This repository state contains Phase 3: the service scaffold, subscription
domain models, localized menus, admin-only manual grants, and CryptoBot purchase
flow with polling-based payment finalization, loyalty pricing, and promo codes.

## Local Requirements

- Docker with Compose
- uv
- PostgreSQL 17 running outside this compose stack

Start a local PostgreSQL 17 container:

```bash
docker run --name pb-pg -e POSTGRES_PASSWORD=pb -e POSTGRES_DB=payment_bot -p 5432:5432 -d postgres:17
```

Create local config files:

```bash
make init-env
```

Use this local `DATABASE_URL` when the bot runs on the host:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:pb@localhost:5432/payment_bot
```

Use this local `DATABASE_URL` when the bot runs inside Docker Desktop:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:pb@host.docker.internal:5432/payment_bot
```

## Commands

```bash
make lock
make test
make lint
make alembic-sql
make docker-build
```

Apply database migrations:

```bash
alembic upgrade head
```

Telegram commands currently implemented:

```text
/start
/profile
/language
/support
/promo <code>
/grant <user_id> <days>   # admins only
```

## CryptoBot

Set `CRYPTOBOT_TOKEN` to enable live Crypto Pay invoices. Keep it blank, or set
`cryptobot.enabled = false`, to run local tests and menu flows without starting
the CryptoBot poller.

The `[cryptobot]` section controls the Crypto Pay API host, accepted assets,
invoice TTL, poll interval, poll batch size, and fee percentage used for quotes.
Use `api_url = "https://testnet-pay.crypt.bot"` with a testnet token for local
payment testing.

Tariffs are configured in `[[tariffs.items]]`. The bot syncs the selected
configured tariff into the database before creating an invoice, so changing
durations or prices is a config change plus restart.

Loyalty rules are configured in `[loyalty]`. Bronze applies a percent discount,
while Silver and Gold use fixed monthly prices plus the tariff duration
discount. `silver_active_days` and `gold_active_days` control cumulative paid or
manual access days needed for each tier.

Promos are stored in the database and selected by users with `/promo <code>`.
The selected promo is applied to the next invoice, snapshotted on that invoice,
and then cleared from the user. `percent` and `fixed_usd` promos discount the
invoice before provider fees; `extra_days` promos add access days after a paid
invoice without increasing loyalty active-day credit. `[promos]` controls the
maximum percent of a quote that a discount promo may remove.

Protected chats are configured in `[access]`:

```toml
[access]
invite_link_ttl_seconds = 86400
protected_chats = [
  { chat_id = -1001234567890, title = "Hydra Monitors" },
]
```

Paid CryptoBot invoices extend the subscription in one database transaction.
After commit, the poller sends a service-channel payment event and one-time
invite links. Telegram delivery failures do not roll back payment processing;
access-link delivery is retried by the running poller.

Run the bot:

```bash
docker compose up --build
```

Health endpoint inside the container listens on `127.0.0.1:8080`.
No payment webhook endpoints are exposed.
