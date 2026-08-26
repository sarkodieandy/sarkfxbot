# Deployment

## Pattern A — recommended first demo

```text
Windows VPS
  MetaTrader 5 terminal (logged into Exness Demo)
  GoldFlow Python worker (official MetaTrader5 package)
  local/private Redis
       |
  private PostgreSQL + FastAPI (local or protected host)
```

Pattern A has the fewest execution boundaries and is recommended for initial demo validation. Keep
the VPS patched, encrypted, firewalled, monitored, and restricted to named administrators. Run one
MT5 execution authority per terminal/account profile.

## Pattern B — separated execution node

```text
Windows MT5 execution node
      | mutually authenticated, signed, replay-protected requests
Linux API/workers ---- Redis
      |
PostgreSQL
```

The repository's Linux Compose stack does not pretend to host native MT5. Pattern B requires an
additional secured broker-command transport, certificate lifecycle, nonce/idempotency persistence,
network partition tests, and a reconciliation protocol. Do not expose MT5 control directly to the
internet.

## Compose services

- `api`: non-root, read-only FastAPI container.
- `worker`: non-root, read-only scheduler/worker container.
- `migrate`: one-shot `alembic upgrade head`.
- `postgres`: durable volume and application truth.
- `redis`: append-only coordination/cache service; not the trade ledger.

Use a cloud secret manager or Docker secrets in shared environments. The included password defaults
are local-demo conveniences only. Put PostgreSQL/Redis on private networks, terminate TLS at a
hardened reverse proxy, restrict Prometheus access, back up PostgreSQL, and test restores.

## Release sequence

1. Run lint, formatting check, mypy, tests, backtest, dependency/security scans.
2. Back up and test the database restore point.
3. Deploy code with `TRADING_MODE=SIGNAL`.
4. Run Alembic migration once.
5. Verify health, time, broker account/type, symbol contract, and reconciliation.
6. Observe signal-only behavior.
7. Enable demo `SEMI_AUTO`, exercise approval/cancel/restart/kill switch.
8. Enable demo `AUTO` only after configured validation thresholds.

Rollback code without rolling back trading data. Database downgrade must be separately reviewed;
never destructively downgrade an active trade ledger by habit.
