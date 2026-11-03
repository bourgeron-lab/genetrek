"""Link previews: fixed tags, absolute addresses, nothing from the request.

``?search=`` can hold patient-derived genes; a preview bot fetching a shared
link must not find them in the HTML.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

import genetrek
from genetrek import link_preview

SRC = Path(genetrek.__file__).parent

BASE = "https://genetrek.example.org"


def _content(html: str, key: str) -> str:
    match = re.search(
        rf'<meta (?:name|property)="{re.escape(key)}" content="([^"]*)">', html
    )
    assert match, key
    return match.group(1)


class TestConfiguration:
    def test_unset_means_no_address(self):
        assert link_preview.public_url({}) is None
        assert link_preview.public_url({link_preview.ENV_VAR: " "}) is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (BASE, BASE),
            (BASE + "/", BASE),
            ("http://localhost:8000", "http://localhost:8000"),
            ("https://example.org/genetrek/", "https://example.org/genetrek"),
        ],
    )
    def test_an_http_url(self, value, expected):
        assert link_preview.public_url({link_preview.ENV_VAR: value}) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "genetrek.example.org",
            "/genetrek",
            "javascript:alert(1)",
            f'{BASE}/"x',
            f"{BASE}?a=1",
        ],
    )
    def test_anything_else_stops_the_start(self, value):
        with pytest.raises(ValueError, match=link_preview.ENV_VAR):
            link_preview.public_url({link_preview.ENV_VAR: value})


class TestTags:
    def test_with_an_address_everything_is_absolute(self):
        html = link_preview.preview_tags(
            BASE, route="/documentation", title="GeneTrek — Documentation"
        )
        assert _content(html, "og:url") == BASE + "/documentation"
        assert _content(html, "og:image") == BASE + link_preview.IMAGE_ROUTE
        assert _content(html, "twitter:image") == BASE + link_preview.IMAGE_ROUTE
        assert _content(html, "twitter:card") == "summary_large_image"
        assert _content(html, "og:title") == "GeneTrek — Documentation"

    def test_without_one_no_relative_address(self):
        html = link_preview.preview_tags(None)
        assert "og:url" not in html
        assert "image" not in html
        assert _content(html, "twitter:card") == "summary"
        assert _content(html, "og:title") == link_preview.TITLE

    def test_values_are_escaped(self):
        html = link_preview.preview_tags(None, title='A "quoted" <b>title</b>')
        assert (
            _content(html, "og:title")
            == "A &quot;quoted&quot; &lt;b&gt;title&lt;/b&gt;"
        )

    def test_the_image_is_the_declared_size(self):
        data = link_preview.IMAGE.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (link_preview.IMAGE_WIDTH, link_preview.IMAGE_HEIGHT)


class TestWiring:
    """Every page adds the tags, built at startup from its route, not the request."""

    def test_both_kinds_of_page_add_them(self):
        for module, call in (
            ("ui/page.py", "link_preview.preview_tags(link_preview.public_url())"),
            ("ui/content.py", "link_preview.preview_tags("),
        ):
            text = (SRC / module).read_text(encoding="utf-8")
            assert call in text, module
            assert "ui.add_head_html(preview)" in text, module

    def test_the_image_is_routed(self):
        text = (SRC / "app.py").read_text(encoding="utf-8")
        assert 'link_preview.IMAGE_ROUTE, methods=["GET", "HEAD"]' in text
