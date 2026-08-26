# Windows MetaTrader 5 and Exness Demo setup

This validation must run on Windows; this repository cannot prove an MT5/Exness connection from
macOS/Linux CI.

1. Create an Exness **Demo** MT5 account. Record its login and exact server name separately from its
   password.
2. Install the official MetaTrader 5 terminal from the broker/approved source.
3. Open the terminal and log into that demo account. Confirm the displayed server/account and that
   account type is demo.
4. In Market Watch, expose Gold/XAU instruments. The suffix may be `XAUUSD`, `XAUUSDm`, `XAUUSDc`,
   `XAUUSD247m`, `.pro`, or another broker-defined value; do not configure a guessed alias.
5. Enable algorithmic trading where the terminal/account policy requires it. GoldFlow still keeps
   its own mode and risk interlocks.
6. Install 64-bit Python 3.12 matching the terminal architecture and install `uv`.
7. In PowerShell:

   ```powershell
   Copy-Item .env.example .env
   uv sync --extra dev --extra mt5
   ```

8. Set these values in the local `.env`; keep the file restricted and untracked:

   ```env
   TRADING_ENV=demo
   TRADING_MODE=SIGNAL
   BROKER_TYPE=mt5
   MT5_LOGIN=<demo-login>
   MT5_SERVER=<exact-demo-server>
   MT5_PASSWORD=<demo-trading-password>
   MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
   CANONICAL_SYMBOL=XAUUSD
   ```

9. Validate without placing an order:

   ```powershell
   .venv\Scripts\python.exe scripts\connect_mt5.py
   .venv\Scripts\python.exe scripts\discover_symbols.py XAUUSD
   .venv\Scripts\python.exe scripts\verify_demo.py
   ```

   Confirm broker, exact server, masked account identifier as appropriate, balance/equity, `DEMO`,
   resolved symbol, digits, point, tick size/value, contract size, volume min/max/step, and stop
   distance. No script should print the password.

10. Run signal-only mode and compare H1/M15/M5 timestamps/OHLC/spread against the terminal. Ensure
    the current unfinished candle is excluded.
11. Exercise the kill switch, restart recovery, and a deliberately rejected minimum-lot/risk case.
12. Move to `SEMI_AUTO` for a tightly supervised demo order. Confirm attached SL/TP immediately in
    MT5 and reconcile the broker ticket before considering demo `AUTO`.

If any command reports a REAL or UNKNOWN account, refuse automatic execution and correct the
session/configuration. Credentials alone never authorize real trading.
