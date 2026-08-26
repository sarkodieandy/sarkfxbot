# GoldFlow Trading Engine

> **Risk warning:** Automated trading can result in financial loss. Past performance and
> backtests do not guarantee future results. A confidence score is a deterministic setup score,
> not a probability of winning. GoldFlow defaults to signal-only demo operation and does not
> promise profits.

GoldFlow is a risk-first, broker-agnostic XAUUSD trading backend. It evaluates a closed-candle
H1/M15/M5 trend-pullback strategy, calculates broker-aware position size, manages protected
positions, persists an audit trail, backtests the same deterministic rules, and exposes a FastAPI
REST/WebSocket backend. MetaTrader 5 with an Exness Demo account is the first real adapter;
development and CI use the deterministic `MockBrokerAdapter` and never require a terminal.

## Safety posture

- Default environment is `demo`; default mode is `SIGNAL`, which never sends an order.
- Strategy code cannot import MetaTrader 5. All broker access goes through a serialized adapter.
- Every entry needs a stop loss, valid targets, adequate risk/reward, fresh data, acceptable
  spread, margin, healthy dependencies, and duplicate checks.
- Volume is calculated from equity, stop distance, broker loss-at-one-lot, minimum/maximum volume,
  and volume step. If the minimum lot risks too much, the trade is rejected; the stop is not moved.
- Daily, weekly, and account drawdown locks block new exposure. Protected positions remain
  manageable. Account drawdown requires manual reset.
- An indeterminate broker send is never blindly retried. Its durable correlation key must be
  reconciled against broker state first.
- A real account cannot execute in demo mode. Live execution requires all production gates listed
  under [Live-trading interlock](#live-trading-interlock).
- Martingale, grids, averaging down, automatic lot doubling, and unprotected trades are absent and
  explicitly rejected.

PostgreSQL is persistent application truth. The broker is authoritative for actual executions.
Redis is only for locks, queues, pub/sub, and cache.

## Architecture

```text
FastAPI / scheduler / workers
            |
  signal + execution orchestration
            |
strategy ---+--- risk + state machine
            |
      BrokerAdapter port
       /             \
MockBroker      serialized MT5 worker
            |
 PostgreSQL + Redis + audit/outbox
```

See [architecture](docs/architecture.md), [strategy](docs/strategy.md), [risk management](docs/risk-management.md),
[operations](docs/operations.md), [deployment](docs/deployment.md), and the
[complete project tree](docs/project-tree.md).

## Requirements

- Python 3.12
- `uv` (recommended) or `pip`
- PostgreSQL 14+ and Redis 7+ for service/worker operation
- Windows, installed MetaTrader 5 terminal, and the official `MetaTrader5` Python package for MT5
- Docker/Compose is optional; Linux containers use the mock or remote execution architecture, not
  a native Windows MT5 terminal.

## Install

macOS/Linux:

```bash
cp .env.example .env
make install
```

Equivalent commands without Make:

```bash
UV_CACHE_DIR=/tmp/goldflow-uv-cache uv sync --extra dev
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
.\scripts\dev.ps1 install
```

For a locked runtime-only pip install:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Use `.venv\Scripts\python.exe` instead on Windows. Install the MT5 extra on the Windows execution
node with `uv sync --extra dev --extra mt5`.

## Start PostgreSQL and Redis

```bash
docker compose up -d postgres redis
```

For this local Compose command, set the matching URLs in `.env`:

```env
DATABASE_URL=postgresql+psycopg://goldflow:local-demo-only@localhost:5432/goldflow
REDIS_URL=redis://:local-demo-only@localhost:6379/0
```

Change both local passwords for any shared machine. To start the complete Linux mock stack:

```bash
POSTGRES_PASSWORD=replace-me REDIS_PASSWORD=replace-me docker compose up --build
```

## Database migrations

```bash
make migrate
# or
.venv/bin/alembic upgrade head
```

Windows:

```powershell
.\scripts\dev.ps1 migrate
```

The initial migration creates all trading, audit, operational, execution-attempt, and outbox
tables. Do not use SQLAlchemy `create_all` in production.

## Run modes

### Signal mode (default)

```env
TRADING_ENV=demo
TRADING_MODE=SIGNAL
BROKER_TYPE=mock
```

```bash
make api       # terminal 1
make worker    # terminal 2
```

No order is placed in this mode.

### Exness Demo semi-automatic mode

On the Windows MT5 node, after completing [the Windows/Exness guide](docs/windows-mt5-setup.md):

```env
TRADING_ENV=demo
TRADING_MODE=SEMI_AUTO
BROKER_TYPE=mt5
MT5_LOGIN=your-demo-login
MT5_SERVER=your-exact-demo-server
MT5_PASSWORD=loaded-from-a-local-secret
MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```

```powershell
.\scripts\dev.ps1 worker
```

Signals must then be approved through `POST /api/v1/signals/{id}/approve`. Immediately before
sending, GoldFlow refreshes broker/account/market state and repeats all risk checks.

### Exness Demo automatic mode

Use the same demo settings but set:

```env
TRADING_MODE=AUTO
```

Then restart the worker. Confirm `GET /ready`, account type `DEMO`, symbol resolution, and the risk
configuration before leaving it unattended. Demo execution can still lose the demo balance and is
not evidence of future live performance.

## Live-trading interlock

Real-account execution is denied unless all of these are true at the instant of execution:

```env
TRADING_ENV=production
LIVE_TRADING_ENABLED=true
LIVE_TRADING_CONFIRMATION=<operator-supplied-value>
LIVE_TRADING_CONFIRMATION_SECRET=<same value from a separate secret setting>
```

The broker must also independently report a `REAL` account, the requested mode must be
`SEMI_AUTO` or `AUTO`, production readiness checks must pass, and all normal risk gates still run.
Possessing MT5 credentials alone never enables live trading. `POST /mode` cannot weaken these
environment gates.

## Backtest

Run the deterministic bundled sample:

```bash
make backtest
# or
.venv/bin/python scripts/run_backtest.py --sample --output reports/sample
```

The command writes JSON and HTML reports containing trade counts, win/loss statistics, profit
factor, expectancy, R multiples, drawdown, Sharpe/Sortino, streaks, and daily/monthly returns. The
engine synchronizes H1/M15/M5 using only candles closed by each decision time and applies configured
spread, slippage, and commission. Same-bar stop/target ambiguity is handled conservatively.

For external CSV data, run `python scripts/run_backtest.py --help` for the required UTC OHLCV/spread
schema. Separate train, validation, and final test periods; do not select parameters on the final
test interval.

## API and authentication

Start the API:

```bash
make api
```

- OpenAPI: `http://localhost:8000/docs`
- Liveness: `GET /health`
- Trading readiness: `GET /ready`
- Prometheus: `GET /metrics`
- Versioned API: `/api/v1/*`
- WebSocket: `/ws?token=<JWT>`

All account, signal, position, trade, strategy, risk, and admin routes require a signed JWT. Roles
are `viewer`, `trader`, and `admin`. For isolated local development only, create a short-lived token:

```bash
.venv/bin/python scripts/create_token.py --subject local-admin --role admin
```

Never put a token into logs, source control, screenshots, or shell history on a shared host. In a
deployed system, issue compatible tokens from the configured identity provider/Supabase Auth.
API request/response details are in [docs/api.md](docs/api.md).

## MT5 validation commands

Run these only on the Windows host with MT5 open and logged into the intended account:

```powershell
.venv\Scripts\python.exe scripts\connect_mt5.py
.venv\Scripts\python.exe scripts\discover_symbols.py XAUUSD
.venv\Scripts\python.exe scripts\verify_demo.py
```

They report connection/account metadata and discovered symbols without printing the password. They
must fail honestly when the terminal, market data, symbol metadata, or account classification is
unavailable.

## Quality checks

```bash
make lint
make typecheck
make test
make backtest
# all of the above
make check
```

Tests cover indicators, no-look-ahead structure, strategy symmetry/default WAIT, position sizing,
risk gates, live/demo mode truth tables, order-check-before-send, timeout reconciliation, duplicate
suppression, position management, restart recovery, persistence, auth/admin API behavior, and the
sample backtest. CI never contacts a real broker.

## Troubleshooting

| Symptom | Safe interpretation and action |
|---|---|
| `/ready` returns 503 | Inspect component statuses. New exposure remains blocked until database, Redis, broker, market data, clock, and worker heartbeats are healthy. |
| `MetaTrader5` import unavailable | Expected on macOS/Linux. Run the adapter on Windows and install the `mt5` extra. |
| MT5 initialize/login fails | Verify terminal path, exact Exness server, demo login, terminal session, architecture, and that another terminal profile did not take over. Never log the password. |
| XAUUSD not found | Run symbol discovery. Exness suffixes vary; do not hardcode `XAUUSDm`. Ambiguous matches are rejected. |
| `MINIMUM_VOLUME_EXCEEDS_RISK` | The account/stop cannot safely support the broker minimum lot. Do not narrow the stop; skip the trade. |
| `TRADE_SKIPPED_HIGH_SPREAD` | Wait for spread normalization or review the price-unit threshold; do not chase the signal. |
| Signal becomes `EXPIRED` | Its time or entry zone has passed. Generate a new setup; never reuse/chase it. |
| Order status `UNKNOWN` | Do not resend. Restore broker connectivity and run reconciliation. |
| Docker API starts but MT5 does not | Native MetaTrader 5 is not in the Linux container. Use Windows Pattern A or a secured Windows execution node. |
| Database migration fails | Check `DATABASE_URL`, PostgreSQL reachability/permissions, then run `alembic current` and `alembic upgrade head`. Never edit production tables manually to “match.” |

## Security checklist

- [ ] `.env` is untracked and permissions are restricted.
- [ ] MT5, JWT, Telegram, database, and Redis secrets come from a secret manager/Docker secrets.
- [ ] JWT secret is rotated from the development default; issuer/audience are verified.
- [ ] CORS origins are explicit; TLS and a rate-limiting reverse proxy protect public deployments.
- [ ] API roles are least privilege; admin token issuance and live-mode changes are audited.
- [ ] PostgreSQL and Redis are private-network only and use distinct rotated credentials.
- [ ] Container runs non-root, read-only, with `no-new-privileges`.
- [ ] Dependency lock, CI lint/type/tests, vulnerability scanning, backups, and restore tests pass.
- [ ] Logs/Sentry have been checked for secret and account-data leakage.
- [ ] The Windows MT5 node is patched, encrypted, firewalled, monitored, and access-controlled.

## Risk checklist before demo AUTO

- [ ] Broker reports `DEMO`; exact account/server were manually verified.
- [ ] Symbol alias, contract size, tick size/value, volume limits/step, stop level, and margin match
      the terminal.
- [ ] Current spread and tick age are within limits; all H1/M15/M5 inputs are closed UTC candles.
- [ ] Every order has broker-validated SL/TP, acceptable RR, and recomputed rounded-volume risk.
- [ ] Daily/weekly/account drawdown baselines and reset rules are correct.
- [ ] Kill switch, pending cancellation, restart recovery, and reconciliation were exercised.
- [ ] Telegram/monitoring alerts and external heartbeat alarm were tested.
- [ ] At least the configured demo-trade count, drawdown, and profit-factor gates were evaluated;
      these metrics do not guarantee future performance.
- [ ] Trailing stop remains disabled unless separately validated out of sample.

## Deployment summary

For the first Exness Demo, use **Pattern A**: Windows VPS + installed MT5 terminal + Python worker,
with PostgreSQL/Redis/API either local or on a protected network. It has fewer failure boundaries.

Pattern B separates a hardened Windows execution node from a Linux API/worker/database stack. That
requires mutually authenticated transport, strict request signing/replay defense, durable
idempotency, and additional failure/recovery testing before use. Details are in
[docs/deployment.md](docs/deployment.md).
