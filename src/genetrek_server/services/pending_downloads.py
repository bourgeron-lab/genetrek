"""Single-use download links for files the page builds: "Download current results".

``ui.download.content`` sends a file over the page's websocket as one socket.io
message. The whole table as TSV is ~25 MB; on a slow link that message holds the
connection long enough for the heartbeat to time out, the page reloads and the
file never arrives (seen on the dev instance, 2026-11-03). So the page builds
the file as before and hands the browser a link to fetch it over plain HTTP.

The file stays in this process's memory only -- never on disk -- under a random
token, for at most :data:`TTL_SECONDS`, and is dropped the first time it is
fetched. The link carries nothing but the token, so access logs see no gene.
Results can hold a visitor's My lists columns, which is why none of this ever
touches a file: the same promise ``ui.download.content`` kept.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

#: How long an unfetched file is kept. The browser fetches it at once; this only
#: bounds what an abandoned tab leaves behind.
TTL_SECONDS = 120

#: Most bytes held at once, across every visitor. Beyond it the oldest file goes
#: -- its link then answers 404 -- rather than the pod nearing its memory limit.
MAX_BYTES = 128 * 1024 * 1024

#: Where a pending file is fetched; the token follows.
ROUTE = "/download/results"


@dataclass(frozen=True)
class PendingFile:
    content: bytes
    filename: str
    media_type: str
    expires: float


class PendingDownloads:
    """Files waiting to be fetched once, keyed by an unguessable token."""

    def __init__(self, *, ttl: float = TTL_SECONDS, max_bytes: int = MAX_BYTES) -> None:
        self._files: dict[str, PendingFile] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._max_bytes = max_bytes

    def put(self, content: bytes, filename: str, media_type: str) -> str:
        """Keep ``content`` for one fetch; return the path to fetch it from."""
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._drop_expired(now)
            self._files[token] = PendingFile(
                content, filename, media_type, now + self._ttl
            )
            # Oldest first: dicts keep insertion order.
            while self._held() > self._max_bytes and len(self._files) > 1:
                del self._files[next(iter(self._files))]
        return f"{ROUTE}/{token}"

    def take(self, token: str) -> PendingFile | None:
        """The file for ``token``, removed; ``None`` if unknown, used or expired."""
        with self._lock:
            pending = self._files.pop(token, None)
        if pending is None or pending.expires < time.monotonic():
            return None
        return pending

    def _held(self) -> int:
        return sum(len(f.content) for f in self._files.values())

    def _drop_expired(self, now: float) -> None:
        for token in [t for t, f in self._files.items() if f.expires < now]:
            del self._files[token]


#: The process's store: the page puts files in, the route takes them out.
PENDING = PendingDownloads()
