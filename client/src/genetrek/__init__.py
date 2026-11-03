"""GeneTrek from Python: the client for GeneTrek's API, and the ``genetrek`` command."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("genetrek")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0+unknown"
