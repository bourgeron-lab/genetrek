"""Read-only commands: list, show, get, export, resolve, validate, history."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Annotated

import polars as pl
import typer

from genetrek import paths
from genetrek.cli._common import (
    JsonOption,
    emit_json,
    fail,
    frame_to_table,
    human_bytes,
    key_value_table,
    open_database,
    out,
    print_report,
    wants_json,
)
from genetrek.db.gene_index import GeneIndex, GeneIndexError

app = typer.Typer(no_args_is_help=True)

#: Characters that separate identifiers in a pasted or piped gene list.
_IDENTIFIER_DELIMITERS = ",;\t "


@app.command("list")
def list_fields(
    ctx: typer.Context,
    sizes: Annotated[
        bool,
        typer.Option("--sizes", help="Show in-memory size and share per field."),
    ] = False,
    on_disk: Annotated[
        bool,
        typer.Option("--on-disk", help="Add compressed parquet size (needs pyarrow)."),
    ] = False,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="Only primary, secondary, or hidden fields."),
    ] = None,
    value_type: Annotated[
        str | None,
        typer.Option("--type", help="Only fields of this value type."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """List every field in the database."""
    database = open_database(ctx)
    listing = database.list_fields(
        visibility=visibility,  # type: ignore[arg-type]
        value_type=value_type,  # type: ignore[arg-type]
    )

    if on_disk:
        listing = _add_on_disk_sizes(database, listing)

    if wants_json(ctx, json_output):
        emit_json(listing.to_dicts())
        return

    display = listing if sizes or on_disk else listing.drop("est_bytes", "pct_memory")
    if sizes or on_disk:
        display = display.with_columns(
            pl.col("est_bytes")
            .map_elements(human_bytes, return_dtype=pl.String)
            .alias("in_memory"),
        ).drop("est_bytes")
    out.print(frame_to_table(display, title=f"{listing.height} field(s)"))


def _add_on_disk_sizes(database: object, listing: pl.DataFrame) -> pl.DataFrame:
    """Attach per-column compressed parquet sizes, which need pyarrow."""
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415 - optional dependency
    except ImportError:
        fail(
            "--on-disk needs pyarrow, which is not installed. "
            "Install it with 'uv add pyarrow' or drop the flag.",
        )

    from genetrek.paths import parquet_path  # noqa: PLC0415 - avoids a cycle

    metadata = pq.ParquetFile(parquet_path(database.resources)).metadata  # type: ignore[attr-defined]
    totals: dict[str, int] = {}
    for group in range(metadata.num_row_groups):
        row_group = metadata.row_group(group)
        for column in range(row_group.num_columns):
            chunk = row_group.column(column)
            name = chunk.path_in_schema.split(".")[0]
            totals[name] = totals.get(name, 0) + chunk.total_compressed_size

    return (
        listing.with_columns(
            pl.col("field")
            .replace_strict(totals, default=None, return_dtype=pl.Int64)
            .alias("disk_bytes"),
        )
        .with_columns(
            pl.col("disk_bytes")
            .map_elements(
                lambda size: "" if size is None else human_bytes(size),
                return_dtype=pl.String,
            )
            .alias("on_disk"),
        )
        .drop("disk_bytes")
    )


@app.command()
def licences(
    ctx: typer.Context,
    write: Annotated[
        Path | None,
        typer.Option(
            "--write",
            help="Write DATA_LICENCES.md to this path instead of printing it.",
        ),
    ] = None,
) -> None:
    """Render DATA_LICENCES.md -- every column's licence -- from the metadata.

    Read-only on the database. The committed copy at the repository root must
    match this output; a test checks it, so re-run with ``--write`` after
    changing any column's ``licence``.
    """
    from genetrek import paths, schema  # noqa: PLC0415 - avoids a cycle
    from genetrek.db.licences import licences_markdown  # noqa: PLC0415

    database = open_database(ctx)
    try:
        order = schema.load_categories_yaml(
            paths.categories_path(database.resources),
            database.fields,
        )
    except schema.CategoriesYamlError as exc:
        fail(str(exc))
    text = licences_markdown(database.fields, order)
    if write is None:
        sys.stdout.write(text)
        return
    write.write_text(text, encoding="utf-8")
    out.print(f"wrote {write}")


@app.command()
def categories(ctx: typer.Context, json_output: JsonOption = False) -> None:
    """Show the declared column categories, in display order.

    Read-only: the order lives in ``config/categories.yaml`` and is
    hand-maintained, as ``badges.yaml`` is. Use ``set --category`` to move a
    field between groups.
    """
    from genetrek import paths, schema  # noqa: PLC0415 - avoids a cycle

    database = open_database(ctx)
    fields = database.fields
    try:
        order = schema.load_categories_yaml(
            paths.categories_path(database.resources),
            fields,
        )
    except schema.CategoriesYamlError as exc:
        fail(str(exc))

    rows: list[dict[str, object]] = []
    for group in schema.group_fields(fields, order):
        rows.append(
            {
                "category": group.name,
                "subcategory": "",
                "columns": len(group.all_columns),
                "description": group.description,
            },
        )
        rows += [
            {
                "category": "",
                "subcategory": sub.name,
                "columns": len(sub.columns),
                "description": "",
            }
            for sub in group.subgroups
        ]

    if wants_json(ctx, json_output):
        emit_json(
            {
                "categories": rows,
                "undeclared": list(order.undeclared),
                "unused": list(order.unused),
            },
        )
        return

    if not order:
        out.print(
            "[dim]No categories declared: "
            f"{paths.categories_path(database.resources)} is absent, "
            "so columns are ordered by priority alone.[/]",
        )
        return

    out.print(frame_to_table(pl.DataFrame(rows), title="column categories"))
    for name in order.undeclared:
        out.print(
            f"[yellow]{name!r} is used but not declared, so its columns sort last[/]",
        )
    for name in order.unused:
        out.print(f"[dim]{name!r} is declared but no field uses it[/]")


@app.command()
def show(ctx: typer.Context, field: str, json_output: JsonOption = False) -> None:
    """Show a field's metadata and type-appropriate statistics."""
    database = open_database(ctx)
    if field not in database:
        fail(f"unknown field: {field!r}")
    bound = database[field]
    stats = bound.stats()

    if wants_json(ctx, json_output):
        emit_json(
            {
                "field": bound.name,
                "requested": field,
                "meta": bound.meta.model_dump(),
                "stats": vars(stats),
            },
        )
        return

    if bound.name != field:
        out.print(f"[dim]{field} -> {bound.name}  (alias)[/]")
    metadata = [
        (key, "" if value is None else str(value))
        for key, value in bound.meta.model_dump().items()
    ]
    out.print(key_value_table(metadata, title=f"{field} - metadata"))
    if bound.meta.is_derived and not bound.meta.is_rule:
        parents = ", ".join(bound.meta.derived_from)
        out.print(f"\n[bold]computed from[/]  {parents}  (outside GeneTrek)")
    elif bound.meta.is_derived:
        rule = f"{bound.meta.derived_op} of {', '.join(bound.meta.derived_from)}"
        out.print(f"\n[bold]rule[/]  {bound.name} = {rule}")
        # Whether it still holds is the question worth answering here; the rule
        # alone does not say if a source has moved since it was evaluated.
        stale = [
            e for e in database.validate().errors if e.startswith(f"{bound.name}:")
        ]
        if stale:
            for problem in stale:
                out.print(f"[red]      {problem}[/]")
            out.print("      Run 'genetrek-db recompute-derived' to refresh it.")
        else:
            out.print("[green]      stored values match the rule[/]")
    out.print()
    out.print(key_value_table(stats.rows(), title=f"{field} - values"))


@app.command()
def get(
    ctx: typer.Context,
    field: str,
    id_col: Annotated[
        str | None,
        typer.Option(
            "--id-col",
            help="Identifier column to pair the values with. Defaults to the "
            "database's symbol column, then its primary key.",
        ),
    ] = None,
    drop_nulls: Annotated[
        bool,
        typer.Option(
            "--drop-nulls",
            help="Omit null rows; for boolean fields, omit False rows too.",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write here instead of stdout."),
    ] = None,
) -> None:
    """Export one field as a TSV of identifier and value."""
    database = open_database(ctx)
    if field not in database:
        fail(f"unknown field: {field!r}")

    bound = database[field]
    try:
        frame = bound.to_frame(id_col)
    except KeyError as exc:
        fail(str(exc).strip("\"'"))

    if drop_nulls:
        # The real column name, never the alias -- the header must name the data.
        frame = frame.filter(pl.col(bound.name).is_not_null())
        if bound.dtype == pl.Boolean:
            frame = frame.filter(pl.col(bound.name))

    if output is None:
        sys.stdout.write(frame.write_csv(separator="\t"))
    else:
        frame.write_csv(output, separator="\t")
        out.print(f"wrote {frame.height:,} row(s) to {output}")


@app.command()
def export(
    ctx: typer.Context,
    output: Annotated[Path, typer.Argument(help="Destination TSV.")],
    columns: Annotated[
        str | None,
        typer.Option("--columns", help="Comma-separated fields to include."),
    ] = None,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="Only fields with this visibility."),
    ] = None,
    gzip_output: Annotated[
        bool,
        typer.Option("--gzip", help="Compress the result with gzip."),
    ] = False,
) -> None:
    """Export the whole database as a TSV."""
    database = open_database(ctx)
    selected = [c.strip() for c in columns.split(",") if c.strip()] if columns else None

    target = output.with_suffix(output.suffix + ".tmp") if gzip_output else output
    try:
        rows = database.export(
            target,
            columns=selected,
            visibility=visibility,  # type: ignore[arg-type]
        )
    except (KeyError, ValueError) as exc:
        fail(str(exc).strip("\"'"))

    if gzip_output:
        _gzip_in_place(target, output)
    out.print(f"wrote {rows:,} row(s) to {output}")


def _gzip_in_place(source: Path, destination: Path) -> None:
    import gzip  # noqa: PLC0415 - only needed for --gzip
    import shutil  # noqa: PLC0415 - only needed for --gzip

    final = (
        destination
        if destination.suffix == ".gz"
        else destination.with_suffix(
            destination.suffix + ".gz",
        )
    )
    with source.open("rb") as raw, gzip.open(final, "wb") as compressed:
        shutil.copyfileobj(raw, compressed)
    source.unlink()


@app.command()
def resolve(
    ctx: typer.Context,
    source: Annotated[
        str,
        typer.Argument(help="File of identifiers, or '-' to read stdin."),
    ],
    id_type: Annotated[
        str,
        typer.Option(
            "--id-type", help="ncbi, ensg, symbol, hgnc, omim, mixed, or auto."
        ),
    ] = "auto",
    unmatched_only: Annotated[
        bool,
        typer.Option(
            "--unmatched-only", help="Print only the identifiers that failed."
        ),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Check how a gene list resolves, without changing anything.

    Run this before ``add`` to see what a list would lose.
    """
    database = open_database(ctx)
    identifiers = _read_identifiers(source)
    if not identifiers:
        fail("no identifiers found in the input")

    try:
        report = database.resolve(identifiers, id_type)
    except GeneIndexError as exc:
        fail(str(exc))

    if wants_json(ctx, json_output):
        emit_json(
            {
                "id_type": report.id_type,
                "detected": report.detected,
                "n_input": report.n_input,
                "n_resolved": report.n_resolved,
                "counts": report.counts,
                "warnings": report.warnings,
                "rows": report.frame.to_dicts(),
            },
        )
        return

    if unmatched_only:
        for value in report.unmatched + report.ambiguous:
            sys.stdout.write(f"{value}\n")
        return

    out.print(frame_to_table(report.frame, title="resolution"))
    out.print()
    out.print(report.describe())
    if not report.ok:
        raise typer.Exit(1)


@app.command("build-aliases")
def build_aliases(
    ctx: typer.Context,
    source: Annotated[
        Path | None,
        typer.Option(
            "--source",
            help="HGNC complete set to distil. Defaults to the newest in the tree.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Where to write. Defaults to the tree's own."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would be written, write nothing."),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Distil HGNC's alias and previous-symbol columns into a shipped index.

    The complete set is 34 MB, gitignored and excluded from the container
    image, so without this file a deployed GeneTrek resolves current symbols
    only and a search for a retired one silently finds nothing. The index is
    ~1 MB, is committed, and reproduces ``with_hgnc()`` exactly.

    Re-run it whenever the parquet's gene set changes: ``validate`` warns when
    the two no longer agree.
    """
    database = open_database(ctx)
    resolved_source = source or paths.latest_hgnc_file(database.resources)
    if resolved_source is None:
        fail(
            "no HGNC complete set found; pass --source or download one to "
            "resources/original_file/",
        )
    destination = output or paths.aliases_path(database.resources)

    try:
        index = GeneIndex.from_frame(database.frame, database.roles).with_hgnc(
            resolved_source,
        )
        frame = index.symbol_lookup()
    except GeneIndexError as exc:
        fail(str(exc))

    counts = dict(Counter(frame["matched_by"].to_list()))
    # Named rather than inferred: three hgnc_complete_set* files can sit in the
    # tree at once, `latest_hgnc_file` picks by mtime, and two of them may be
    # annotated derivatives of an older release. Which one was used is the
    # single most useful thing this command can print.
    summary = {
        "source": str(resolved_source),
        "output": str(destination),
        "index_rows": frame.height,
        "frame_rows": database.n_rows,
        "key_column": index.key,
        "counts": counts,
        "written": not dry_run,
    }

    if not dry_run:
        index.write_alias_index(destination)

    if wants_json(ctx, json_output):
        emit_json(summary)
        return

    out.print(
        key_value_table(
            [
                ("source", str(resolved_source)),
                ("output", str(destination)),
                ("key column", index.key),
                ("index rows", f"{frame.height:,}"),
                ("genes in parquet", f"{database.n_rows:,}"),
            ],
            title="alias index",
        ),
    )
    out.print(frame_to_table(frame.head(5), title="sample"))
    for kind, n in sorted(counts.items()):
        out.print(f"  {kind:<16} {n:>8,}")
    if dry_run:
        out.print("\n[yellow]--dry-run: nothing written[/yellow]")
    else:
        out.print(f"\nWrote {frame.height:,} rows to {destination}")


def _read_identifiers(source: str) -> list[str]:
    """Read identifiers from a file or stdin, one per line or delimited."""
    text = (
        sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    )
    tokens: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        for separator in _IDENTIFIER_DELIMITERS:
            cleaned = cleaned.replace(separator, "\n")
        tokens.extend(part for part in cleaned.split("\n") if part)
    return tokens


@app.command()
def validate(ctx: typer.Context, json_output: JsonOption = False) -> None:
    """Check that the parquet and its metadata agree. Exits non-zero on error."""
    database = open_database(ctx)
    report = database.validate()

    if wants_json(ctx, json_output):
        emit_json(
            {
                "ok": report.ok,
                "errors": report.errors,
                "warnings": report.warnings,
                "notes": report.notes,
            },
        )
    else:
        print_report(report.errors, "red")
        print_report(report.warnings, "yellow")
        print_report(report.notes, "dim")
        if report.ok:
            out.print(
                f"[green]OK[/] {len(database)} field(s), "
                f"{database.n_rows:,} gene(s), no errors.",
            )
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def history(
    ctx: typer.Context,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Show only the most recent N entries."),
    ] = 20,
    json_output: JsonOption = False,
) -> None:
    """Show the log of changes made through this tool."""
    database = open_database(ctx)
    records = database.history(limit)

    if wants_json(ctx, json_output):
        emit_json(records)
        return
    if not records:
        out.print("No history yet.")
        return

    frame = pl.DataFrame(
        [
            {
                "when": record.get("ts", ""),
                "op": record.get("op", ""),
                "field": record.get("field", ""),
                "actor": record.get("actor", ""),
                "note": record.get("note", ""),
            }
            for record in records
        ],
    )
    out.print(frame_to_table(frame, title=f"last {len(records)} change(s)"))
