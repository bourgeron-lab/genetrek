"""Which GeneTrek server the client talks to.

The public instance is the default. Any other -- a development instance, one
running on your own machine -- is named in a small TOML file kept in the user's
configuration directory, so a private address never has to be written into
code or into this package::

    default = "dev"                       # optional; unset means the public one
    [servers]
    dev = "https://genetrek.dev.example.org"
    local = "http://localhost:8000"

``genetrek config add dev https://... --default`` writes it.

The file holds server addresses only, never genes.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_config_path

from genetrek.errors import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The public GeneTrek instance.
PUBLIC_URL = "https://genetrek.pasteur.fr"

URL_ENV = "GENETREK_URL"
SERVER_ENV = "GENETREK_SERVER"
CONFIG_ENV = "GENETREK_CONFIG"

_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")
_URL_RE = re.compile(r"https?://[^\s/?#]+(?:/[^\s?#]*)?")


def config_path(environ: Mapping[str, str] = os.environ) -> Path:
    """Where the configuration file is: ``$GENETREK_CONFIG``, else the platform's."""
    if value := environ.get(CONFIG_ENV, "").strip():
        return Path(value).expanduser()
    return user_config_path("genetrek") / "config.toml"


@dataclass
class Config:
    """The configuration file's contents."""

    servers: dict[str, str] = field(default_factory=dict)
    default: str | None = None

    def to_toml(self) -> str:
        """The file's text. Only strings, so JSON's string quoting is valid TOML."""
        lines = []
        if self.default:
            lines.append(f"default = {json.dumps(self.default)}")
        lines.append("")
        lines.append("[servers]")
        lines.extend(
            f"{name} = {json.dumps(url)}" for name, url in self.servers.items()
        )
        return "\n".join(lines).lstrip("\n") + "\n"


def load_config(path: Path | None = None) -> Config:
    """Read the configuration file; an absent file is an empty configuration."""
    path = path or config_path()
    if not path.is_file():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise ConfigError(msg) from exc
    servers = data.get("servers", {})
    default = data.get("default")
    if not isinstance(servers, dict) or not all(
        isinstance(v, str) for v in servers.values()
    ):
        msg = f"{path}: [servers] must map names to URLs"
        raise ConfigError(msg)
    if default is not None and not isinstance(default, str):
        msg = f"{path}: default must be a server name"
        raise ConfigError(msg)
    return Config(servers=dict(servers), default=default)


def save_config(config: Config, path: Path | None = None) -> Path:
    """Write the configuration file, creating its directory."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.to_toml(), encoding="utf-8")
    return path


def check_name(name: str) -> str:
    """A server name: letters, digits, ``_`` and ``-``."""
    if not _NAME_RE.fullmatch(name):
        msg = f"invalid server name {name!r}: use letters, digits, _ and -"
        raise ConfigError(msg)
    return name


def check_url(url: str) -> str:
    """An http(s) base URL, without a trailing slash."""
    url = url.strip()
    if not _URL_RE.fullmatch(url):
        msg = f"not an http(s) URL: {url!r}"
        raise ConfigError(msg)
    return url.rstrip("/")


@dataclass(frozen=True)
class Resolved:
    """The server chosen, and why -- so it is never unclear who answered."""

    url: str
    name: str | None
    source: str

    def describe(self) -> str:
        """``https://... (dev, config file)``."""
        who = f"{self.name}, {self.source}" if self.name else self.source
        return f"{self.url} ({who})"


def resolve_server(
    base_url: str | None = None,
    server: str | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    path: Path | None = None,
) -> Resolved:
    """Pick the server: the first of these that is set wins.

    1. ``base_url`` -- any URL;
    2. ``server`` -- a name from the configuration file (a URL is accepted too);
    3. ``$GENETREK_URL``;
    4. ``$GENETREK_SERVER`` -- a name;
    5. the configuration file's ``default``;
    6. the public instance.
    """
    if base_url:
        return Resolved(check_url(base_url), None, "argument")
    if server:
        return _named(server, "argument", path)
    if url := environ.get(URL_ENV, "").strip():
        return Resolved(check_url(url), None, f"${URL_ENV}")
    if name := environ.get(SERVER_ENV, "").strip():
        return _named(name, f"${SERVER_ENV}", path)
    config = load_config(path)
    if config.default:
        return _named(config.default, "config file default", path, config)
    return Resolved(PUBLIC_URL, "public", "default")


def _named(
    name: str,
    source: str,
    path: Path | None,
    config: Config | None = None,
) -> Resolved:
    if _URL_RE.fullmatch(name.strip()):
        return Resolved(check_url(name), None, source)
    if name == "public":
        return Resolved(PUBLIC_URL, "public", source)
    config = config or load_config(path)
    if name not in config.servers:
        known = ", ".join(["public", *config.servers]) or "none"
        where = path or config_path()
        msg = (
            f"no server named {name!r} in {where} (known: {known}); add it with "
            f"`genetrek config add {name} <url>`"
        )
        raise ConfigError(msg)
    return Resolved(check_url(config.servers[name]), name, source)
