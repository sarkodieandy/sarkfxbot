# Gold H1-M15-M5 Trend Pullback Strategy

Name: **Gold H1-M15-M5 Trend Pullback Strategy**

Version: **1.0.0**
Identifier: `gold_h1_m15_m5`

The strategy returns only `LONG`, `SHORT`, `WAIT`, or `EXIT`. It prefers `WAIT` when history,
alignment, quality, or confirmation is insufficient. `EXIT` is considered only when an existing
position direction is supplied.

## Inputs

- H1: dominant trend and confirmed market structure.
- M15: pullback to the configurable EMA/support/resistance area without an opposing break.
- M5: at least one deterministic confirmation—engulfing, rejection, local break, or confirmed
  higher-low/lower-high.
- Optional quality: RSI range, ATR regime, normalized spread, and session eligibility.

EMA, RSI, ATR, MACD, volume, and spread calculations live outside the strategy. Warm-up values are
explicitly unavailable rather than silently filled.

## Long path

H1 requires bullish EMA alignment/slope and bullish confirmed structure. M15 must pull back into
the EMA zone without a bearish structure break. M5 must show one of the allowed bullish
confirmations. The short path mirrors these rules.

Not every optional indicator must agree. Thresholds and weights are configuration, and every
signal stores the values and conditions that produced it.

## Confidence

Default maximum weights:

| Factor | Points |
|---|---:|
| H1 trend | 30 |
| M15 pullback | 25 |
| M5 confirmation | 25 |
| Structure alignment | 10 |
| Spread quality | 5 |
| Volatility/RSI quality | 5 |

The default execution threshold is 75. The score is deterministic evidence coverage, **not a
probability of profit**.

## Structure and look-ahead protection

A pivot with right span `n` becomes known only after those `n` later candles have closed. The
structure engine records pivot time separately from detection time and derives HH/HL/LH/LL, break
of structure, change of character, support, and resistance only from detected pivots. Backtests and
live scans use identical closed-candle rules.

## Entry and expiration

The M5 ATR defines a bounded entry zone and structural/ATR stop. Targets are calculated in R
multiples. A signal expires after its configured number of M5 bars or when current price leaves the
entry zone. An expired setup is not chased or reactivated.

## Version discipline

Any material rule, weight, or default change must create a new semantic strategy version. Store it
with every signal/trade and never combine reports from different versions without an explicit
comparison.
