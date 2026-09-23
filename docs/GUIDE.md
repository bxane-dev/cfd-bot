# CFD Bot Setup & Operating Guide

## 1. Start with a Capital.com demo account

Create/use a Capital.com **demo** account and create API credentials for that environment. Demo and live credentials are treated as separate on purpose; this build refuses to switch environments automatically.

You need:

- `CAPITAL_EMAIL`
- `CAPITAL_API_KEY`
- `CAPITAL_API_PASSWORD` — the API password, not necessarily your normal website password
- optional `CAPITAL_ACCOUNT_ID`

## 2. Install the bot

### Windows

The easiest route is `START.bat`. It creates `.venv`, installs dependencies, copies `.env.example` to `.env`, validates required fields, and starts **demo mode only**.

### Manual setup

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

Activate the environment, copy `.env.example` to `.env`, then fill your credentials.

Never commit `.env`.

## 3. Check configuration before first run

Open `config.yaml` and verify:

```yaml
mode: demo
risk:
  risk_per_trade_pct: 0.4
  max_portfolio_allocation_pct: 30.0
  daily_loss_enabled: false
  max_daily_loss_pct: 2.0
  max_open_positions: 12
  max_positions_per_market: 4
  max_index_positions: 8
```

`max_portfolio_allocation_pct: 30.0` limits total gross open CFD exposure to 30% of account equity; it is not 30% per individual trade. These are configuration examples, not recommendations.

The bot will also reject a trade when the minimum permitted broker size would risk more cash than `risk_per_trade_pct` allows.

## 4. First demo test

Run exactly one cycle:

```bash
python -m app.main --mode demo --once
```

Check the startup output. It should identify:

- DEMO environment
- account equity/currency
- enabled markets
- per-trade and daily risk limits

If startup reports an error, resolve it before trading.

## 5. Continuous demo run

```bash
python -m app.main --mode demo
```

Stop with `Ctrl+C`.

The loop checks market sessions, configured position limits, spread, news, strategy signal, predictor filter, position sizing, then broker execution.

## 6. Understanding order states

- **accepted** — Capital.com confirmed the order.
- **rejected** — Capital.com explicitly rejected it.
- **unknown** — submission happened but confirmation was unavailable/ambiguous.

`unknown` is intentionally treated as unsafe. The bot persists it and blocks another order in the same market until broker state resolves it.

## 7. Daily risk and restart recovery

At each cycle the bot reconciles broker positions and trade transaction history. Closed-trade cash deltas are deduplicated before entering the daily realized P&L counter.

The daily halt also watches equity drawdown from the session start-equity baseline.

Local state is stored under `logs/`. Broker state is treated as the source of truth whenever possible.

## 8. News gate

Default behavior:

```yaml
news:
  enabled: true
  refresh_seconds: 60
  max_headline_age_seconds: 1800
  fail_closed: false
```

Only fresh headlines relevant to the desk/current market can block new entries. If every news request fails and `fail_closed` is `false`, the bot logs the outage and continues. Set it to `true` if you prefer no new entries during a total news outage.

## 9. Predictor

```yaml
predict:
  enabled: true
  filter: true
  horizon: 5
  min_p: 0.55
  min_edge: 0.0
```

The model trains only on labels whose future bars are already known. A recent held-out block measures Brier score against a base-rate predictor. If it fails to improve on that baseline, it is marked unhealthy and bypassed instead of vetoing the core strategy.

## 10. Walk-forward research

Run without `--apply` first:

```bash
python -m research.walk --mode demo --method rolling --bars 1000 --train 400 --test 100 --embargo 5 --optuna --trials 15
```

Review `logs/walk.json` before applying changes.

The hardened simulator:

- uses the live minimum-lot sizing rule;
- subtracts typical spread;
- supports configured slippage/fixed trade cost;
- selects final strategies from OOS performance with guardrails rather than training-selection popularity.

## 11. Going live

Only after extended demo validation, explicitly run:

```bash
python -m app.main --mode live
```

On Windows, use `START_LIVE.bat`. It shows the active risk settings and requires typing `LIVE` before connecting to the live endpoint.

## 12. Troubleshooting

**Missing login values** — check `.env` names and make sure values are not blank.

**Login fails on demo** — confirm the API key/password were created for the demo environment.

**“minimum lot risks … > budget …”** — the smallest possible order is too large for your configured risk budget. The bot skips it instead of exceeding the budget.

**“unknown order pending reconciliation”** — do not force another order. The bot is waiting for broker state to establish whether the first submission opened.

**News unavailable** — inspect the message and choose whether `news.fail_closed` should be `true` or `false` for your operating preference.

**No signal** — this is normal. Session, spread, risk, strategy, news, predictor, or existing-position gates can all intentionally skip a trade.

## 13. Validate after any code/config change

```bash
python -m pytest -q
python -m py_compile app/main.py app/auto.py app/desk.py app/web_app.py app/risk.py app/predict.py app/news.py research/walk.py
```

Do not treat passing tests or backtests as evidence that live trading will be profitable.
