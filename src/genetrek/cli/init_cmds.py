"""``genetrek-db init``: build a database from scratch out of live sources."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from genetrek import paths
from genetrek.cli._common import (
    confirm,
    emit_json,
    err,
    fail,
    human_bytes,
    out,
    wants_json,
)
from genetrek.cli._common import (
    get_context as _get_context,
)
from genetrek.db import Database, ValidationFailedError, storage
from genetrek.db.build import BuildError, BuildSpec, build, write_tree
from genetrek.db.database import DEFAULT_KEEP_BACKUPS
from genetrek.db.sources import HttpFetcher
from genetrek.db.sources.fetcher import FetchError

if TYPE_CHECKING:
    from genetrek.db.build import BuildResult

app = typer.Typer(no_args_is_help=True)

#: Default build spec, relative to the resources directory.
BUILD_CONFIG = Path("config") / "hgnc_build.yaml"

#: Copied aside before a replacement. Wider than ``save``'s own backup, because
#: ``init`` rewrites the webapp configuration as well as the database pair.
#: ``categories`` is carried over rather than regenerated, so it should survive
#: untouched -- it is backed up anyway, because it is hand-curated and a lost
#: one cannot be rebuilt from anything.
_BACKED_UP = (
    "parquet",
    "fields_yaml",
    "external_links",
    "badges",
    "categories",
    "backgrounds",
    "aliases",
)


@app.command()
def init(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help=f"Build spec. Defaults to {BUILD_CONFIG} in the resources tree.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Build into this resources tree instead of replacing the live one.",
        ),
    ] = None,
    release: Annotated[
        str | None,
        typer.Option(
            "--release",
            help="HGNC release: 'latest', or a YYYY-MM-DD in the archive.",
        ),
    ] = None,
    gencode_release: Annotated[
        int | None,
        typer.Option(
            "--gencode-release",
            help="Pin a GENCODE release number instead of the newest complete one.",
        ),
    ] = None,
    primary: Annotated[
        list[str] | None,
        typer.Option("--primary", help="Make this column primary. Repeatable."),
    ] = None,
    secondary: Annotated[
        list[str] | None,
        typer.Option("--secondary", help="Make this column secondary. Repeatable."),
    ] = None,
    hidden: Annotated[
        list[str] | None,
        typer.Option("--hidden", help="Make this column hidden. Repeatable."),
    ] = None,
    drop: Annotated[
        list[str] | None,
        typer.Option("--drop", help="Discard this column. Repeatable."),
    ] = None,
    no_ncbi: Annotated[
        bool,
        typer.Option(
            "--no-ncbi",
            help="Skip the NCBI fallback; leave those coordinates null.",
        ),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Re-download the cached source files."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Resolve and print the plan, then stop. Downloads nothing but "
            "the HGNC file, which the plan is checked against.",
        ),
    ] = False,
    assume_yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    actor: Annotated[
        str | None,
        typer.Option("--actor", help="Recorded in the log."),
    ] = None,
    note: Annotated[
        str | None,
        typer.Option("--note", help="Recorded in the log."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the build report as JSON."),
    ] = False,
) -> None:
    """Create a database from the HGNC complete set plus GENCODE coordinates.

    The result has no annotation columns: it is the gene backbone, ready for
    'genetrek-db add'. Writing to the live tree replaces the database that is
    there, so that path always takes a backup and always asks first.
    """
    options = _get_context(ctx)
    source_root = _source_root(options.resources)
    spec_path = config or source_root / BUILD_CONFIG
    if not spec_path.is_file():
        fail(f"no build config at {spec_path}. Pass --config PATH.")

    try:
        spec = BuildSpec.load(spec_path).with_overrides(
            primary=primary or (),
            secondary=secondary or (),
            hidden=hidden or (),
            drop=drop or (),
            release=release,
            gencode_release=gencode_release,
            use_ncbi_fallback=False if no_ncbi else None,
        )
    except BuildError as exc:
        fail(str(exc))

    target = (output or source_root).expanduser().resolve()
    # Not paths.parquet_path(): --output may name a tree that does not exist yet,
    # and resolving one insists that it does.
    replacing = (target / paths.LAYOUT["parquet"]).is_file()
    cache = paths.original_files_dir(source_root)

    out.print(f"[bold]building from[/] {spec_path}")
    try:
        with HttpFetcher(timeout=600, min_interval=0.34) as fetcher:
            if dry_run:
                _report_plan_only(fetcher, cache, spec, target, refresh=refresh)
                return
            result = build(fetcher, cache, spec, refresh=refresh)
    except (BuildError, FetchError) as exc:
        fail(str(exc))

    out.print(result.plan.describe())
    out.print("")
    out.print(result.describe())

    if not _confirm_write(result, target, replacing=replacing, assume_yes=assume_yes):
        out.print("aborted; nothing was written.")
        raise typer.Exit(1)

    written = _write(
        result,
        target,
        source_tree=source_root,
        replacing=replacing,
        actor=actor,
        note=note,
    )
    if wants_json(ctx, json_output):
        emit_json(
            {
                "resources": str(target),
                "n_genes": result.frame.height,
                "n_fields": len(result.fields),
                "hgnc_release": result.plan.hgnc_release,
                "gencode_release": result.coordinates.gencode_release,
                "backup": str(written["backup"]) if written.get("backup") else None,
                "config": [str(p) for k, p in written.items() if k != "backup"],
            },
        )


def _source_root(override: Path | None) -> Path:
    """The resources tree the build spec and the caches come from."""
    try:
        return paths.resources_dir(override)
    except paths.ResourcesNotFoundError as exc:
        fail(str(exc))


def _report_plan_only(
    fetcher: HttpFetcher,
    cache: Path,
    spec: BuildSpec,
    target: Path,
    *,
    refresh: bool,
) -> None:
    """Resolve the plan against the real release and print it, writing nothing."""
    from genetrek.db.build import plan_build  # noqa: PLC0415 - only the dry path
    from genetrek.db.sources import hgnc  # noqa: PLC0415

    downloaded = hgnc.fetch(fetcher, cache, spec.release, refresh=refresh)
    raw = hgnc.read(downloaded.cached)
    plan = plan_build(spec, raw.columns)
    out.print(plan.describe())
    out.print("")
    out.print(f"[bold]would write[/] {plan.n_genes or raw.height:,} genes to {target}")
    out.print("[yellow]--dry-run:[/] no coordinates were fetched, nothing written.")


def _confirm_write(
    result: BuildResult,
    target: Path,
    *,
    replacing: bool,
    assume_yes: bool,
) -> bool:
    """Ask before writing, naming what a replacement would cost."""
    if not replacing:
        out.print(f"\n[bold]writing a new database to[/] {target}")
        return confirm(f"Create the database in {target}?", assume_yes=assume_yes)

    out.print(f"\n[red]about to REPLACE the database at[/] {target}")
    try:
        current = Database.open(target)
    except Exception:  # noqa: BLE001 - an unreadable current database is not fatal here
        out.print("  current: unreadable")
    else:
        out.print(f"  current: {len(current):,} fields, {current.n_rows:,} genes")
        keeping = set(result.fields) & set(current.fields)
        out.print(
            f"  new:     {len(result.fields):,} fields, "
            f"{result.frame.height:,} genes "
            f"({len(keeping)} field name(s) in common)",
        )
        lost = sorted(set(current.fields) - set(result.fields))
        if lost:
            out.print(
                f"  [red]this discards {len(lost)} field(s), every annotation "
                f"column included[/] (a backup is taken first)",
            )
    return confirm(
        f"Replace the database in {target}?",
        assume_yes=assume_yes,
    )


def _write(
    result: BuildResult,
    target: Path,
    *,
    source_tree: Path,
    replacing: bool,
    actor: str | None,
    note: str | None,
) -> dict[str, Path | None]:
    """Back up what is there, write the new tree, and report every file touched.

    The backup lives here rather than in :func:`write_tree` because it is a CLI
    policy: only this layer knows whether it is replacing a curated database or
    creating one, and it is wider than ``save``'s own backup because ``init``
    rewrites the webapp configuration too.
    """
    written: dict[str, Path | None] = {"backup": None}

    if replacing:
        paths.make_tree(target)
        backups = paths.backups_dir(target)
        written["backup"] = storage.make_backup(
            backups,
            [target / paths.LAYOUT[kind] for kind in _BACKED_UP],
        )
        if written["backup"]:
            # Printed here rather than at the end: if the save is refused after
            # this point, the user still needs to know where the copy went.
            out.print(f"[dim]backup[/]  {written['backup']}")
        storage.prune_backups(backups, DEFAULT_KEEP_BACKUPS)

    try:
        report = write_tree(
            result,
            target,
            source_tree=source_tree,
            actor=actor,
            note=note,
        )
    except ValidationFailedError as exc:
        err.print(exc.report.describe())
        fail(
            "the built database does not validate, so nothing was written. "
            "This usually means the webapp configuration in the target tree "
            "references columns the new schema does not have.",
        )
    written.update(report.ui_config)

    out.print("")
    out.print(
        f"[green]written[/] {report.parquet} "
        f"({human_bytes(report.parquet.stat().st_size)})",
    )
    out.print(f"[green]written[/] {report.fields_yaml}")
    for path in report.ui_config.values():
        out.print(f"[green]written[/] {path}  (matches the new column names)")
    if report.alias_index:
        out.print(
            f"[green]written[/] {report.alias_index}  "
            f"(so retired symbols resolve without the 34 MB source file)",
        )
    for path in report.carried:
        out.print(f"[dim]copied[/]  {path}")

    validation = Database.open(target).validate()
    if validation.errors or validation.warnings:
        out.print("")
        out.print(validation.describe())
    else:
        out.print("\n[green]validate:[/] clean")
    if target != paths.resources_dir(None).resolve():
        out.print(f"\ntry it: GENETREK_RESOURCES={target} uv run genetrek")
    return written


__all__ = ["app"]
