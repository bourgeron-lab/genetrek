"""Tools for creating and maintaining the GeneTrek parquet/YAML database.

Intended for interactive use in a notebook::

    from genetrek.db import Database

    db = Database.open()
    db.list_fields()
    db["loeuf"].stats()

and from the command line through ``genetrek-db``. Importing this package pulls
in only Polars, PyYAML, and Pydantic -- never the webapp.
"""

from __future__ import annotations

from genetrek.db.database import (
    LEGACY_PRIMARY_KEY,
    Database,
    DatabaseError,
    DiffReport,
    ResolutionFailedError,
    SyncReport,
    ValidationFailedError,
    ValidationReport,
)
from genetrek.db.field import BoundField, Field, FieldStats, field_stats
from genetrek.db.gene_index import (
    Detection,
    GeneIndex,
    GeneIndexError,
    ResolutionReport,
)
from genetrek.db.storage import SaveResult

__all__ = [
    "LEGACY_PRIMARY_KEY",
    "BoundField",
    "Database",
    "DatabaseError",
    "Detection",
    "DiffReport",
    "Field",
    "FieldStats",
    "GeneIndex",
    "GeneIndexError",
    "ResolutionFailedError",
    "ResolutionReport",
    "SaveResult",
    "SyncReport",
    "ValidationFailedError",
    "ValidationReport",
    "field_stats",
]
