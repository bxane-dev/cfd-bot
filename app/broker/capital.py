from __future__ import annotations

import json
import os
import time
import threading
from collections import deque
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import AccountState, Fill, Position, utcnow

DEMO_URL = "https://demo-api-capital.backend-capital.com"
LIVE_URL = "https://api-capital.backend-capital.com"
STREAM_URL = "wss://api-streaming-capital.backend-capital.com/connect"
SESSION_STAMP = Path(__file__).resolve().parents[2] / "logs" / "capital_session_post.txt"
SESSION_MIN_INTERVAL = 1.10
_SESSION_LOCK = threading.Lock()

# Capital.com market codes for this desk
EPICS = {
    "germany40": "DE40",
    "ustech100": "US100",
    "wallstreet30": "US30",
    "gold": "GOLD",
    "DE40": "DE40",
    "US100": "US100",
    "US30": "US30",
    "GOLD": "GOLD",
}


class CapitalBroker:
    """Capital.com demo or live CFD account via official REST API."""

    def __init__(self, demo: bool = True):
        self.demo = demo
        self.base = DEMO_URL if demo else LIVE_URL
        self.api_key = self._clean(os.getenv("CAPITAL_API_KEY"))
        self.identifier = self._clean(os.getenv("CAPITAL_EMAIL") or os.getenv("CAPITAL_IDENTIFIER"))
        self.password = self._clean(os.getenv("CAPITAL_API_PASSWORD"))
        # CAPITAL_ACCOUNT_ID is optional. Capital.com does NOT require it to
        # create a session; it is only used if the user explicitly wants to
        # switch to a particular financial account after authentication.
        self.requested_account_id = self._clean(os.getenv("CAPITAL_ACCOUNT_ID"))
        self.account_id = ""
        if not (self.api_key and self.identifier and self.password):
            raise RuntimeError(
                "Missing Capital.com login. Set CAPITAL_API_KEY, CAPITAL_EMAIL, CAPITAL_API_PASSWORD in .env"
            )
        self.cst = ""
        self.security = ""
        self._last_account_state = None
        self._last_account_at = 0.0
        self._request_times = deque()
        self._request_lock = threading.Lock()
        self._stream_lock = threading.Lock()
        self._stream_quotes: dict[str, dict] = {}
        self._stream_thread: threading.Thread | None = None
        self._stream_stop = threading.Event()
        self._stream_epics: tuple[str, ...] = ()
        self._stream_connected = False
        self._stream_last_error = ""
        self._stream_last_message_at = 0.0
        try:
            self._login()
            self._select_account()
        except Exception as exc:
            kind = "DEMO" if demo else "LIVE"
            raise RuntimeError(
                f"Capital.com {kind} login failed.\n"
                "CAPITAL_ACCOUNT_ID is optional and is not needed to log in.\n"
                "Check CAPITAL_EMAIL, CAPITAL_API_KEY, and CAPITAL_API_PASSWORD. "
                "The password must be the custom password for that API key when one was set.\n"
                "Also make sure the API key belongs to the same DEMO/LIVE environment you started.\n"
                f"Detail: {exc}"
            ) from exc
        kind = "DEMO" if self.base == DEMO_URL else "LIVE"
        shown = self.account_id or "(Capital.com active account)"
        print(f"Capital.com session OK ({kind}) account={shown}")

    @staticmethod
    def _clean(value: str | None) -> str:
        return (value or "").strip().strip('"').strip("'").strip()

    @staticmethod
    def _wait_for_session_slot() -> None:
        """Respect Capital.com's POST /session limit across local bot processes."""
        with _SESSION_LOCK:
            SESSION_STAMP.parent.mkdir(exist_ok=True)
            now = time.time()
            try:
                last = float(SESSION_STAMP.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                last = 0.0
            wait = SESSION_MIN_INTERVAL - (now - last)
            if wait > 0:
                time.sleep(wait)
            try:
                SESSION_STAMP.write_text(str(time.time()), encoding="utf-8")
            except Exception:
                pass

    def _login(self) -> None:
        url = self.base + "/api/v1/session"
        # Official POST /session requires identifier + API-key password.
        # accountId does not belong in this request.
        payload = {
            "identifier": self.identifier,
            "password": self.password,
            "encryptedPassword": False,
        }
        body = json.dumps(payload).encode()
        self._wait_for_session_slot()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CAP-API-KEY": self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                self.cst = resp.headers.get("CST") or ""
                self.security = resp.headers.get("X-SECURITY-TOKEN") or ""
                raw = resp.read().decode("utf-8", errors="ignore")
                data = json.loads(raw) if raw else {}
                self.account_id = str(
                    data.get("currentAccountId") or data.get("accountId") or ""
                )
                if not self.cst or not self.security:
                    raise RuntimeError(f"Capital.com session headers missing: {raw[:200]}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            raise RuntimeError(f"login {self.base} failed ({exc.code}): {detail}") from exc

    def _select_account(self) -> None:
        try:
            data = self._request("GET", "/api/v1/accounts")
        except Exception:
            return
        accounts = data.get("accounts") or []
        if not accounts:
            return
        chosen = None

        if self.requested_account_id:
            chosen = next(
                (a for a in accounts if str(a.get("accountId") or "") == self.requested_account_id),
                None,
            )
            if chosen is None:
                available = ", ".join(str(a.get("accountId") or "?") for a in accounts)
                raise RuntimeError(
                    "CAPITAL_ACCOUNT_ID was set but was not found. "
                    f"Available account IDs: {available}"
                )

        if chosen is None and self.account_id:
            chosen = next(
                (a for a in accounts if str(a.get("accountId") or "") == self.account_id),
                None,
            )
        if chosen is None:
            chosen = next((a for a in accounts if a.get("preferred")), accounts[0])

        aid = str(chosen.get("accountId") or "")
        if not aid:
            raise RuntimeError("Capital.com returned an account without an accountId")

        # The login response already selects an active account. Only call
        # PUT /session when we actually need to switch to a different one.
        if aid != self.account_id:
            self._request("PUT", "/api/v1/session", payload={"accountId": aid})
        self.account_id = aid
        print(f"Capital.com account {aid} ({chosen.get('accountName') or chosen.get('currency') or 'CFD'})")

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-CAP-API-KEY": self.api_key,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.security,
        }

    def _wait_for_request_slot(self, limit: int = 8, window: float = 1.0) -> None:
        """Smooth REST traffic when scanning many markets."""
        if not hasattr(self, "_request_times"):
            self._request_times = deque()
            self._request_lock = threading.Lock()
        with self._request_lock:
            while True:
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] >= window:
                    self._request_times.popleft()
                if len(self._request_times) < limit:
                    self._request_times.append(now)
                    return
                wait = window - (now - self._request_times[0]) + 0.02
                if wait > 0:
                    time.sleep(wait)

    def _request(self, method: str, path: str, payload: dict | None = None, query: dict | None = None):
        self._wait_for_request_slot()
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self._login()
                req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            detail = exc.read().decode("utf-8", errors="ignore")[:400]
            raise RuntimeError(f"Capital.com {method} {path} failed ({exc.code}): {detail}") from exc

    def _invalidate_account_cache(self) -> None:
        self._last_account_state = None
        self._last_account_at = 0.0

    def account_cached(self, max_age: float = 6.0) -> AccountState:
        cached = getattr(self, "_last_account_state", None)
        cached_at = float(getattr(self, "_last_account_at", 0.0) or 0.0)
        if cached is not None and time.monotonic() - cached_at <= max(0.0, float(max_age)):
            return cached
        return self.account()

    def account(self) -> AccountState:
        data = self._request("GET", "/api/v1/accounts")
        accounts = data.get("accounts") or []
        acc = next(
            (a for a in accounts if str(a.get("accountId") or "") == self.account_id),
            None,
        )
        if acc is None:
            acc = next((a for a in accounts if a.get("preferred")), accounts[0] if accounts else {})
        bal = acc.get("balance") or {}
        balance = float(bal.get("balance") or 0)
        pnl = float(bal.get("profitLoss") or bal.get("pnl") or 0)
        available = float(bal.get("available") or 0)
        # Equity is balance + floating P&L. `available` is free margin and
        # shrinks with open trades — do not use it as the risk base.
        equity = balance + pnl if balance else available
        if equity <= 0:
            equity = available or balance
        ccy = acc.get("currency") or "EUR"
        state = AccountState(equity, balance, ccy, self.positions())
        self._last_account_state = state
        self._last_account_at = time.monotonic()
        return state

    def positions(self) -> list[Position]:
        data = self._request("GET", "/api/v1/positions")
        out = []
        for row in data.get("positions") or []:
            pos = row.get("position") or row
            market = row.get("market") or {}
            deal = str(pos.get("dealId") or "")
            side = "buy" if str(pos.get("direction", "")).upper() == "BUY" else "sell"
            out.append(
                Position(
                    ticket=abs(hash(deal)) % 10_000_000 or 1,
                    symbol=str(market.get("epic") or pos.get("epic") or ""),
                    side=side,
                    lots=float(pos.get("size") or 0),
                    entry=float(pos.get("level") or 0),
                    sl=float(pos.get("stopLevel") or 0),
                    tp=float(pos.get("profitLevel") or 0),
                    opened_at=utcnow(),
                    deal_id=deal,
                    deal_reference=str(pos.get("dealReference") or ""),
                    contract_size=float(pos.get("contractSize") or market.get("lotSize") or 1),
                    upl=(float(pos.get("upl")) if pos.get("upl") is not None else None),
                    currency=str(pos.get("currency") or ""),
                    leverage=(float(pos.get("leverage")) if pos.get("leverage") is not None else None),
                )
            )
        return out

    def start_quote_stream(self, epics) -> bool:
        """Subscribe to Capital.com's WebSocket quotes for up to 40 epics."""
        try:
            import websocket  # noqa: F401
        except Exception as exc:
            self._stream_last_error = f"websocket unavailable: {exc}"
            return False

        cleaned = tuple(dict.fromkeys(str(x or "").strip() for x in epics if str(x or "").strip()))[:40]
        if not cleaned:
            return False
        if self._stream_thread is not None and self._stream_thread.is_alive():
            if cleaned == self._stream_epics:
                return True
            return False

        self._stream_epics = cleaned
        self._stream_stop.clear()
        self._stream_thread = threading.Thread(
            target=self._quote_stream_loop,
            name="capital-quote-stream",
            daemon=True,
        )
        self._stream_thread.start()
        return True

    def stop_quote_stream(self) -> None:
        self._stream_stop.set()

    def _quote_stream_loop(self) -> None:
        import websocket

        while not self._stream_stop.is_set():
            ws = None
            try:
                ws = websocket.create_connection(
                    STREAM_URL,
                    timeout=15,
                    enable_multithread=True,
                )
                ws.settimeout(2.0)
                subscribe = {
                    "destination": "marketData.subscribe",
                    "correlationId": "desk-subscribe",
                    "cst": self.cst,
                    "securityToken": self.security,
                    "payload": {"epics": list(self._stream_epics)},
                }
                ws.send(json.dumps(subscribe))
                self._stream_connected = True
                self._stream_last_error = ""
                last_ping = time.monotonic()

                while not self._stream_stop.is_set():
                    now = time.monotonic()
                    if now - last_ping >= 240:
                        ws.send(
                            json.dumps(
                                {
                                    "destination": "ping",
                                    "correlationId": "desk-ping",
                                    "cst": self.cst,
                                    "securityToken": self.security,
                                }
                            )
                        )
                        last_ping = now
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        continue
                    try:
                        message = json.loads(raw)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if str(message.get("destination") or "") != "quote":
                        continue
                    payload = message.get("payload") or {}
                    epic = str(payload.get("epic") or "").strip()
                    try:
                        bid = float(payload.get("bid") or 0)
                        ask = float(payload.get("ofr") or payload.get("offer") or 0)
                    except (TypeError, ValueError):
                        continue
                    if not epic or bid <= 0 or ask <= 0:
                        continue
                    received = time.monotonic()
                    with self._stream_lock:
                        self._stream_quotes[epic] = {
                            "bid": bid,
                            "ask": ask,
                            "timestamp": payload.get("timestamp"),
                            "received_at": received,
                        }
                    self._stream_last_message_at = received
            except Exception as exc:
                self._stream_connected = False
                self._stream_last_error = str(exc)[:180]
                if not self._stream_stop.wait(1.0):
                    continue
            finally:
                self._stream_connected = False
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def stream_quote(self, epic: str, max_age: float = 5.0):
        with self._stream_lock:
            row = dict(self._stream_quotes.get(str(epic or "")) or {})
        if not row:
            return None
        received = float(row.get("received_at") or 0.0)
        age = time.monotonic() - received
        if max_age > 0 and age > max_age:
            return None
        return float(row["bid"]), float(row["ask"]), max(0.0, age)

    def quote_stream_status(self) -> dict:
        age = None
        if self._stream_last_message_at:
            age = max(0.0, time.monotonic() - self._stream_last_message_at)
        with self._stream_lock:
            quote_count = len(self._stream_quotes)
        return {
            "connected": bool(self._stream_connected),
            "subscribed": len(self._stream_epics),
            "quotes": quote_count,
            "last_message_age": age,
            "error": self._stream_last_error,
        }

    def resolve_epic(self, search_term: str) -> str:
        """Resolve a configured market name/code to an account-available Capital.com epic."""
        term = (search_term or "").strip()
        if not term:
            raise RuntimeError("empty Capital.com market search term")
        data = self._request("GET", "/api/v1/markets", query={"searchTerm": term})
        rows = data.get("markets") or []
        if not rows:
            raise RuntimeError(f"Capital.com market not found for search term: {term}")

        needle = "".join(ch for ch in term.lower() if ch.isalnum())

        def norm(value) -> str:
            return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

        def score(row: dict) -> tuple[int, int]:
            epic = norm(row.get("epic"))
            name = norm(row.get("instrumentName") or row.get("marketName"))
            status = str(row.get("marketStatus") or "").upper()
            exact = 0
            if epic == needle:
                exact = 4
            elif name == needle:
                exact = 3
            elif needle and (needle in name or needle in epic):
                exact = 2
            tradeable = 1 if status in {"TRADEABLE", "OPEN"} else 0
            return exact, tradeable

        chosen = max(rows, key=score)
        epic = str(chosen.get("epic") or "").strip()
        if not epic:
            raise RuntimeError(f"Capital.com returned no epic for search term: {term}")
        print(f"Capital.com market resolved: {term} -> {epic}")
        return epic

    def market_details(self, epic: str) -> dict:
        """Return Capital.com's full instrument, dealing rules, and snapshot payload."""
        return self._request("GET", f"/api/v1/markets/{epic}")

    def quote(self, epic: str) -> tuple[float, float]:
        data = self.market_details(epic)
        snap = data.get("snapshot") or data
        bid = float(snap.get("bid") or 0)
        ask = float(snap.get("offer") or snap.get("ofr") or 0)
        return bid, ask

    def candles(self, epic: str, resolution: str = "MINUTE", count: int = 400, start=None, end=None):
        query = {"resolution": resolution, "max": str(min(int(count), 1000))}
        if start:
            query["from"] = start
        if end:
            query["to"] = end
        data = self._request("GET", f"/api/v1/prices/{epic}", query=query)
        return data.get("prices") or []

    def _confirm_deal(self, deal_reference: str, attempts: int = 5, delay: float = 0.35) -> tuple[dict, str]:
        """Poll Capital.com for the authoritative result of a submitted order."""
        last_error = ""
        for attempt in range(max(1, attempts)):
            try:
                confirm = self._request("GET", f"/api/v1/confirms/{deal_reference}")
                if confirm:
                    return confirm, ""
            except Exception as exc:
                last_error = str(exc)[:180]
            if attempt + 1 < attempts:
                time.sleep(max(0.10, delay))
        return {}, last_error

    def market_order(self, symbol: str, side: str, lots: float, sl: float, tp: float, comment: str) -> Fill:
        epic = EPICS.get(symbol, symbol)
        body = {
            "epic": epic,
            "direction": "BUY" if side == "buy" else "SELL",
            "size": float(lots),
            "stopLevel": float(sl),
            "profitLevel": float(tp),
        }
        try:
            data = self._request("POST", "/api/v1/positions", payload=body)
            self._invalidate_account_cache()
            ref = str(data.get("dealReference") or "")
            if not ref:
                return Fill(
                    0, epic, side, lots, 0, sl, tp, comment, False,
                    f"order submitted without dealReference: {str(data)[:120]}",
                )

            confirm, confirm_error = self._confirm_deal(ref)
            if not confirm:
                msg = f"UNCONFIRMED dealReference={ref}"
                if confirm_error:
                    msg += f" confirm_error={confirm_error}"
                return Fill(
                    abs(hash(ref)) % 10_000_000 or 1,
                    epic, side, lots, 0, sl, tp, comment, False, msg[:180],
                )

            status = str(confirm.get("dealStatus") or confirm.get("status") or "").upper()
            ok = status in {"ACCEPTED", "OPEN", "OPENED"}
            price = float(confirm.get("level") or 0)
            deal_id = str(confirm.get("dealId") or "")
            if not deal_id:
                affected = confirm.get("affectedDeals") or []
                if affected and isinstance(affected[0], dict):
                    deal_id = str(
                        affected[0].get("dealId")
                        or affected[0].get("dealReference")
                        or ""
                    )
            ticket = abs(hash(deal_id or ref)) % 10_000_000 or 1
            reason = str(confirm.get("reason") or confirm.get("message") or "")
            message = f"status={status or 'UNKNOWN'} ref={ref}"
            if reason:
                message += f" reason={reason}"
            return Fill(
                ticket, epic, side, lots, price, sl, tp, comment, ok, message[:180],
                deal_id=deal_id,
                deal_reference=ref,
            )
        except Exception as exc:
            return Fill(0, epic, side, lots, 0, sl, tp, comment, False, str(exc)[:180])

    def modify_position(self, deal_id: str, sl: float, tp: float, comment: str = "modify") -> Fill:
        deal_id = str(deal_id or "").strip()
        if not deal_id:
            return Fill(0, "", "", 0, 0, sl, tp, comment, False, "no deal id")
        body = {
            "stopLevel": float(sl),
            "profitLevel": float(tp),
        }
        try:
            data = self._request("PUT", f"/api/v1/positions/{deal_id}", payload=body)
            self._invalidate_account_cache()
            ref = str(data.get("dealReference") or "")
            if not ref:
                return Fill(
                    abs(hash(deal_id)) % 10_000_000 or 1,
                    "", "", 0, 0, sl, tp, comment, False,
                    f"position update returned no dealReference: {str(data)[:120]}",
                    deal_id=deal_id,
                )
            confirm, confirm_error = self._confirm_deal(ref)
            if not confirm:
                msg = f"UNCONFIRMED dealReference={ref}"
                if confirm_error:
                    msg += f" confirm_error={confirm_error}"
                return Fill(
                    abs(hash(deal_id)) % 10_000_000 or 1,
                    "", "", 0, 0, sl, tp, comment, False, msg[:180],
                    deal_id=deal_id,
                    deal_reference=ref,
                )
            status = str(confirm.get("dealStatus") or confirm.get("status") or "").upper()
            ok = status in {"ACCEPTED", "OPEN", "OPENED", "UPDATED", "AMENDED"}
            reason = str(confirm.get("reason") or confirm.get("message") or "")
            message = f"status={status or 'UNKNOWN'} ref={ref}"
            if reason:
                message += f" reason={reason}"
            return Fill(
                abs(hash(deal_id)) % 10_000_000 or 1,
                "", "", 0, 0, sl, tp, comment, ok, message[:180],
                deal_id=deal_id,
                deal_reference=ref,
            )
        except Exception as exc:
            return Fill(
                abs(hash(deal_id)) % 10_000_000 or 1,
                "", "", 0, 0, sl, tp, comment, False, str(exc)[:180],
                deal_id=deal_id,
            )

    def close(self, ticket: int, comment: str = "close") -> Fill:
        for p in self.positions():
            if p.ticket == ticket:
                deal = getattr(p, "deal_id", None)
                if not deal:
                    return Fill(ticket, p.symbol, p.side, p.lots, 0, 0, 0, comment, False, "no deal id")
                try:
                    self._request("DELETE", f"/api/v1/positions/{deal}")
                    self._invalidate_account_cache()
                    return Fill(ticket, p.symbol, p.side, p.lots, 0, p.sl, p.tp, comment, True, "closed")
                except Exception as exc:
                    return Fill(ticket, p.symbol, p.side, p.lots, 0, p.sl, p.tp, comment, False, str(exc)[:180])
        return Fill(ticket, "", "", 0, 0, 0, 0, comment, False, "not found")

    def mark(self, symbol: str, bid: float, ask: float) -> list[dict]:
        return []
