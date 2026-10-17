"""Throttled, cached tile fetcher.

* One SQLite cache file per source under the cache directory. Cached tiles are
  reused forever by default (basemaps change slowly; re-exporting the same area
  costs zero network). ``--refresh`` bypasses the cache.
* A token-bucket limiter per source enforces ``TileSource.rate_limit``; a
  per-source thread pool enforces ``max_workers``. These are the mechanism that
  keeps us inside tile servers' usage policies.
* Missing tiles (HTTP 404, empty ArcGIS responses) are recorded in the cache as
  "missing" so they aren't re-requested every run.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests

from .sources import TileSource, USER_AGENT

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_ERROR = "error"


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "voymap" / "tiles"


class TokenBucket:
    def __init__(self, rate: float, burst: Optional[int] = None):
        self.rate = max(rate, 0.01)
        self.capacity = burst or max(1, int(rate))
        self.tokens = float(self.capacity)
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            time.sleep(wait)


class TileCache:
    """SQLite-backed cache, one connection per thread."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS tiles (z INTEGER, x INTEGER, y INTEGER, "
                "status TEXT, data BLOB, fetched_at REAL, PRIMARY KEY (z, x, y))"
            )
        self._wlock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def get(self, z: int, x: int, y: int) -> Optional[tuple[str, Optional[bytes]]]:
        row = self._conn().execute(
            "SELECT status, data FROM tiles WHERE z=? AND x=? AND y=?", (z, x, y)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def put(self, z: int, x: int, y: int, status: str, data: Optional[bytes]):
        with self._wlock:
            c = self._conn()
            c.execute(
                "INSERT OR REPLACE INTO tiles (z, x, y, status, data, fetched_at) VALUES (?,?,?,?,?,?)",
                (z, x, y, status, data, time.time()),
            )
            c.commit()

    def stats(self) -> dict:
        c = self._conn()
        n, size = c.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(data)),0) FROM tiles WHERE status='ok'").fetchone()
        return {"tiles": n, "bytes": size}


@dataclass
class TileResult:
    x: int
    y: int
    z: int
    status: str
    data: Optional[bytes]
    from_cache: bool = False
    error: str = ""


class TileFetcher:
    def __init__(self, source: TileSource, cache_dir: Optional[Path] = None,
                 refresh: bool = False, timeout: float = 30.0, retries: int = 3):
        self.source = source
        self.cache = TileCache((cache_dir or default_cache_dir()) / f"{source.id}.sqlite")
        self.refresh = refresh
        self.timeout = timeout
        self.retries = retries
        self.bucket = TokenBucket(source.rate_limit)
        self._local = threading.local()
        self.stop_event = threading.Event()

    def _session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.8"})
            s.headers.update(self.source.headers)
            self._local.session = s
        return s

    # -- single tile ------------------------------------------------------
    def fetch(self, x: int, y: int, z: int) -> TileResult:
        if z < self.source.min_zoom or z > self.source.max_zoom:
            return TileResult(x, y, z, STATUS_MISSING, None)
        if not self.refresh:
            hit = self.cache.get(z, x, y)
            if hit and hit[0] in (STATUS_OK, STATUS_MISSING):
                return TileResult(x, y, z, hit[0], hit[1], from_cache=True)

        url = self.source.tile_url(x, y, z)
        last_err = ""
        for attempt in range(self.retries + 1):
            if self.stop_event.is_set():
                return TileResult(x, y, z, STATUS_ERROR, None, error="cancelled")
            self.bucket.acquire()
            try:
                r = self._session().get(url, timeout=self.timeout)
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(min(8, 0.5 * 2 ** attempt))
                continue
            if r.status_code == 404:
                self.cache.put(z, x, y, STATUS_MISSING, None)
                return TileResult(x, y, z, STATUS_MISSING, None)
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(16, 1.0 * 2 ** attempt)
                except ValueError:
                    delay = min(16, 1.0 * 2 ** attempt)
                time.sleep(delay)
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                break
            ctype = r.headers.get("Content-Type", "")
            body = r.content
            if "image" not in ctype or len(body) < 24:
                # ArcGIS returns JSON error bodies with HTTP 200; treat as error,
                # but an empty/blank image is a legitimate "nothing here".
                last_err = f"non-image response ({ctype[:40]}): {body[:120]!r}"
                break
            self.cache.put(z, x, y, STATUS_OK, body)
            return TileResult(x, y, z, STATUS_OK, body)
        return TileResult(x, y, z, STATUS_ERROR, None, error=last_err)

    # -- many tiles -------------------------------------------------------
    def fetch_many(self, tiles: Iterable[tuple[int, int, int]],
                   on_result: Optional[Callable[[TileResult], None]] = None,
                   workers: Optional[int] = None) -> dict[tuple[int, int, int], TileResult]:
        results: dict[tuple[int, int, int], TileResult] = {}
        tiles = list(tiles)
        workers = workers or self.source.max_workers
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self.fetch, x, y, z): (x, y, z) for (x, y, z) in tiles}
            for fut in as_completed(futs):
                res = fut.result()
                results[(res.x, res.y, res.z)] = res
                if on_result:
                    on_result(res)
                if self.stop_event.is_set():
                    for f in futs:
                        f.cancel()
                    break
        return results

    def cancel(self):
        self.stop_event.set()
