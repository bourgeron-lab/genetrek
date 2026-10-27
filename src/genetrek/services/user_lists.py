"""User gene lists: the model, its compact codec, share links and export files.

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

import base64
import binascii
import json
import re
import secrets
import zlib
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import polars as pl

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from zoneinfo import ZoneInfo

#: Version of the stored record and of the share token. Bumped only by a change
#: an older reader would misread; a newer value is refused, never guessed at.
FORMAT = 1

#: Prefix of a user list's column id. ``schema.FIELD_NAME_RE`` forbids ``:``,
#: so no parquet column can ever be mistaken for one, and it differs from the
#: column selector's own ``cat:``/``sub:`` node prefixes.
COLUMN_PREFIX = "mylist:"

#: Where a list came from. Shown in the manager; a shared list keeps
#: ``shared`` even once saved, which is how a recipient tells it apart.
Source = Literal["search", "paste", "import", "shared", "compare"]
SOURCES: tuple[str, ...] = ("search", "paste", "import", "shared", "compare")

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

#: Decompressed size refused by the decoders, so a crafted link cannot make
#: the server inflate a payload without bound. Every gene in the database
#: packs to well under this.
_MAX_INFLATED = 4 * 1024 * 1024

_INT_KIND = 0
_TEXT_KIND = 1
_INT_KEY = re.compile(r"^(?:([A-Za-z][A-Za-z0-9_]*):)?(0|[1-9][0-9]*)$")
_FILE_MARKER = "genetrek_lists"


class UserListError(ValueError):
    """A stored record, share token or file that cannot be read.

    One exception for every way input can be wrong -- damaged, truncated,
    from a newer GeneTrek, or for another database -- because all of it
    arrives from a browser or a file, and the caller's only job is to say so.
    """


@dataclass(frozen=True)
class UserList:
    """One saved gene set.

    Attributes:
        id: Random, 12 hex characters; also the column id's suffix.
        name: Editable label. Never unique on its own -- ``label`` adds the
            creation time, which is what tells two same-named lists apart.
        created: ISO-8601 with the creator's UTC offset. Immutable.
        source: Where it came from, one of :data:`SOURCES`.
        release: The database release it was made against.
        key: The key column its genes are values of.
        genes: Key values, as strings.
        exported: When it was last exported, or ``None`` -- the "not backed
            up" marker.
        saved: ``False`` for a shared list used for this visit only: it lives
            in the tab and is never written to the browser's storage.
    """

    id: str
    name: str
    created: str
    source: str
    release: str
    key: str
    genes: frozenset[str]
    exported: str | None = None
    saved: bool = True

    @property
    def column(self) -> str:
        """The id this list has as a table column."""
        return f"{COLUMN_PREFIX}{self.id}"


def new_id() -> str:
    """A fresh list id."""
    return secrets.token_hex(6)


def is_list_column(column: str) -> bool:
    """Whether a column id names a user list rather than a database column."""
    return column.startswith(COLUMN_PREFIX)


def create(  # noqa: PLR0913
    name: str,
    genes: Iterable[object],
    *,
    source: str,
    release: str,
    key: str,
    now: datetime,
) -> UserList:
    """A new list. ``now`` must be timezone-aware: it is the user's clock."""
    if now.tzinfo is None:
        msg = "a list's creation time needs a timezone"
        raise ValueError(msg)
    return UserList(
        id=new_id(),
        name=clean_name(name) or "Untitled list",
        created=now.isoformat(timespec="seconds"),
        source=source if source in SOURCES else "import",
        release=release,
        key=key,
        genes=frozenset(str(g) for g in genes if g is not None),
    )


def clean_name(name: object) -> str:
    """A list name, trimmed and bounded, with control characters removed."""
    # Control characters become spaces, so a pasted tab still separates words.
    text = "".join(ch if ch.isprintable() else " " for ch in str(name or ""))
    return " ".join(text.split())[:120]


def created_at(user_list: UserList) -> datetime | None:
    """The creation time, or ``None`` when the stored value is not ISO-8601."""
    try:
        return datetime.fromisoformat(user_list.created)
    except ValueError:
        return None


def label(user_list: UserList, tz: ZoneInfo | None = None) -> str:
    """``name · YYYY-MM-DD HH:MM``, the creation time in the viewer's zone."""
    when = created_at(user_list)
    if when is None:
        return user_list.name
    if tz is not None and when.tzinfo is not None:
        when = when.astimezone(tz)
    return f"{user_list.name} · {when:%Y-%m-%d %H:%M}"


# -- gene codec ---------------------------------------------------------------


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        msg = "not a base64url token"
        raise UserListError(msg)
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        msg = "damaged token"
        raise UserListError(msg) from exc


def _deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def _inflate(data: bytes) -> bytes:
    decompressor = zlib.decompressobj(-15)
    try:
        out = decompressor.decompress(data, _MAX_INFLATED)
    except zlib.error as exc:
        msg = "damaged token"
        raise UserListError(msg) from exc
    if decompressor.unconsumed_tail:
        msg = "token too large"
        raise UserListError(msg)
    if not decompressor.eof:
        msg = "truncated token"
        raise UserListError(msg)
    return out


def _varint(value: int, out: bytearray) -> None:
    while value >= 0x80:  # noqa: PLR2004
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)


class _Reader:
    """Sequential reads over a packed body, failing as :class:`UserListError`."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def done(self) -> bool:
        return self.pos >= len(self.data)

    def byte(self) -> int:
        if self.done():
            msg = "truncated token"
            raise UserListError(msg)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def varint(self) -> int:
        result = shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7
            if shift > 63:  # noqa: PLR2004
                msg = "damaged token"
                raise UserListError(msg)

    def raw(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            msg = "truncated token"
            raise UserListError(msg)
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def text(self) -> str:
        try:
            return self.raw(self.varint()).decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = "damaged token"
            raise UserListError(msg) from exc


def _text(value: str, out: bytearray) -> None:
    data = value.encode("utf-8")
    _varint(len(data), out)
    out.extend(data)


def _pack_genes(genes: Iterable[str]) -> bytes:
    """The uncompressed gene body: integer deltas when possible, else text."""
    values = sorted(set(genes))
    prefix: str | None = None
    numbers: list[int] = []
    for value in values:
        match = _INT_KEY.match(value)
        if match is None or (prefix is not None and (match[1] or "") != prefix):
            break
        prefix = match[1] or ""
        numbers.append(int(match[2]))
    else:
        out = bytearray([_INT_KIND])
        _text(prefix or "", out)
        previous = 0
        for number in sorted(numbers):
            _varint(number - previous, out)
            previous = number
        return bytes(out)
    out = bytearray([_TEXT_KIND])
    out.extend("\n".join(values).encode("utf-8"))
    return bytes(out)


def _unpack_genes(body: bytes) -> frozenset[str]:
    reader = _Reader(body)
    kind = reader.byte()
    if kind == _INT_KIND:
        prefix = reader.text()
        head = f"{prefix}:" if prefix else ""
        genes: set[str] = set()
        current = 0
        while not reader.done():
            current += reader.varint()
            genes.add(f"{head}{current}")
        return frozenset(genes)
    if kind == _TEXT_KIND:
        try:
            text = reader.raw(len(body) - 1).decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = "damaged token"
            raise UserListError(msg) from exc
        return frozenset(g for g in text.split("\n") if g)
    msg = "unknown gene encoding"
    raise UserListError(msg)


def encode_genes(genes: Iterable[str]) -> str:
    """Pack a gene set into a base64url token."""
    return _b64encode(_deflate(_pack_genes(genes)))


def decode_genes(token: str) -> frozenset[str]:
    """Unpack :func:`encode_genes`. Raises :class:`UserListError` on bad input."""
    return _unpack_genes(_inflate(_b64decode(token)))


# -- the browser's store ------------------------------------------------------


def to_storage(user_list: UserList) -> dict[str, Any]:
    """The record the browser keeps for one list."""
    return {
        "format": FORMAT,
        "id": user_list.id,
        "name": user_list.name,
        "created": user_list.created,
        "source": user_list.source,
        "release": user_list.release,
        "key": user_list.key,
        "n": len(user_list.genes),
        "genes": encode_genes(user_list.genes),
        "exported": user_list.exported,
    }


def from_storage(record: Mapping[str, Any]) -> UserList:
    """Read one stored record. Raises :class:`UserListError` on a bad one."""
    if not isinstance(record, dict):
        msg = "not a list record"
        raise UserListError(msg)
    if record.get("format", FORMAT) != FORMAT:
        msg = "saved by a newer GeneTrek"
        raise UserListError(msg)
    list_id = record.get("id")
    if not isinstance(list_id, str) or not re.fullmatch(r"[0-9a-f]{12}", list_id):
        msg = "bad list id"
        raise UserListError(msg)
    genes = record.get("genes")
    if not isinstance(genes, str):
        msg = "no genes"
        raise UserListError(msg)
    exported = record.get("exported")
    return UserList(
        id=list_id,
        name=clean_name(record.get("name")) or "Untitled list",
        created=str(record.get("created") or ""),
        source=str(record.get("source") or "import"),
        release=str(record.get("release") or ""),
        key=str(record.get("key") or ""),
        genes=decode_genes(genes),
        exported=str(exported) if exported else None,
    )


def read_store(records: object) -> tuple[list[UserList], int]:
    """Every readable record, and how many were not.

    A damaged record is skipped rather than failing the whole store: one bad
    entry -- hand-edited, or from a newer version -- must not hide the rest.
    """
    if not isinstance(records, list):
        return [], 0
    lists: list[UserList] = []
    bad = 0
    for record in records:
        try:
            lists.append(from_storage(record))
        except UserListError:
            bad += 1
    return lists, bad


# -- share links --------------------------------------------------------------


def encode_share(lists: Sequence[UserList], *, release: str, key: str) -> str:
    """One URL-fragment token carrying ``lists``."""
    out = bytearray([FORMAT])
    _text(release, out)
    _text(key, out)
    _varint(len(lists), out)
    for user_list in lists:
        _text(user_list.name, out)
        _text(user_list.created, out)
        body = _pack_genes(user_list.genes)
        _varint(len(body), out)
        out.extend(body)
    return _b64encode(_deflate(bytes(out)))


@dataclass(frozen=True)
class SharedLists:
    """What a share token carried, before the recipient decides on it."""

    release: str
    key: str
    lists: tuple[UserList, ...]


def decode_share(token: str) -> SharedLists:
    """Read a share token. Raises :class:`UserListError` on any bad token.

    The lists come back with fresh ids, ``source="shared"`` and
    ``saved=False``: nothing is the recipient's until they choose to keep it.
    """
    reader = _Reader(_inflate(_b64decode(token)))
    version = reader.byte()
    if version != FORMAT:
        msg = "made by a newer GeneTrek"
        raise UserListError(msg)
    release = reader.text()
    key = reader.text()
    count = reader.varint()
    lists: list[UserList] = []
    for _ in range(count):
        name = clean_name(reader.text()) or "Shared list"
        created = reader.text()
        genes = _unpack_genes(reader.raw(reader.varint()))
        lists.append(
            UserList(
                id=new_id(),
                name=name,
                created=created,
                source="shared",
                release=release,
                key=key,
                genes=genes,
                saved=False,
            ),
        )
    if not reader.done():
        msg = "damaged token"
        raise UserListError(msg)
    return SharedLists(release=release, key=key, lists=tuple(lists))


#: Fragment parameter carrying a share token: ``/#lists=<token>``.
SHARE_PARAM = "lists"


def share_url(base: str, token: str) -> str:
    """The link for a token. ``base`` is the page URL as the browser sees it."""
    return f"{base}#{SHARE_PARAM}={token}"


ShareClass = Literal["ok", "long", "too_long"]


def share_class(url_length: int) -> ShareClass:
    """Whether a link of this length can be offered, and with what warning."""
    if url_length <= SAFE_URL_LENGTH:
        return "ok"
    if url_length <= MAX_URL_LENGTH:
        return "long"
    return "too_long"


# -- export files -------------------------------------------------------------


def to_export_json(  # noqa: PLR0913
    lists: Sequence[UserList],
    *,
    df: pl.DataFrame,
    key: str,
    symbol: str | None,
    release: str,
    now: datetime,
) -> str:
    """A human-readable file holding ``lists``, symbols included.

    The symbol rides along for the reader's benefit only, under its column's
    name -- import keys on ``key``, because symbols change between releases.
    """
    symbols: dict[str, str] = {}
    if symbol is not None and symbol in df.columns:
        wanted = {g for user_list in lists for g in user_list.genes}
        frame = df.select(
            pl.col(key).cast(pl.String).alias("_key"),
            pl.col(symbol).cast(pl.String).alias("_symbol"),
        ).filter(pl.col("_key").is_in(list(wanted)))
        symbols = dict(zip(frame["_key"], frame["_symbol"], strict=True))
    payload = {
        _FILE_MARKER: FORMAT,
        "exported": now.isoformat(timespec="seconds"),
        "database_release": release,
        "key": key,
        "note": (
            "Gene lists exported from GeneTrek. Membership only; import this "
            "file into GeneTrek's My lists to use it again."
        ),
        "lists": [
            {
                "name": user_list.name,
                "created": user_list.created,
                "source": user_list.source,
                "release": user_list.release,
                "genes": [
                    # Fields named after the columns, as `key` already is.
                    {key: gene, **({symbol: symbols.get(gene)} if symbol else {})}
                    for gene in _natural_order(user_list.genes)
                ],
            }
            for user_list in lists
        ],
    }
    return json.dumps(payload, indent=1, ensure_ascii=False)


def _natural_order(genes: Iterable[str]) -> list[str]:
    """Keys sorted so ``HGNC:5`` precedes ``HGNC:10``."""

    def sort_key(gene: str) -> tuple[str, int, str]:
        match = _INT_KEY.match(gene)
        if match is None:
            return ("~", 0, gene)
        return (match[1] or "", int(match[2]), gene)

    return sorted(genes, key=sort_key)


def is_export_file(text: str) -> bool:
    """Whether ``text`` looks like :func:`to_export_json` output."""
    return f'"{_FILE_MARKER}"' in text[:200]


def from_export_json(
    text: str,
    *,
    key: str,
    release: str,
    now: datetime,
) -> list[UserList]:
    """Read an export file into new lists for this database.

    Raises :class:`UserListError` when the file is not one, is from a newer
    GeneTrek, or keys on another column -- a list of ``ncbi_id`` values read
    as ``hgnc_id`` values would match nothing, silently.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = "not a GeneTrek lists file"
        raise UserListError(msg) from exc
    if not isinstance(payload, dict) or _FILE_MARKER not in payload:
        msg = "not a GeneTrek lists file"
        raise UserListError(msg)
    if payload[_FILE_MARKER] != FORMAT:
        msg = "made by a newer GeneTrek"
        raise UserListError(msg)
    file_key = payload.get("key")
    if file_key != key:
        msg = f"lists keyed on {file_key!r}, this database keys on {key!r}"
        raise UserListError(msg)
    entries = payload.get("lists")
    if not isinstance(entries, list):
        msg = "no lists in the file"
        raise UserListError(msg)
    lists: list[UserList] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("genes"), list):
            msg = "damaged list in the file"
            raise UserListError(msg)
        genes = [
            gene.get(key) if isinstance(gene, dict) else gene for gene in entry["genes"]
        ]
        user_list = create(
            str(entry.get("name") or "Imported list"),
            (g for g in genes if isinstance(g, str | int)),
            source="import",
            release=str(
                entry.get("release") or payload.get("database_release") or release
            ),
            key=key,
            now=now,
        )
        created = entry.get("created")
        if isinstance(created, str) and created:
            user_list = replace(user_list, created=created)
        # The file it came from is a backup, so it is not "never exported".
        exported = payload.get("exported")
        user_list = replace(
            user_list,
            exported=exported if isinstance(exported, str) and exported else None,
        )
        lists.append(user_list)
    return lists


def identifiers_from_text(text: str) -> list[str]:
    """The identifiers in a pasted list or a plain text/TSV/CSV file.

    One per line, first column -- so a spreadsheet export with extra columns
    still reads. Blank lines and ``#`` comments are skipped. A pasted line of
    several space- or comma-separated identifiers contributes all of them.
    """
    values: list[str] = []
    rows = text.splitlines()
    tabular = any("\t" in row for row in rows)
    for row in rows:
        line = row.strip()
        if not line or line.startswith("#"):
            continue
        if tabular:
            first = line.split("\t", 1)[0].strip()
            if first:
                values.append(first)
        else:
            values.extend(v for v in re.split(r"[,;\s]+", line) if v)
    return values


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
