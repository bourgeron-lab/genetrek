"""User gene lists on the website: the browser limits, labels and table columns.

The model, the codec, share links and export files live in :mod:`genetrek.lists`
(the client package), re-exported here so both read lists the same way.

A user list is a *frozen gene set* -- membership only, nothing quantitative --
kept in the visitor's browser (``localStorage``) and nowhere else. The server
sees a list only while a tab that uses it is open, holds it in that
connection's memory, and never writes it anywhere. Everything in this module
is therefore a pure transformation: no I/O, no UI, no logging of contents.

Three representations, one codec:

* **Stored** (:func:`to_storage` / :func:`from_storage`): what the browser
  keeps, one JSON object per list with the genes packed by
  :func:`encode_genes`. Packing matters because every page load sends the
  whole store to the server.
* **Shared** (:func:`encode_share` / :func:`decode_share`): one URL-fragment
  token carrying several lists. The fragment never reaches the server, so a
  shared list is not in any access log.
* **Exported** (:func:`to_export_json` / :func:`from_export_json`): a
  human-readable file, the only copy that survives the browser's storage
  being cleared.

The codec sorts the integer part of each key (``HGNC:5`` -> 5), stores the
gaps as varints and deflates the result: about one URL character per gene on
the real database, four times smaller than the symbols. Keys that are not
``PREFIX:int`` -- or that would not survive the round trip exactly, such as
``"007"`` -- fall back to deflated text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import polars as pl
from genetrek.lists import (
    COLUMN_PREFIX,
    FORMAT,
    SHARE_PARAM,
    SOURCES,
    SharedLists,
    Source,
    UserList,
    UserListError,
    clean_name,
    create,
    created_at,
    decode_genes,
    decode_share,
    encode_genes,
    encode_share,
    from_export_json,
    from_storage,
    identifiers_from_text,
    is_export_file,
    new_id,
    read_store,
    share_url,
    to_export_json,
    to_storage,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from zoneinfo import ZoneInfo

__all__ = [
    "COLUMN_PREFIX",
    "FORMAT",
    "IMPORT_CHUNK_CHARS",
    "MAX_IMPORT_BYTES",
    "MAX_MESSAGE_BYTES",
    "MAX_URL_LENGTH",
    "SAFE_URL_LENGTH",
    "SHARE_PARAM",
    "SOURCES",
    "FileChunks",
    "ShareClass",
    "SharedLists",
    "Source",
    "UserList",
    "UserListError",
    "clean_name",
    "create",
    "created_at",
    "decode_genes",
    "decode_share",
    "encode_genes",
    "encode_share",
    "from_export_json",
    "from_storage",
    "identifiers_from_text",
    "is_export_file",
    "is_list_column",
    "label",
    "list_columns",
    "missing",
    "new_id",
    "read_store",
    "share_class",
    "share_url",
    "to_export_json",
    "to_storage",
]

#: A share link up to this many characters survives mail and chat clients.
SAFE_URL_LENGTH = 2000
#: Beyond this a link is not offered at all: the file is the only way.
MAX_URL_LENGTH = 8000

#: Largest message the browser sends in one piece: a part of the stored lists
#: or a chunk of an imported file. The websocket refuses any message over
#: engine.io's ``max_http_buffer_size`` (1,000,000 bytes) -- and drops the
#: connection doing so -- hence a margin below it. ``ui/js/my_lists.js``
#: repeats the number and ``tests/test_user_lists.py`` pins the two together.
MAX_MESSAGE_BYTES = 900_000

#: Largest file an import accepts. Imports are read *in the browser* and their
#: text sent over the page's websocket in chunks, never as an HTTP upload: a
#: multipart upload above 1 MiB is spooled to a temp file on the server's disk
#: (Starlette, and ``ui.upload`` with it), and ``ui.upload``'s size cap is only
#: enforced client-side. An export of every gene in the database is ~3 MB.
MAX_IMPORT_BYTES = 8_000_000

#: Characters per file chunk. A JavaScript character is at most three UTF-8
#: bytes (an astral character is two characters, four bytes), so a chunk this
#: long always fits in one message.
IMPORT_CHUNK_CHARS = 250_000


def is_list_column(column: str) -> bool:
    """Whether a column id names a user list rather than a database column."""
    return column.startswith(COLUMN_PREFIX)


def label(user_list: UserList, tz: ZoneInfo | None = None) -> str:
    """``name · YYYY-MM-DD HH:MM``, the creation time in the viewer's zone."""
    when = created_at(user_list)
    if when is None:
        return user_list.name
    if tz is not None and when.tzinfo is not None:
        when = when.astimezone(tz)
    return f"{user_list.name} · {when:%Y-%m-%d %H:%M}"


ShareClass = Literal["ok", "long", "too_long"]


def share_class(url_length: int) -> ShareClass:
    """Whether a link of this length can be offered, and with what warning."""
    if url_length <= SAFE_URL_LENGTH:
        return "ok"
    if url_length <= MAX_URL_LENGTH:
        return "long"
    return "too_long"


class FileChunks:
    """Reassembles a file the browser sends in chunks.

    One file at a time: a new upload id discards an unfinished one, which is
    what a second import started before the first finished should do. The
    size is checked as chunks arrive, so an oversized file is refused without
    being held whole.
    """

    def __init__(self) -> None:
        self._upload = ""
        self._parts: list[str] = []
        self._expected = 0
        self._size = 0

    def add(self, upload: str, part: int, parts: int, text: str) -> str | None:
        """Take one chunk; return the whole text once the last one arrives.

        Raises :class:`UserListError` on a file over :data:`MAX_IMPORT_BYTES`
        or chunks out of order.
        """
        if part == 0:
            self._upload, self._parts, self._expected, self._size = upload, [], parts, 0
        if (
            upload != self._upload
            or part != len(self._parts)
            or parts != self._expected
            or parts < 1
        ):
            self._upload = ""
            msg = "the file arrived incomplete; try again"
            raise UserListError(msg)
        self._size += len(text.encode("utf-8"))
        if self._size > MAX_IMPORT_BYTES:
            self._upload, self._parts = "", []
            msg = f"larger than {MAX_IMPORT_BYTES // 1_000_000} MB"
            raise UserListError(msg)
        self._parts.append(text)
        if len(self._parts) < self._expected:
            return None
        whole = "".join(self._parts)
        self._upload, self._parts = "", []
        return whole


# -- use against the database -------------------------------------------------


def list_columns(key: str, lists: Iterable[UserList]) -> list[pl.Expr]:
    """One Boolean column expression per list, named by its column id.

    Null-free by construction: a gene is in the list or it is not, which is
    what lets a list take part in search, enrichment and later set operations
    exactly as a database gene list does.
    """
    keys = pl.col(key).cast(pl.String)
    return [
        keys.is_in(sorted(user_list.genes))
        .fill_null(value=False)
        .alias(user_list.column)
        for user_list in lists
    ]


def missing(user_list: UserList, database_keys: frozenset[str]) -> int:
    """How many of a list's genes the current database does not have.

    Kept, not dropped: they are genes the list was made with, withdrawn or
    merged since, and an export should still carry them.
    """
    return len(user_list.genes - database_keys)
