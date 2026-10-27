"""Commands that change the database: add, remove, set, sync."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from genetrek import schema
from genetrek.cli._common import (
    JsonOption,
    confirm,
    emit_json,
    fail,
    open_database,
    out,
    require_real_field,
    save_or_fail,
    wants_json,
)
from genetrek.db import (
    Database,
    DatabaseError,
    ResolutionFailedError,
    SaveResult,
)
from genetrek.db.gene_index import GeneIndexError

app = typer.Typer(no_args_is_help=True)


def _parse_priority(text: str) -> int | None:
    """Parse a ``--priority`` option: an integer, or empty to clear it."""
    if not text.strip():
        return None
    try:
        return int(text)
    except ValueError:
        fail(f"--priority must be an integer or empty, not {text!r}")


@app.command()
def add(
    ctx: typer.Context,
    source: Annotated[
        Path,
        typer.Argument(help="TSV/CSV with identifiers, or a one-column gene list."),
    ],
    name: Annotated[
        str | None,
        typer.Option(
            "--name", help="Field key. Defaults to a slug of the value column."
        ),
    ] = None,
    id_col: Annotated[
        str | None,
        typer.Option("--id-col", help="Identifier column. Defaults to the first."),
    ] = None,
    value_col: Annotated[
        str | None,
        typer.Option("--value-col", help="Value column. Defaults to the second."),
    ] = None,
    id_type: Annotated[
        str,
        typer.Option(
            "--id-type", help="ncbi, ensg, symbol, hgnc, omim, mixed, or auto."
        ),
    ] = "auto",
    fullname: Annotated[str | None, typer.Option("--fullname")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="primary, secondary, or hidden."),
    ] = None,
    priority: Annotated[
        str | None,
        typer.Option(
            "--priority",
            help="Display order among non-hidden columns, smaller first. "
            "An integer, or empty to clear.",
        ),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Display group. Declared in config/categories.yaml, which "
            "also fixes the order the groups appear in.",
        ),
    ] = None,
    subcategory: Annotated[
        str | None,
        typer.Option(
            "--subcategory",
            help="Subgroup within --category. Alphabetical unless the "
            "category declares an order.",
        ),
    ] = None,
    source_name: Annotated[
        str | None,
        typer.Option("--source", help="Where the data came from, for the metadata."),
    ] = None,
    doi: Annotated[str | None, typer.Option("--doi")] = None,
    licence: Annotated[
        str | None,
        typer.Option(
            "--licence",
            help="Terms the values are redistributed under (SPDX id if one exists).",
        ),
    ] = None,
    universe: Annotated[
        str | None,
        typer.Option(
            "--universe",
            help="Which genes were measured, in words. Pair with --missing keep "
            "so unmeasured genes stay null rather than False.",
        ),
    ] = None,
    value_type: Annotated[
        str | None,
        typer.Option("--value-type", help="Override the inferred value type."),
    ] = None,
    missing: Annotated[
        str,
        typer.Option(
            "--missing",
            help="Uncovered rows: auto (False for booleans, null otherwise), "
            "keep (null even for booleans), fill, or error.",
        ),
    ] = "auto",
    fill: Annotated[
        str | None,
        typer.Option("--fill", help="Value used when --missing=fill."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would happen, then stop."),
    ] = False,
    allow_unmatched: Annotated[
        bool,
        typer.Option(
            "--allow-unmatched",
            help="Proceed when the source covers genes the database does not have.",
        ),
    ] = False,
    allow_ambiguous: Annotated[
        bool,
        typer.Option(
            "--allow-ambiguous",
            help="Proceed when an identifier matches several genes, dropping it.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Shorthand for both --allow-* flags."),
    ] = False,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    actor: Annotated[
        str | None, typer.Option("--actor", help="Recorded in the log.")
    ] = None,
    note: Annotated[
        str | None, typer.Option("--note", help="Recorded in the log.")
    ] = None,
) -> None:
    """Add a field from a gene list or annotation table."""
    database = open_database(ctx)

    meta: dict[str, Any] = {
        key: value
        for key, value in (
            ("fullname", fullname),
            ("description", description),
            ("visibility", visibility),
            ("category", category),
            ("subcategory", subcategory),
            ("source", source_name),
            ("doi", doi),
            ("licence", licence),
            ("universe", universe),
            ("value_type", value_type),
        )
        if value is not None
    }
    if priority is not None:
        meta["priority"] = _parse_priority(priority)

    if missing not in ("auto", "keep", "fill", "error"):
        fail(f"--missing must be auto, keep, fill, or error, not {missing!r}")
    if missing == "fill" and fill is None:
        fail("--missing=fill needs --fill VALUE")

    try:
        field = database.add_field(
            source,
            name,
            id_col=id_col,
            value_col=value_col,
            meta=meta or None,
            missing=missing,  # type: ignore[arg-type]
            fill_value=_parse_fill(fill),
            id_type=id_type,
            allow_unmatched=allow_unmatched,
            allow_ambiguous=allow_ambiguous,
            force=force,
        )
    except ResolutionFailedError as exc:
        out.print(exc.report.describe())
        hints = " and ".join(f"--allow-{kind}" for kind in exc.blocking)
        fail(
            "some identifiers did not resolve. Inspect them with "
            f"'genetrek-db resolve', or pass {hints} to proceed.",
        )
    except (DatabaseError, GeneIndexError, ValueError) as exc:
        fail(str(exc))

    stats = field.stats()
    out.print(f"[green]staged[/] {field.name} ({field.meta.value_type})")
    for label, value in stats.rows():
        out.print(f"  {label:<20} {value}")

    if dry_run:
        out.print("\n[yellow]--dry-run:[/] nothing was written.")
        return
    if not confirm(f"Add {field.name} to the database?", assume_yes=assume_yes):
        out.print("aborted; nothing was written.")
        raise typer.Exit(1)

    result = save_or_fail(database, actor=actor, note=note)
    _report_save(database, result)


@app.command()
def remove(
    ctx: typer.Context,
    field: str,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Remove even if the webapp references it."),
    ] = False,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Remove a field from both the parquet and the metadata.

    Takes the real field name; an alias is refused so it can never be what gets
    deleted.
    """
    database = open_database(ctx)
    require_real_field(database, field)

    bound = database[field]
    out.print(f"about to remove [bold]{field}[/] ({bound.meta.fullname})")
    out.print(f"  visibility {bound.meta.visibility}, type {bound.meta.value_type}")

    if not confirm(f"Remove {field}?", assume_yes=assume_yes):
        out.print("aborted; nothing was written.")
        raise typer.Exit(1)

    try:
        database.remove_field(field, force=force)
    except DatabaseError as exc:
        fail(str(exc))

    result = save_or_fail(database, actor=actor, note=note)
    _report_save(database, result)


@app.command()
def rename(
    ctx: typer.Context,
    old: Annotated[str, typer.Argument(help="Current field key.")],
    new: Annotated[str, typer.Argument(help="New field key.")],
    keep_config: Annotated[
        bool,
        typer.Option(
            "--keep-config",
            help="Do not rewrite badges.yaml / external_links.yaml / column_mapping.json.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Rename even a column that code refers to."),
    ] = False,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Rename a field in the parquet, the metadata, and the YAML configuration.

    Use this when a field's meaning becomes version-specific -- renaming `loeuf`
    to `loeuf_gnomad_v4` before adding a v5 column, say. Refuses columns whose
    names appear in code, since the code will not follow.
    """
    database = open_database(ctx)
    require_real_field(database, old)

    bound = database[old]
    out.print(f"renaming [bold]{old}[/] -> [bold]{new}[/]  ({bound.meta.fullname})")

    if not confirm(f"Rename {old} to {new}?", assume_yes=assume_yes):
        out.print("aborted; nothing was written.")
        raise typer.Exit(1)

    try:
        database.rename_field(old, new, update_config=not keep_config, force=force)
    except DatabaseError as exc:
        fail(str(exc))

    for edit in database.pending_config_edits():
        out.print(f"  updated {edit}")

    result = save_or_fail(database, actor=actor, note=note)
    _report_save(database, result)
    hint = f"genetrek-db set {new} --fullname ..."
    out.print(
        "\n[yellow]note:[/] the field's [bold]fullname[/] is unchanged. "
        f"Update it with '{hint}' if the label should change too.",
    )


@app.command()
def derive(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Field key for the new column.")],
    sources: Annotated[
        list[str],
        typer.Option(
            "--from",
            help="A field to combine. Repeat for each one.",
        ),
    ],
    op: Annotated[
        str,
        typer.Option("--op", help="union or intersection."),
    ] = "union",
    fullname: Annotated[str | None, typer.Option("--fullname")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="primary, secondary, or hidden."),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Display group. Declared in config/categories.yaml, which "
            "also fixes the order the groups appear in.",
        ),
    ] = None,
    subcategory: Annotated[
        str | None,
        typer.Option(
            "--subcategory",
            help="Subgroup within --category. Alphabetical unless the "
            "category declares an order.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would happen, then stop."),
    ] = False,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Add a boolean column computed from other boolean columns.

    The rule is stored in the field's metadata, so 'validate' reports the column
    drifting from its sources and 'recompute-derived' refreshes it after one of
    them changes.
    """
    database = open_database(ctx)
    if op not in schema.EVALUATED_OPS:
        fail(f"--op must be one of {', '.join(schema.EVALUATED_OPS)}, not {op!r}")

    meta: dict[str, Any] = {
        key: value
        for key, value in (
            ("fullname", fullname),
            ("description", description),
            ("visibility", visibility),
            ("category", category),
            ("subcategory", subcategory),
        )
        if value is not None
    }

    try:
        field = database.add_derived_field(
            name,
            sources,
            op=op,  # type: ignore[arg-type]
            meta=meta or None,
        )
    except (DatabaseError, schema.DerivationError, ValueError) as exc:
        fail(str(exc))

    stats = field.stats()
    out.print(
        f"[green]staged[/] {name} = {op} of {', '.join(sources)}\n"
        f"  {stats.n_true:,} true, {stats.n_false:,} false, "
        f"{stats.n_null:,} not measured",
    )
    for source in sources:
        column = database.frame[source]
        out.print(
            f"  {source:<40} {int(column.sum()):>8,} true"
            f" {column.null_count():>8,} not measured",
        )

    if dry_run:
        out.print("\n[yellow]--dry-run:[/] nothing was written.")
        return
    if not confirm(f"Add {name} to the database?", assume_yes=assume_yes):
        out.print("aborted; nothing was written.")
        raise typer.Exit(1)

    result = save_or_fail(database, actor=actor, note=note)
    _report_save(database, result)


@app.command("recompute-derived")
def recompute_derived(
    ctx: typer.Context,
    fields: Annotated[
        list[str] | None,
        typer.Option("--field", help="Only this field. Repeat; default is all."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would change, then stop."),
    ] = False,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Re-evaluate derived columns after one of their sources changed."""
    database = open_database(ctx)
    derived = [n for n, m in database.fields.items() if m.is_rule]
    if not derived:
        out.print("no derived fields in this database; nothing to do.")
        return

    try:
        changed = database.recompute_derived(names=fields or None)
    except schema.DerivationError as exc:
        fail(str(exc))

    if not changed:
        out.print(
            f"[green]up to date[/] {len(derived)} derived field(s) already match "
            f"their rules.",
        )
        return

    out.print(f"[yellow]{len(changed)} field(s) changed:[/]")
    for name in changed:
        meta = database.fields[name]
        out.print(
            f"  {name:<40} {int(database.frame[name].sum()):>8,} true"
            f"   ({meta.derived_op} of {', '.join(meta.derived_from)})",
        )

    if dry_run:
        out.print("\n[yellow]--dry-run:[/] nothing was written.")
        return
    if not confirm(
        f"Write {len(changed)} recomputed column(s)?", assume_yes=assume_yes
    ):
        out.print("aborted; nothing was written.")
        raise typer.Exit(1)

    result = save_or_fail(database, actor=actor, note=note)
    _report_save(database, result)


@app.command("set")
def set_meta(
    ctx: typer.Context,
    field: str,
    fullname: Annotated[str | None, typer.Option("--fullname")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
    visibility: Annotated[
        str | None,
        typer.Option("--visibility", help="primary, secondary, or hidden."),
    ] = None,
    priority: Annotated[
        str | None,
        typer.Option(
            "--priority",
            help="Display order among non-hidden columns, smaller first. "
            "An integer, or empty to clear.",
        ),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "--category",
            help="Display group. Declared in config/categories.yaml, which "
            "also fixes the order the groups appear in. Empty to clear.",
        ),
    ] = None,
    subcategory: Annotated[
        str | None,
        typer.Option(
            "--subcategory",
            help="Subgroup within --category. Alphabetical unless the "
            "category declares an order. Empty to clear.",
        ),
    ] = None,
    source_name: Annotated[str | None, typer.Option("--source")] = None,
    doi: Annotated[str | None, typer.Option("--doi")] = None,
    licence: Annotated[
        str | None,
        typer.Option(
            "--licence",
            help="Terms the values are redistributed under (SPDX id if one exists).",
        ),
    ] = None,
    universe: Annotated[
        str | None,
        typer.Option(
            "--universe",
            help="Which genes were measured, in words. Empty to clear.",
        ),
    ] = None,
    update: Annotated[
        str | None,
        typer.Option("--update", help="YYYYMMDD, or empty to clear."),
    ] = None,
    touch: Annotated[
        bool,
        typer.Option("--touch", help="Set the update date to today."),
    ] = False,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Change a field's metadata without touching its values."""
    database = open_database(ctx)
    if field not in database:
        fail(f"unknown field: {field!r}")

    changes: dict[str, Any] = {
        key: value
        for key, value in (
            ("fullname", fullname),
            ("description", description),
            ("visibility", visibility),
            ("category", category),
            ("subcategory", subcategory),
            ("source", source_name),
            ("doi", doi),
            ("licence", licence),
            ("universe", universe),
            ("update", update),
        )
        if value is not None
    }
    # Not part of the comprehension: an empty string has to reach the database
    # as `None`, which the `is not None` filter above cannot express.
    if priority is not None:
        changes["priority"] = _parse_priority(priority)
    if touch:
        from genetrek.schema import today_stamp  # noqa: PLC0415 - avoids a cycle

        changes["update"] = today_stamp()

    if not changes:
        fail("nothing to change; pass at least one option")

    try:
        database.set_meta(field, **changes)
    except ValueError as exc:
        fail(str(exc))

    out.print(database.diff().describe())
    result = save_or_fail(database, actor=actor, note=note)
    _report_save(database, result)


@app.command()
def sync(
    ctx: typer.Context,
    prune: Annotated[
        bool,
        typer.Option("--prune", help="Delete metadata entries with no column."),
    ] = False,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    json_output: JsonOption = False,
) -> None:
    """Reconcile the metadata with the parquet columns, preserving what exists.

    Adds a stub for each new column and normalises key order. Curated
    descriptions, visibilities, and DOIs are never blanked.
    """
    database = open_database(ctx)
    report = database.sync_fields_description(prune=prune)
    as_json = wants_json(ctx, json_output)

    payload: dict[str, Any] = {
        "added": report.added,
        "orphans": report.orphans,
        "removed": report.removed,
        "reordered": report.reordered,
        "saved": False,
    }
    if not as_json:
        out.print(report.describe())

    if not report.changed:
        if as_json:
            emit_json(payload)
        return
    if not confirm("Write these metadata changes?", assume_yes=assume_yes):
        if as_json:
            emit_json(payload)
        else:
            out.print("aborted; nothing was written.")
        raise typer.Exit(1)

    result = save_or_fail(database, actor=actor, note=note)
    payload["saved"] = True
    payload["backup"] = str(result.backup) if result.backup else None
    if as_json:
        emit_json(payload)
    else:
        _report_save(database, result)


@app.command()
def alias(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Alias name. Omit to list every alias."),
    ] = None,
    field: Annotated[
        str | None,
        typer.Argument(help="Field the alias should point at."),
    ] = None,
    remove: Annotated[
        bool,
        typer.Option("--remove", help="Drop the alias instead of setting it."),
    ] = False,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    json_output: JsonOption = False,
) -> None:
    """Point a stable short name at a specific field.

    Useful for versioned families: `alias hc_ndd hc_ndd_v3` lets analyses refer to
    `hc_ndd` without pinning the version in every query.

    An alias is an explicit pointer, never computed. Nothing resolves "the latest
    version" on its own -- retargeting is a deliberate act that appears in the
    metadata diff and the history log, so an analysis that referred to the alias
    can still be interpreted later.

    Aliases resolve in `show`, `get` and the library. They are deliberately *not*
    accepted by `add`, `remove` or `rename`, so an alias can never be the thing
    that gets deleted.
    """
    database = open_database(ctx)

    if name is None:
        _list_aliases(database, as_json=wants_json(ctx, json_output))
        return

    if remove:
        try:
            target = database.remove_alias(name)
        except KeyError as exc:
            fail(str(exc).strip("\"'"))
        out.print(f"removed alias [bold]{name}[/] (was -> {target})")
    else:
        if field is None:
            fail(f"give a field for {name!r} to point at, or pass --remove")
        try:
            previous = database.set_alias(name, field)
        except KeyError as exc:
            fail(str(exc).strip("\"'"))
        except DatabaseError as exc:
            fail(str(exc))
        if previous == field:
            out.print(f"[dim]{name} already points at {field}; nothing to do[/]")
            return
        arrow = f"{previous} -> {field}" if previous else f"-> {field}"
        out.print(f"alias [bold]{name}[/]: {arrow}")

    result = save_or_fail(database, actor=actor, note=note)
    _report_save(database, result)


def _list_aliases(database: Database, *, as_json: bool) -> None:
    """Print every alias and what it points at."""
    mapping = database.aliases
    if as_json:
        emit_json(mapping)
        return
    if not mapping:
        out.print("No aliases defined.")
        return
    table = Table(title=f"{len(mapping)} alias(es)", header_style="bold")
    table.add_column("alias")
    table.add_column("field")
    table.add_column("label")
    for name, target in sorted(mapping.items()):
        table.add_row(name, target, database.fields[target].fullname)
    out.print(table)


@app.command()
def diff(ctx: typer.Context) -> None:
    """Show staged changes. Always empty for a freshly opened database."""
    database = open_database(ctx)
    out.print(database.diff().describe())


def _parse_fill(raw: str | None) -> Any:
    """Interpret a --fill string as a bool, number, or plain string."""
    if raw is None:
        return None
    lowered = raw.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none", ""):
        return None
    for converter in (int, float):
        try:
            return converter(raw)
        except ValueError:
            continue
    return raw


def _report_save(database: Database, result: SaveResult) -> None:
    """Print where things ended up after a successful save."""
    out.print(
        f"[green]saved[/] {len(database)} field(s), {database.n_rows:,} gene(s)",
    )
    if result.backup is not None:
        out.print(f"  backup: {result.backup}")
    if result.pruned:
        out.print(f"  pruned {len(result.pruned)} old backup(s)")
