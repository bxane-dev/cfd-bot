from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
MEM_PATH = LOG_DIR / "memory.jsonl"
DB_PATH = LOG_DIR / "memory.db"
SUMMARY_PATH = LOG_DIR / "memory_latest.json"
MAX_NORMAL_ROWS = 50000
_WORD_RE = re.compile(r"[a-z0-9_%-]+", re.I)
_DB_READY_FOR = ""
_DB_INIT_LOCK = threading.Lock()
_MAINT_LOCK = threading.Lock()
_LAST_PRUNE_AT = 0.0
_LAST_SUMMARY_AT = 0.0
PRUNE_INTERVAL_SECONDS = 60.0
SUMMARY_INTERVAL_SECONDS = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    LOG_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=8)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _init_db() -> None:
    global _DB_READY_FOR
    db_key = str(DB_PATH.resolve())
    if _DB_READY_FOR == db_key:
        return
    with _DB_INIT_LOCK:
        if _DB_READY_FOR == db_key:
            return
        with _connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    when_utc TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    market TEXT,
                    what TEXT NOT NULL,
                    how TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    importance REAL NOT NULL DEFAULT 0.5,
                    extra_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_mem_market ON memories(market);
                CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);
                CREATE INDEX IF NOT EXISTS idx_mem_when ON memories(when_utc);
                CREATE TABLE IF NOT EXISTS memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            migrated = con.execute(
                "SELECT value FROM memory_meta WHERE key='jsonl_migrated'"
            ).fetchone()
            if migrated is None:
                _migrate_jsonl(con)
                con.execute(
                    "INSERT OR REPLACE INTO memory_meta(key,value) VALUES('jsonl_migrated',?)",
                    (_now(),),
                )
        _DB_READY_FOR = db_key

def _migrate_jsonl(con: sqlite3.Connection) -> None:
    if not MEM_PATH.exists():
        return
    for line in MEM_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        con.execute(
            """
            INSERT INTO memories(when_utc,kind,market,what,how,tags,importance,extra_json)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                str(row.get("when") or _now()),
                str(row.get("kind") or "other"),
                row.get("market"),
                str(row.get("what") or ""),
                str(row.get("how") or ""),
                str(row.get("tags") or ""),
                float(row.get("importance") or 0.5),
                json.dumps(row.get("extra") or {}, default=str),
            ),
        )


def _row_to_dict(row: sqlite3.Row) -> dict:
    try:
        extra = json.loads(row["extra_json"] or "{}")
    except Exception:
        extra = {}
    return {
        "id": int(row["id"]),
        "when": row["when_utc"],
        "kind": row["kind"],
        "what": row["what"],
        "how": row["how"],
        "market": row["market"],
        "tags": row["tags"],
        "importance": float(row["importance"]),
        "extra": extra,
    }


def _prune() -> None:
    with _connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM memories WHERE importance < 0.95"
        ).fetchone()[0]
        if count <= MAX_NORMAL_ROWS:
            return
        remove = count - MAX_NORMAL_ROWS
        con.execute(
            """
            DELETE FROM memories
            WHERE id IN (
                SELECT id FROM memories
                WHERE importance < 0.95
                ORDER BY id ASC
                LIMIT ?
            )
            """,
            (remove,),
        )


def _maybe_maintenance(force: bool = False) -> None:
    """Throttle expensive pruning/summary rebuilds; event inserts stay immediate."""
    global _LAST_PRUNE_AT, _LAST_SUMMARY_AT
    now = time.monotonic()
    if (
        not force
        and now - _LAST_PRUNE_AT < PRUNE_INTERVAL_SECONDS
        and now - _LAST_SUMMARY_AT < SUMMARY_INTERVAL_SECONDS
    ):
        return
    with _MAINT_LOCK:
        now = time.monotonic()
        if force or now - _LAST_PRUNE_AT >= PRUNE_INTERVAL_SECONDS:
            _prune()
            _LAST_PRUNE_AT = now
        if force or now - _LAST_SUMMARY_AT >= SUMMARY_INTERVAL_SECONDS:
            _refresh_summary()
            _LAST_SUMMARY_AT = now


def remember(
    kind: str,
    what: str,
    how: str = "",
    market: str | None = None,
    extra: dict | None = None,
    *,
    tags: str | list[str] | None = None,
    importance: float = 0.5,
) -> dict:
    _init_db()
    when = _now()
    if isinstance(tags, (list, tuple, set)):
        tags_text = " ".join(str(x) for x in tags if x)
    else:
        tags_text = str(tags or "")
    importance = max(0.0, min(1.0, float(importance)))
    extra = extra or {}

    with _connect() as con:
        cur = con.execute(
            """
            INSERT INTO memories(when_utc,kind,market,what,how,tags,importance,extra_json)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                when,
                str(kind),
                market,
                str(what),
                str(how),
                tags_text,
                importance,
                json.dumps(extra, default=str),
            ),
        )
        mem_id = int(cur.lastrowid)

    row = {
        "id": mem_id,
        "when": when,
        "kind": kind,
        "what": what,
        "how": how,
        "market": market,
        "tags": tags_text,
        "importance": importance,
        "extra": extra,
    }
    with MEM_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")

    _maybe_maintenance()
    return row


def pin(
    what: str,
    *,
    market: str | None = None,
    tags: str | list[str] | None = None,
    extra: dict | None = None,
) -> dict:
    return remember(
        "fact",
        what,
        how="manual",
        market=market,
        extra=extra,
        tags=tags,
        importance=1.0,
    )


def read_all(limit: int = 500) -> list[dict]:
    _init_db()
    limit = max(1, min(int(limit), 10000))
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM memories ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in reversed(rows)]


def last(kind: str | None = None, n: int = 20) -> list[dict]:
    _init_db()
    n = max(1, min(int(n), 1000))
    with _connect() as con:
        if kind:
            rows = con.execute(
                "SELECT * FROM memories WHERE kind=? ORDER BY id DESC LIMIT ?",
                (kind, n),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def last_tune() -> dict | None:
    rows = last("tune", 1)
    return rows[0] if rows else None


def _tokens(value: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(value or "") if len(t) > 1}


def _age_days(when_text: str) -> float:
    try:
        dt = datetime.fromisoformat(when_text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
            / 86400.0,
        )
    except Exception:
        return 3650.0


def search_memory(
    query: str,
    *,
    market: str | None = None,
    kind: str | None = None,
    limit: int = 8,
    scan_limit: int = 4000,
) -> list[dict]:
    _init_db()
    limit = max(1, min(int(limit), 50))
    scan_limit = max(limit, min(int(scan_limit), 20000))

    sql = "SELECT * FROM memories"
    clauses = []
    params: list[Any] = []
    if market:
        clauses.append("(market=? OR market IS NULL)")
        params.append(market)
    if kind:
        clauses.append("kind=?")
        params.append(kind)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(scan_limit)

    with _connect() as con:
        rows = con.execute(sql, params).fetchall()

    q = _tokens(query)
    ranked = []
    for raw in rows:
        row = _row_to_dict(raw)
        hay = " ".join(
            [
                str(row.get("what") or ""),
                str(row.get("how") or ""),
                str(row.get("tags") or ""),
                json.dumps(row.get("extra") or {}, default=str),
            ]
        )
        t = _tokens(hay)
        overlap = len(q & t)
        union = max(1, len(q | t))
        lexical = overlap / union if q else 0.0
        structured = 0.0
        if market and row.get("market") == market:
            structured += 0.35
        if kind and row.get("kind") == kind:
            structured += 0.15
        importance = 0.18 * float(row.get("importance") or 0.0)
        recency = 0.22 * math.exp(-_age_days(str(row.get("when") or "")) / 30.0)
        score = lexical * 2.5 + structured + importance + recency

        if q and overlap == 0 and not (market and row.get("market") == market):
            continue
        row["memory_score"] = round(score, 4)
        ranked.append(row)

    ranked.sort(key=lambda r: (r["memory_score"], r["id"]), reverse=True)
    return ranked[:limit]


def remember_case(
    *,
    event: str,
    market: str,
    strategy: str,
    side: str | None,
    technical_reason: str = "",
    news: dict | None = None,
    predictor: dict | None = None,
    price: float | None = None,
    spread: float | None = None,
    parent_id: int | None = None,
    extra: dict | None = None,
) -> dict:
    news = news or {}
    predictor = predictor or {}
    side_text = str(side or "none")
    news_side = str(news.get("side") or "neutral")
    pred_side = str(predictor.get("side") or "neutral")
    tags = [
        "case",
        event,
        market,
        strategy,
        side_text,
        f"news_{news_side}",
        f"pred_{pred_side}",
    ]
    merged = {
        "event": event,
        "strategy": strategy,
        "side": side_text,
        "technical_reason": technical_reason,
        "news_side": news_side,
        "news_confidence": news.get("confidence"),
        "news_score": news.get("score"),
        "predictor_side": pred_side,
        "predictor_p_up": predictor.get("p_up"),
        "predictor_edge": predictor.get("edge"),
        "price": price,
        "spread": spread,
        "parent_id": parent_id,
        **(extra or {}),
    }
    what = (
        f"{event} {market} {strategy} side={side_text} "
        f"news={news_side} pred={pred_side}"
    )
    return remember(
        "case",
        what,
        how=technical_reason,
        market=market,
        extra=merged,
        tags=tags,
        importance=0.72 if event == "order" else 0.55,
    )


def recall_context(
    *,
    market: str,
    strategy: str,
    side: str | None = None,
    news_side: str | None = None,
    predictor_side: str | None = None,
    limit: int = 5,
) -> dict:
    parts = [market, strategy]
    if side:
        parts.append(side)
    if news_side:
        parts.append(f"news_{news_side}")
    if predictor_side:
        parts.append(f"pred_{predictor_side}")

    matches = search_memory(
        " ".join(parts),
        market=market,
        kind="case",
        limit=limit,
    )
    events = Counter()
    sides = Counter()
    for row in matches:
        extra = row.get("extra") or {}
        events[str(extra.get("event") or "case")] += 1
        sides[str(extra.get("side") or "none")] += 1

    bits = []
    if events:
        bits.append(", ".join(f"{k}={v}" for k, v in events.most_common()))
    if sides:
        bits.append("sides " + ", ".join(f"{k}={v}" for k, v in sides.most_common()))

    summary = (
        f"{len(matches)} similar memories: " + "; ".join(bits)
        if matches
        else "no similar memories yet"
    )
    return {"summary": summary, "matches": matches}


def stats() -> dict:
    _init_db()
    with _connect() as con:
        total = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        kinds = dict(
            con.execute(
                "SELECT kind, COUNT(*) FROM memories GROUP BY kind ORDER BY COUNT(*) DESC"
            ).fetchall()
        )
        markets = dict(
            con.execute(
                """
                SELECT COALESCE(market,'global'), COUNT(*)
                FROM memories
                GROUP BY COALESCE(market,'global')
                ORDER BY COUNT(*) DESC
                """
            ).fetchall()
        )
        pinned = con.execute(
            "SELECT COUNT(*) FROM memories WHERE importance >= 0.95"
        ).fetchone()[0]
    return {
        "total": int(total),
        "pinned": int(pinned),
        "kinds": kinds,
        "markets": markets,
        "db": str(DB_PATH),
    }


def _refresh_summary() -> None:
    rows = read_all(400)
    by_kind: dict[str, int] = {}
    for r in rows:
        k = r.get("kind") or "other"
        by_kind[k] = by_kind.get(k, 0) + 1
    latest = {}
    for r in rows:
        latest[r.get("kind") or "other"] = r
    SUMMARY_PATH.write_text(
        json.dumps(
            {
                "updated": _now(),
                "stats": stats(),
                "counts_recent": by_kind,
                "latest": latest,
                "tail": rows[-15:],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def _cli() -> None:
    parser = argparse.ArgumentParser(description="CFD bot persistent memory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="pin a manual memory")
    p_add.add_argument("text")
    p_add.add_argument("--market")
    p_add.add_argument("--tags", default="")

    p_search = sub.add_parser("search", help="search remembered context")
    p_search.add_argument("query")
    p_search.add_argument("--market")
    p_search.add_argument("--kind")
    p_search.add_argument("--limit", type=int, default=8)

    p_recent = sub.add_parser("recent", help="show recent memories")
    p_recent.add_argument("--kind")
    p_recent.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="show memory statistics")
    args = parser.parse_args()

    if args.cmd == "add":
        print(json.dumps(pin(args.text, market=args.market, tags=args.tags), indent=2))
    elif args.cmd == "search":
        print(
            json.dumps(
                search_memory(
                    args.query,
                    market=args.market,
                    kind=args.kind,
                    limit=args.limit,
                ),
                indent=2,
                default=str,
            )
        )
    elif args.cmd == "recent":
        print(json.dumps(last(args.kind, args.limit), indent=2, default=str))
    elif args.cmd == "stats":
        print(json.dumps(stats(), indent=2, default=str))


if __name__ == "__main__":
    _cli()
