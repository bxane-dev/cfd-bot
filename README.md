# CFD Bot

Built by **bxane**

Capital.com CFD bot + local trading desk for **21 strategy-mapped markets**.

> CFDs are leveraged and high risk. No strategy, model, or backtest guarantees profit. Use demo mode first.

## Main features

- Capital.com **demo + live** trading.
- 21 markets with a dedicated strategy per market.
- Equity/portfolio-based position sizing and SL/TP.
- One-time SL/TP sync for new manual trades using the bot recommendation.
- Spread, news, predictor, quality, margin, position, and duplicate-order gates.
- Fast local desk with Capital.com **WebSocket price streaming** and REST fallback.
- Local trading memory, trade/equity logs, walk-forward analysis, and tuning.

## Start

### Windows

- **Demo:** double-click `START.bat`
- **Live:** double-click `START_LIVE.bat`
- **Update:** run `UPDATE.bat` — updates only from `bxane-dev/cfd-bot`

On first run, add your Capital.com credentials to `.env`:

```text
CAPITAL_API_KEY=
CAPITAL_EMAIL=
CAPITAL_API_PASSWORD=
CAPITAL_ACCOUNT_ID=
```

`CAPITAL_ACCOUNT_ID` is optional.

### Terminal

```bash
python -m pip install -r requirements.txt
python -m app.main --mode demo
```

Live:

```bash
python -m app.main --mode live
```

## Risk

Current standard profile at **200+ account-currency units**:

- 0.7% target risk per trade
- 30% portfolio margin cap
- 12 open positions max
- 4 positions per market
- 8 index positions max

Small-account bootstrap:

- **40–199.99:** bootstrap mode
- 2% target risk per trade
- 4% hard minimum-lot risk cap
- 80% portfolio margin cap
- 3 open positions max
- 2 positions per market
- 2 index positions max
- **Below 40:** new entries blocked
- **200+:** automatically returns to standard profile

The broker-reported account equity/currency is used for live risk calculations.

## Markets, strategies & main trading times

Times are **Europe/Zurich / Swiss time**. These are the main high-activity windows, not guaranteed entry times. DST differences can temporarily shift some markets by about one hour.

| Market | Strategy | Main time |
|---|---|---|
| AUD/USD | `williams` | 00:00–04:00 |
| USD/JPY | `stochastic` | 02:00–05:00 |
| Japan 225 | `sar` | 02:00–04:30 |
| Hong Kong 50 | `supertrend` | 03:30–06:00 |
| EUR/JPY | `engulfing` | 08:00–10:30 |
| Germany 40 | `orb` | 09:00–10:30 |
| UK 100 | `adx_di` | 09:00–11:30 |
| France 40 | `vwap` | 09:00–11:30 |
| Switzerland 20 | `momentum` | 09:00–11:30 |
| GBP/USD | `triple_ema` | 09:00–12:00 |
| GBP/JPY | `inside_bar` | 09:00–12:00 |
| USD/CHF | `bollinger` | 09:30–12:00 |
| Gold | `rsi_reversion` | 14:00–17:00 |
| EUR/USD | `sma_cross` | 14:00–17:00 |
| Silver | `squeeze` | 14:00–17:30 |
| Natural Gas | `keltner` | 14:30–18:00 |
| Copper | `cci` | 14:30–18:00 |
| US Crude Oil | `donchian` | 14:30–18:30 |
| US Tech 100 | `ema_pullback` | 15:45–18:00 |
| Wall Street 30 | `macd_trend` | 15:45–18:30 |
| US 500 | `ema_atr` | 15:45–18:30 |

**Main overall activity window:** roughly **14:00–18:00 Swiss time**.

Every order still has to pass the bot's strategy and risk filters.

## Desk

- WebSocket prices when available.
- Visible price/status refresh: about **500 ms**.
- Account cache: **2 s**.
- Trades/activity refresh: **5 s**.
- Charts refresh: **10 s**.
- Shows the exact no-trade reason per market.
- Dashboard is localhost-only by default.

To expose it to your trusted LAN:

```text
CFD_WEB_HOST=0.0.0.0
```

Do not expose the dashboard port directly to the public internet.

## Important files

- `config.yaml` — bot/risk settings
- `app/main.py` — trading loop
- `app/risk.py` — sizing and risk limits
- `app/broker/capital.py` — Capital.com REST/WebSocket integration
- `app/strategy/` — strategies
- `web/index.html` — desk UI
- `docs/GUIDE.md` — detailed guide

## Tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```

GitHub Actions also runs Python, dashboard, launcher, and risk smoke tests.

## License

Source-Available Use-Only License v1.0. See `LICENSE.md`.
