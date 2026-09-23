from __future__ import annotations

import argparse
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNER_PATH = ROOT / "BXANE.txt"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _enable_vt() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _banner() -> list[str]:
    try:
        text = BANNER_PATH.read_text(encoding="utf-8").rstrip("\r\n")
    except Exception:
        text = "BXANE"
    return text.splitlines() or ["BXANE"]


def _clean(text: str) -> str:
    return ANSI_RE.sub("", str(text or "")).replace("\r", "")


def _visual_lines(lines: deque[str], width: int, limit: int) -> list[str]:
    out: list[str] = []
    safe_width = max(20, width)
    for raw in reversed(lines):
        parts = textwrap.wrap(
            _clean(raw),
            width=safe_width,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]
        for part in reversed(parts):
            out.append(part[:safe_width])
            if len(out) >= limit:
                return list(reversed(out))
    return list(reversed(out))


class Screen:
    def __init__(self, mode: str):
        self.mode = mode.upper()
        self.banner = _banner()
        self.vt = _enable_vt()
        self._last_size: tuple[int, int] | None = None

    def render(self, logs: deque[str], running: bool, exit_code: int | None = None) -> None:
        size = shutil.get_terminal_size((110, 32))
        width, height = max(40, size.columns), max(16, size.lines)

        header = [line[:width] for line in self.banner]
        header.append("")
        header.append("─" * min(width, 110))

        status = (
            f" {self.mode} · {'RUNNING' if running else 'STOPPED'} · "
            f"{'Ctrl+C to stop' if running else 'exit ' + str(exit_code if exit_code is not None else '')}"
        )
        footer = status[:width]

        body_height = max(1, height - len(header) - 1)
        body = _visual_lines(logs, width, body_height)
        if len(body) < body_height:
            body = [""] * (body_height - len(body)) + body

        rows = header + body + [footer]
        rows = rows[:height]

        if self.vt:
            # The renderer owns the viewport: erase it and repaint from row 1.
            # Child output is piped, so nothing else can push BXANE off-screen.
            sys.stdout.write("\x1b[?25l\x1b[H\x1b[2J" + "\n".join(rows))
            sys.stdout.flush()
        else:
            # Legacy CMD fallback. It may flicker, but the banner still stays
            # at the top because every frame clears and redraws the viewport.
            os.system("cls")
            sys.stdout.write("\n".join(rows))
            sys.stdout.flush()

        self._last_size = (width, height)

    def restore(self) -> None:
        if self.vt:
            sys.stdout.write("\x1b[?25h\x1b[0m\n")
            sys.stdout.flush()


def _reader(proc: subprocess.Popen[str], events: queue.Queue[tuple[str, str]]) -> None:
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            events.put(("line", line.rstrip("\r\n")))
    finally:
        events.put(("eof", ""))


def run(mode: str) -> int:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "app.auto",
        "--mode",
        mode,
        "--skip-tune",
    ]

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=None,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )

    events: queue.Queue[tuple[str, str]] = queue.Queue()
    logs: deque[str] = deque(maxlen=1000)
    reader = threading.Thread(target=_reader, args=(proc, events), daemon=True)
    reader.start()

    screen = Screen(mode)
    dirty = True
    last_render = 0.0

    try:
        while True:
            try:
                kind, value = events.get(timeout=0.08)
                if kind == "line":
                    logs.append(value)
                    dirty = True
            except queue.Empty:
                pass

            now = time.monotonic()
            size = shutil.get_terminal_size((110, 32))
            current_size = (size.columns, size.lines)
            resized = current_size != screen._last_size

            if dirty or resized or now - last_render >= 0.5:
                screen.render(logs, running=proc.poll() is None)
                dirty = False
                last_render = now

            code = proc.poll()
            if code is not None:
                while True:
                    try:
                        kind, value = events.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "line":
                        logs.append(value)
                screen.render(logs, running=False, exit_code=code)
                return int(code)

    except KeyboardInterrupt:
        logs.append("Stopping CFD bot...")
        screen.render(logs, running=True)
        try:
            if os.name == "nt" and creationflags:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                proc.wait(timeout=3)
            else:
                proc.terminate()
                proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return int(proc.returncode or 130)
    finally:
        screen.restore()


def main() -> int:
    parser = argparse.ArgumentParser(description="Sticky BXANE console renderer")
    parser.add_argument("--mode", choices=("demo", "live"), required=True)
    args = parser.parse_args()
    return run(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
