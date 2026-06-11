# Getting started

## Prerequisites

- **Python 3.12+**
- **[uv](https://github.com/astral-sh/uv)** for dependency management
- **Rust toolchain** — the `acdp` Python package is a compiled (maturin/pyo3)
  extension built from the sibling `../acdp-rs/bindings/acdp-py`
- **Docker** (optional) — for running the registries and full stack
- An **LLM API key** (OpenAI or Anthropic), or use `LLM_PROVIDER=mock` for
  fully offline runs

The playground expects the sibling repos to be checked out next to it:

```
agentcontextdistributionprotocol/
├── acdp-playground/      ← you are here
├── acdp-rs/              ← the Rust SDK (acdp-py bindings)
├── acdp-registry-rs/     ← the registry binary
├── acdp-control-plane/   ← the control plane (full stack only)
└── acdp-ui-console/      ← the UI (full stack only)
```

## Install

```bash
make dev
```

This runs `uv sync --extra llm --extra dev` and then `make build-sdk`, which
compiles the `acdp` extension via `maturin develop --release`.

> **Rebuilding the SDK.** An editable pin does **not** recompile Rust. After
> pulling `acdp-rs` changes (e.g. to pick up `AcdpP256Producer` or
> `verify_signature_p256`), rerun `make build-sdk`.

## Sanity check (no LLM, no registry)

```bash
make smoke
```

`scripts/smoke_test.py` runs ~14 offline wiring checks: scenario catalog load,
SDK round-trip, agent publish path, webhook signature validation, P-256
round-trip, JCS number stability, the SSRF guard, idempotent replay, typed wire
errors, and the reserved-tenant guard. No API keys or running registry required.

## Run the stack

```bash
# Two registries + the playground
cp .env.example .env
# edit .env: set OPENAI_API_KEY=... (or LLM_PROVIDER=mock for offline runs)
make up
```

This launches the playground on `:8000`, `registry-a` on `:8100`, and
`registry-b` on `:8200` via `docker-compose.yml`.

For the full stack (adds the control plane on `:3001` and the UI console on
`:3000`):

```bash
make up-full
```

To run the API locally without Docker (registries still needed for live calls):

```bash
make run      # uvicorn playground.main:app --reload --port 8000
```

## Your first run

```bash
# 1) List scenarios
curl localhost:8000/scenarios | jq

# 2) Start a run
curl -X POST localhost:8000/runs \
    -H 'content-type: application/json' \
    -d '{"scenario_id":"s4_chain","inputs":{"topic":"GPU supply chains"}}'
# → {"run_id":"...","scenario_id":"s4_chain","status":"running","stream_url":"/runs/<id>/events", ...}

# 3) Stream events (replace RUN_ID with the returned id)
curl -N localhost:8000/runs/RUN_ID/events

# 4) Poll the final result
curl localhost:8000/runs/RUN_ID | jq
```

The SSE stream emits one `StepEvent` per protocol action — `agent.started`,
`llm.thinking`, `acdp.publish`, `acdp.retrieve`, `webhook.received`,
`run.complete` — terminating on `run.complete` or `run.error`. See the
[HTTP API](http-api.md) for the full event schema.

## Common Make targets

| Target | What it does |
|--------|--------------|
| `make dev` | Install deps + build the SDK |
| `make build-sdk` | Recompile the `acdp` Rust extension |
| `make run` | Run the API locally with `--reload` |
| `make smoke` | Offline wiring checks |
| `make smoke-live` | Smoke checks against a running full stack |
| `make test` | Unit suite (`pytest -q`, offline) |
| `make test-live` | Live conformance suite (needs `make up-full`) |
| `make up` / `make down` | Playground + two registries |
| `make up-full` / `make down-full` | Full stack incl. control plane |
| `make fmt` / `make lint` | `ruff format` / `ruff check` |

## Offline mode

Everything except real registry calls runs without external services:

- `LLM_PROVIDER=mock` — deterministic echo LLM, no API key
- `make smoke` / `make test` — fully offline, use `httpx.MockTransport`
- Scenarios **S16**, **S19**, **S20**, **S21** are fully offline by design
  (injected DNS resolver / no network), proving crypto and guard logic without
  any running infrastructure.
