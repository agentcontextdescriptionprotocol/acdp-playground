# acdp-playground

The ACDP playground generates real protocol traffic so the SDK
(`acdp-rs`), the registry (`acdp-registry-rs`), and (later) the
control plane can be exercised end-to-end. It spins agents, calls real
LLMs, publishes context, streams events over SSE, and forwards
registry webhooks.

## Layout

```
acdp_client/                  # async httpx + Pydantic aliases over the acdp-py SDK
playground/
  agents/                     # BasePlaygroundAgent + LangChain/CrewAI/LangGraph
  scenarios/
    catalog/                  # S1–S5, S7, S8 — runnable end-to-end
  api/                        # FastAPI routers: scenarios, runs, contexts, webhooks
  config.py                   # pydantic-settings (.env)
  events.py                   # in-process SSE bus
  control_plane.py            # no-op when CONTROL_PLANE_URL unset
scripts/
  smoke_test.py               # offline wiring check
  gen_keys.py                 # deterministic agent identity material
config/                       # registry-a.toml, registry-b.toml
docker-compose.yml            # playground + two registries
```

## Quickstart

```bash
# 1) Install (uses uv; resolves acdp from ../acdp-rs/bindings/acdp-py)
make dev

# 2) Sanity check (no LLM, no registry needed)
make smoke

# 3) Run the playground + both registries
cp .env.example .env
# edit .env: set OPENAI_API_KEY=... (or LLM_PROVIDER=mock for offline runs)
make up

# 4) List scenarios
curl localhost:8000/scenarios | jq

# 5) Start a run
curl -X POST localhost:8000/runs \
    -H 'content-type: application/json' \
    -d '{"scenario_id":"s4_chain","inputs":{"topic":"GPU supply chains"}}'

# 6) Stream events (replace RUN_ID with the returned id)
curl -N localhost:8000/runs/RUN_ID/events
```

## Scenarios

| ID | Name | What it shows |
|----|------|---------------|
| `s1_single_publish` | Single Publish | Smallest publish round-trip |
| `s2_producer_consumer` | Producer → Consumer | One derivation edge |
| `s3_fanout` | Fan-out 1→N | One source, parallel facet analyses |
| `s4_chain` | Linear Chain A→B→C | C derives from both A and B |
| `s5_cross_registry` | Cross-Registry Chain | Edge crosses registry-a → registry-b |
| `s7_supersession` | Supersession v1→v2 | Same lineage, two versions |
| `s8_cross_org` | Cross-Org Isolation | Two orgs, no cross-references |

## LLM provider

Set `LLM_PROVIDER` in `.env` to one of:

- `openai` (default) — `LLM_MODEL=gpt-4o-mini`, needs `OPENAI_API_KEY`
- `anthropic` — needs `ANTHROPIC_API_KEY`
- `mock` — deterministic offline echo (for smoke / CI runs without API keys)

## Control plane

`acdp-control-plane` is a separate sibling project (not yet implemented).
When `CONTROL_PLANE_URL` is empty, the playground runs standalone. When
set, every registry webhook is HMAC-signed and forwarded; run
start/complete notifications are also posted there.

## Compose layout

`docker-compose.yml` uses `context: ..` so the build can COPY both this
repo and the sibling `acdp-rs/` + `acdp-registry-rs/` repos. The
playground image installs the Python SDK from the sibling path; the
registry images are built from the sibling repo's Dockerfile.
