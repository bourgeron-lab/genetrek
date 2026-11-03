"""The ``genetrek`` command: GeneTrek from the shell.

Tables go to stdout as TSV (nulls as ``NA``), so commands pipe into each other
and into ``cut``, ``sort`` or R; ``--json`` prints JSON instead. Messages and
warnings go to stderr. Exit status: 0 on success, 1 when the server or the
configuration refuses, 2 on bad usage.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import polars as pl
import typer

from genetrek import __version__
from genetrek.client import GeneTrek, UnresolvedWarning
from genetrek.config import (
    PUBLIC_URL,
    check_name,
    check_url,
    config_path,
    load_config,
    save_config,
)
from genetrek.errors import ApiError, GeneTrekError
from genetrek.lists import identifiers_from_text
from genetrek.sets import Set

if TYPE_CHECKING:
    from collections.abc import Callable

    from genetrek.models import Comparison

app = typer.Typer(
    help="GeneTrek from the command line: gene lists, genes, search, comparisons.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
config_app = typer.Typer(help="Name the servers you use, and pick the default.")
app.add_typer(config_app, name="config")

_state: dict[str, Any] = {"url": None, "server": None, "json": False}


def _version(value: bool) -> None:
    if value:
        typer.echo(f"genetrek {__version__}")
        raise typer.Exit


@app.callback()
def _main(
    url: Annotated[
        str | None,
        typer.Option("--url", help="Server address (overrides everything)."),
    ] = None,
    server: Annotated[
        str | None,
        typer.Option("--server", "-s", help="A server named in your config."),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print JSON instead of TSV.")
    ] = False,
    version: Annotated[  # noqa: ARG001
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Print the version."
        ),
    ] = False,
) -> None:
    _state.update(url=url, server=server, json=as_json)


def _client() -> GeneTrek:
    return GeneTrek(_state["url"], server=_state["server"])


def _run(action: Callable[[GeneTrek], object]) -> None:
    """Run one command: report errors and warnings on stderr, never a traceback."""

    def show(message: Warning | str, category: type[Warning], *_: object) -> None:
        label = "warning" if category is not UnresolvedWarning else "note"
        typer.echo(f"{label}: {message}", err=True)

    warnings.showwarning = show  # type: ignore[assignment]
    try:
        with _client() as gt:
            result = action(gt)
    except ApiError as exc:
        code = f" [{exc.code}]" if exc.code else ""
        typer.echo(f"error{code}: {exc.detail}", err=True)
        raise typer.Exit(1) from exc
    except (GeneTrekError, OSError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print(result)


def _print(result: object) -> None:
    if result is None:
        return
    if isinstance(result, pl.DataFrame):
        if _state["json"]:
            typer.echo(json.dumps(result.to_dicts(), default=str, indent=1))
        elif result.width:
            typer.echo(_tsv(result), nl=False)
        return
    if isinstance(result, list):
        if _state["json"]:
            typer.echo(json.dumps(result, indent=1))
        else:
            typer.echo("\n".join(map(str, result)))
        return
    if isinstance(result, dict) and not _state["json"]:
        for key, value in result.items():
            shown = json.dumps(value) if isinstance(value, dict | list) else value
            typer.echo(f"{key}\t{shown}")
        return
    typer.echo(json.dumps(result, indent=1, default=str))


def _tsv(table: pl.DataFrame) -> str:
    # Nested values (lists) would not survive TSV; write them as JSON.
    nested = [name for name, dtype in table.schema.items() if dtype.is_nested()]
    if nested:
        table = table.with_columns(
            pl.col(n).map_elements(
                lambda v: json.dumps(v.to_list() if isinstance(v, pl.Series) else v),
                return_dtype=pl.String,
            )
            for n in nested
        )
    return table.write_csv(separator="\t", null_value="NA")


def _identifiers(source: str) -> list[str]:
    """Identifiers from a file, or from stdin for ``-``."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text("utf-8")
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    return identifiers_from_text(text)


def _columns(value: str | None) -> list[str] | None:
    return [c.strip() for c in value.split(",") if c.strip()] if value else None


# -- the server ---------------------------------------------------------------


@app.command()
def info() -> None:
    """Which server answers, its release and its API version."""
    _run(lambda gt: gt.info())


@config_app.command("list")
def config_list() -> None:
    """The servers you have named, and the default."""
    config = load_config()
    typer.echo(f"# {config_path()}", err=True)
    default = config.default or "public"
    rows = {"public": PUBLIC_URL, **config.servers}
    for name, url in rows.items():
        mark = " (default)" if name == default else ""
        typer.echo(f"{name}\t{url}{mark}")


@config_app.command("add")
def config_add(
    name: str,
    url: str,
    default: Annotated[
        bool, typer.Option("--default", help="Make it the default server.")
    ] = False,
) -> None:
    """Name a server, e.g. ``genetrek config add local http://localhost:8000``."""
    try:
        config = load_config()
        config.servers[check_name(name)] = check_url(url)
        if default:
            config.default = name
        path = save_config(config)
    except GeneTrekError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"saved {name} in {path}", err=True)


@config_app.command("remove")
def config_remove(name: str) -> None:
    """Forget a named server."""
    config = load_config()
    if config.servers.pop(name, None) is None:
        typer.echo(f"error: no server named {name!r}", err=True)
        raise typer.Exit(1)
    if config.default == name:
        config.default = None
    save_config(config)


@config_app.command("default")
def config_default(
    name: Annotated[str, typer.Argument(help="A named server, or 'public'.")],
) -> None:
    """Make a named server the default."""
    config = load_config()
    if name != "public" and name not in config.servers:
        typer.echo(f"error: no server named {name!r}", err=True)
        raise typer.Exit(1)
    config.default = None if name == "public" else name
    save_config(config)


# -- columns ------------------------------------------------------------------


@app.command()
def fields(
    category: Annotated[str | None, typer.Option()] = None,
    value_type: Annotated[
        str | None,
        typer.Option("--type", help="boolean, numeric, categorical, …"),
    ] = None,
    hidden: Annotated[bool, typer.Option(help="Include API-only columns.")] = False,
    counts: Annotated[bool, typer.Option(help="Add each column's gene count.")] = False,
) -> None:
    """Every column, in the table's order."""
    _run(
        lambda gt: gt.fields(
            category=category,
            value_type=value_type,
            include_hidden=hidden,
            include_counts=counts,
        ).select(
            "name",
            "fullname",
            "category",
            "value_type",
            "visibility",
            *(["n_genes"] if counts else []),
        )
    )


@app.command()
def field(name: str) -> None:
    """One column's metadata and coverage (an alias works)."""
    _run(lambda gt: gt.field(name))


@app.command("list")
def gene_list(
    name: Annotated[str, typer.Argument(help="A gene list, e.g. hc_ndd.")],
    id_type: Annotated[
        str, typer.Option(help="symbol, hgnc, ensg, ncbi, omim")
    ] = "symbol",
) -> None:
    """A gene list's members, one per line."""
    _run(lambda gt: gt.gene_list(name, id_type=id_type))


@app.command()
def universe(
    name: str,
    id_type: Annotated[str, typer.Option()] = "symbol",
) -> None:
    """The genes a column measured, one per line."""
    _run(lambda gt: gt.universe(name, id_type=id_type))


# -- genes --------------------------------------------------------------------


@app.command()
def gene(
    identifier: Annotated[str, typer.Argument(help="Any identifier of one gene.")],
    columns: Annotated[
        str | None,
        typer.Option(help="Comma-separated columns; hidden ones by name."),
    ] = None,
) -> None:
    """One gene's identifiers and values."""

    def action(gt: GeneTrek) -> object:
        row = gt.gene(identifier, columns=_columns(columns))
        return {
            "input": row["input"],
            "matched_by": row["matched_by"],
            **row["identifiers"],
            **row["values"],
        }

    _run(action)


@app.command()
def genes(
    source: Annotated[
        str, typer.Argument(help="A file of identifiers, or - for stdin.")
    ],
    columns: Annotated[str | None, typer.Option()] = None,
    from_: Annotated[str, typer.Option("--from")] = "auto",
) -> None:
    """Many genes at once, one row each, with the columns asked for."""
    ids = _identifiers(source)
    _run(lambda gt: gt.genes(ids, columns=_columns(columns), from_=from_))


@app.command()
def convert(
    source: Annotated[
        str, typer.Argument(help="A file of identifiers, or - for stdin.")
    ],
    to: Annotated[
        str | None,
        typer.Option(help="Comma-separated roles: symbol,hgnc,ensg,ncbi,omim."),
    ] = None,
    from_: Annotated[str, typer.Option("--from")] = "auto",
) -> None:
    """Identifiers onto every identifier type, every input kept, in order."""
    ids = _identifiers(source)
    _run(lambda gt: gt.convert(ids, to=_columns(to), from_=from_))


@app.command()
def search(
    tokens: Annotated[
        list[str] | None,
        typer.Argument(help="Genes and loci, as in the search box."),
    ] = None,
    genome: Annotated[str | None, typer.Option()] = None,
    filters: Annotated[
        list[str] | None,
        typer.Option("--filter", help="A gene list to filter on (repeatable)."),
    ] = None,
    prefix: Annotated[bool, typer.Option(help="Match symbols as prefixes.")] = False,
    columns: Annotated[str | None, typer.Option()] = None,
) -> None:
    """The table's search: genes, loci and gene-list filters."""
    text = " ".join(tokens or [])
    _run(
        lambda gt: gt.search(
            text or None,
            genome=genome,
            exact=not prefix,
            filters=filters or [],
            columns=_columns(columns),
        )
    )


# -- comparisons --------------------------------------------------------------


def _set(value: str) -> Set:
    """``hc_ndd``, ``genes.txt``, ``genes.txt:label`` or a ``#lists=`` link."""
    if "#lists=" in value:
        return Set.lists(value)
    if Path(value).is_file():
        return Set.file(value)
    path, _, label = value.rpartition(":")
    if path and Path(path).is_file():
        return Set.file(path, label=label or None)
    return Set.field(value)


@app.command()
def compare(
    sets: Annotated[
        list[str],
        typer.Argument(
            help="2-12 sets: a gene list name, a file (path[:label]) or a share link."
        ),
    ],
    background: Annotated[str | None, typer.Option()] = None,
    adjust: Annotated[
        str | None,
        typer.Option(help="Covariate to adjust for; 'none' for crude only."),
    ] = None,
    with_genes: Annotated[
        bool, typer.Option("--genes", help="List the genes of each region.")
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(help="Write pairs, sets, regions, methods and request.json here."),
    ] = None,
) -> None:
    """Compare gene sets as the Compare tab does; prints the pairs table."""
    built = [_set(value) for value in sets]
    adjust_for = None if adjust is None else ([] if adjust == "none" else [adjust])
    _run(
        lambda gt: _write(
            gt.compare(
                built,
                background=background,
                adjust_for=adjust_for,
                include_genes=with_genes,
            ),
            out,
        )
    )


@app.command()
def replay(
    request: Annotated[Path, typer.Argument(help="A request.json from a download.")],
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Run a downloaded comparison again, from its request.json."""
    _run(lambda gt: _write(gt.replay(request), out))


def _write(comparison: Comparison, out: Path | None) -> pl.DataFrame:
    for warning in comparison.warnings:
        typer.echo(f"warning: {warning}", err=True)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "pairs.tsv").write_text(_tsv(comparison.pairs), encoding="utf-8")
        (out / "sets.tsv").write_text(_tsv(comparison.sets), encoding="utf-8")
        (out / "regions.tsv").write_text(_tsv(comparison.regions), encoding="utf-8")
        (out / "methods.txt").write_text(comparison.methods + "\n", encoding="utf-8")
        comparison.to_request_json(out / "request.json")
        typer.echo(
            f"wrote {out}/: pairs, sets, regions, methods, request.json", err=True
        )
    return comparison.pairs


@app.command()
def download(
    output: Annotated[Path, typer.Option("--output", "-o", help="File to write.")],
    file_format: Annotated[
        str, typer.Option("--format", help="parquet or tsv")
    ] = "parquet",
) -> None:
    """The whole database, to a file."""
    if file_format not in {"parquet", "tsv"}:
        typer.echo("error: --format is parquet or tsv", err=True)
        raise typer.Exit(2)
    _run(lambda gt: (gt.download(file_format, output), None)[1])  # type: ignore[arg-type]


def main() -> None:
    """Entry point of the ``genetrek`` command."""
    app()
