#!/usr/bin/env python3
"""Local web desk. Open http://127.0.0.1:8484 on this computer or your phone on the same Wi-Fi."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
HOST = os.getenv("CFD_WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = 8484
CONTROL_TOKEN = os.getenv("CFD_WEB_TOKEN", "").strip() or secrets.token_urlsafe(24)


def _json(handler: BaseHTTPRequestHandler, code: int, payload) -> None:
    raw = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _api_error(self, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {str(exc)[:300]}"
        sys.stderr.write(f"dashboard API error {self.command} {self.path}: {message}\n")
        try:
            _json(self, 500, {"error": message})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except Exception as exc:
            self._api_error(exc)

    def _do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._file(WEB / "index.html", "text/html; charset=utf-8")
            return
        from desk import get_desk

        desk = get_desk()
        if path == "/api/status":
            _json(self, 200, desk.snapshot())
            return
        if path == "/api/trades":
            _json(self, 200, desk.trades())
            return
        if path == "/api/memory":
            # MEMORY_V2_API
            from memory import last, search_memory, stats
            q = parse_qs(urlparse(self.path).query)
            query = (q.get("q") or [""])[0].strip()
            market = (q.get("market") or [None])[0]
            kind = (q.get("kind") or [None])[0]
            if query:
                _json(
                    self,
                    200,
                    {
                        "query": query,
                        "market": market,
                        "kind": kind,
                        "results": search_memory(
                            query, market=market, kind=kind, limit=20
                        ),
                        "stats": stats(),
                    },
                )
            else:
                _json(
                    self,
                    200,
                    {"recent": last(kind=kind, n=80), "stats": stats()},
                )
            return
        if path == "/api/charts":
            q = parse_qs(urlparse(self.path).query)
            bars = int((q.get("bars") or ["180"])[0])
            only = (q.get("market") or [None])[0]
            _json(self, 200, desk.charts(count=max(60, min(bars, 1000)), only=only))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        supplied = self.headers.get("X-CFD-Control-Token") or ""
        if not hmac.compare_digest(supplied, CONTROL_TOKEN):
            _json(self, 403, {"error": "dashboard control token required"})
            return
        try:
            self._do_POST()
        except Exception as exc:
            self._api_error(exc)

    def _do_POST(self) -> None:
        path = urlparse(self.path).path
        from desk import get_desk

        desk = get_desk()
        if path == "/api/scan":
            _json(self, 200, desk.scan_once())
            return
        if path == "/api/start":
            _json(self, 200, desk.start())
            return
        if path == "/api/stop":
            _json(self, 200, desk.stop())
            return
        if path == "/api/reset-day":
            _json(self, 200, desk.reset_daily_loss())
            return
        self.send_error(404)

    def _file(self, path: Path, ctype: str) -> None:
        if not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_background(open_browser: bool = True) -> ThreadingHTTPServer:
    server = ReusableThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    local_url = f"http://127.0.0.1:{PORT}/?token={CONTROL_TOKEN}"
    if HOST in {"0.0.0.0", "::"}:
        phone = f"http://{lan_ip()}:{PORT}/?token={CONTROL_TOKEN}"
        print(f"web {local_url}  phone {phone}")
    else:
        print(f"web {local_url}  LAN disabled (set CFD_WEB_HOST=0.0.0.0 to enable)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(local_url)).start()
    return server


def main() -> None:
    server = start_background(True)
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        print("stopped")
        server.shutdown()


if __name__ == "__main__":
    main()
