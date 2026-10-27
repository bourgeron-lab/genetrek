"""Shared plumbing for the ``genetrek-db`` commands.

Nothing here imports :mod:`genetrek.app`: that module loads the config and calls
``sys.exit(1)`` at import time, which would make a missing file unrecoverable
before a command had a chance to report it properly.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from genetrek.db import Database, DatabaseError, ValidationFailedError
from genetrek.db.storage import SaveResult, interrupted_save
from genetrek.paths import ResourcesNotFoundError, parquet_path
from genetrek.schema import FieldsYamlError

if TYPE_CHECKING:
    from pathlib import Path

    import polars as pl

#: Written to stdout; diagnostics go to stderr so output stays pipeable.
out = Console()
err = Console(stderr=True)


@dataclass
class Context:
    """Options shared by every command."""

    resources: Path | None = None
    json_output: bool = False


#: Reusable ``--json`` flag. Declared per command as well as globally, because a
#: callback-only option would have to be written before the subcommand
#: (``genetrek-db --json list``), which nobody expects.
JsonOption = Annotated[
    bool,
    typer.Option("--json", help="Emit JSON instead of tables."),
]


def get_context(ctx: typer.Context) -> Context:
    """Return the shared options attached to the Typer context."""
    if isinstance(ctx.obj, Context):
        return ctx.obj
    return Context()


def wants_json(ctx: typer.Context, local: bool = False) -> bool:
    """Whether JSON output was requested, before or after the subcommand."""
    return local or get_context(ctx).json_output


def save_or_fail(database: Database, **kwargs: Any) -> SaveResult:
    """Save, turning a validation failure into a clean CLI error.

    Removing a field the webapp configuration still references is the usual
    cause: the staged change is legal, but writing it would leave the app with a
    dangling reference, so the config has to be updated too.
    """
    try:
        return database.save(**kwargs)
    except ValidationFailedError as exc:
        err.print(exc.report.describe())
        msg = (
            "the staged changes would leave the database inconsistent, so "
            "nothing was written. Fix the problems above (the webapp YAML "
            "configuration may also need updating) and try again."
        )
        err.print(f"[red]error:[/] {msg}")
        raise typer.Exit(1) from exc


def open_database(ctx: typer.Context) -> Database:
    """Open the database, turning setup problems into clean CLI errors.

    Also warns when a previous save was interrupted between the two file swaps,
    since that is the one situation where the pair on disk may disagree.
    """
    options = get_context(ctx)
    try:
        database = Database.open(options.resources)
    except ResourcesNotFoundError as exc:
        fail(str(exc))
    except FieldsYamlError as exc:
        fail(str(exc))
    except DatabaseError as exc:
        fail(str(exc))

    if interrupted_save(parquet_path(database.resources).parent):
        err.print(
            "[yellow]warning:[/] a previous save was interrupted; the parquet and "
            "the metadata may disagree. Run 'genetrek-db validate' and restore "
            "from processed_data/.backups/ if needed.",
        )
    return database


def fail(message: str, code: int = 1) -> NoReturn:
    """Print an error and exit."""
    err.print(f"[red]error:[/] {message}")
    raise typer.Exit(code)


def require_real_field(database: Database, name: str) -> None:
    """Refuse an alias where only a real field name will do.

    Destructive operations must never act on an alias: ``remove hc_ndd`` should
    not delete whatever ``hc_ndd`` happens to point at today.
    """
    if name in database.fields:
        return
    if (target := database.aliases.get(name)) is not None:
        fail(
            f"{name!r} is an alias for {target!r}. This command needs the real "
            f"field name, so an alias can never be what gets changed.",
        )
    fail(f"unknown field: {name!r}")


def emit_json(payload: Any) -> None:
    """Write a JSON payload to stdout."""
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")


def frame_to_table(frame: pl.DataFrame, title: str | None = None) -> Table:
    """Render a Polars frame as a Rich table, right-aligning numeric columns."""
    table = Table(title=title, header_style="bold", title_justify="left")
    for name, dtype in frame.schema.items():
        table.add_column(name, justify="right" if dtype.is_numeric() else "left")
    for row in frame.iter_rows():
        table.add_row(*("" if value is None else str(value) for value in row))
    return table


def key_value_table(rows: list[tuple[str, str]], title: str | None = None) -> Table:
    """Render label/value pairs as a two-column table without a header."""
    table = Table(title=title, show_header=False, box=None, title_justify="left")
    table.add_column(style="dim")
    table.add_column()
    for label, value in rows:
        table.add_row(label, value)
    return table


#: Bytes per binary unit step.
_UNIT_STEP = 1024


def human_bytes(size: float) -> str:
    """Format a byte count with a binary unit suffix."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(size) < _UNIT_STEP or unit == "GiB":
            precision = 0 if unit == "B" else 1
            return f"{size:.{precision}f} {unit}"
        size /= _UNIT_STEP
    raise AssertionError  # pragma: no cover - unreachable


def print_report(lines: list[str], style: str) -> None:
    """Print report lines with a consistent prefix style."""
    for line in lines:
        err.print(f"[{style}]{line}[/]")


def confirm(question: str, *, assume_yes: bool) -> bool:
    """Ask for confirmation unless it has been waived.

    Refuses rather than blocking when stdin is not a terminal, so a piped
    invocation cannot hang or silently proceed.
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        err.print("[red]error:[/] not a terminal; pass --yes to confirm.")
        return False
    return typer.confirm(question)
