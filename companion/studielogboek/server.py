"""Local HTTP endpoint for the Firefox extension. Listens on 127.0.0.1 only."""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit

from . import config
from .db import Database

log = logging.getLogger(__name__)

ALLOWED_KINDS = {"page", "video", "search", "pdf", "ai"}


def host_excluded(url: str | None, excluded: list[str]) -> bool:
    if not url:
        return False
    host = (urlsplit(url).hostname or "").lower()
    return any(pat and (pat.lower() in host) for pat in excluded)


class Companion:
    """Shared state between the HTTP server, the window tracker and the tray."""

    def __init__(self, db: Database):
        self.db = db
        self.active_page: dict | None = None  # latest focused Firefox tab, for attaching notes
        self.listeners: list[Callable[[bool], None]] = []

    def set_study_mode(self, on: bool) -> None:
        changed = on != self.db.study_mode()
        self.db.set_study_mode(on)
        if changed:
            log.info("Studiemodus %s", "aan" if on else "uit")
            for fn in self.listeners:
                try:
                    fn(on)
                except Exception:  # a broken listener must not break the server
                    log.exception("listener failed")


def make_handler(app: Companion):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Studielogboek/1.0"

        def log_message(self, fmt, *args):  # route to our log instead of stderr
            log.debug("http: " + fmt, *args)

        # Only the extension (moz-extension://) or tools without an Origin (curl) may talk to us.
        # Regular web pages always send an Origin header, so they cannot inject fake entries.
        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin.startswith("moz-extension://")

        def _send(self, code: int, body: dict | None = None):
            data = json.dumps(body or {}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            origin = self.headers.get("Origin")
            if origin and origin.startswith("moz-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 5_000_000:
                raise ValueError("too large")
            return json.loads(self.rfile.read(length) or b"{}")

        def do_OPTIONS(self):
            self._send(204 if self._origin_ok() else 403)

        def do_GET(self):
            if not self._origin_ok():
                return self._send(403, {"error": "origin"})
            if self.path == "/status":
                return self._send(200, {
                    "studyMode": app.db.study_mode(),
                    "excludedDomains": app.db.excluded_domains(),
                    "app": config.APP_NAME,
                })
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._origin_ok():
                return self._send(403, {"error": "origin"})
            try:
                body = self._json()
            except ValueError:
                return self._send(400, {"error": "bad json"})

            if self.path == "/studymode":
                app.set_study_mode(bool(body.get("on")))
                return self._send(200, {"studyMode": app.db.study_mode()})

            if self.path == "/exclude":
                domain = str(body.get("domain") or "").strip().lower()
                if domain:
                    current = app.db.excluded_domains()
                    if domain not in current:
                        app.db.set_setting("excluded_domains", current + [domain])
                return self._send(200, {"excludedDomains": app.db.excluded_domains()})

            if self.path == "/active":
                url = body.get("url")
                log.debug("active: %s", url)
                if url and not host_excluded(url, app.db.excluded_domains()):
                    app.active_page = {"url": url, "title": body.get("title"), "ts": time.time()}
                else:
                    app.active_page = None
                return self._send(200, {})

            if self.path == "/events":
                excluded = app.db.excluded_domains()
                stored = 0
                for ev in body.get("events", []):
                    if ev.get("kind") not in ALLOWED_KINDS or not ev.get("start"):
                        continue
                    if host_excluded(ev.get("url"), excluded):
                        continue
                    ev["source"] = "firefox"
                    log.debug("event %s %s %ss %s", ev.get("key"), ev.get("kind"), ev.get("seconds"), ev.get("url"))
                    try:
                        app.db.upsert_event(ev)
                        stored += 1
                    except Exception:
                        log.exception("could not store event %r", ev)
                return self._send(200, {"stored": stored})

            self._send(404, {"error": "not found"})

    return Handler


def start_server(app: Companion) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((config.HOST, config.PORT), make_handler(app))
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    log.info("Luistert op http://%s:%d", config.HOST, config.PORT)
    return httpd
