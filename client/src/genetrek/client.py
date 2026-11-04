"""The GeneTrek client: one object per server, one method per question."""

from __future__ import annotations

import io
import json
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

import httpx
import polars as pl

from genetrek import __version__
from genetrek.config import Resolved, resolve_server
from genetrek.errors import (
    AmbiguousGene,
    ConnectionFailed,
    ServerTooOld,
    UnknownGene,
    from_response,
)
from genetrek.models import Comparison, frame
from genetrek.sets import Set

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from types import TracebackType
    from typing import Self

#: The most identifiers one request carries; longer lists are sent in chunks.
MAX_IDS_PER_REQUEST = 50_000

#: Retries on a 503 or a connection error, with these pauses (seconds).
_BACKOFF = (0.5, 1.0, 2.0)

Version = tuple[int, int]


class UnresolvedWarning(UserWarning):
    """Some identifiers matched no gene, or several."""


class GeneTrek:
    """A client for one GeneTrek server.

    >>> gt = GeneTrek()                       # the public instance
    >>> gt = GeneTrek(server="dev")           # a server named in your config
    >>> gt = GeneTrek("http://localhost:8000")
    >>> hc = gt.gene_list("hc_ndd")
    >>> table = gt.genes(["SHANK3", "CHD8"], columns=["loeuf", "hc_ndd"])

    Tables come back as polars DataFrames (``.to_pandas()`` with the
    ``genetrek[pandas]`` extra). Nulls are kept: a null is "not measured",
    never "no".

    Your genes travel in request bodies only, never in a URL, and nothing is
    written to disk unless you ask for a file.

    Args:
        base_url: The server's address. Overrides everything else.
        server: A server named in the configuration file, or ``"public"``.
        timeout: Seconds to wait for each answer.
        user_agent: Appended to the ``User-Agent``, e.g. your tool's name.
        http: An ``httpx.Client`` to use as is (its ``base_url`` is the
            server) -- for tests, or for proxies and certificates of your own.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        server: str | None = None,
        timeout: float = 60.0,
        user_agent: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        agent = f"genetrek-python/{__version__}"
        if user_agent:
            agent = f"{agent} {user_agent}"
        if http is not None:
            self.server = Resolved(str(http.base_url).rstrip("/"), None, "argument")
            self._http = http
            self._owns_http = False
        else:
            self.server = resolve_server(base_url, server)
            self._http = httpx.Client(
                base_url=self.server.url,
                timeout=timeout,
                headers={"User-Agent": agent},
                follow_redirects=True,
            )
            self._owns_http = True
        self._health: dict[str, Any] | None = None
        self._booleans: list[str] | None = None

    # -- plumbing -----------------------------------------------------------

    @property
    def url(self) -> str:
        """The server's address."""
        return self.server.url

    def close(self) -> None:
        """Close the connection (only one this client opened)."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"GeneTrek({self.server.describe()})"

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
        """One request, retried on a 503 or a connection error only."""
        attempts = len(_BACKOFF) + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if last:
                    msg = f"cannot reach {self.url}: {exc}"
                    raise ConnectionFailed(msg) from exc
                time.sleep(_BACKOFF[attempt])
                continue
            if response.status_code == 503 and not last:  # noqa: PLR2004
                time.sleep(_BACKOFF[attempt])
                continue
            break
        if response.is_error:
            try:
                body: object = response.json()
            except ValueError:
                body = response.text
            raise from_response(response.status_code, body)
        return response

    def _get(self, path: str, **params: Any) -> Any:  # noqa: ANN401
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, params=clean).json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:  # noqa: ANN401
        return self._request("POST", path, json=body).json()

    def api_version(self) -> Version:
        """The server's API version; a server that does not say is 1.0."""
        text = str(self.info().get("api_version") or "1.0")
        major, _, minor = text.partition(".")
        return int(major), int(minor or 0)

    def _require(self, needed: Version, what: str) -> None:
        found = self.api_version()
        if found < needed:
            raise ServerTooOld(
                self.url,
                ".".join(map(str, found)),
                ".".join(map(str, needed)),
                what,
            )

    # -- the server ---------------------------------------------------------

    def info(self) -> dict[str, Any]:
        """``/api/health``: the server's state, release and API version.

        Read once and remembered for the life of this client.
        """
        if self._health is None:
            self._health = dict(self._get("/api/health"))
        return {
            **self._health,
            "server": self.url,
            "server_source": self.server.describe(),
            "client_version": __version__,
        }

    def release(self) -> str | None:
        """The database release (``None`` from a server older than API 1.1)."""
        return self.info().get("database_release")

    # -- columns ------------------------------------------------------------

    def fields(
        self,
        *,
        category: str | None = None,
        subcategory: str | None = None,
        value_type: str | None = None,
        include_hidden: bool = False,
        include_counts: bool = False,
    ) -> pl.DataFrame:
        """The columns, in the order the table shows them, with their metadata."""
        body = self._get(
            "/api/fields",
            category=category,
            subcategory=subcategory,
            value_type=value_type,
            include_hidden=include_hidden,
            include_counts=include_counts,
        )
        return frame(body["fields"])

    def field(self, name: str) -> dict[str, Any]:
        """One column's metadata and coverage. ``name`` may be an alias."""
        return dict(self._get(f"/api/fields/{_segment(name)}"))

    def values(
        self,
        field: str,
        *,
        id_type: str = "symbol",
        drop_empty: bool = False,
    ) -> pl.DataFrame:
        """One column as ``gene``/``value`` pairs; nulls are "not measured"."""
        body = self._get(
            f"/api/fields/{_segment(field)}/genes",
            id_type=id_type,
            drop_empty=drop_empty,
        )
        pairs = body["genes"]
        if not pairs:
            return pl.DataFrame({"gene": [], "value": []})
        return frame(pairs)

    def gene_list(self, field: str, *, id_type: str = "symbol") -> list[str]:
        """A gene list's members (a Boolean column's True genes)."""
        return self.values(field, id_type=id_type, drop_empty=True)["gene"].to_list()

    def universe(self, field: str, *, id_type: str = "symbol") -> list[str]:
        """The genes a column measured: the background to test it against."""
        body = self._get(f"/api/fields/{_segment(field)}/universe", id_type=id_type)
        return list(body["genes"])

    # -- genes --------------------------------------------------------------

    def genes(
        self,
        ids: Iterable[object],
        *,
        columns: Sequence[str] | None = None,
        from_: str = "auto",
        include_hidden: bool = False,
    ) -> pl.DataFrame:
        """Many genes at once, with the columns asked for (API 1.1).

        One row per gene found, in the order of ``ids``, with ``input`` and
        ``matched_by`` saying how each was recognised, then the identifier
        columns and ``columns`` (every visible column when unset). Inputs that
        match no gene, or several, are left out and reported in an
        :class:`UnresolvedWarning`.
        """
        self._require((1, 1), "genes()")
        values = _strings(ids)
        if not values:
            return pl.DataFrame()
        rows: list[dict[str, Any]] = []
        not_found: list[str] = []
        ambiguous: list[str] = []
        for chunk in _chunks(values):
            body = self._post(
                "/api/genes",
                {
                    "ids": chunk,
                    "from": from_,
                    "columns": list(columns) if columns else None,
                    "include_hidden": include_hidden,
                },
            )
            id_columns = body["id_columns"]
            rows.extend(
                {
                    "input": gene["input"],
                    "matched_by": gene["matched_by"],
                    **{id_columns[r]: v for r, v in gene["identifiers"].items()},
                    **gene["values"],
                }
                for gene in body["genes"]
            )
            not_found += body["not_found"]
            ambiguous += body["ambiguous"]
        answered = {row["input"] for row in rows} | set(not_found) | set(ambiguous)
        repeated = [v for v in dict.fromkeys(values) if v not in answered]
        _warn_unresolved(not_found, ambiguous, repeated=repeated)
        return frame(rows)

    def gene(
        self,
        gene: str,
        *,
        columns: Sequence[str] | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        """One gene: ``identifiers`` (by role) and ``values`` (by column).

        Raises :class:`UnknownGene` or :class:`AmbiguousGene`.
        """
        if self.api_version() < (1, 1):
            params = {"columns": ",".join(columns) if columns else None}
            body = self._get(
                f"/api/genes/{_segment(gene)}",
                include_hidden=include_hidden,
                **params,
            )
            return {
                "input": body["gene"]["input"],
                "matched_by": body["gene"]["matched_by"],
                "key": body["gene"]["key"],
                "identifiers": body["identifiers"],
                "values": body["values"],
            }
        body = self._post(
            "/api/genes",
            {
                "ids": [gene],
                "columns": list(columns) if columns else None,
                "include_hidden": include_hidden,
            },
        )
        if body["ambiguous"]:
            raise AmbiguousGene(
                409, "ambiguous_gene", f"{gene!r} matches several genes"
            )
        if not body["genes"]:
            raise UnknownGene(404, "unknown_gene", f"no gene matches {gene!r}")
        row = body["genes"][0]
        return {
            k: row[k] for k in ("input", "matched_by", "key", "identifiers", "values")
        }

    def genesets(self, gene: str, *, category: str | None = None) -> pl.DataFrame:
        """Every gene list (Boolean column) the gene is on, with its metadata."""
        if self.api_version() < (1, 1):
            body = self._get(f"/api/genes/{_segment(gene)}/genesets", category=category)
            return frame(body["genesets"])
        # Through POST, so the gene stays out of the URL like every other.
        if self._booleans is None:
            booleans = self.fields(value_type="boolean", include_hidden=True)
            self._booleans = booleans["name"].to_list() if booleans.height else []
        values = self.gene(gene, columns=self._booleans)["values"]
        members = [name for name, value in values.items() if value is True]
        listed = self.fields(
            value_type="boolean",
            include_hidden=True,
            include_counts=True,
            category=category,
        )
        if not listed.height:
            return listed
        return listed.filter(pl.col("name").is_in(members))

    def convert(
        self,
        ids: Iterable[object],
        *,
        to: Sequence[str] | None = None,
        from_: str = "auto",
    ) -> pl.DataFrame:
        """Identifiers onto the database's identifier roles.

        Every input comes back, in order, with ``matched_by`` (``unmatched``
        and ``ambiguous`` included) and one column per role in ``to`` (every
        role when unset).
        """
        values = _strings(ids)
        if not values:
            return pl.DataFrame()
        rows: list[dict[str, Any]] = []
        for chunk in _chunks(values):
            body = self._post(
                "/api/convert",
                {"ids": chunk, "from": from_, "to": list(to) if to else None},
            )
            rows.extend(
                {
                    "input": r["input"],
                    "matched_by": r["matched_by"],
                    "note": r["note"],
                    **r["ids"],
                }
                for r in body["results"]
            )
        return frame(rows)

    def search(  # noqa: PLR0913
        self,
        tokens: Iterable[object] | str | None = None,
        *,
        genome: str | None = None,
        exact: bool = True,
        filters: Sequence[str] = (),
        filter_mode: Literal["union", "intersection"] = "union",
        merge: Literal["union", "intersection"] = "union",
        columns: Sequence[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> pl.DataFrame:
        """The table's search (API 1.1): genes, loci, and gene-list filters.

        ``tokens`` is a list, or text cut as the search box cuts it -- so a
        pasted ``"['SHANK3', 'CHD8']"`` or ``"chr22:50,674,408-50,733,212"``
        works. Tokens that find nothing are reported in an
        :class:`UnresolvedWarning`.
        """
        self._require((1, 1), "search()")
        body: dict[str, Any] = {
            "genome": genome,
            "exact": exact,
            "filters": list(filters),
            "filter_mode": filter_mode,
            "merge": merge,
            "columns": list(columns) if columns else None,
            "limit": limit,
            "offset": offset,
        }
        if isinstance(tokens, str):
            body["text"] = tokens
        elif tokens is not None:
            body["tokens"] = _strings(tokens)
        answer = self._post("/api/search", body)
        _warn_unresolved(answer["unmatched"], [], what="search tokens")
        return frame(answer["rows"])

    # -- comparisons --------------------------------------------------------

    def backgrounds(self) -> dict[str, Any]:
        """The comparison backgrounds and covariates this server offers."""
        return dict(self._get("/api/backgrounds"))

    def compare(
        self,
        sets: Sequence[Set | str | dict[str, Any] | Iterable[object]],
        *,
        background: str | Set | None = None,
        adjust_for: Sequence[str] | None = None,
        include_genes: bool = False,
    ) -> Comparison:
        """Compare 2 to 12 gene sets, as the Compare tab does.

        Each set is a :class:`Set`, a column name (a database gene list), or
        identifiers.

        ``background`` is a preset's name (see :meth:`backgrounds`; the server's
        first when unset), or a :class:`Set` to run the comparison within: a
        database gene list (``Set.field("hc_ndd")``) or your own genes
        (``Set.genes(...)``, ``Set.file(...)``, ``Set.lists(...)``), at least
        200. ``adjust_for`` names up to two covariates (crossed); unset, the
        server's first; ``[]`` for crude results only. A set background and two
        covariates need API 1.2.
        """
        request: dict[str, Any] = {
            "sets": [Set.of(s).to_request() for s in sets],
            "include_genes": include_genes,
        }
        if isinstance(background, Set):
            self._require((1, 2), "a gene-set background")
            request["background"] = background.to_request()
        elif background is not None:
            request["background"] = background
        if adjust_for is not None:
            if len(adjust_for) > 1:
                self._require((1, 2), "adjusting for two covariates")
            request["adjust_for"] = list(adjust_for)
        return self._compare(request)

    def replay(self, request: str | Path | dict[str, Any]) -> Comparison:
        """Run a comparison again from its ``request.json`` (as the Compare
        tab's downloads carry it) or from that body as a dict."""
        body: dict[str, Any] = (
            dict(request)
            if isinstance(request, dict)
            else json.loads(Path(request).read_text(encoding="utf-8"))
        )
        return self._compare(body)

    def _compare(self, request: dict[str, Any]) -> Comparison:
        response = self._post("/api/compare", request)
        return Comparison(request, response, self.url, __version__)

    # -- the whole table ----------------------------------------------------

    def download(
        self,
        format: Literal["parquet", "tsv"] = "parquet",  # noqa: A002
        path: str | Path | None = None,
    ) -> pl.DataFrame | Path:
        """The whole database: as a DataFrame, or written to ``path``.

        Parquet keeps the columns' types; TSV writes nulls as ``NA``.
        """
        response = self._request(
            "GET",
            "/api/download/database",
            params={"file_format": format},
        )
        if path is not None:
            target = Path(path)
            target.write_bytes(response.content)
            return target
        if format == "parquet":
            return pl.read_parquet(io.BytesIO(response.content))
        return pl.read_csv(
            io.BytesIO(response.content),
            separator="\t",
            null_values="NA",
            infer_schema_length=None,
        )


def _segment(value: str) -> str:
    """One URL path segment."""
    return quote(value, safe="")


def _strings(ids: Iterable[object]) -> list[str]:
    if isinstance(ids, str):
        ids = [ids]
    return [str(v).strip() for v in ids if v is not None and str(v).strip()]


def _chunks(values: list[str]) -> list[list[str]]:
    return [
        values[i : i + MAX_IDS_PER_REQUEST]
        for i in range(0, len(values), MAX_IDS_PER_REQUEST)
    ]


def _warn_unresolved(
    not_found: list[str],
    ambiguous: list[str],
    *,
    repeated: Sequence[str] = (),
    what: str = "identifiers",
) -> None:
    parts = []
    if repeated:
        parts.append(
            f"{len(repeated)} named a gene already listed under another input: "
            f"{_some(repeated)}"
        )
    if not_found:
        parts.append(f"{len(not_found)} matched no gene: {_some(not_found)}")
    if ambiguous:
        parts.append(f"{len(ambiguous)} matched several: {_some(ambiguous)}")
    if parts:
        warnings.warn(f"{what}: " + "; ".join(parts), UnresolvedWarning, stacklevel=3)


def _some(values: Sequence[str], n: int = 10) -> str:
    shown = ", ".join(values[:n])
    return shown + (f" and {len(values) - n} more" if len(values) > n else "")
