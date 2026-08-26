# Risk management

Capital protection has priority over signal frequency. Risk rejection is a successful safe outcome.

## Position sizing

For a protected entry, GoldFlow asks the broker to calculate profit/loss for one lot from entry to
stop. It computes:

```text
allowed cash risk = equity * configured risk fraction
raw volume        = allowed cash risk / absolute one-lot stop loss
broker volume     = floor(raw volume to broker volume step)
```

It then recomputes actual stop risk and required margin at the rounded volume. Volume must remain
inside minimum/maximum bounds and on the broker step. A $50 account at 1% has a $0.50 risk budget.
If the minimum lot exceeds it, GoldFlow rejects the order and never tightens the analytical stop.

Fallback tick-size/value arithmetic is only valid when complete contract metadata is available;
broker-native calculation is preferred.

## Default limits

```text
risk per trade:          1%
maximum daily loss:      3%
maximum weekly loss:     7%
maximum account DD:     10%
maximum open positions:  1
maximum gold positions:  1
minimum RR:             1.8
preferred RR:           2.0
```

Daily/weekly use realized loss plus configured open risk. Peak-to-valley drawdown uses recorded
peak equity. Daily and weekly locks reset only by configured UTC period rules. Account drawdown
disables AUTO and always requires an authorized manual reset.

## Pre-trade gate order

1. Environment, account type, approval, mode, and live interlock.
2. Kill switch/circuit state and dependency health.
3. Symbol metadata, trade availability, fresh tick, session, and spread.
4. Signal time and entry-zone eligibility.
5. Stop/target side, broker minimum stop distance, and minimum RR.
6. Rounded volume risk, daily/weekly/account loss, and position limits.
7. Existing broker position/order and durable idempotency key.
8. Free margin and broker margin calculation.
9. Broker `order_check`.
10. One broker send with SL/TP included.

Any missing/uncertain value fails closed and records a safe reason code.

## Position management

Multiple targets and partial close are configurable. A typical policy closes 50% at TP1, then moves
the stop to entry plus spread, commission, and slippage buffer. Break-even is optional and triggers
only at its configured R threshold. Fixed, ATR, and structure trailing modes exist but trailing is
disabled by default until out-of-sample evidence justifies it.

GoldFlow never averages down, opens a recovery grid, doubles after loss, or raises leverage.
