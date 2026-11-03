"""Retries, error mapping and old servers, against a mocked server."""

from __future__ import annotations

import httpx
import pytest
import respx

import genetrek.client as client_module
from genetrek import (
    AmbiguousGene,
    ApiError,
    ConnectionFailed,
    GeneTrek,
    ServerTooOld,
    UnknownField,
)

URL = "https://genetrek.example.org"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)


@pytest.fixture
def gt():
    with GeneTrek(URL) as client:
        yield client


@respx.mock
def test_retries_a_503_then_succeeds(gt):
    route = respx.get(f"{URL}/api/health").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"status": "ok"})]
    )
    assert gt.info()["status"] == "ok"
    assert route.call_count == 2


@respx.mock
def test_gives_up_on_a_dead_server(gt):
    respx.get(f"{URL}/api/health").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ConnectionFailed, match=URL):
        gt.info()


@respx.mock
def test_never_retries_a_client_error(gt):
    route = respx.get(f"{URL}/api/fields/nope").mock(
        return_value=httpx.Response(
            404, json={"detail": "unknown", "code": "unknown_field"}
        )
    )
    with pytest.raises(UnknownField):
        gt.field("nope")
    assert route.call_count == 1


@respx.mock
def test_an_unlisted_code_is_an_api_error(gt):
    respx.get(f"{URL}/api/backgrounds").mock(
        return_value=httpx.Response(503, json={"detail": "x", "code": "unavailable"})
    )
    with pytest.raises(ApiError) as caught:
        gt.backgrounds()
    assert caught.value.code == "unavailable"


@respx.mock
def test_an_api_1_0_server(gt):
    """Today's public instance: no api_version, no codes, no POST /api/genes."""
    respx.get(f"{URL}/api/health").mock(
        return_value=httpx.Response(200, json={"status": "ok", "release": "v2"})
    )
    assert gt.api_version() == (1, 0)
    assert gt.release() is None
    with pytest.raises(ServerTooOld, match="GeneTrek v8"):
        gt.genes(["SHANK3"])
    with pytest.raises(ServerTooOld):
        gt.search("SHANK3")
    # gene() falls back to the GET that 1.0 has.
    respx.get(f"{URL}/api/genes/SHANK3").mock(
        return_value=httpx.Response(
            200,
            json={
                "gene": {"input": "SHANK3", "matched_by": "symbol", "key": "85358"},
                "identifiers": {"symbol": "SHANK3"},
                "values": {"loeuf": 0.37},
            },
        )
    )
    assert gt.gene("SHANK3")["values"] == {"loeuf": 0.37}
    # 1.0 sends a 409 with no code: still an AmbiguousGene.
    respx.get(f"{URL}/api/genes/X").mock(
        return_value=httpx.Response(409, json={"detail": "ambiguous"})
    )
    with pytest.raises(AmbiguousGene):
        gt.gene("X")


@respx.mock
def test_genes_travel_in_the_body_only(gt):
    respx.get(f"{URL}/api/health").mock(
        return_value=httpx.Response(200, json={"api_version": "1.1"})
    )
    route = respx.post(f"{URL}/api/genes").mock(
        return_value=httpx.Response(
            200,
            json={
                "id_columns": {"symbol": "symbol"},
                "genes": [],
                "not_found": ["PATIENTGENE"],
                "ambiguous": [],
            },
        )
    )
    with pytest.warns(UserWarning, match="PATIENTGENE"):
        gt.genes(["PATIENTGENE"])
    request = route.calls.last.request
    assert "PATIENTGENE" not in str(request.url)
    assert b"PATIENTGENE" in request.content


def test_user_agent_names_the_package():
    with GeneTrek(URL, user_agent="mytool/1") as gt:
        agent = gt._http.headers["User-Agent"]
    assert agent.startswith("genetrek-python/")
    assert agent.endswith("mytool/1")
