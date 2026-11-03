"""Visit counting reports a page's path and nothing else.

``?search=`` can hold patient-derived genes and ``#lists=`` a visitor's lists;
neither may reach the counter, and nothing but a page load may be counted.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import genetrek_server
from genetrek_server import visits

SRC = Path(genetrek_server.__file__).parent


class TestConfiguration:
    def test_unset_means_no_counter(self):
        assert visits.counter_url({}) is None
        assert visits.counter_url({visits.ENV_VAR: "  "}) is None
        assert visits.counter_snippet(None) == ""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("/stats", "/stats"),
            ("/stats/", "/stats"),
            ("https://stats.example.org/gc", "https://stats.example.org/gc"),
        ],
    )
    def test_a_path_or_url(self, value, expected):
        assert visits.counter_url({visits.ENV_VAR: value}) == expected

    @pytest.mark.parametrize(
        "value",
        ["stats", "javascript:alert(1)", "/stats</script><script>", "/a b", '/"x'],
    )
    def test_anything_else_stops_the_start(self, value):
        with pytest.raises(ValueError, match=visits.ENV_VAR):
            visits.counter_url({visits.ENV_VAR: value})


class TestSnippet:
    SNIPPET = visits.counter_snippet("/stats")

    def test_it_reports_the_path(self):
        assert "location.pathname" in self.SNIPPET
        assert '"/stats/count"' in self.SNIPPET

    @pytest.mark.parametrize(
        "leak",
        ["location.search", "location.hash", "location.href", "document.referrer"],
    )
    def test_it_reads_nothing_else_of_the_url(self, leak):
        assert leak not in self.SNIPPET

    def test_it_does_not_load_goatcounters_script(self):
        """That script always sends the query string and the referrer."""
        assert "count.js" not in self.SNIPPET
        assert "data-goatcounter" not in self.SNIPPET

    @pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
    def test_in_a_browser_only_the_path_leaves(self):
        """Run it on a page whose URL carries a search and a list link."""
        script = re.sub(r"</?script>", "", self.SNIPPET)
        harness = (
            "const calls = [];\n"
            "const window = {fetch: (url, opts) => calls.push([url, opts])};\n"
            "const fetch = window.fetch;\n"
            "const location = {pathname: '/documentation',"
            " search: '?search=SHANK3', hash: '#lists=abc',"
            " href: 'https://g.example/documentation?search=SHANK3#lists=abc'};\n"
            f"{script}\n"
            "console.log(JSON.stringify(calls));\n"
        )
        result = subprocess.run(  # noqa: S603
            ["node", "-e", harness],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
        [[url, options]] = json.loads(result.stdout)
        assert url == "/stats/count?p=%2Fdocumentation"
        assert options["referrerPolicy"] == "no-referrer"
        assert options["credentials"] == "omit"


class TestPageLoadsOnly:
    def test_no_event_counting_anywhere(self):
        """Searches, downloads and dialogs are never counted."""
        offenders = [
            f"{path.relative_to(SRC)}: {needle}"
            for path in SRC.rglob("*")
            if path.suffix in {".py", ".js"}
            for needle in ("goatcounter.count(", "data-goatcounter", "count.js")
            if needle in path.read_text(encoding="utf-8")
        ]
        assert not offenders

    def test_both_kinds_of_page_add_the_counter(self):
        """The table page and every prose page; nothing else is a page."""
        for module in ("ui/page.py", "ui/content.py"):
            text = (SRC / module).read_text(encoding="utf-8")
            assert "visits.counter_snippet(visits.counter_url())" in text, module
            assert "ui.add_head_html(visit_counter)" in text, module
