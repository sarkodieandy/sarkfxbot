# API contract summary

OpenAPI is generated at `/openapi.json` and rendered at `/docs`.

Public operational endpoints:

- `GET /health` — process liveness and component detail.
- `GET /ready` — 200 only when dependencies required for new exposure are healthy; otherwise 503.
- `GET /metrics` — Prometheus text exposition (protect at the network boundary).

Authenticated `/api/v1` endpoints:

- viewer+: `GET /account`, `/symbols`, `/signals`, `/signals/{id}`, `/positions`, `/trades`,
  `/metrics`, `/strategy`, `/risk`.
- trader+: `POST /signals/{id}/approve`, `/positions/{id}/close`.
- admin: `POST /strategy/config`, `/risk/config`, `/mode`, `/admin/kill-switch`,
  `/admin/reconcile`, `/admin/circuit-reset`.

Bearer tokens must contain `sub`, `role`, `jti`, `iss`, `aud`, `iat`, and `exp`, signed with the
configured algorithm/secret. Roles are `viewer`, `trader`, and `admin`. A compatible external
identity provider can issue the same claims.

WebSocket clients connect to `/ws?token=<JWT>` and receive envelopes:

```json
{
  "event": "SIGNAL",
  "timestamp": "2026-01-01T12:00:00+00:00",
  "payload": {}
}
```

Event types include price, signal, position, P&L, bot status, and health. Treat the query token as a
secret and prefer short expiration/TLS; a browser client should avoid retaining the URL in logs.

Config changes require a human-readable reason. Strategy updates create a semantic patch version;
risk versions are immutable snapshots. Execution approvals never skip expiry or risk revalidation.
