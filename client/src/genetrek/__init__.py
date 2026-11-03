"""GeneTrek from Python: the client for GeneTrek's API, and the ``genetrek`` command.

>>> from genetrek import GeneTrek, Set
>>> gt = GeneTrek()
>>> gt.gene_list("hc_ndd")[:3]
>>> gt.compare([Set.field("hc_ndd"), Set.genes(["SHANK3", "CHD8"], label="mine")])
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("genetrek")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0+unknown"

# After __version__: the client reads it.
from genetrek.client import GeneTrek, UnresolvedWarning
from genetrek.errors import (
    AmbiguousGene,
    ApiError,
    ConfigError,
    ConnectionFailed,
    GeneTrekError,
    NotAGeneList,
    ServerTooOld,
    UnknownField,
    UnknownGene,
)
from genetrek.models import Comparison
from genetrek.sets import Set

__all__ = [
    "AmbiguousGene",
    "ApiError",
    "Comparison",
    "ConfigError",
    "ConnectionFailed",
    "GeneTrek",
    "GeneTrekError",
    "NotAGeneList",
    "ServerTooOld",
    "Set",
    "UnknownField",
    "UnknownGene",
    "UnresolvedWarning",
    "__version__",
]
