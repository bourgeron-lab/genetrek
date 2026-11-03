"""Which server the client talks to, and the configuration file behind it."""

from __future__ import annotations

import pytest

from genetrek.config import (
    PUBLIC_URL,
    Config,
    load_config,
    resolve_server,
    save_config,
)
from genetrek.errors import ConfigError


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "config.toml"
    save_config(
        Config(
            servers={
                "dev": "https://dev.example.org/",
                "local": "http://localhost:8000",
            },
            default="local",
        ),
        path,
    )
    return path


def test_the_file_round_trips(path):
    config = load_config(path)
    assert config.servers == {
        "dev": "https://dev.example.org/",
        "local": "http://localhost:8000",
    }
    assert config.default == "local"


def test_an_absent_file_is_empty(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


@pytest.mark.parametrize(
    ("kwargs", "environ", "url", "source"),
    [
        (
            {"base_url": "http://a.example"},
            {"GENETREK_URL": "http://b.example"},
            "http://a.example",
            "argument",
        ),
        (
            {"server": "dev"},
            {"GENETREK_URL": "http://b.example"},
            "https://dev.example.org",
            "argument",
        ),
        (
            {},
            {"GENETREK_URL": "http://b.example/", "GENETREK_SERVER": "dev"},
            "http://b.example",
            "$GENETREK_URL",
        ),
        ({}, {"GENETREK_SERVER": "dev"}, "https://dev.example.org", "$GENETREK_SERVER"),
        ({}, {}, "http://localhost:8000", "config file default"),
        ({"server": "public"}, {}, PUBLIC_URL, "argument"),
        ({"server": "http://c.example"}, {}, "http://c.example", "argument"),
    ],
)
def test_precedence(path, kwargs, environ, url, source):
    resolved = resolve_server(**kwargs, environ=environ, path=path)
    assert (resolved.url, resolved.source) == (url, source)


def test_the_public_instance_by_default(tmp_path):
    resolved = resolve_server(environ={}, path=tmp_path / "none.toml")
    assert resolved.url == PUBLIC_URL
    assert "default" in resolved.describe()


def test_an_unknown_name_says_how_to_add_it(path):
    with pytest.raises(ConfigError, match="genetrek config add staging"):
        resolve_server(server="staging", environ={}, path=path)


@pytest.mark.parametrize("url", ["ftp://x", "genetrek.example.org", "http://a b"])
def test_a_bad_url_is_refused(url):
    with pytest.raises(ConfigError):
        resolve_server(base_url=url, environ={})


def test_a_damaged_file_is_reported(tmp_path):
    bad = tmp_path / "config.toml"
    bad.write_text("[servers\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(bad)
