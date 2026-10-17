"""Tiny loopback HTTP server for the map page.

QtWebEngine treats a page loaded from ``file://`` as local content and, by
default, does not let it reach remote URLs — which can leave the tile layers
blank. Serving the page from http://127.0.0.1:<port>/ gives it an ordinary web
origin, so tile requests to the map services work like they do in a browser.
Only files under the web directory are served, and only on the loopback
interface.
"""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _QuietHandler(SimpleHTTPRequestHandler):
    extra: dict[str, tuple[bytes, str]] = {}   # "/name" -> (body, content-type)

    def log_message(self, *args):  # silence per-request console noise
        pass

    def do_GET(self):
        hit = self.extra.get(self.path.split("?", 1)[0])
        if hit is None:
            return super().do_GET()
        body, ctype = hit
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class LocalServer:
    def __init__(self, directory: Path, extra: dict[str, tuple[bytes, str]] | None = None):
        self.directory = Path(directory)
        handler_cls = type("Handler", (_QuietHandler,), {"extra": dict(extra or {})})
        handler = partial(handler_cls, directory=str(self.directory))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="voymap-web")
        self._thread.start()

    def url(self, name: str = "map.html") -> str:
        return f"http://127.0.0.1:{self.port}/{name}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
