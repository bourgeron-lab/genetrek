"""The ``genetrek-validate-fields`` entry point.

Kept as a thin wrapper over :meth:`Database.validate` so there is one definition
of what "consistent" means. ``genetrek-db validate`` is the fuller command --
it takes ``--resources`` and ``--json`` -- but this name is documented and
scripted against, so it stays.
"""

from __future__ import annotations

import sys

from genetrek.db import Database, DatabaseError
from genetrek.paths import ResourcesNotFoundError
from genetrek.schema import FieldsYamlError


def main() -> None:
    """Validate the database, exiting non-zero on any error."""
    try:
        database = Database.open()
    except (ResourcesNotFoundError, DatabaseError, FieldsYamlError) as exc:
        print(exc)  # noqa: T201
        sys.exit(1)

    report = database.validate()
    for line in report.errors:
        print(f"ERROR: {line}")  # noqa: T201
    for line in report.warnings:
        print(f"WARNING: {line}")  # noqa: T201
    for line in report.notes:
        print(f"NOTE: {line}")  # noqa: T201

    if report.ok:
        print(  # noqa: T201
            f"All columns match between data and config "
            f"({len(database)} fields, {database.n_rows:,} genes).",
        )
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
