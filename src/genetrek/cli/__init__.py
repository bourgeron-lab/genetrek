"""``genetrek-db``: maintain the parquet/YAML database from the command line.

Kept separate from the ``genetrek`` webapp entry point so the tool never imports
FastAPI or NiceGUI, and so a broken database can still be inspected and repaired.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from genetrek.cli._common import Context
from genetrek.cli.init_cmds import app as init_app
from genetrek.cli.inspect_cmds import app as inspect_app
from genetrek.cli.mutate_cmds import app as mutate_app

app = typer.Typer(
    name="genetrek-db",
    help=(
        "Inspect and maintain the GeneTrek database "
        "(resources/processed_data/genetrek.parquet plus "
        "resources/config/fields_description.yaml)."
    ),
    no_args_is_help=True,
    add_completion=False,
)

for source in (inspect_app, mutate_app, init_app):
    for command in source.registered_commands:
        app.registered_commands.append(command)


@app.callback()
def main_options(
    ctx: typer.Context,
    resources: Annotated[
        Path | None,
        typer.Option(
            "--resources",
            envvar="GENETREK_RESOURCES",
            help="Resources directory. Defaults to the one in the checkout.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of tables."),
    ] = False,
) -> None:
    """Store the options every command shares."""
    ctx.obj = Context(resources=resources, json_output=json_output)


def main() -> None:
    """Console-script entry point."""
    app()


__all__ = ["app", "main"]
