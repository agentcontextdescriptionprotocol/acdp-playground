.PHONY: dev run test docker smoke fmt lint clean

PYTHON ?= python
UV ?= uv

dev:
	$(UV) sync --extra llm --extra dev

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

fmt:
	$(UV) run ruff format .

lint:
	$(UV) run ruff check .

clean:
	rm -rf .venv .pytest_cache __pycache__ */__pycache__ */*/__pycache__
