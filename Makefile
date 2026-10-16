.PHONY: init-env lock test lint alembic-sql docker-build

init-env:
	@if [ ! -f .env ]; then cp .env.example .env; echo ".env created"; else echo ".env already exists"; fi
	@if [ ! -f config.toml ]; then cp config.example.toml config.toml; echo "config.toml created"; else echo "config.toml already exists"; fi

lock:
	uv lock --python 3.13

test:
	uv run --python 3.13 pytest -v

lint:
	uv run --python 3.13 ruff check .

alembic-sql:
	DATABASE_URL=$${DATABASE_URL:-postgresql+asyncpg://postgres:pb@localhost:5432/payment_bot} uv run --python 3.13 alembic upgrade head --sql

docker-build:
	docker build -t hydra-payment-bot .
