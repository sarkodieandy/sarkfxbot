# Architecture

GoldFlow uses one-way dependencies so broker SDK behavior cannot leak into strategy or risk logic.

```text
REST / WebSocket / scheduled jobs
              |
     application orchestration
       /       |          \
 signals     execution   reconciliation
    |           |              |
strategy      risk + state machine
       \        |             /
           BrokerAdapter
          /             \
 deterministic mock    serialized MT5 adapter

PostgreSQL: signals, orders, attempts, positions, trades, events, audit, outbox
Redis: leases, scheduling coordination, execution wake-up queue, pub/sub/cache only
Broker: authoritative source for executed positions and orders
```

## Boundaries

- `app/domain`: immutable broker-neutral values, enums, errors, and trade state machine.
- `app/indicators`, `app/market`, `app/strategies`, `app/signals`: pure closed-candle analysis.
- `app/brokers`: async broker contract, deterministic mock, alias resolver, serialized facade, and
  lazy-imported MT5 adapter. Strategy modules never import this package.
- `app/risk`: broker-native sizing, drawdown/circuit state, mode gates, and pre-trade validation.
- `app/execution`: the only authority allowed to create exposure, plus position management and
  broker reconciliation.
- `app/db`: SQLAlchemy 2 models/repositories and Alembic-managed PostgreSQL schema.
- `app/api`: JWT/RBAC FastAPI routes and WebSocket events. Routes enqueue/approve work; they cannot
  bypass execution validation.
- `app/workers`: Redis-coordinated scheduled scans, snapshots, execution, heartbeats, and recovery.
- `app/backtesting`: conservative multi-timeframe simulation using the same strategy outputs.

## Source-of-truth model

The broker decides whether an order or position actually exists. PostgreSQL stores durable
application intent, decisions, state transitions, and audit evidence. Redis is intentionally not a
permanent ledger. Its execution list contains disposable signal wake-up hints; workers always query
PostgreSQL for authoritative candidates, so a lost or consumed hint cannot lose or duplicate an
order. If state disagrees after restart, reconciliation reads the broker first, records an incident,
and repairs application state only when the mapping is unambiguous.

## Execution authority and uncertainty

Broker mutation is serialized. A durable idempotency key combines account and signal identity.
The executor claims it before send, refreshes all broker/risk inputs, calls broker `order_check`,
then sends once with protection attached. A timeout or exception after mutation becomes `UNKNOWN`.
No caller may resend until positions/orders/history have been searched by the durable broker
correlation marker.

Read-only broker calls can use bounded retry with backoff. Mutation is not a normal retry target.

## Trade lifecycle

```text
SCANNING -> SIGNAL_FOUND -> WAITING_FOR_ENTRY -> ENTRY_READY
         -> ORDER_PENDING -> ORDER_FILLED -> POSITION_OPEN
         -> TP1_HIT / BREAK_EVEN -> CLOSING -> CLOSED

Terminal branches: STOPPED_OUT, EXPIRED, CANCELLED, ERROR
```

Every transition has a reason and UTC timestamp. Invalid transitions raise a domain error.

## Failure posture

Loss of database, Redis coordination, broker health, market freshness, clock confidence, or worker
heartbeat blocks new exposure. It does not suppress broker reconciliation or protective management
of positions that already have a valid stop. Notification failure is reported but never blocks a
protective action.

## Concurrency

MT5 calls run in a dedicated one-thread executor and are also wrapped by a serialized async facade.
This avoids blocking FastAPI's event loop and prevents concurrent access to terminal state. Redis
leases coordinate jobs across processes; PostgreSQL uniqueness constraints remain the final
idempotency authority.

## Time

All internal timestamps are timezone-aware UTC. A candle represents `[open_time, close_time)`.
Signals use candles whose close time is at or before the injected decision time. UI code may
convert timestamps; strategy and persistence do not use local/broker wall-clock time.
