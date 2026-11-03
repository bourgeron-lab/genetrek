"""Single-use, in-memory download links for "Download current results"."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from genetrek_server.api.routes import create_downloads_router
from genetrek_server.services import pending_downloads
from genetrek_server.services.pending_downloads import PendingDownloads


def _client(store: PendingDownloads) -> TestClient:
    app = FastAPI()
    app.include_router(create_downloads_router(store))
    return TestClient(app)


def test_a_link_works_once():
    store = PendingDownloads()
    link = store.put(b"a\tb\n1\t2\n", "results.tsv", "text/tab-separated-values")
    client = _client(store)
    first = client.get(link)
    assert first.status_code == 200
    assert first.content == b"a\tb\n1\t2\n"
    assert first.headers["content-disposition"] == 'attachment; filename="results.tsv"'
    assert first.headers["cache-control"] == "no-store"
    assert client.get(link).status_code == 404


def test_the_link_names_nothing_but_a_token():
    store = PendingDownloads()
    link = store.put(b"SHANK3", "genes.tsv", "text/plain")
    assert link.startswith(pending_downloads.ROUTE + "/")
    token = link.rsplit("/", 1)[1]
    assert len(token) >= 40
    assert "SHANK3" not in link
    assert "genes" not in link


def test_an_unknown_token_is_a_404():
    assert (
        _client(PendingDownloads()).get(f"{pending_downloads.ROUTE}/nope").status_code
        == 404
    )


def test_an_expired_file_is_gone(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(pending_downloads.time, "monotonic", lambda: clock[0])
    store = PendingDownloads(ttl=60)
    link = store.put(b"x", "x.tsv", "text/plain")
    clock[0] += 61
    assert store.take(link.rsplit("/", 1)[1]) is None


def test_memory_is_bounded_oldest_first():
    store = PendingDownloads(max_bytes=10)
    old = store.put(b"123456", "a", "text/plain").rsplit("/", 1)[1]
    new = store.put(b"789012", "b", "text/plain").rsplit("/", 1)[1]
    assert store.take(old) is None
    assert store.take(new) is not None


def test_the_page_uses_the_link_not_the_websocket():
    from pathlib import Path  # noqa: PLC0415

    import genetrek_server  # noqa: PLC0415

    page = (Path(genetrek_server.__file__).parent / "ui" / "page.py").read_text()
    body = page.split("async def download_current_results", 1)[1].split(
        "ui.button(", 1
    )[0]
    assert "pending_downloads.PENDING.put(" in body
    assert "ui.download.from_url(" in body
    assert "ui.download(" not in body
    assert "ui.download.content(" not in body
