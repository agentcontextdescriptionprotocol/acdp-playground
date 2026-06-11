# Deployment

## Docker Compose

Two compose files describe the stack:

- **`docker-compose.yml`** — the playground + two registries
- **`docker-compose.full.yml`** — an overlay adding the control plane and UI
  console (run together via `make up-full`)

Both build with `context: ..` (the parent directory) so the image build can
`COPY` this repo alongside the sibling `acdp-rs/`, `acdp-registry-rs/`,
`acdp-control-plane/`, and `acdp-ui-console/` repos.

### Services and ports

| Service | Repo | Port | Public | Compose file |
|---------|------|------|--------|--------------|
| `playground` | acdp-playground | `8000` | yes | base |
| `registry-a` | acdp-registry-rs | `8100` | no | base |
| `registry-b` | acdp-registry-rs | `8200` | no | base |
| `control-plane` | acdp-control-plane | `3001` | no | full |
| `ui-console` | acdp-ui-console | `3000` | yes | full |

The registries store to an **ephemeral SQLite** db on a `tmpfs` mount, so state
resets on restart. The control plane runs **DB-less** (`AUTH_PERSISTENCE=memory`,
`STREAM_HUB_STRATEGY=memory`) for the demo.

### Registry config (`config/registry-a.toml`, `-b.toml`)

These are **demo configs for the registry binary** — the registry owns the
config schema (see its
[CONFIGURATION.md](https://github.com/agentcontextdistributionprotocol/acdp-registry-rs/blob/main/docs/CONFIGURATION.md)).
The playground-relevant choices in them are:

- `authority` / `port` — `registry-a.playground.local:8100`,
  `registry-b.playground.local:8200`
- `cross_registry_resolution = true` — lets S5 route an edge across registries
- `auth.anonymous_public_reads = true`, `require_tenant = false` — keeps the
  legacy single-tenant scenarios (S1–S8) working with anonymous publish
- `webhook.enabled = false` — the registry's SSRF policy refuses the loopback
  `http://playground:8000` target in the demo (webhook-driven events are
  exercised in the unit suite instead)
- `[playground]` pinned keys — a demo-only block that lets the playground's
  per-run `did:web` agents publish without live DID resolution (see the
  [live auth caveat](#live-auth-caveat))

The `[[playground.pinned_keys]]` entries also demonstrate **key rotation** (the
`rotating-publisher` agent has overlapping old/new Ed25519 windows, plus a
`p256-publisher` P-256 key). `scripts/pinned_keys_diff.py` translates these into
the control plane's `CONTROL_PLANE_PINNED_KEYS` wire format.

### Secrets that must line up

| Secret | Shared between |
|--------|----------------|
| `WEBHOOK_SECRET` | registries ↔ playground (inbound webhook HMAC) |
| `CONTROL_PLANE_HMAC_SECRET` / CP `WEBHOOK_SECRET` | playground ↔ control plane (forwarded webhook HMAC) |
| `CONTROL_PLANE_ADMIN_TOKEN` / CP `AUTH_ADMIN_API_KEYS` | playground ↔ control plane (admin surface) |

## The Dockerfile

`python:3.12-slim` base. Installs `uv` and a Rust toolchain (to build the
`acdp-py` C extension via maturin), copies the sibling `acdp-rs` + this repo,
runs `uv sync --extra llm`, exposes `8000`, and launches:

```
uv run --no-sync uvicorn playground.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
```

`--no-sync` skips a slow re-resolve/maturin rebuild at boot (the image is
pre-built); `exec` hands signals to uvicorn for graceful shutdown; `HOST`/`PORT`
are dynamic for Railway.

## Live auth caveat

The registry verifies challenge signatures by resolving the agent's `did:web`
document. The playground's `*.playground.local` DIDs aren't web-hosted and keys
rotate per run, so token issuance **can't fully complete against a stock
registry**. The auth-dependent scenarios are built to
[**degrade gracefully**](scenarios.md#graceful-degradation) and are validated by
the unit suite (mocked registry/CP). The deterministic cores — P-256 crypto,
cursor logic, tenant-header policy, rotation windows, `Retry-After` — are fully
exercised offline. The `[playground] pinned_only = false` + pinned-key config is
what lets the demo's run-keyed agents publish without live DID resolution.

## Railway

`.github/workflows/deploy-images.yml` builds the full-stack images and pushes
them to `ghcr.io/<owner>/acdp-{registry,control-plane,playground,ui-console}` on
a `v*` tag or manual dispatch (needs a `SIBLING_REPO_TOKEN` secret to clone the
sibling repos). The playground image binds Railway's dynamic `$PORT`/`$HOST`.

**Full deploy guide:** [`railway/DEPLOY.md`](../railway/DEPLOY.md) — service
topology, IPv6 private networking, per-service env vars, and the shared-secret
wiring. Key points:

- Each repo owns its own ghcr image (tag-triggered).
- Set IPv6 binds for `.railway.internal` reachability: `HOST=::` (playground,
  CP), `HOSTNAME=::` (ui-console), `ACDP_REGISTRY__REGISTRY__BIND=::`
  (registries).
- Give each service a deterministic internal port and wire the shared secrets
  above across services.

## CI/CD

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `ci.yml` | push / PR | `ruff check`, the offline unit suite; live conformance on manual `workflow_dispatch` (boots registry-a + control-plane) |
| `deploy-images.yml` | `v*` tag / dispatch | Build + push full-stack ghcr images |
| `notify-website.yml` | push to `main` touching `docs/**` or `README.md` | Dispatch a `docs-updated` event to `acdp-website` |
