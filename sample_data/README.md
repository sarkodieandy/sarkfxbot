# Synthetic XAUUSD candles

`xauusd_synthetic.csv` is deterministic, generated test data. It is not broker
history and must not be used to infer expected profitability. Regenerate it with:

```bash
python sample_data/generate_sample.py
```

The single CSV contains closed H1, M15, and M5 candles using the resolved symbol
`XAUUSDm`. Candle timestamps are UTC bar-open times.
