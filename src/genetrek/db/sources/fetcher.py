"""The one seam every upstream call goes through.

Tests substitute a fetcher backed by fixture files, so the suite never depends on
a network, on an upstream being up, or on a release not having moved.
"""

from __future__ import annotations

import gzip
import io
import time
from typing import IO, TYPE_CHECKING, Protocol, Self, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


class FetchError(RuntimeError):
    """An upstream request failed, or returned something unusable."""


@runtime_checkable
class Fetcher(Protocol):
    """Minimal HTTP surface the source modules need."""

    def get_json(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        """GET a URL and parse the response as JSON."""
        ...

    def post_form_json(self, url: str, data: Mapping[str, str]) -> object:
        """POST a form body and parse the response as JSON."""
        ...

    def get_text(self, url: str) -> str:
        """GET a URL and return the body as text."""
        ...

    def head(self, url: str) -> Mapping[str, str]:
        """HEAD a URL and return its response headers."""
        ...

    def stream_lines(self, url: str, *, gunzip: bool = False) -> Iterator[str]:
        """Stream a body line by line, decompressing on the fly when asked.

        Used for the GENCODE GTFs, which are ~150 MB compressed. Streaming keeps
        peak memory to one line and lets the caller discard the 95% of lines that
        are not gene features.
        """
        ...


class HttpFetcher:
    """A :class:`Fetcher` backed by httpx, with retries and a polite pace."""

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        retries: int = 3,
        min_interval: float = 0.0,
        user_agent: str = "genetrek-db (https://github.com/bourgeron-lab/genetrek)",
    ) -> None:
        import httpx  # noqa: PLC0415 - keeps the import cost off the CLI's happy path

        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
        self._retries = retries
        self._min_interval = min_interval
        self._last_call = 0.0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._client.close()

    # ------------------------------------------------------------------ pacing

    def _wait(self) -> None:
        """Honour the minimum interval between calls to the same upstream."""
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    def _attempt(self, describe: str, call: object) -> object:
        """Run a request with retries, reporting the last failure if all fail."""
        import httpx  # noqa: PLC0415

        last: Exception | None = None
        for attempt in range(self._retries):
            self._wait()
            try:
                return call()  # type: ignore[operator]
            except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
                last = exc
                if attempt + 1 < self._retries:
                    # Linear rather than exponential: these upstreams are slow,
                    # not flaky, so a long backoff mostly wastes wall clock.
                    time.sleep(1.0 * (attempt + 1))
        msg = f"{describe} failed after {self._retries} attempt(s): {last}"
        raise FetchError(msg) from last

    # ----------------------------------------------------------------- methods

    def get_json(self, url: str, params: Mapping[str, str] | None = None) -> object:
        def call() -> object:
            response = self._client.get(url, params=dict(params or {}))
            response.raise_for_status()
            return response.json()

        return self._attempt(f"GET {url}", call)

    def post_form_json(self, url: str, data: Mapping[str, str]) -> object:
        def call() -> object:
            response = self._client.post(url, data=dict(data))
            response.raise_for_status()
            return response.json()

        return self._attempt(f"POST {url}", call)

    def get_text(self, url: str) -> str:
        def call() -> str:
            response = self._client.get(url)
            response.raise_for_status()
            return response.text

        return str(self._attempt(f"GET {url}", call))

    def head(self, url: str) -> Mapping[str, str]:
        def call() -> Mapping[str, str]:
            response = self._client.head(url)
            response.raise_for_status()
            return {k.lower(): v for k, v in response.headers.items()}

        result = self._attempt(f"HEAD {url}", call)
        return result  # type: ignore[return-value]

    def stream_lines(self, url: str, *, gunzip: bool = False) -> Iterator[str]:
        """Stream a body line by line. Not retried: a partial read cannot resume."""
        import httpx  # noqa: PLC0415

        try:
            # identity encoding so httpx does not double-decompress a .gz body
            with self._client.stream(
                "GET",
                url,
                headers={"Accept-Encoding": "identity"},
            ) as response:
                response.raise_for_status()
                buffered = io.BufferedReader(_ResponseReader(response))
                binary = cast(
                    "IO[bytes]",
                    gzip.GzipFile(fileobj=buffered) if gunzip else buffered,
                )
                yield from io.TextIOWrapper(binary, encoding="utf-8")
        except httpx.HTTPError as exc:
            msg = f"streaming {url} failed: {exc}"
            raise FetchError(msg) from exc


class _ResponseReader(io.RawIOBase):
    """Adapts an httpx streaming response to a file object gzip can read."""

    def __init__(self, response: object) -> None:
        self._chunks = response.iter_bytes()  # type: ignore[attr-defined]
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def readinto(self, target: memoryview) -> int:  # type: ignore[override]
        while not self._buffer:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                return 0
        take = min(len(target), len(self._buffer))
        target[:take] = self._buffer[:take]
        self._buffer = self._buffer[take:]
        return take
