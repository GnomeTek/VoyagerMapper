"""TileFetcher against a loopback HTTP server: hostile Retry-After values,
oversized bodies and cross-origin redirects."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from voymap import fetch
from voymap.fetch import STATUS_ERROR, STATUS_OK, TileFetcher
from voymap.sources import TileSource

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def _serve(handle):
    """Start a loopback server whose GET is ``handle(handler)``; returns (server, base_url)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            handle(self)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


@pytest.fixture
def server():
    servers = []

    def start(handle):
        srv, base = _serve(handle)
        servers.append(srv)
        return base

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _fetcher(tmp_path, base, headers=None):
    src = TileSource(id="t", name="t", kind="xyz", url=base + "/{z}/{x}/{y}.png",
                     min_zoom=0, max_zoom=20, rate_limit=1000, headers=headers or {})
    return TileFetcher(src, cache_dir=tmp_path, retries=1)


def _send(h, status, body=b"", headers=()):
    h.send_response(status)
    for k, v in headers:
        h.send_header(k, v)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf", "garbage"])
def test_bad_retry_after_falls_back_to_backoff(tmp_path, server, monkeypatch, value):
    monkeypatch.setattr(fetch, "MAX_RETRY_AFTER", 0.05)
    base = server(lambda h: _send(h, 503, headers=[("Retry-After", value)]))
    r = _fetcher(tmp_path, base).fetch(1, 1, 3)
    assert (r.status, r.error) == (STATUS_ERROR, "HTTP 503")


def test_huge_retry_after_is_capped(tmp_path, server, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_RETRY_AFTER", 0.05)
    base = server(lambda h: _send(h, 503, headers=[("Retry-After", "86400")]))
    t0 = time.monotonic()
    r = _fetcher(tmp_path, base).fetch(1, 1, 3)
    assert (r.status, r.error) == (STATUS_ERROR, "HTTP 503")
    assert time.monotonic() - t0 < 5


def test_retry_after_wait_is_cancellable(tmp_path, server):
    base = server(lambda h: _send(h, 503, headers=[("Retry-After", "86400")]))
    f = _fetcher(tmp_path, base)
    threading.Timer(0.3, f.cancel).start()
    t0 = time.monotonic()
    r = f.fetch(1, 1, 3)
    assert (r.status, r.error) == (STATUS_ERROR, "cancelled")
    assert time.monotonic() - t0 < 5


def test_oversized_body_is_rejected_and_not_cached(tmp_path, server, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_TILE_BYTES", 1024)
    base = server(lambda h: _send(h, 200, PNG + b"\0" * 2048, [("Content-Type", "image/png")]))
    f = _fetcher(tmp_path, base)
    r = f.fetch(1, 1, 3)
    assert (r.status, r.error) == (STATUS_ERROR, "response larger than 1024 bytes")
    assert f.cache.get(3, 1, 1) is None


def test_body_at_cap_is_accepted(tmp_path, server, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_TILE_BYTES", len(PNG))
    base = server(lambda h: _send(h, 200, PNG, [("Content-Type", "image/png")]))
    r = _fetcher(tmp_path, base).fetch(1, 1, 3)
    assert (r.status, r.data) == (STATUS_OK, PNG)


def test_custom_headers_not_forwarded_cross_origin(tmp_path, server):
    seen = {}

    def target(h):
        seen["target"] = h.headers.get("X-Api-Key")
        _send(h, 200, PNG, [("Content-Type", "image/png")])

    target_base = server(target)

    def origin(h):
        seen.setdefault("origin", []).append(h.headers.get("X-Api-Key"))
        if h.path.startswith("/hop"):  # same-origin redirect keeps the header
            _send(h, 302, headers=[("Location", target_base + "/tile.png")])
        else:
            _send(h, 302, headers=[("Location", "/hop" + h.path)])

    base = server(origin)
    r = _fetcher(tmp_path, base, {"X-Api-Key": "s3cret"}).fetch(1, 1, 3)
    assert r.status == STATUS_OK
    assert seen == {"origin": ["s3cret", "s3cret"], "target": None}
