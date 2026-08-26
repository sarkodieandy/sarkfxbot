# Operations runbook

## Startup

1. Connect to PostgreSQL and apply migrations.
2. Connect Redis and acquire the instance/worker lease.
3. Initialize broker and verify exact account identity/type/server.
4. Resolve canonical `XAUUSD` from broker metadata.
5. Load durable circuit/config/execution state.
6. Fetch broker positions and pending orders; reconcile database state.
7. Resume protection for existing positions before scanning for exposure.
8. Record heartbeat and expose readiness.

## Kill switch

`POST /api/v1/admin/kill-switch` requires an admin JWT and reason. Activation immediately blocks
new orders, attempts to cancel pending entries, preserves management of open protected positions,
records an audit event, and emits an alert. A broker outage may defer pending cancellation, so run
reconciliation as soon as connectivity returns.

## Unknown execution result

Never resend. Mark the execution attempt `UNKNOWN`, preserve its idempotency/correlation key, block
that signal, query broker open positions and orders, then query history for the bounded incident
window. Classify it as filled/pending/rejected only with broker evidence. Escalate an ambiguous
result for manual review.

## Drawdown re-enablement

Account-drawdown locks persist across restarts and UTC daily/weekly boundaries. After investigating
the cause and independently verifying the broker account, an administrator may call
`POST /api/v1/admin/circuit-reset` with a reason. The action is audited and clears durable circuit
locks only; it leaves the engine in `SIGNAL`, does not clear the emergency kill switch, and does not
bypass subsequent pre-trade validation. A separate reviewed mode change is required to resume
automatic demo execution.

## Restart recovery

Startup does not equate empty memory with an empty account. Broker positions/orders are loaded and
compared by ticket/correlation. Broker-only records are recovered; database-only open records are
closed/cancelled only when broker health is confirmed and absence is authoritative. Every repair is
an incident and audit event.

## Heartbeats and alerts

Workers write a heartbeat every 30 seconds. External monitoring should alert after two missed
intervals. Alert on database/Redis/broker loss, stale market data, clock problems, circuit locks,
unknown sends, order rejections, protection failures, and reconciliation drift. Notification
failure itself must be monitored.

## Daily checks

- Broker/account/server/type and trading permission.
- Open positions each have expected SL/TP and match PostgreSQL.
- Pending entries are unexpired and match PostgreSQL.
- Spread/tick freshness, clock, disk, worker heartbeat, queue depth.
- Daily/weekly loss and peak-equity baselines.
- Unknown execution attempts, outbox backlog, failed notifications, and audit anomalies.

## Incident containment

Activate the kill switch for uncertain broker behavior, duplicate suspicion, stale/corrupt market
data, clock drift, persistence loss, or unexplained state divergence. Do not terminate management of
an already protected position unless operational policy explicitly transfers management to a human
in the terminal. Preserve logs, database snapshot, broker history, and correlation IDs.
