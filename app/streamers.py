from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request

from instruments import Market

_CACHE: dict[str, dict] = {}
_AUTH_CACHE: dict[str, dict] = {}
_DIRECTORY_CACHE: dict[str, dict] = {}

BUY_PHRASES = (
    "going long",
    "long setup",
    "long trade",
    "buy setup",
    "buy signal",
    "buy now",
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
    "sell now",
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
TRADING_CONTEXT = (
    "trading",
    "trade",
    "trader",
    "forex",
    "cfd",
    "futures",
    "market",
    "markets",
    "scalp",
    "scalping",
    "daytrade",
    "day trading",
    "technical analysis",
    "price action",
    "long",
    "short",
    "buy",
    "sell",
    "bullish",
    "bearish",
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


def _request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: int = 12,
) -> dict:
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
            ),
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("unexpected JSON response")
    return payload


def _client_credentials_token(
    cache_key: str,
    token_url: str,
    client_id: str,
    client_secret: str,
) -> str:
    now = time.time()
    cached = _AUTH_CACHE.get(cache_key) or {}
    if cached.get("token") and now < float(cached.get("expires_at") or 0) - 60:
        return str(cached["token"])

    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    payload = _request_json(
        token_url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
        method="POST",
    )
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"{cache_key} app token unavailable")
    expires_in = max(300, int(payload.get("expires_in") or 3600))
    _AUTH_CACHE[cache_key] = {
        "token": token,
        "expires_at": now + expires_in,
    }
    return token


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
                "platform": "youtube",
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


def _twitch_token() -> tuple[str, str]:
    client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
    client_secret = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Twitch credentials not configured")
    token = _client_credentials_token(
        "twitch",
        "https://id.twitch.tv/oauth2/token",
        client_id,
        client_secret,
    )
    return client_id, token


def _twitch_search(query: str, limit: int = 20) -> list[dict]:
    client_id, token = _twitch_token()
    params = urllib.parse.urlencode(
        {
            "query": query,
            "live_only": "true",
            "first": max(1, min(100, int(limit))),
        }
    )
    payload = _request_json(
        f"https://api.twitch.tv/helix/search/channels?{params}",
        headers={
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id,
        },
    )
    out = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not item.get("is_live"):
            continue
        login = str(item.get("broadcaster_login") or "").strip()
        channel = str(item.get("display_name") or login or "unknown").strip()
        title = str(item.get("title") or "").strip()
        game_name = str(item.get("game_name") or "").strip()
        tags = [str(x) for x in (item.get("tags") or []) if str(x).strip()]
        out.append(
            {
                "platform": "twitch",
                "channel": channel,
                "channel_key": login or channel,
                "title": title,
                "published": "live",
                "live": True,
                "text": " ".join([title, game_name, *tags]).strip(),
                "url": f"https://www.twitch.tv/{login}" if login else "https://www.twitch.tv/",
            }
        )
    return out


def _kick_token() -> str:
    client_id = os.getenv("KICK_CLIENT_ID", "").strip()
    client_secret = os.getenv("KICK_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Kick credentials not configured")
    return _client_credentials_token(
        "kick",
        "https://id.kick.com/oauth/token",
        client_id,
        client_secret,
    )


def _kick_directory(limit: int, refresh_seconds: float) -> list[dict]:
    cache_key = f"kick:{max(1, min(1000, int(limit)))}"
    now = time.time()
    cached = _DIRECTORY_CACHE.get(cache_key) or {}
    if cached and now - float(cached.get("ts") or 0) < refresh_seconds:
        return list(cached.get("items") or [])

    token = _kick_token()
    params = urllib.parse.urlencode({"limit": max(1, min(1000, int(limit)))})
    payload = _request_json(
        f"https://api.kick.com/public/v2/livestreams?{params}",
        headers={"Authorization": f"Bearer {token}"},
    )
    out = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        user = item.get("broadcaster_user") or {}
        channel_obj = item.get("channel") or {}
        category = item.get("category") or {}
        channel = str(user.get("username") or channel_obj.get("slug") or "unknown").strip()
        slug = str(channel_obj.get("slug") or "").strip()
        title = str(item.get("title") or "").strip()
        category_name = str(category.get("name") or "").strip()
        tags = [str(x) for x in (item.get("tags") or []) if str(x).strip()]
        out.append(
            {
                "platform": "kick",
                "channel": channel,
                "channel_key": slug or channel,
                "title": title,
                "published": str(item.get("started_at") or "live"),
                "live": True,
                "text": " ".join([title, category_name, *tags]).strip(),
                "url": f"https://kick.com/{slug}" if slug else "https://kick.com/",
            }
        )
    _DIRECTORY_CACHE[cache_key] = {"ts": now, "items": out}
    return out


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

    if buy_hits > sell_hits and buy_hits >= 1:
        return "buy"
    if sell_hits > buy_hits and sell_hits >= 1:
        return "sell"
    return "neutral"


def _norm_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _market_terms(market: Market) -> tuple[list[str], list[str]]:
    raw = [market.search_term or "", market.name, market.key, market.epic, *market.live_aliases]
    phrases: list[str] = []
    strong: list[str] = []
    seen = set()
    for value in raw:
        term = str(value or "").strip().lower()
        if not term or term in seen:
            continue
        seen.add(term)
        phrases.append(term)
        compact = _compact(term)
        if len(compact) >= 5 and (
            any(ch.isdigit() for ch in compact)
            or compact.endswith("usd")
            or compact in {"xauusd", "xagusd", "eurjpy", "gbpjpy", "usdchf", "usdjpy", "audusd", "eurusd", "gbpusd"}
        ):
            strong.append(compact)
    return phrases, strong


def _matches_market(item: dict, market: Market) -> bool:
    text = str(item.get("text") or item.get("title") or "").lower()
    if not text:
        return False
    compact = _compact(text)
    phrases, strong = _market_terms(market)
    if any(term and term in compact for term in strong):
        return True
    phrase_hit = any(term in text or _compact(term) in compact for term in phrases if term)
    if not phrase_hit:
        return False
    return any(word in text for word in TRADING_CONTEXT)


def _platform_list(cfg: dict) -> list[str]:
    raw = (cfg.get("streamers") or {}).get("platforms") or ["youtube", "twitch", "kick"]
    out = []
    for value in raw:
        name = str(value or "").strip().lower()
        if name in {"youtube", "twitch", "kick"} and name not in out:
            out.append(name)
    return out


def _unique_creator_count(items: list[dict]) -> int:
    seen = set()
    for item in items:
        platform = str(item.get("platform") or "unknown").strip().lower()
        channel = str(item.get("channel") or "unknown").strip()
        identity = _norm_identity(item.get("creator_id") or channel)
        seen.add(identity or f"{platform}:{channel.lower()}")
    return len(seen)


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
        platform = str(item.get("platform") or "unknown").strip().lower()
        if allowlist and channel.lower() not in allowlist and f"{platform}:{channel.lower()}" not in allowlist:
            continue
        side = classify_text(item.get("text") or item.get("title") or "")
        if side not in ("buy", "sell"):
            continue

        # Deduplicate the same creator name across YouTube/Twitch/Kick so a
        # multistream does not receive multiple votes just for being mirrored.
        identity = _norm_identity(item.get("creator_id") or channel)
        key = identity or f"{platform}:{channel.lower()}"
        if key in unique:
            continue
        unique[key] = {**item, "side": side, "age_hours": age_hours, "platform": platform}

    votes = list(unique.values())
    buys = sum(1 for x in votes if x["side"] == "buy")
    sells = sum(1 for x in votes if x["side"] == "sell")
    total = buys + sells
    min_sources = int(scfg.get("min_sources", 3) or 3)
    majority = float(scfg.get("majority_threshold", 0.70) or 0.70)
    platform_counts: dict[str, int] = {}
    for vote in votes:
        p = str(vote.get("platform") or "unknown")
        platform_counts[p] = platform_counts.get(p, 0) + 1

    if total < min_sources:
        return {
            "side": "neutral",
            "confidence": 0.0,
            "votes": total,
            "buy_votes": buys,
            "sell_votes": sells,
            "reason": f"creator consensus unavailable ({total}/{min_sources} directional creators)",
            "sources": votes[:12],
            "platform_counts": platform_counts,
        }

    side = "buy" if buys > sells else "sell" if sells > buys else "neutral"
    winner = max(buys, sells)
    confidence = winner / total if total else 0.0
    if side == "neutral" or confidence < majority:
        side = "neutral"
        reason = f"creator split buy={buys} sell={sells} ({confidence:.0%}<{majority:.0%})"
    else:
        reason = f"creator majority {side} {winner}/{total} ({confidence:.0%})"

    return {
        "side": side,
        "confidence": round(confidence, 3),
        "votes": total,
        "buy_votes": buys,
        "sell_votes": sells,
        "reason": reason,
        "sources": votes[:12],
        "platform_counts": platform_counts,
    }


def streamer_signal(cfg: dict, market: Market, *, force_refresh: bool = False) -> dict:
    """Cross-platform public creator consensus. This module never sends orders itself."""
    scfg = cfg.get("streamers") or {}
    if not scfg.get("enabled", False):
        return {
            "side": "neutral",
            "confidence": 0.0,
            "votes": 0,
            "buy_votes": 0,
            "sell_votes": 0,
            "reason": "creator consensus off",
            "sources": [],
            "platform_counts": {},
            "platform_status": {},
            "matched_creators": 0,
            "lookup_succeeded": False,
            "fallback_without_streamers": False,
            "errors": [],
        }

    refresh = max(60.0, float(scfg.get("refresh_seconds", 60) or 60))
    now = time.time()
    cached = _CACHE.get(market.key)
    if not force_refresh and cached and now - float(cached.get("ts", 0.0)) < refresh:
        return cached["data"]

    base = market.search_term or market.name
    query = str(
        (scfg.get("queries") or {}).get(market.key)
        or f'{base} CFD trading live market analysis bullish bearish long short buy sell'
    )
    max_results = max(5, min(40, int(scfg.get("max_results", 20) or 20)))
    platforms = _platform_list(cfg)
    items: list[dict] = []
    errors: list[str] = []
    status: dict[str, dict] = {}

    if "youtube" in platforms:
        try:
            found = _youtube_search(query, limit=max_results)
            matched = [x for x in found if _matches_market(x, market)]
            items.extend(matched)
            status["youtube"] = {"ok": True, "found": len(matched)}
        except Exception as exc:
            msg = f"youtube: {str(exc)[:140]}"
            errors.append(msg)
            status["youtube"] = {"ok": False, "error": str(exc)[:140]}

    if "twitch" in platforms:
        twitch_cfg = scfg.get("twitch") or {}
        twitch_limit = max(5, min(100, int(twitch_cfg.get("max_results", 25) or 25)))
        query_terms = [base, *(twitch_cfg.get("extra_queries") or ["Stocks", "Trading"])]
        seen_urls = set()
        twitch_items = []
        try:
            for term in query_terms:
                if not str(term or "").strip():
                    continue
                for item in _twitch_search(str(term), limit=twitch_limit):
                    url = str(item.get("url") or "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    if _matches_market(item, market):
                        twitch_items.append(item)
            items.extend(twitch_items)
            status["twitch"] = {"ok": True, "found": len(twitch_items)}
        except Exception as exc:
            msg = f"twitch: {str(exc)[:140]}"
            errors.append(msg)
            status["twitch"] = {"ok": False, "error": str(exc)[:140]}

    if "kick" in platforms:
        kick_cfg = scfg.get("kick") or {}
        kick_limit = max(25, min(1000, int(kick_cfg.get("directory_limit", 300) or 300)))
        try:
            directory = _kick_directory(kick_limit, refresh)
            kick_items = [x for x in directory if _matches_market(x, market)]
            items.extend(kick_items)
            status["kick"] = {"ok": True, "found": len(kick_items)}
        except Exception as exc:
            msg = f"kick: {str(exc)[:140]}"
            errors.append(msg)
            status["kick"] = {"ok": False, "error": str(exc)[:140]}

    result = consensus_from_items(items, cfg)
    matched_creators = _unique_creator_count(items)
    lookup_succeeded = any(bool(row.get("ok")) for row in status.values() if isinstance(row, dict))
    fallback_enabled = bool(scfg.get("fallback_if_no_matching_creators", True))
    fallback_without_streamers = bool(
        fallback_enabled
        and lookup_succeeded
        and matched_creators == 0
    )
    result["matched_creators"] = matched_creators
    result["lookup_succeeded"] = lookup_succeeded
    result["fallback_without_streamers"] = fallback_without_streamers
    if fallback_without_streamers:
        result["reason"] = (
            "no matching creators found on available platforms — "
            "fallback without creator gate; search will be retried"
        )
    elif not lookup_succeeded:
        result["reason"] = "creator lookup unavailable on all configured platforms"
    result["query"] = query
    result["errors"] = errors
    result["platform_status"] = status
    result["platforms"] = platforms
    _CACHE[market.key] = {"ts": now, "data": result}
    return result
