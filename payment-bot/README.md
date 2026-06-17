# Hydra Payment Bot

Telegram payment bot for Hydra Monitors subscriptions.

This repository state contains Phase 1: the service scaffold plus subscription
domain models, Alembic schema, localized menus, user profile/language/support
flows, and admin-only manual subscription grants.

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

Telegram commands implemented in Phase 1:

```text
/start
/profile
/language
/support
/grant <user_id> <days>   # admins only
```

Run the bot:

```bash
docker compose up --build
```

Health endpoint inside the container listens on `127.0.0.1:8080`.
No payment webhook endpoints are exposed.
