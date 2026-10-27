"""Location of the on-disk ``resources/`` tree.

The webapp and the maintenance tooling must agree on where the data lives, so
resolution happens here and nowhere else. Order of precedence:

1. an explicit argument,
2. the ``GENETREK_RESOURCES`` environment variable,
3. discovery by walking up from this module looking for a ``resources/``
   directory that contains ``config/fields_description.yaml``.

Discovery is marker-based rather than a fixed number of ``.parent`` hops so it
keeps working when a module moves to a different depth in the package.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "GENETREK_RESOURCES"

# Relative path that identifies a directory as a GeneTrek resources tree.
_MARKER = Path("config") / "fields_description.yaml"

# Subpaths within the resources tree.
_FIELDS_YAML = Path("config") / "fields_description.yaml"
_CATEGORIES_YAML = Path("config") / "categories.yaml"
_BACKGROUNDS_YAML = Path("config") / "backgrounds.yaml"
_BADGES_YAML = Path("config") / "badges.yaml"
_EXTERNAL_LINKS_YAML = Path("config") / "external_links.yaml"
_TOKEN_COLORS_YAML = Path("config") / "token_colors.yaml"
_SITE_DESCRIPTION_MD = Path("content") / "site_description.md"
_CONTENT = Path("content")
_PARQUET = Path("processed_data") / "genetrek.parquet"
_COLUMN_MAPPING = Path("processed_data") / "column_mapping.json"
_ALIASES = Path("processed_data") / "gene_aliases.parquet"
_HISTORY = Path("processed_data") / "history.jsonl"
_BACKUPS = Path("processed_data") / ".backups"
_ORIGINAL_FILES = Path("original_file")
_STATIC = Path("static")

#: Every file in a resources tree, relative to its root.
#:
#: The lookup functions below resolve a tree that already exists, which is what
#: every caller wants except one: ``genetrek-db init --output`` has to name the
#: files of a tree it is about to create. Use this there, so a new layout is
#: still described in exactly one place.
LAYOUT: dict[str, Path] = {
    "fields_yaml": _FIELDS_YAML,
    "categories": _CATEGORIES_YAML,
    "backgrounds": _BACKGROUNDS_YAML,
    "badges": _BADGES_YAML,
    "external_links": _EXTERNAL_LINKS_YAML,
    "token_colors": _TOKEN_COLORS_YAML,
    "site_description": _SITE_DESCRIPTION_MD,
    "parquet": _PARQUET,
    "column_mapping": _COLUMN_MAPPING,
    "aliases": _ALIASES,
    "history": _HISTORY,
}

#: Directories in a resources tree that hold no single named file.
_DIRECTORIES = (_ORIGINAL_FILES, _STATIC)


def make_tree(root: Path | str) -> Path:
    """Create the directory skeleton of a resources tree, and return its root.

    Only directories: an empty tree is not yet a valid one, since discovery keys
    on ``config/fields_description.yaml`` being present.
    """
    base = Path(root).expanduser()
    for relative in {*(p.parent for p in LAYOUT.values()), *_DIRECTORIES}:
        (base / relative).mkdir(parents=True, exist_ok=True)
    return base


class ResourcesNotFoundError(RuntimeError):
    """The resources directory could not be located."""


def resources_dir(override: Path | str | None = None) -> Path:
    """Return the resources directory.

    Args:
        override: Explicit path, taking precedence over the environment
            variable and over discovery.

    Returns:
        The resolved resources directory.

    Raises:
        ResourcesNotFoundError: If an explicit or environment-provided path is
            not an existing directory, or if discovery finds no candidate.
    """
    if override is not None:
        return _check_explicit(Path(override).expanduser(), source="argument")

    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return _check_explicit(
            Path(env_value).expanduser(),
            source=f"${ENV_VAR}",
        )

    return _discover()


def _check_explicit(path: Path, *, source: str) -> Path:
    if not path.exists():
        msg = f"Resources directory from {source} does not exist: {path}"
        raise ResourcesNotFoundError(msg)
    if not path.is_dir():
        msg = f"Resources path from {source} is not a directory: {path}"
        raise ResourcesNotFoundError(msg)
    return path.resolve()


def _discover() -> Path:
    start = Path(__file__).resolve().parent
    for ancestor in (start, *start.parents):
        candidate = ancestor / "resources"
        if (candidate / _MARKER).is_file():
            return candidate
    msg = (
        "Could not locate a GeneTrek resources directory: no 'resources/"
        f"{_MARKER.as_posix()}' found in any parent of {start}. "
        f"Set ${ENV_VAR} to point at it."
    )
    raise ResourcesNotFoundError(msg)


def fields_yaml_path(resources: Path | str | None = None) -> Path:
    """Path to ``config/fields_description.yaml``."""
    return resources_dir(resources) / _FIELDS_YAML


def categories_path(resources: Path | str | None = None) -> Path:
    """Path to ``config/categories.yaml``.

    Unlike its siblings this file is optional: ``load_categories_yaml`` treats
    its absence as "no categories declared" rather than an error.
    """
    return resources_dir(resources) / _CATEGORIES_YAML


def backgrounds_path(resources: Path | str | None = None) -> Path:
    """Path to ``config/backgrounds.yaml``, the gene-set comparison universes.

    Optional, like ``categories.yaml``: without one the only background is
    every gene. See :mod:`genetrek.backgrounds`.
    """
    return resources_dir(resources) / _BACKGROUNDS_YAML


def badges_path(resources: Path | str | None = None) -> Path:
    """Path to ``config/badges.yaml``."""
    return resources_dir(resources) / _BADGES_YAML


def external_links_path(resources: Path | str | None = None) -> Path:
    """Path to ``config/external_links.yaml``."""
    return resources_dir(resources) / _EXTERNAL_LINKS_YAML


def token_colors_path(resources: Path | str | None = None) -> Path:
    """Path to ``config/token_colors.yaml``."""
    return resources_dir(resources) / _TOKEN_COLORS_YAML


def site_description_path(resources: Path | str | None = None) -> Path:
    """Path to ``content/site_description.md``."""
    return resources_dir(resources) / _SITE_DESCRIPTION_MD


def content_dir(resources: Path | str | None = None) -> Path:
    """The directory holding the prose pages.

    One accessor rather than one per page: the pages are declared in
    :data:`genetrek.content.CONTENT_PAGES`, and a file per entry there would
    otherwise mean a constant and an accessor per entry here too.
    """
    return resources_dir(resources) / _CONTENT


def content_path(slug: str, resources: Path | str | None = None) -> Path:
    """The markdown file backing one prose page."""
    return content_dir(resources) / f"{slug}.md"


def parquet_path(resources: Path | str | None = None) -> Path:
    """Path to the parquet database read by the webapp."""
    return resources_dir(resources) / _PARQUET


def aliases_path(resources: Path | str | None = None) -> Path:
    """Where the precomputed gene-symbol alias index lives.

    Optional, unlike the parquet beside it: it is a distillation of the HGNC
    complete set, which is gitignored and absent from the container, so this is
    how retired symbols resolve in a deployment. A tree without it resolves
    current symbols only.
    """
    return resources_dir(resources) / _ALIASES


def column_mapping_path(resources: Path | str | None = None) -> Path:
    """Path to ``processed_data/column_mapping.json``."""
    return resources_dir(resources) / _COLUMN_MAPPING


def history_path(resources: Path | str | None = None) -> Path:
    """Path to the append-only mutation log."""
    return resources_dir(resources) / _HISTORY


def backups_dir(resources: Path | str | None = None) -> Path:
    """Directory holding timestamped parquet/YAML backups."""
    return resources_dir(resources) / _BACKUPS


def original_files_dir(resources: Path | str | None = None) -> Path:
    """Directory holding upstream source files (HGNC complete set, TSVs)."""
    return resources_dir(resources) / _ORIGINAL_FILES


def static_dir(resources: Path | str | None = None) -> Path:
    """Directory served verbatim at ``/data``.

    Curated companion files rather than database content: the UCSC track hub and
    its bigBeds, and the Rolland et al. VCF cohorts. They are served under the
    URLs the previous GeneTrek published, which third parties reference -- the
    track hub in particular is loaded by UCSC sessions we do not control.
    """
    return resources_dir(resources) / _STATIC


def latest_hgnc_file(resources: Path | str | None = None) -> Path | None:
    """Return the newest ``hgnc_complete_set*`` file, or ``None`` if absent.

    Selection is by modification time so a freshly downloaded release wins over
    an older one left in place.
    """
    directory = original_files_dir(resources)
    if not directory.is_dir():
        return None
    candidates = sorted(
        (p for p in directory.glob("hgnc_complete_set*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None
