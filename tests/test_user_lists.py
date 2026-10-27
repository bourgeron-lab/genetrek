"""User gene lists: the codec, the stored record, share links and export files.

Everything here reaches the server from a browser or a file -- a damaged
store, a truncated link, a file from another database -- so most of these
pin that bad input becomes one ``UserListError`` and never a crash or a
silently wrong gene set.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from engineio.base_server import BaseServer

from genetrek.db.database import Database
from genetrek.db.gene_index import GeneIndex
from genetrek.schema import Roles, is_valid_field_name
from genetrek.services import user_lists as ul
from genetrek.services.search import filter_dataframe

NOW = datetime(2026, 10, 3, 14, 32, 5, tzinfo=ZoneInfo("Europe/Paris"))
HGNC = [f"HGNC:{n}" for n in (5, 37133, 24086, 10, 59305)]


def make(
    genes,
    name: str = "Search results",
    *,
    key: str = "hgnc_id",
    now: datetime = NOW,
) -> ul.UserList:
    return ul.create(name, genes, source="search", release="20260918", key=key, now=now)


class TestGeneCodec:
    @pytest.mark.parametrize(
        "genes",
        [
            [],
            ["HGNC:5"],
            HGNC,
            [str(n) for n in (1, 2, 3, 503538)],  # bare NCBI-style ids
            ["ENSG00000000003", "ENSG00000000005"],  # text fallback
            ["007", "7"],  # would not survive an integer round trip
            ["HGNC:1", "OTHER:2"],  # mixed prefixes
            ["GÈNE", "基因"],
        ],
    )
    def test_round_trips_exactly(self, genes):
        assert ul.decode_genes(ul.encode_genes(genes)) == frozenset(genes)

    def test_every_gene_in_the_id_space_round_trips(self):
        genes = [f"HGNC:{n}" for n in range(1, 60_000)]
        token = ul.encode_genes(genes)
        assert ul.decode_genes(token) == frozenset(genes)
        # A dense set packs to well under a byte per gene.
        assert len(token) < len(genes)

    def test_integer_keys_pack_smaller_than_text(self):
        genes = [f"HGNC:{n}" for n in range(1, 30_000, 7)]
        text = ul._b64encode(ul._deflate("\n".join(genes).encode()))
        assert len(ul.encode_genes(genes)) < len(text) / 2

    @pytest.mark.parametrize("token", ["", "!!!", "AAAA", "not base64 at all"])
    def test_garbage_is_refused(self, token):
        with pytest.raises(ul.UserListError):
            ul.decode_genes(token)

    def test_a_truncated_token_is_refused(self):
        token = ul.encode_genes(HGNC * 1)
        with pytest.raises(ul.UserListError):
            ul.decode_genes(token[: len(token) // 2])

    def test_an_inflation_bomb_is_refused(self):
        bomb = ul._b64encode(ul._deflate(b"\x01" + b"a" * (ul._MAX_INFLATED + 10)))
        with pytest.raises(ul.UserListError, match="too large"):
            ul.decode_genes(bomb)


class TestCreate:
    def test_needs_a_timezone(self):
        with pytest.raises(ValueError, match="timezone"):
            make(HGNC, now=datetime(2026, 1, 1))  # noqa: DTZ001

    def test_records_the_creators_offset(self):
        assert make(HGNC).created == "2026-10-03T14:32:05+02:00"

    def test_names_are_cleaned_and_bounded(self):
        assert make(HGNC, name="  a\tb\n c  ").name == "a b c"
        assert len(make(HGNC, name="x" * 500).name) == 120
        assert make(HGNC, name="   ").name == "Untitled list"

    def test_ids_are_fresh_and_columns_cannot_be_database_columns(self):
        a, b = make(HGNC), make(HGNC)
        assert a.id != b.id
        assert a.column == f"mylist:{a.id}"
        assert ul.is_list_column(a.column)
        # FIELD_NAME_RE forbids ':', so no parquet column can collide.
        assert not is_valid_field_name(a.column)


class TestLabel:
    def test_carries_the_creation_minute(self):
        assert ul.label(make(HGNC)) == "Search results · 2026-10-03 14:32"

    def test_is_shown_in_the_viewers_zone(self):
        tokyo = ZoneInfo("Asia/Tokyo")
        assert ul.label(make(HGNC), tokyo) == "Search results · 2026-10-03 21:32"

    def test_an_unparseable_time_falls_back_to_the_name(self):
        assert ul.label(replace(make(HGNC), created="yesterday")) == "Search results"


class TestStorage:
    def test_round_trips(self):
        lst = replace(make(HGNC), exported="2026-10-04T09:00:00+02:00")
        record = ul.to_storage(lst)
        assert record["n"] == len(HGNC)
        assert json.loads(json.dumps(record)) == record
        assert ul.from_storage(record) == lst

    @pytest.mark.parametrize(
        "change",
        [
            {"format": 2},
            {"id": "../../etc"},
            {"id": None},
            {"genes": None},
            {"genes": "!!!"},
        ],
    )
    def test_bad_records_are_refused(self, change):
        record = ul.to_storage(make(HGNC)) | change
        with pytest.raises(ul.UserListError):
            ul.from_storage(record)

    def test_one_bad_record_does_not_hide_the_rest(self):
        good = ul.to_storage(make(HGNC))
        lists, bad = ul.read_store([good, {"id": "nope"}, "junk", None])
        assert [lst.id for lst in lists] == [good["id"]]
        assert bad == 3

    @pytest.mark.parametrize("store", [None, {}, "x", 3])
    def test_a_store_that_is_not_a_list_reads_as_empty(self, store):
        assert ul.read_store(store) == ([], 0)


class TestShare:
    def test_round_trips_several_lists(self):
        lists = [make(HGNC, name="A"), make(HGNC[:2], name="B ✓")]
        token = ul.encode_share(lists, release="20260918", key="hgnc_id")
        shared = ul.decode_share(token)
        assert shared.release == "20260918"
        assert shared.key == "hgnc_id"
        assert [(s.name, s.created, s.genes) for s in shared.lists] == [
            (lst.name, lst.created, lst.genes) for lst in lists
        ]

    def test_nothing_shared_is_the_recipients_until_they_keep_it(self):
        lst = make(HGNC)
        (got,) = ul.decode_share(
            ul.encode_share([lst], release="r", key="hgnc_id"),
        ).lists
        assert got.saved is False
        assert got.source == "shared"
        assert got.id != lst.id

    def test_a_token_is_url_safe(self):
        token = ul.encode_share([make(HGNC)], release="r", key="hgnc_id")
        assert all(c.isalnum() or c in "-_" for c in token)

    def test_a_newer_format_is_refused(self):
        body = bytearray(
            ul._inflate(ul._b64decode(ul.encode_share([], release="r", key="k")))
        )
        body[0] = ul.FORMAT + 1
        with pytest.raises(ul.UserListError, match="newer"):
            ul.decode_share(ul._b64encode(ul._deflate(bytes(body))))

    def test_trailing_bytes_are_refused(self):
        body = ul._inflate(ul._b64decode(ul.encode_share([], release="r", key="k")))
        with pytest.raises(ul.UserListError):
            ul.decode_share(ul._b64encode(ul._deflate(body + b"x")))

    @pytest.mark.parametrize("cut", [1, 3, 10])
    def test_a_truncated_link_is_refused(self, cut):
        token = ul.encode_share([make(HGNC)], release="r", key="hgnc_id")
        with pytest.raises(ul.UserListError):
            ul.decode_share(token[:-cut])

    def test_the_url_uses_the_fragment(self):
        url = ul.share_url("https://genetrek.pasteur.fr/", "abc")
        assert url == "https://genetrek.pasteur.fr/#lists=abc"

    @pytest.mark.parametrize(
        ("length", "verdict"),
        [
            (100, "ok"),
            (ul.SAFE_URL_LENGTH, "ok"),
            (ul.SAFE_URL_LENGTH + 1, "long"),
            (ul.MAX_URL_LENGTH, "long"),
            (ul.MAX_URL_LENGTH + 1, "too_long"),
        ],
    )
    def test_length_classes(self, length, verdict):
        assert ul.share_class(length) == verdict


class TestExportFile:
    FRAME = pl.DataFrame(
        {"hgnc_id": ["HGNC:5", "HGNC:10", "HGNC:37133"], "sym": ["A1BG", "A2M", "X"]},
    )

    def export(self, lists) -> str:
        return ul.to_export_json(
            lists,
            df=self.FRAME,
            key="hgnc_id",
            symbol="sym",
            release="20260918",
            now=NOW,
        )

    def test_is_readable_and_names_fields_after_columns(self):
        payload = json.loads(self.export([make(["HGNC:10", "HGNC:5", "HGNC:99"])]))
        assert payload["key"] == "hgnc_id"
        (entry,) = payload["lists"]
        # Natural order, and a gene the frame lacks keeps a null symbol.
        assert entry["genes"] == [
            {"hgnc_id": "HGNC:5", "sym": "A1BG"},
            {"hgnc_id": "HGNC:10", "sym": "A2M"},
            {"hgnc_id": "HGNC:99", "sym": None},
        ]

    def test_round_trips(self):
        lists = [make(HGNC, name="A"), make(["HGNC:5"], name="B")]
        text = self.export(lists)
        assert ul.is_export_file(text)
        back = ul.from_export_json(text, key="hgnc_id", release="x", now=NOW)
        assert [(b.name, b.created, b.genes, b.source) for b in back] == [
            (a.name, a.created, a.genes, "import") for a in lists
        ]
        # The file is a backup: an imported list is not "never exported".
        assert all(b.exported == NOW.isoformat(timespec="seconds") for b in back)

    def test_a_file_for_another_key_is_refused(self):
        text = self.export([make(HGNC)])
        with pytest.raises(ul.UserListError, match="keyed on"):
            ul.from_export_json(text, key="ncbi_id", release="x", now=NOW)

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            "[]",
            '{"something": 1}',
            '{"genetrek_lists": 2, "key": "hgnc_id", "lists": []}',
            '{"genetrek_lists": 1, "key": "hgnc_id", "lists": "x"}',
            '{"genetrek_lists": 1, "key": "hgnc_id", "lists": [{"genes": 3}]}',
        ],
    )
    def test_bad_files_are_refused(self, text):
        with pytest.raises(ul.UserListError):
            ul.from_export_json(text, key="hgnc_id", release="x", now=NOW)

    def test_the_whole_database_fits_the_import_limit(self):
        """An export of every gene must import back: ~66 bytes a gene."""
        n = 45_083
        frame = pl.DataFrame(
            {
                "hgnc_id": [f"HGNC:{i}" for i in range(1, n + 1)],
                "sym": ["ABCDEF12"] * n,
            },
        )
        text = ul.to_export_json(
            [make(frame["hgnc_id"].to_list())],
            df=frame,
            key="hgnc_id",
            symbol="sym",
            release="r",
            now=NOW,
        )
        assert len(text.encode()) < ul.MAX_IMPORT_BYTES


class TestFileChunks:
    def test_reassembles_in_order(self):
        chunks = ul.FileChunks()
        assert chunks.add("u", 0, 3, "ab") is None
        assert chunks.add("u", 1, 3, "cd") is None
        assert chunks.add("u", 2, 3, "e") == "abcde"

    def test_a_single_chunk_is_whole(self):
        assert ul.FileChunks().add("u", 0, 1, "x") == "x"

    def test_a_new_upload_replaces_an_unfinished_one(self):
        chunks = ul.FileChunks()
        chunks.add("u", 0, 2, "old")
        assert chunks.add("v", 0, 1, "new") == "new"

    @pytest.mark.parametrize(
        ("upload", "part", "parts"), [("u", 2, 3), ("x", 1, 3), ("u", 1, 4)]
    )
    def test_out_of_order_chunks_are_refused(self, upload, part, parts):
        chunks = ul.FileChunks()
        chunks.add("u", 0, 3, "a")
        with pytest.raises(ul.UserListError, match="incomplete"):
            chunks.add(upload, part, parts, "b")

    def test_an_oversized_file_is_refused_as_it_arrives(self):
        chunks = ul.FileChunks()
        piece = "x" * ul.IMPORT_CHUNK_CHARS
        parts = ul.MAX_IMPORT_BYTES // ul.IMPORT_CHUNK_CHARS + 2

        def send_all() -> None:
            for part in range(parts):
                chunks.add("u", part, parts, piece)

        with pytest.raises(ul.UserListError, match="larger"):
            send_all()


class TestMessageLimits:
    """The browser sends lists and files over the websocket, never by upload."""

    def test_a_message_stays_under_the_websocket_cap(self):
        cap = BaseServer.__init__.__kwdefaults__ or {}
        default = cap.get("max_http_buffer_size", 1_000_000)
        assert default > ul.MAX_MESSAGE_BYTES
        # A chunk of three-byte characters still fits one message.
        assert ul.IMPORT_CHUNK_CHARS * 3 < ul.MAX_MESSAGE_BYTES

    def test_the_browser_uses_the_same_numbers(self):
        js = (Path(ul.__file__).parent.parent / "ui" / "js" / "my_lists.js").read_text(
            encoding="utf-8"
        )
        for name in ("MAX_MESSAGE_BYTES", "MAX_IMPORT_BYTES", "IMPORT_CHUNK_CHARS"):
            assert f"var {name} = {getattr(ul, name)};" in js, name

    def test_the_browser_never_uploads(self):
        """No `ui.upload` anywhere: it would spool large files to disk."""
        root = Path(ul.__file__).parent.parent
        for path in root.rglob("*.py"):
            assert "ui.upload(" not in path.read_text(encoding="utf-8"), path


class TestIdentifiersFromText:
    def test_a_pasted_list(self):
        assert ul.identifiers_from_text("SHANK3, MECP2\nCHD8;  SCN2A\n\n") == [
            "SHANK3",
            "MECP2",
            "CHD8",
            "SCN2A",
        ]

    def test_a_tsv_reads_its_first_column(self):
        text = "# genes\nsymbol\tscore\nSHANK3\t0.9\nMECP2 \t0.1\n"
        assert ul.identifiers_from_text(text) == ["symbol", "SHANK3", "MECP2"]

    def test_resolves_former_symbols_through_the_gene_index(self, resources, hgnc_file):
        index = GeneIndex.from_frame(Database.open(resources.root).frame).with_hgnc(
            hgnc_file,
        )
        report = index.resolve(ul.identifiers_from_text("GENE03\nKIAA0002\nNOPE\n"))
        resolved = set(report.resolved()[report.key].cast(pl.String).to_list())
        assert resolved == {"3", "2"}
        assert report.unmatched == ["NOPE"]


class TestAgainstTheDatabase:
    FRAME = pl.DataFrame(
        {"key": ["1", "2", "3", None], "flag": [True, False, True, False]}
    )

    def test_columns_are_null_free_booleans(self):
        lst = ul.UserList(
            "a" * 12, "x", "", "search", "", "key", frozenset({"1", "3", "9"})
        )
        out = self.FRAME.with_columns(ul.list_columns("key", [lst]))
        assert out[lst.column].to_list() == [True, False, True, False]
        assert out[lst.column].dtype == pl.Boolean

    def test_integer_keys_match_their_string_form(self):
        frame = pl.DataFrame({"key": pl.Series([1, 2, 3], dtype=pl.Int32)})
        lst = ul.UserList("b" * 12, "x", "", "search", "", "key", frozenset({"2"}))
        out = frame.with_columns(ul.list_columns("key", [lst]))
        assert out[lst.column].to_list() == [False, True, False]

    def test_missing_genes_are_counted_not_dropped(self):
        lst = ul.UserList("c" * 12, "x", "", "search", "", "key", frozenset({"1", "9"}))
        assert ul.missing(lst, frozenset({"1", "2", "3"})) == 1
        assert lst.genes == {"1", "9"}

    def test_a_list_filters_like_a_gene_list(self):
        lst = ul.UserList("d" * 12, "x", "", "search", "", "key", frozenset({"1", "2"}))
        frame = self.FRAME.with_columns(ul.list_columns("key", [lst]))
        out = filter_dataframe(
            frame,
            "",
            exact_match=True,
            selected_bool_cols=["flag", lst.column],
            results_merge="union",
            annotations_merge="intersection",
            selected_genome="",
            roles=_roles(),
        )
        assert out["key"].to_list() == ["1"]


def _roles() -> Roles:
    return Roles(primary_key="key")


def test_a_timestamp_with_a_fixed_offset_still_labels():
    lst = make(
        HGNC, now=datetime(2026, 1, 2, 3, 4, tzinfo=timezone(timedelta(hours=-5)))
    )
    assert ul.label(lst, ZoneInfo("UTC")) == "Search results · 2026-01-02 08:04"
    assert ul.label(lst) == "Search results · 2026-01-02 03:04"
    assert make(HGNC, now=datetime(2026, 1, 1, tzinfo=UTC)).created.endswith("+00:00")


def test_no_ui_handler_binds_its_first_parameter_by_default():
    """``lambda i=x: f(i)`` as a NiceGUI handler gets the *event* as ``i``.

    NiceGUI passes the event arguments to any handler that accepts a
    parameter, so the default never applies. The manager's per-row buttons did
    exactly this and silently did nothing; ``lambda _, i=x: f(i)`` is the form.
    """
    root = Path(ul.__file__).parent.parent / "ui"
    offenders = [
        f"{path.name}:{lineno}"
        for path in root.rglob("*.py")
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"lambda [a-z_]+=", line)
    ]
    assert not offenders
