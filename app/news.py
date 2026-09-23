from __future__ import annotations

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

CAPITAL_NEWS_URL = "https://capital.com/en-int/news"

HOT = (
    "fomc",
    "nfp",
    "nonfarm",
    "non-farm",
    "cpi",
    "pce",
    "ecb",
    "fed chair",
    "powell",
    "lagarde",
    "rate decision",
    "interest rate",
    "emergency meeting",
    "bank failure",
    "flash crash",
    "halted",
    "opec",
    "invasion",
    "missile",
    "sanctions",
    "payrolls",
    "inflation surprise",
    "jobs report",
)

QUERIES = {
    "desk": 'FOMC OR NFP OR CPI OR ECB OR "interest rate" OR gold OR DAX OR Nasdaq',
    "germany40": "DAX OR ECB OR Germany inflation OR Bundesbank OR German economy",
    "ustech100": "Nasdaq OR FOMC OR CPI OR Nvidia OR semiconductors OR tech stocks",
    "wallstreet30": "Dow Jones OR FOMC OR CPI OR US jobs OR industrial stocks",
    "gold": "gold price OR XAU OR Fed OR dollar OR Treasury yields OR inflation",
}

COMMON_INDEX_RULES = (
    ("rate cut bets fade", -1.0),
    ("rate cuts delayed", -1.0),
    ("fewer rate cuts", -0.9),
    ("rules out rate cut", -1.0),
    ("rate hike bets fade", 0.8),
    ("rate cut", 0.7),
    ("cuts rates", 0.8),
    ("lower rates", 0.6),
    ("dovish", 0.7),
    ("easing", 0.45),
    ("rate hike", -0.8),
    ("raises rates", -0.8),
    ("higher rates", -0.6),
    ("hawkish", -0.7),
    ("tightening", -0.45),
    ("recession", -0.8),
    ("growth downgrade", -0.6),
    ("bank failure", -1.0),
    ("flash crash", -1.2),
    ("missile", -0.65),
    ("invasion", -0.8),
    ("escalation", -0.6),
)

MARKET_RULES = {
    "germany40": COMMON_INDEX_RULES + (
        ("dax rises", 0.8), ("dax gains", 0.8), ("dax rallies", 0.9),
        ("dax hits record", 0.8), ("dax falls", -0.8), ("dax slides", -0.8),
        ("dax tumbles", -1.0), ("german recession", -1.0),
        ("manufacturing contraction", -0.7), ("factory orders rise", 0.55),
        ("business sentiment improves", 0.55), ("ecb cuts", 0.8), ("ecb hike", -0.8),
    ),
    "ustech100": COMMON_INDEX_RULES + (
        ("nasdaq rises", 0.8), ("nasdaq gains", 0.8), ("nasdaq rallies", 0.9),
        ("nasdaq falls", -0.8), ("nasdaq slides", -0.8), ("nasdaq tumbles", -1.0),
        ("tech stocks rise", 0.65), ("tech stocks fall", -0.65),
        ("chip stocks rise", 0.65), ("chip stocks fall", -0.65),
        ("semiconductor rally", 0.7), ("semiconductor selloff", -0.8),
        ("nvidia beats", 0.7), ("nvidia misses", -0.7),
        ("treasury yields rise", -0.65), ("treasury yields fall", 0.65),
    ),
    "wallstreet30": COMMON_INDEX_RULES + (
        ("dow rises", 0.8), ("dow gains", 0.8), ("dow rallies", 0.9),
        ("dow falls", -0.8), ("dow slides", -0.8), ("dow tumbles", -1.0),
        ("jobs beat", 0.45), ("jobs miss", -0.55),
        ("industrial stocks rise", 0.55), ("industrial stocks fall", -0.55),
    ),
    "gold": (
        ("rate cut bets fade", -1.0), ("rate cuts delayed", -1.0),
        ("fewer rate cuts", -0.9), ("rules out rate cut", -1.0),
        ("rate hike bets fade", 0.8), ("rate cut", 0.8), ("cuts rates", 0.9),
        ("dovish", 0.8), ("rate hike", -0.9), ("raises rates", -0.9),
        ("hawkish", -0.8), ("dollar rises", -0.8), ("dollar strengthens", -0.8),
        ("strong dollar", -0.75), ("dollar falls", 0.8), ("dollar weakens", 0.8),
        ("weak dollar", 0.75), ("treasury yields rise", -0.8), ("yields rise", -0.65),
        ("treasury yields fall", 0.8), ("yields fall", 0.65), ("gold rises", 0.8),
        ("gold gains", 0.8), ("gold rallies", 0.9), ("record high", 0.65),
        ("gold falls", -0.8), ("gold slides", -0.8), ("gold tumbles", -1.0),
        ("safe haven", 0.55), ("bank failure", 0.8), ("missile", 0.55),
        ("invasion", 0.7), ("escalation", 0.55),
    ),
}


def _rss(query: str, timeout: int = 12) -> list[dict]:
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    req = urllib.request.Request(url, headers={"User-Agent": "cfd-bot/1.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "link": link, "published": pub})
    return items[:12]


class _CapitalNewsParser(HTMLParser):
    """Extract article links from Capital.com's public latest-news page."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a" or self._href is not None:
            return
        values = dict(attrs)
        href = str(values.get("href") or "")
        if "/en-int/analysis/" not in href and "/en-int/news/" not in href:
            return
        self._href = href
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        title = " ".join(" ".join(self._text).split())
        href = self._href
        self._href = None
        self._text = []
        if len(title) >= 24:
            self.items.append({"title": title, "link": href})


def _capital_news(news_cfg: dict | None = None, timeout: int = 8) -> list[dict]:
    cfg = news_cfg or {}
    url = str(cfg.get("url") or CAPITAL_NEWS_URL)
    limit = max(1, min(40, int(cfg.get("max_items", 20) or 20)))
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "cfd-bot/1.1",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    parser = _CapitalNewsParser()
    parser.feed(html)
    out = []
    seen = set()
    for item in parser.items:
        title = item["title"]
        if title in seen:
            continue
        seen.add(title)
        link = urllib.parse.urljoin(url, item["link"])
        out.append({
            "title": title,
            "link": link,
            # The listing page does not expose a reliable publication
            # timestamp for every card. Leave it unverified so this source
            # can be displayed but cannot masquerade as fresh market-moving
            # news in the directional gate.
            "published": "",
            "freshness_verified": False,
            "query": "capital.com",
            "source": "capital.com",
            "hot": _hot(title),
            "hot_flag": bool(_hot(title)),
        })
        if len(out) >= limit:
            break
    return out


def _hot(title: str) -> list[str]:
    low = title.lower()
    return [k for k in HOT if k in low]


def _published_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _headline_age_seconds(item: dict, now: datetime | None = None) -> float | None:
    published = _published_utc(str(item.get("published") or ""))
    if published is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - published).total_seconds())


def _headline_score(title: str, market_key: str) -> tuple[float, list[str]]:
    """Score non-overlapping phrases, preferring the most specific match.

    Example: "rate cuts delayed" should not also receive the generic
    bullish "rate cut" score from the same words.
    """
    low = title.lower()
    score = 0.0
    hits: list[str] = []
    spans: list[tuple[int, int]] = []
    rules = sorted(MARKET_RULES.get(market_key, ()), key=lambda item: len(item[0]), reverse=True)
    for phrase, weight in rules:
        start = low.find(phrase)
        if start < 0:
            continue
        end = start + len(phrase)
        if any(start < used_end and end > used_start for used_start, used_end in spans):
            continue
        spans.append((start, end))
        score += float(weight)
        hits.append(phrase)
    return max(-2.0, min(2.0, score)), hits


def fetch_desk_news(markets: list[str] | None = None, news_cfg: dict | None = None) -> dict:
    keys = ["desk"] + list(markets or [])
    seen = set()
    headlines = []
    errors = []
    capital_cfg = ((news_cfg or {}).get("capital") or {})
    if capital_cfg.get("enabled", True):
        try:
            for it in _capital_news_cached(capital_cfg):
                title = it["title"]
                if title in seen:
                    continue
                seen.add(title)
                headlines.append(it)
        except Exception as exc:
            errors.append(f"capital.com: {exc}")
    for key in keys:
        q = QUERIES.get(key) or key
        try:
            for it in _rss(q):
                title = it["title"]
                if title in seen:
                    continue
                seen.add(title)
                hits = _hot(title)
                headlines.append({**it, "query": key, "source": "news.google.com", "hot": hits, "hot_flag": bool(hits)})
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    hot = [h for h in headlines if h["hot_flag"]]
    return {
        "when": datetime.now(timezone.utc).isoformat(),
        "headlines": headlines[:40],
        "hot": hot,
        "blocked": False,
        "errors": errors,
    }


_CACHE: dict[str, dict] = {}
_CAPITAL_CACHE: dict[str, dict] = {}


def _capital_news_cached(news_cfg: dict) -> list[dict]:
    key = str(news_cfg.get("url") or CAPITAL_NEWS_URL)
    refresh = max(30.0, float(news_cfg.get("refresh_seconds", 60) or 60))
    now = time.time()
    cached = _CAPITAL_CACHE.get(key)
    if cached and now - float(cached.get("ts", 0.0)) < refresh:
        return cached.get("data") or []
    data = _capital_news(news_cfg)
    _CAPITAL_CACHE[key] = {"ts": now, "data": data}
    return data


def _get_market_news(cfg: dict, market_key: str) -> dict:
    news_cfg = cfg.get("news") or {}
    refresh = float(news_cfg.get("refresh_seconds", 60))
    now = time.time()
    cached = _CACHE.get(market_key)
    if cached is None or now - float(cached.get("ts", 0.0)) > refresh:
        payload = fetch_desk_news([market_key], news_cfg)
        cached = {"ts": now, "data": payload}
        _CACHE[market_key] = cached
    return cached.get("data") or {}


def news_signal(cfg: dict, market_key: str) -> dict:
    """Directional news bias for one market. Never places an order by itself."""
    news_cfg = cfg.get("news") or {}
    if news_cfg.get("enabled", True) is False:
        return {"side": "neutral", "confidence": 0.0, "score": 0.0,
                "reason": "news off", "headlines": [], "errors": []}

    payload = _get_market_news(cfg, market_key)
    max_age = float(news_cfg.get("max_headline_age_seconds", 10800))
    min_abs = float(news_cfg.get("min_abs_score", 0.35))
    now_dt = datetime.now(timezone.utc)
    scored = []

    for item in payload.get("headlines") or []:
        age = _headline_age_seconds(item, now_dt)
        if age is None or age > max_age:
            continue
        raw, hits = _headline_score(str(item.get("title") or ""), market_key)
        if abs(raw) < 1e-9:
            continue
        freshness = max(0.15, 1.0 - age / max(max_age, 1.0))
        weighted = raw * freshness
        scored.append({
            "title": item.get("title"),
            "published": item.get("published"),
            "age_minutes": round(age / 60.0, 1),
            "score": round(weighted, 3),
            "raw_score": round(raw, 3),
            "hits": hits,
        })

    pos = sum(max(float(x["score"]), 0.0) for x in scored)
    neg = sum(max(-float(x["score"]), 0.0) for x in scored)
    total = pos + neg
    net = pos - neg

    if total <= 1e-9 or abs(net) < min_abs:
        reason = "news unavailable/neutral" if payload.get("errors") and not scored else "news neutral"
        return {
            "side": "neutral", "confidence": 0.0, "score": round(net, 3),
            "reason": reason,
            "headlines": sorted(scored, key=lambda x: abs(float(x["score"])), reverse=True)[:3],
            "errors": payload.get("errors") or [],
        }

    side = "buy" if net > 0 else "sell"
    agreement = min(1.0, abs(net) / total)
    evidence = min(1.0, total / 1.5)
    confidence = 0.5 + 0.5 * agreement * evidence
    top = sorted(scored, key=lambda x: abs(float(x["score"])), reverse=True)[:3]
    reason = f"news {side} confidence={confidence:.2f} score={net:+.2f}"
    if top:
        reason += f" | {top[0]['title'][:90]}"
    return {
        "side": side, "confidence": round(confidence, 3), "score": round(net, 3),
        "reason": reason, "headlines": top, "errors": payload.get("errors") or [],
    }


def news_gate(cfg: dict, market_key: str | None = None) -> tuple[bool, str, dict]:
    """Compatibility wrapper: news is no longer a blanket trading halt."""
    if not market_key:
        return True, "news market not specified", {}
    signal = news_signal(cfg, market_key)
    return True, signal["reason"], signal
