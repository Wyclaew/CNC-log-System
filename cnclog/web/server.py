"""Local HTTP server for the operator screen.

Standard library only -- http.server is more than enough for one machine and
a handful of clients, and it means nothing has to be installed on a locked-down
control PC.

The server is read-only: there is no POST handler and no endpoint that can
change a setting, a file or anything on the machine.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .api import Api

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class _Handler(BaseHTTPRequestHandler):
    server_version = "cnclog"
    #: Injected by build_server.
    api: Optional[Api] = None

    # http.server logs every request to stderr; that would bury the startup
    # message the operator needs to read.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        self._responded = False
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        path = parsed.path

        try:
            if path.startswith("/api/"):
                self._handle_api(path[len("/api/"):], params)
            else:
                self._serve_static(path)
        except (BrokenPipeError, ConnectionResetError):
            # The browser navigated away mid-response. Not an error.
            return
        except Exception as exc:  # noqa: BLE001 - one bad request must not kill the UI
            # If the failure happened after headers went out, sending a second
            # response would raise again and take the handler thread with it.
            if getattr(self, "_responded", False):
                return
            try:
                self._send_json({"hata": f"{type(exc).__name__}: {exc}"}, status=500)
            except Exception:  # noqa: BLE001
                return

    # ------------------------------------------------------------------- api

    def _handle_api(self, endpoint: str, params: Dict[str, str]) -> None:
        api = self.api
        if api is None:
            self._send_json({"hata": "API hazır değil"}, status=503)
            return

        if endpoint == "durum":
            self._send_json(api.durum())
        elif endpoint == "loglar":
            self._send_json(api.loglar(params))
        elif endpoint == "rapor":
            self._send_json(api.rapor(params))
        elif endpoint == "gunler":
            self._send_json(api.gunler())
        elif endpoint == "rapor.csv":
            tarih = params.get("tarih", "bugun")
            body = api.rapor_csv(params).encode("utf-8")
            self._responded = True
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="cnclog-rapor-{tarih}.csv"',
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json({"hata": f"Bilinmeyen uç: {endpoint}"}, status=404)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode(
            "utf-8"
        )
        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------------------------------------------------------------- static

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(STATIC_DIR, relative))

        # Refuse anything that escapes the static directory.
        if not target.startswith(STATIC_DIR + os.sep) and target != STATIC_DIR:
            self._send_json({"hata": "Geçersiz yol"}, status=403)
            return
        if not os.path.isfile(target):
            self._send_json({"hata": "Bulunamadı"}, status=404)
            return

        extension = os.path.splitext(target)[1].lower()
        content_type = _CONTENT_TYPES.get(extension, "application/octet-stream")
        with open(target, "rb") as handle:
            body = handle.read()

        self._responded = True
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _json_default(value: Any) -> Any:
    """Last-resort encoder so an unexpected type cannot 500 the whole page."""
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return str(value)


class WebServer:
    """Runs the HTTP server on its own thread."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.cfg = app.cfg
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        host = self.cfg.web_bind
        # 0.0.0.0 is not a browsable address; point at the loopback instead.
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{self.cfg.web_port}"

    @property
    def is_public(self) -> bool:
        return self.cfg.web_bind not in ("127.0.0.1", "localhost", "::1")

    def start(self) -> Tuple[str, bool]:
        handler = type("_BoundHandler", (_Handler,), {"api": Api(self.app)})
        self._httpd = ThreadingHTTPServer(
            (self.cfg.web_bind, self.cfg.web_port), handler
        )
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="cnclog-web", daemon=True
        )
        self._thread.start()
        return self.url, self.is_public

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
