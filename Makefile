.PHONY: help install dev test coverage typecheck audit lint format db-up db-down demo-trace ui ci-gate ci-gate-real shadow-smoke adversarial-smoke sequential-smoke calibration-smoke kappa-smoke docker-build tf-init tf-plan tf-apply tf-destroy deploy deploy-migrate clean

TF_DIR ?= deploy/terraform
IMAGE ?= evalgate:local

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install all deps (incl. dev group) into .venv
	uv sync

dev: db-up  ## Start local dev stack (Postgres for now)

test:  ## Run pytest
	uv run pytest

coverage:  ## Run pytest with coverage report
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

typecheck:  ## Static type check (mypy)
	uv run mypy

audit:  ## Scan dependencies for known vulnerabilities
	uv run pip-audit

lint:  ## Ruff check + format check + mypy (no writes)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

format:  ## Auto-fix lint + format
	uv run ruff check --fix .
	uv run ruff format .

db-up:  ## Start local Postgres
	docker compose up -d db

db-down:  ## Stop local Postgres
	docker compose down

demo-trace: db-up  ## End-to-end OTel demo: start API, push a trace, curl it back.
	@echo "--- waiting for Postgres ---"
	@for i in $$(seq 1 30); do \
		docker compose exec -T db pg_isready -U evalgate -d evalgate >/dev/null 2>&1 && break; \
		sleep 1; \
	done
	uv run alembic upgrade head
	@echo "--- starting EvalGate API ---"
	@uv run uvicorn evalgate.api.main:app --host 127.0.0.1 --port 8000 > /tmp/evalgate-api.log 2>&1 & \
		echo $$! > /tmp/evalgate-api.pid
	@for i in $$(seq 1 30); do \
		curl -fs http://127.0.0.1:8000/healthz >/dev/null && break; \
		sleep 0.5; \
	done
	@echo "--- pushing trace from examples/demo_app ---"
	@uv run python -m examples.demo_app.pipeline || (kill `cat /tmp/evalgate-api.pid`; exit 1)
	@sleep 1
	@echo "--- GET /v1/traces ---"
	@curl -s http://127.0.0.1:8000/v1/traces?limit=5 | python -m json.tool
	@kill `cat /tmp/evalgate-api.pid` 2>/dev/null || true
	@rm -f /tmp/evalgate-api.pid

ci-gate:  ## Phase 12 end-to-end gate in mock mode (offline, deterministic, what CI runs)
	EVALGATE_MOCK_LLM=1 PYTHONPATH='src:.' uv run python scripts/phase12_ci_gate.py --out gate-report.json

ci-gate-real:  ## Phase 12 end-to-end gate against local Ollama (needs qwen3.5:9b + qwen3-embedding:8b)
	PYTHONPATH='src:.' uv run python scripts/phase12_ci_gate.py --out gate-report.json

shadow-smoke:  ## Phase 13 shadow mode end-to-end (offline: 1k traffic -> rolling report -> alert)
	PYTHONPATH='src:.' uv run python scripts/phase13_shadow_smoke.py

adversarial-smoke:  ## Phase 14 adversarial synth end-to-end (offline: generate -> review -> gate)
	EVALGATE_MOCK_LLM=1 PYTHONPATH='src:.' uv run python scripts/phase14_adversarial_smoke.py

sequential-smoke:  ## Phase 15 sequential gate (offline synthetic: early FAIL + early PASS)
	PYTHONPATH='src:.' uv run python scripts/phase15_sequential_smoke.py

calibration-smoke:  ## Phase 16 judge calibration (offline synthetic: ECE drop + temperature)
	PYTHONPATH='src:.' uv run python scripts/phase16_calibration_smoke.py

kappa-smoke:  ## Phase 17 (offline synthetic: Cohen's kappa + guarded p95 + conditional calibration)
	PYTHONPATH='src:.' uv run python scripts/phase17_kappa_smoke.py

ui:  ## Start the streamlit ops UI on http://127.0.0.1:8501 (talks to evalgate-api over HTTP)
	uv run streamlit run src/evalgate/ui/Home.py \
		--server.port 8501 \
		--server.address 127.0.0.1

docker-build:  ## Build the production API image locally
	docker build -t $(IMAGE) .

tf-init:  ## terraform init (AWS ECS + RDS stack)
	terraform -chdir=$(TF_DIR) init

tf-plan:  ## terraform plan
	terraform -chdir=$(TF_DIR) plan

tf-apply:  ## terraform apply — provisions VPC + RDS + ECS Fargate + ALB
	terraform -chdir=$(TF_DIR) apply

tf-destroy:  ## Tear down all cloud resources
	terraform -chdir=$(TF_DIR) destroy

deploy:  ## Build+push image to ECR and roll the ECS service (needs applied TF + aws/docker)
	./deploy/scripts/deploy.sh

deploy-migrate:  ## Run the one-off DB migration task on ECS (for desired_count > 1)
	./deploy/scripts/run_migrations.sh

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
