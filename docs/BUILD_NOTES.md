# Complete build notes

This repository contains the full CFD bot source tree and the current build changes.

## Directional news engine

`app/news.py` now produces a per-market `buy`, `sell`, or `neutral` bias with a confidence score.

- Separate news cache per market.
- Only timestamped headlines inside the configured freshness window affect direction.
- More-specific phrases take priority over overlapping generic phrases.
- Strong news that agrees with a technical setup confirms it.
- Strong opposite news vetoes the setup by default.
- Neutral/no-news does not block an otherwise valid technical setup by default.
- The old predictor still logs its forecast, but `predict.filter` is disabled in the supplied config so it does not become an extra veto layer.

This is a rules-based headline classifier, not an LLM. It does not reliably understand every nuance, expectation/surprise, or sarcasm in financial news.

## Persistent Memory v2

`app/memory.py` now uses SQLite at `logs/memory.db` and keeps the original JSONL stream for compatibility.

It remembers:
- market and strategy
- technical direction/reason
- news direction/confidence
- predictor direction
- price/spread
- setup and order events
- similar prior cases retrieved before an order
- manually pinned memories

Commands:

```bash
python -m app.memory stats
python -m app.memory recent --limit 20
python -m app.memory search "gold buy news_buy" --market gold
python -m app.memory add "Gold reacts strongly around Fed decisions" --market gold
```

The web endpoint is also searchable:

```text
/api/memory
/api/memory?q=gold%20buy&market=gold
```

## Important current limitations

This package does **not** make profitability claims. Demo-test it before live use.

The original broker layer still has important execution/risk limitations that are outside the two requested feature changes:
- realized P&L from closed positions is not reconciled into memory/risk automatically;
- a missing Capital.com order confirmation can still be treated as accepted by the original broker adapter;
- minimum-lot sizing in the original risk code can exceed the configured cash-risk target;
- the LAN web desk has no authentication.

Because of those limitations, treat this build as a development/demo build until those execution safeguards are fixed.
