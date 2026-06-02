.PHONY: dev build-sdk run test smoke docker up down up-full down-full fmt lint clean

PYTHON ?= python
UV ?= uv
COMPOSE_FULL = docker compose -f docker-compose.yml -f docker-compose.full.yml

dev:
	$(UV) sync --extra llm --extra dev
	$(MAKE) build-sdk

# The acdp SDK is a compiled (maturin/pyo3) extension. An editable pin
# does NOT recompile Rust, so rebuild it explicitly after pulling
# acdp-rs changes (e.g. to pick up AcdpP256Producer / verify_signature_p256).
build-sdk:
	$(UV) run --with maturin maturin develop --release \
		--manifest-path ../acdp-rs/bindings/acdp-py/Cargo.toml

run:
	$(UV) run uvicorn playground.main:app --reload --port 8000

test:
	$(UV) run pytest -q

smoke:
	$(UV) run python scripts/smoke_test.py

docker:
	docker compose build

up:
	docker compose up

down:
	docker compose down -v

# Full stack incl. the control plane (see docker-compose.full.yml).
up-full:
	$(COMPOSE_FULL) up

down-full:
	$(COMPOSE_FULL) down -v

fmt:
	$(UV) run ruff format .

lint:
	$(UV) run ruff check .

clean:
	rm -rf .venv .pytest_cache __pycache__ */__pycache__ */*/__pycache__
