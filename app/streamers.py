from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from instruments import Market

_CACHE: dict[str, dict] = {}

BUY_PHRASES = (
    "going long",
    "long setup",
    "long trade",
    "buy setup",
    "buy signal",
    "buying",
    "bullish",
    "breakout higher",
    "breakout up",
    "upside breakout",
)
SELL_PHRASES = (
    "going short",
    "short setup",
    "short trade",
    "sell setup",
    "sell signal",
    "selling",
    "bearish",
    "breakdown lower",
    "breakdown",
    "downside breakout",
)
AMBIGUOUS_PHRASES = (
    "buy or sell",
    "sell or buy",
    "long or short",
    "short or long",
    "bullish or bearish",
    "bearish or bullish",
)


def _text(node) -> str:
    if isinstance(node, str):
        return node.strip()
    if not isinstance(node, dict):
        return ""
    if node.get("simpleText"):
        return str(node["simpleText"]).strip()
    runs = node.get("runs") or []
    return "".join(str(r.get("text") or "") for r in runs if isinstance(r, dict)).strip()


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _initial_data(html: str) -> dict:
    decoder = json.JSONDecoder()
    for marker in ("var ytInitialData = ", "ytInitialData = "):
        pos = html.find(marker)
        if pos < 0:
            continue
        pos = html.find("{", pos + len(marker))
        if pos < 0:
            continue
        try:
            payload, _ = decoder.raw_decode(html[pos:])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    raise ValueError("YouTube search payload not found")


def _extract_results(payload: dict, limit: int) -> list[dict]:
    out = []
    seen = set()
    for node in _walk(payload):
        renderer = None
        for key in ("videoRenderer", "gridVideoRenderer", "compactVideoRenderer"):
            if isinstance(node.get(key), dict):
                renderer = node[key]
                break
        if not renderer:
            continue
        video_id = str(renderer.get("videoId") or "")
        title = _text(renderer.get("title"))
        if not video_id or not title or video_id in seen:
            continue
        seen.add(video_id)
        channel = (
            _text(renderer.get("ownerText"))
            or _text(renderer.get("longBylineText"))
            or _text(renderer.get("shortBylineText"))
            or "unknown"
        )
        published = _text(renderer.get("publishedTimeText"))
        badges = " ".join(
            _text(x.get("metadataBadgeRenderer"))
            for x in (renderer.get("badges") or [])
            if isinstance(x, dict)
        )
        snippets = " ".join(
            _text(x.get("snippetText") or x.get("text"))
            for x in (renderer.get("detailedMetadataSnippets") or [])
            if isinstance(x, dict)
        )
        live = "live" in badges.lower() or "live now" in title.lower()
        out.append(
            {
                "video_id": video_id,
                "title": title,
                "channel": channel,
                "published": published,
                "live": live,
                "text": f"{title} {snippets}".strip(),
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
        if len(out) >= limit:
            break
    return out


def _youtube_search(query: str, limit: int = 20, timeout: int = 12) -> list[dict]:
    q = urllib.parse.quote_plus(query)
    url = f"https://www.youtube.com/results?search_query={q}&hl=en"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    return _extract_results(_initial_data(html), limit)


def _age_hours(published: str, live: bool = False) -> float | None:
    if live:
        return 0.0
    low = str(published or "").lower()
    if not low:
        return None
    match = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)", low)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    scale = {
        "minute": 1.0 / 60.0,
        "hour": 1.0,
        "day": 24.0,
        "week": 24.0 * 7.0,
        "month": 24.0 * 30.0,
        "year": 24.0 * 365.0,
    }[unit]
    return value * scale


def _remove_negated_direction(text: str) -> str:
    """Remove explicit negated stance phrases before directional scoring."""
    patterns = (
        r"\b(?:not|never|no|avoid|without|dont|don't|do not)\s+(?:to\s+)?(?:buy|buying|long|bullish)\b",
        r"\b(?:not|never|no|avoid|without|dont|don't|do not)\s+(?:to\s+)?(?:sell|selling|short|bearish)\b",
    )
    out = text
    for pattern in patterns:
        out = re.sub(pattern, " ", out)
    return re.sub(r"\s+", " ", out).strip()


def classify_text(text: str) -> str:
    low = re.sub(r"\s+", " ", str(text or "").lower())
    if any(p in low for p in AMBIGUOUS_PHRASES):
        return "neutral"

    cleaned = _remove_negated_direction(low)
    buy_hits = sum(1 for p in BUY_PHRASES if p in cleaned)
    sell_hits = sum(1 for p in SELL_PHRASES if p in cleaned)

    # Only explicit stance phrases count. Bare words like "buy", "sell",
    # "long" or "short" are too ambiguous for a trading veto.
    if buy_hits > sell_hits and buy_hits >= 1:
        return "buy"
    if sell_hits > buy_hits and sell_hits >= 1:
        return "sell"
    return "neutral"


def consensus_from_items(items: list[dict], cfg: dict) -> dict:
    scfg = cfg.get("streamers") or {}
    allowlist = {
        str(x).strip().lower()
        for x in (scfg.get("channels") or [])
        if str(x).strip()
    }
    unique: dict[str, dict] = {}
    max_age_hours = float(scfg.get("max_age_hours", 12) or 0)
    recent_only = bool(scfg.get("recent_only", True))
    for item in items:
        age_hours = _age_hours(item.get("published") or "", bool(item.get("live")))
        if recent_only and age_hours is None:
            continue
        if max_age_hours > 0 and age_hours is not None and age_hours > max_age_hours:
            continue
        channel = str(item.get("channel") or "unknown").strip()
        if allowlist and channel.lower() not in allowlist:
            continue
        side = classify_text(item.get("text") or item.get("title") or "")
        if side not in ("buy", "sell"):
            continue
        key = channel.lower()
        if key in unique:
            continue
        unique[key] = {**item, "side": side, "age_hours": age_hours}

    votes = list(unique.values())
    buys = sum(1 for x in votes if x["side"] == "buy")
    sells = sum(1 for x in votes if x["side"] == "sell")
    total = buys + sells
    min_sources = int(scfg.get("min_sources", 3) or 3)
    majority = float(scfg.get("majority_threshold", 0.60) or 0.60)

    if total < min_sources:
        return {
            "side": "neutral",
            "confidence": 0.0,
            "votes": total,
            "buy_votes": buys,
            "sell_votes": sells,
            "reason": f"streamer consensus unavailable ({total}/{min_sources} directional creators)",
            "sources": votes[:8],
        }

    side = "buy" if buys > sells else "sell" if sells > buys else "neutral"
    winner = max(buys, sells)
    confidence = winner / total if total else 0.0
    if side == "neutral" or confidence < majority:
        side = "neutral"
        reason = f"streamer split buy={buys} sell={sells} ({confidence:.0%}<{majority:.0%})"
    else:
        reason = f"streamer majority {side} {winner}/{total} ({confidence:.0%})"

    return {
        "side": side,
        "confidence": round(confidence, 3),
        "votes": total,
        "buy_votes": buys,
        "sell_votes": sells,
        "reason": reason,
        "sources": votes[:8],
    }


def streamer_signal(cfg: dict, market: Market) -> dict:
    """Public creator consensus. This module never sends orders itself."""
    scfg = cfg.get("streamers") or {}
    if not scfg.get("enabled", False):
        return {
            "side": "neutral",
            "confidence": 0.0,
            "votes": 0,
            "buy_votes": 0,
            "sell_votes": 0,
            "reason": "streamer consensus off",
            "sources": [],
            "errors": [],
        }

    refresh = max(60.0, float(scfg.get("refresh_seconds", 60) or 60))
    now = time.time()
    cached = _CACHE.get(market.key)
    if cached and now - float(cached.get("ts", 0.0)) < refresh:
        return cached["data"]

    base = market.search_term or market.name
    query = str(
        (scfg.get("queries") or {}).get(market.key)
        or f'{base} CFD trading live market analysis bullish bearish long short buy sell'
    )
    max_results = max(5, min(40, int(scfg.get("max_results", 20) or 20)))

    try:
        items = _youtube_search(query, limit=max_results)
        result = consensus_from_items(items, cfg)
        result["query"] = query
        result["errors"] = []
    except Exception as exc:
        result = {
            "side": "neutral",
            "confidence": 0.0,
            "votes": 0,
            "buy_votes": 0,
            "sell_votes": 0,
            "reason": f"streamer lookup unavailable: {str(exc)[:120]}",
            "sources": [],
            "query": query,
            "errors": [str(exc)[:180]],
        }

    _CACHE[market.key] = {"ts": now, "data": result}
    return result
