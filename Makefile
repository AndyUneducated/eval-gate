.PHONY: help install dev test lint format db-up db-down clean

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install all deps (incl. dev group) into .venv
	uv sync

dev: db-up  ## Start local dev stack (Postgres for now)

test:  ## Run pytest
	uv run pytest

lint:  ## Ruff check + format check (no writes)
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-fix lint + format
	uv run ruff check --fix .
	uv run ruff format .

db-up:  ## Start local Postgres
	docker compose up -d db

db-down:  ## Stop local Postgres
	docker compose down

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
