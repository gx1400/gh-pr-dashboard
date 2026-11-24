"""Background poller + stdlib HTTP server."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from . import __version__
from .config import Config, ConfigError
from .github import build_rows, fetch_owner

log = logging.getLogger(__name__)

MANUAL_REFRESH_GAP = 30  # seconds between browser-triggered polls, across all clients

Fetcher = Callable[[str, str], tuple[list[dict], "dict | None", list[str]]]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class State:
    def __init__(self, config: Config, environ: Mapping[str, str], fetcher: Fetcher = fetch_owner):
        self.config = config
        self.environ = environ
        self.fetcher = fetcher
        self.lock = threading.Lock()
        self.raw: dict[str, list[dict]] = {}  # owner -> last good PR nodes
        self.errors: dict[str, str] = {}
        self.fetched: dict[str, str] = {}
        self.rate: dict | None = None
        self.last_poll: str | None = None
        self.wake = threading.Event()
        self.last_manual = float("-inf")

    def poll(self) -> None:
        for owner in self.config.owners:
            try:
                token = owner.token.read(self.environ)
                prs, rate, warnings = self.fetcher(owner.name, token)
            except (ConfigError, RuntimeError, OSError) as e:
                # keep the last good data and surface the error on the page
                with self.lock:
                    self.errors[owner.name] = str(e)
                log.warning("%s: fetch failed: %s", owner.name, e)
                continue
            with self.lock:
                self.raw[owner.name] = prs
                self.fetched[owner.name] = now_iso()
                self.rate = rate or self.rate
                if warnings:
                    self.errors[owner.name] = "partial results: " + "; ".join(warnings)
                else:
                    self.errors.pop(owner.name, None)
            log.info("%s: %d open PRs%s", owner.name, len(prs), " (partial)" if warnings else "")
        with self.lock:
            self.last_poll = now_iso()

    def loop(self) -> None:
        while True:
            self.poll()
            self.wake.wait(self.config.refresh_seconds)
            self.wake.clear()

    def request_refresh(self) -> bool:
        now = time.monotonic()
        if now - self.last_manual < MANUAL_REFRESH_GAP:
            return False
        self.last_manual = now
        self.wake.set()
        return True

    def snapshot(self) -> dict:
        names = [o.name for o in self.config.owners]
        with self.lock:
            raw = [pr for name in names for pr in self.raw.get(name, [])]
            return {
                "version": __version__,
                "owners": names,
                "generated": self.last_poll,
                "refresh_seconds": self.config.refresh_seconds,
                "fetched": dict(self.fetched),
                "errors": dict(self.errors),
                "rate_limit": self.rate,
                "prs": build_rows(raw, self.config.exclude_repos),
            }


def make_handler(state: State) -> type[BaseHTTPRequestHandler]:
    index = resources.files("gh_pr_dashboard").joinpath("static/index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"gh-pr-dashboard/{__version__}"

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, index, "text/html; charset=utf-8")
            elif path == "/api/prs":
                self._send(200, json.dumps(state.snapshot()).encode(), "application/json")
            elif path == "/healthz":
                self._send(200, b"ok\n", "text/plain")
            else:
                self._send(404, b"not found\n", "text/plain")

        def do_POST(self) -> None:
            if self.path != "/api/refresh":
                self._send(404, b"not found\n", "text/plain")
            elif state.request_refresh():
                self._send(202, b"refresh queued\n", "text/plain")
            else:
                self._send(429, b"refreshed recently\n", "text/plain")

        def log_message(self, format, *args) -> None:  # noqa: A002 (base signature)
            if not self.path.startswith("/healthz"):
                log.debug("%s %s", self.address_string(), format % args)

    return Handler


def serve(config: Config, environ: Mapping[str, str], host: str = "0.0.0.0") -> None:
    state = State(config, environ)
    threading.Thread(target=state.loop, name="poller", daemon=True).start()
    ThreadingHTTPServer((host, config.port), make_handler(state)).serve_forever()
