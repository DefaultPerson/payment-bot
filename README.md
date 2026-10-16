# Hydra Payment Bot

Telegram payment bot for Hydra Monitors subscriptions.

Features: localized menus (ru/ua/en), plans and tariffs, CryptoBot, xRocket,
Telegram Stars and Tribute payments, loyalty tiers, promo codes, referrals with a
spendable balance, a one-time trial, lifecycle notifications, an inline admin
panel, and Prometheus metrics.

## Local Requirements

- Docker
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
/language
/promo <code>
/referral
```

Admins (`ADMIN_IDS`) get an inline admin panel in the main menu: grant access,
personal referral percent, referral balance reset, promo codes, broadcast and stats.
Broadcast first asks which segment to target (all/active/inactive), then opens the
`aiogram-broadcast` interactive composer: send the message content (text/media),
optionally add inline URL buttons, preview, and send now or schedule for later.
Scheduled broadcasts are stored in a Redis APScheduler job store.

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

Referrals are opened with `/referral`. The bot shows a deep link in the form
`https://t.me/<bot.username>?start=ref_<telegram_id>`. Referral binding is
accepted only on a user's first `/start`; later deep links do not change an
existing user. Successful paid CryptoBot and Telegram Stars invoices create a
manual-payout earning for the referrer from `invoice.net`. `[referral]`
controls the default percent, minimum withdrawal amount, and referrer-change
policy. Admins can set a personal percent for current and future referrals from
the admin panel. Profile and referral views show lifetime earned
USD and unpaid USD available toward the manual withdrawal minimum.

Telegram Stars payments are configured in `[stars]`. The bot sends Stars
invoices with `currency = "XTR"` and an empty provider token through
`createInvoiceLink`. Every tariff is a one-shot invoice; renewal is manual.
`app_store_fee_percent` and `star_usd_rate`
control the Star amount used to preserve the configured USD access price. Stars
invoice accounting is stored in USD gross/fee/net fields, while the XTR charge
amount and Telegram payment payload stay in provider metadata.

Protected chats are configured in `[access]`:

```toml
[access]
invite_link_ttl_seconds = 86400
protected_chats = [
  { chat_id = -1001234567890, title = "Hydra Monitors" },
]
```

Paid CryptoBot and Telegram Stars invoices extend the subscription in one
database transaction. CryptoBot payments are detected by the poller; Stars
payments are finalized from Telegram `successful_payment` updates after
pre-checkout validation. Recurring Stars renewals create one paid invoice per
new Telegram `telegram_payment_charge_id`, while duplicate charge updates are
idempotent and can retry undelivered access links. After commit, the bot sends a
service-channel payment event and one-time invite links.

Run the bot:

```bash
uv run --python 3.13 python -m bot
```

The HTTP server inside the container listens on `127.0.0.1:8080` (override the bind
with `HTTP__HOST`). It serves `/health`, `/metrics`, and — for each enabled provider —
a signed inbound webhook: `/webhooks/cryptobot`, `/webhooks/xrocket`, and
`/webhooks/tribute`. Every webhook verifies the provider's signature and returns
`401` on a missing or bad one; the pollers stay on as a fallback so a missed webhook
still settles. To receive webhooks from outside the container, bind to all interfaces
with `HTTP__HOST=0.0.0.0` and route the public endpoint (e.g. Traefik) to port `8080`.

## Testing payments

One switch routes the live crypto providers to their TEST networks (Telegram Stars
has no test network):

```toml
# config.toml
[payments]
test_mode = true   # CryptoBot -> testnet host + CRYPTOBOT_TESTNET_TOKEN;
                   # xRocket   -> testnet + XROCKET_TESTNET_TOKEN
```

Put testnet tokens in `.env` (`CRYPTOBOT_TESTNET_TOKEN` from `@CryptoTestnetBot`,
`XROCKET_TESTNET_TOKEN` from `@xrocket_testnet_bot`). Keep `test_mode = false` in
production, or force it via env with `PAYMENTS__TEST_MODE=false`.

A cheap `test_only` tariff (`code = "test"`) ships in `config.toml`. It is
auto-hidden unless `test_mode` is on, so it can never be sold in production. Buy it
through any provider for an end-to-end check; its Stars price is pinned to **1 ⭐**
via `stars_amount_override` (crypto uses `base_price_usd` on testnet).

Telegram Stars has no sandbox: pay the test tariff with 1 real Star, then refund
manually — the bot exposes no refund command:

```bash
uv run python -m bot.tools.refund_star --invoice-id <id>
# or: uv run python -m bot.tools.refund_star --user-id <tg_id> --charge-id <telegram_payment_charge_id>
```

## Observability

The HTTP server exposes Prometheus metrics at `/metrics`.

Run the local metric load smoke without external services:

```bash
uv run --python 3.13 python -m bot.tools.load_smoke --iterations 1000
```
