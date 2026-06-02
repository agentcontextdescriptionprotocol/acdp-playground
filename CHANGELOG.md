# Changelog

Notable changes to the ACDP stack as observed from the playground.
Tracks cross-repo work — playground, control plane, registry, SDK —
so operators reading any one repo can see the system-wide picture.

## 2026-06-01 — Playground V2 sibling sync

The playground now exercises the features that landed across the RFC,
the Rust SDK, the registry, and the control plane since late May. See
`plans/2026-06-01-playground-sibling-sync.md` for the full plan.

### Client / SDK

- **ECDSA-P256 signing.** `acdp_client.signing` abstracts over
  `AcdpProducer` (Ed25519) and `AcdpP256Producer` (P-256); the token
  manager posts the matching `algorithm` to `/auth/token` and the
  verifier dispatches to the right path. `make build-sdk` rebuilds the
  compiled `acdp` extension to pick up the new producer.
- **Cursor pagination.** `AcdpClient.search(cursor=...)` +
  `search_all(...)`; the latter continues through an empty-but-cursored
  page (RFC-ACDP-0005 §2.3) and surfaces `invalid_cursor` /
  `cursor_expired` as `CursorError`.
- **Token revocation.** `TokenManager.revoke(...)` (RFC 7009) +
  unverified `tenant`/`jti` claim surfacing for telemetry.
- **Tenant header policy.** `AcdpClient(tenant_id=, tenant_header_mode=)`
  — `X-Tenant-Id` is a fallback only and is suppressed for
  bearer-authenticated requests so it never contradicts the JWT claim.
- **Extended body fields.** Agents thread `data_refs` / `data_period` /
  `expires_at`; supersession uses `expected_lineage_id` via
  `build_supersede_request`.

### Control plane bridge

- Forwards `X-Tenant-Id` + `X-ACDP-Event-Id` on webhooks and run
  notifications; honours a cooperative `Retry-After` (RFC 9110).
- Operator surface: `introspect` (RFC 7662), `revocations` feed,
  `reload_pinned_keys` — gated on `CONTROL_PLANE_ADMIN_TOKEN`.
- `docker-compose.full.yml` + `make up-full` run the CP as a
  first-class service (DB-less memory mode).

### Scenarios + tooling

- New scenarios **S9–S15**: P-256 publish, tenant isolation, revocation,
  key rotation + admin reload, policy/authz, domain-pack gating, and
  supersession with a lineage guard.
- `gen_keys.py --algorithm ecdsa-p256` emits SEC1/JWK/`verificationMethod`;
  `pinned_keys_diff.py` encodes algorithm + validity windows.
- Registry configs gain `auth.tenant_agents`, pinned-key rotation
  windows, and documented EdDSA / revocation-feed blocks.
- `smoke_test.py` grows to 8 checks (adds P-256, JCS float stability,
  extended body fields); 44 new unit tests cover signing, cursor
  pagination, tenant headers, revocation, Retry-After, pinned-key
  windows, and the control-plane bridge.

## 2026-05-26 — Auth, security, and tenancy hardening

A coordinated pass across the control plane and the registry that
closes a long list of audit findings under one heading. Every item
below is shipped on `main` of the relevant repo; this entry is a
flat summary, not a roadmap.

### Authentication

- **JWT validation in the control plane's `AuthGuard`.** Until now the
  guard validated only API keys; the entire JWT issuance surface
  (challenge / token / introspect / revoke / cross-issuer) was tested
  but never consumed by the request path of the application
  controllers. The guard now dispatches on token shape (3-segment
  JWT vs opaque api key), validates via `CrossIssuerValidator`, and
  populates `request.actorDid` from the `sub` claim. A JWT-shaped
  token that fails verification is rejected outright — no
  fall-through to API-key matching (which would have been a silent
  oracle).

- **EdDSA signing + JWKS endpoint on both control plane and registry.**
  Opt-in via `JWT_SIGNING_ALG=EdDSA` + `JWT_PRIVATE_KEY_PEM`. The
  public key is published at `GET /.well-known/jwks.json` so
  federated peers can verify without out-of-band secret distribution.
  Trusted-issuer wire format extended to accept `EdDSA + jwks_url`
  alongside the HS256 shared-secret form. `kid` lookup with TTL
  cache, error caching, in-flight coalescing, and malformed-entry
  tolerance.

- **ECDSA-P256 acceptance on the registry's `/auth/token`.** The
  publish path already accepted P-256; the auth handshake hard-
  rejected it. Algorithm-downgrade defense kept; verifier dispatches
  on `req.algorithm`.

- **Tighter throttle on credential endpoints.** `/auth/challenge`
  and `/auth/token` (control plane) carry a 20-req/min/IP override
  on top of the 200/min global. Defends against nonce-grinding,
  credential-stuffing, and DID-resolver DoS.

- **Subject-match gate on `/auth/token/revoke`.** Admin api keys
  (listed in `AUTH_ADMIN_API_KEYS`) can revoke any JTI; JWT-
  authenticated callers can revoke only tokens whose `sub` matches
  their own DID. Mirrors the registry's `owner_of(jti) ==
  caller_did` semantics that was already in place.

- **Revocation list consulted by introspect.** Previously
  `CrossIssuerValidator.verify()` only checked signature/issuer/
  expiry; revoked tokens still came back as `{active:true}` from
  `/auth/introspect`. The validator now optional-injects the
  revocation repository and rejects local-issued tokens whose JTI
  has been revoked. Trusted-peer tokens still bypass the local list
  — peers own their own revocation feeds (see below).

### Policy engine

- **OPA / Rego backend** alongside the static-rules engine. Opt-in
  via `POLICY_BACKEND=opa`; reference Rego corpus mirrors the
  static rules so deployments can switch transparently. Caching
  wrapper preserved on top of either backend.

- **`@CheckPolicy` mounted on runs + contexts.** Previously only
  `capability.declare` carried the decorator. List, get, lineage,
  events, and run-complete are now gated by `run.read` / `run.start`;
  context retrieve is gated by `context.retrieve`. Static-rules
  decider was adjusted so retrieve-without-visibility allows
  authenticated requests through (the service layer enforces
  visibility post-fetch — the guard runs before the resource is
  loaded).

### Multi-tenancy

- **`tenant_id` column on the registry's `contexts` table.** PG +
  SQLite migrations; existing rows backfill to `'default'`. New
  composite indexes on `(tenant_id, created_at)` and
  `(tenant_id, lineage_id)` keep tenant-filtered queries off
  seqscans.

- **Tenant filter on retrieve / search / lineage / current /
  retrieve-body / admin-list.** Same opt-in semantics across all
  read paths: request without `X-Tenant-Id` returns the V0
  unfiltered view; request with the header narrows to the caller's
  tenant. Mismatch returns the same 404 shape as a non-existent row
  (no oracle).

- **JWT `tenant` claim — authoritative.** Control plane mints the
  claim from a `TENANT_AGENTS=tenant:did,...` config. Both control
  plane's `AuthGuard` and the registry's read handlers prefer the
  claim over the `X-Tenant-Id` header; if both are present and
  disagree → 403 (control plane) / `AuthChallenge` error
  (registry). A bearer can no longer assert a tenant it was never
  issued for.

- **Redis-backed quota middleware.** `TENANT_QUOTAS=tenant:action=N/min`
  wire format. Atomic INCR + EXPIRE-NX via a Lua script defends
  against the race that would have reset the window on every call.
  Fail-open on Redis outage with a warn log.

### Cross-issuer federation

- **`GET /auth/revocations` on the control plane** publishes a paged
  feed of recent revocations keyed by `revoked_at_ms`. Admin-gated.
  Strict-greater-than cursor semantics keep pages non-overlapping;
  secondary sort on `jti` keeps pagination deterministic when
  multiple revocations share a timestamp.

- **Cross-issuer revocation poller on the registry.** One background
  task per peer feed configured under `[[auth.revocation_feeds]]`.
  Polls the bearer-gated feed, writes propagated entries into the
  local revocation store, retries on transport / decode / store
  failures. With both halves running, a token revoked at the issuer
  is rejected at every consuming registry within one poll interval
  without shared state.

### Cleanup

- `auth.guard.spec.ts` slice-mismatch failures fixed.
- `ingest.service.spec.ts` TS errors fixed.
- OpenAPI/Swagger annotations on the new auth endpoints.
- Pinned-keys diff CLI for syncing the registry's `[playground]
  pinned_keys` block to the control plane's
  `CONTROL_PLANE_PINNED_KEYS` env.
- Token-manager telemetry: structured logs distinguish proactive-
  refresh from reactive-401-refresh so operators can spot
  abnormally short token lifetimes (clock skew, secret rotation).
