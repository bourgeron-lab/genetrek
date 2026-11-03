"""The gene sets a comparison is made of.

A set is one of: a database gene list (by column name or alias), identifiers
of any kind, a text or TSV file of identifiers, or one list of a My lists
export file or share link. Each becomes one entry of ``POST /api/compare``'s
``sets``, which is also what a downloaded ``request.json`` holds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from genetrek.lists import SHARE_PARAM, identifiers_from_text, is_export_file

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class Set:
    """One set of a comparison. Build it with one of the class methods."""

    spec: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def field(cls, name: str, *, label: str | None = None) -> Set:
        """A database gene list (a Boolean column), by name or alias."""
        return cls(_labelled({"field": name}, label))

    @classmethod
    def genes(
        cls,
        ids: Iterable[object],
        *,
        label: str | None = None,
        id_type: str = "auto",
    ) -> Set:
        """Identifiers of any kind -- symbols, HGNC, Ensembl, NCBI, OMIM, mixed."""
        values = [str(v).strip() for v in ids if v is not None and str(v).strip()]
        return cls(_labelled({"genes": values, "id_type": id_type}, label))

    @classmethod
    def file(
        cls,
        path: str | Path,
        *,
        label: str | None = None,
        id_type: str = "auto",
        index: int = 0,
    ) -> Set:
        """A file of identifiers, one per line (first column of a TSV).

        A My lists export file is read as one of its lists (``index``), as
        :meth:`lists` does.
        """
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if is_export_file(text):
            return cls.lists(path, index=index, label=label)
        return cls.genes(
            identifiers_from_text(text),
            label=label or path.stem,
            id_type=id_type,
        )

    @classmethod
    def lists(
        cls,
        source: str | Path,
        *,
        index: int = 0,
        label: str | None = None,
    ) -> Set:
        """One list of a My lists export file, or of a share link or token.

        The link's genes stay in the request body: ``#lists=`` is read here,
        and only the token is sent.
        """
        text = str(source)
        marker = f"#{SHARE_PARAM}="
        if marker in text:
            token = text.split(marker, 1)[1].split("&", 1)[0]
            return cls(_labelled({"share": token, "list_index": index}, label))
        path = Path(source)
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return cls(_labelled({"lists_file": payload, "list_index": index}, label))
        # A bare token, as copied from after `#lists=`.
        return cls(_labelled({"share": text.strip(), "list_index": index}, label))

    @classmethod
    def of(cls, value: Set | str | dict[str, Any] | Iterable[object]) -> Set:
        """Coerce what :meth:`GeneTrek.compare` accepts: a Set, a column name,
        a request ``sets`` entry, or identifiers."""
        if isinstance(value, Set):
            return value
        if isinstance(value, str):
            return cls.field(value)
        if isinstance(value, dict):
            return cls(dict(value))
        return cls.genes(value)

    def to_request(self) -> dict[str, Any]:
        """The ``POST /api/compare`` ``sets`` entry."""
        return dict(self.spec)


def _labelled(spec: dict[str, Any], label: str | None) -> dict[str, Any]:
    if label:
        spec["label"] = label
    return spec
