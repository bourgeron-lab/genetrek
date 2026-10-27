from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import polars as pl
import yaml

from genetrek import paths, schema
from genetrek.backgrounds import (
    Background,
    Covariate,
    load_backgrounds,
    load_covariates,
)
from genetrek.content import CONTENT_PAGES
from genetrek.schema import (
    CategoriesYamlError,
    CategoryOrder,
    ColumnGroup,
    FieldsYamlError,
    Roles,
    load_categories_yaml,
    load_fields_yaml,
    resolve_roles,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    df: pl.DataFrame = field(repr=False)
    display_config: dict[str, dict[str, Any]] = field(repr=False)
    common_columns: list[str] = field(repr=False)
    primary_columns: list[str] = field(repr=False)
    secondary_columns: list[str] = field(repr=False)
    visible_columns: list[str] = field(repr=False)
    #: Column name -> its rank in ``visible_columns``. Lets any component put
    #: an arbitrary subset back into display order without redoing the sort.
    column_order: dict[str, int] = field(repr=False)
    boolean_columns: list[str] = field(repr=False)
    column_names: dict[str, str] = field(repr=False)
    boolean_column_names: dict[str, str] = field(repr=False)
    badges_config: dict[str, Any] = field(repr=False)
    external_links_config: dict[str, Any] = field(repr=False)
    token_colors_config: dict[str, str] = field(repr=False)
    site_description: str = field(repr=False)
    #: Slug -> markdown, one entry per :data:`genetrek.content.CONTENT_PAGES`.
    documents: dict[str, str] = field(repr=False)
    #: Which column fills each semantic role, so search, enrichment and
    #: resolution work without hardcoding any column name.
    roles: Roles = field(repr=False)
    #: The declared category order, which is what puts ``visible_columns``
    #: into groups before ``priority`` orders them inside one.
    categories: CategoryOrder = field(repr=False)
    #: ``visible_columns`` as a category -> subcategory -> column tree, ready
    #: for the column selector. Flattens back to ``visible_columns``.
    column_groups: list[ColumnGroup] = field(repr=False)
    #: The parsed field metadata for every column in ``df``, keyed by column
    #: name. ``display_config`` holds the same information as plain dicts for
    #: the UI; this keeps the typed objects, which the API reads through.
    fields: dict[str, schema.FieldMeta] = field(repr=False)
    #: Alias -> the real column it points at, built once. ``Database.aliases``
    #: rebuilds this on every access, which is fine per CLI invocation and
    #: wrong per HTTP request.
    field_aliases: dict[str, str] = field(repr=False)
    #: Every boolean column in ``df``, hidden ones included -- so it is not
    #: ``boolean_columns``, which is deliberately restricted to what the table
    #: can show. This is the set the API treats as gene lists.
    all_boolean_columns: list[str] = field(repr=False)
    #: The resolved resources tree, so a component can find an optional file
    #: (the alias index) without re-running ``paths.resources_dir()``.
    resources: Path = field(repr=False)
    #: The release the gene set comes from -- the primary key's ``update``
    #: stamp, which ``genetrek-db init`` writes from the HGNC release. A user
    #: list records it, so a list opened against a later database can say
    #: which genes it was made with. ``""`` when the metadata does not say.
    release: str = field(default="", repr=False)
    #: The universes a gene-set comparison can be computed within, the default
    #: first. Never empty: without ``backgrounds.yaml`` it is every gene.
    backgrounds: list[Background] = field(default_factory=list, repr=False)
    #: Properties a comparison can be stratified by (``backgrounds.yaml``
    #: ``covariates``). Empty without the file: comparisons are then crude only.
    covariates: list[Covariate] = field(default_factory=list, repr=False)


def _load_yaml(path: Path) -> Any:  # noqa: ANN401
    with path.open() as f:
        return yaml.safe_load(f)


def _load_categories(
    path: Path,
    fields: dict[str, schema.FieldMeta],
) -> CategoryOrder:
    """Load the declared category order, warning about anything undeclared.

    A missing file is not an error: it means no categories are declared, which
    orders columns by ``priority`` alone.
    """
    try:
        categories = load_categories_yaml(path, fields)
    except CategoriesYamlError as exc:
        print(exc)  # noqa: T201
        sys.exit(1)
    for name in categories.undeclared:
        print(  # noqa: T201
            f"Warning: category {name!r} is not declared in {path.name}; "
            f"its columns sort after every declared category",
        )
    return categories


def _require_file(path: Path) -> None:
    if not path.exists():
        print(f"Required file not found: {path}")  # noqa: T201
        sys.exit(1)


def _backgrounds(
    root: Path,
    columns: list[str],
) -> tuple[list[Background], list[Covariate]]:
    """The comparison universes and covariates, dropping bad entries with a warning."""
    path = paths.backgrounds_path(root)
    backgrounds, problems = load_backgrounds(path, columns)
    covariates, covariate_problems = load_covariates(path, columns)
    for problem in [*problems, *covariate_problems]:
        print(f"Warning: backgrounds.yaml: {problem}")  # noqa: T201
    return backgrounds, covariates


def load_config(resources_dir: Path | None = None) -> AppConfig:
    """Load all configuration files and data, returning an AppConfig.

    Resource locations come from :mod:`genetrek.paths`, and field metadata is
    parsed through :class:`~genetrek.schema.FieldMeta`, so an invalid
    ``visibility`` or a missing ``fullname`` is reported at startup instead of
    silently hiding a column or raising a bare ``KeyError`` later.
    """
    root = paths.resources_dir(resources_dir)

    config_path = paths.fields_yaml_path(root)
    badges_path = paths.badges_path(root)
    external_links_path = paths.external_links_path(root)
    token_colors_path = paths.token_colors_path(root)
    site_description_path = paths.site_description_path(root)
    document_paths = {
        page.slug: paths.content_path(page.slug, root) for page in CONTENT_PAGES
    }
    data_path = paths.parquet_path(root)
    # Deliberately absent from the loop below: this file is optional, and
    # `_require_file` exits. A tree without one orders columns by priority
    # alone, which is what every tree did before categories existed.
    categories_path = paths.categories_path(root)

    for path in (
        config_path,
        badges_path,
        external_links_path,
        token_colors_path,
        site_description_path,
        *document_paths.values(),
        data_path,
    ):
        _require_file(path)

    try:
        fields = load_fields_yaml(config_path)
    except FieldsYamlError as exc:
        print(exc)  # noqa: T201
        sys.exit(1)
    config: dict[str, dict[str, Any]] = {
        name: meta.model_dump() for name, meta in fields.items()
    }
    roles = resolve_roles(fields)
    for problem in roles.problems:
        print(f"Warning: {problem}")  # noqa: T201

    categories = _load_categories(categories_path, fields)

    badges_config: dict[str, Any] = _load_yaml(badges_path)
    external_links_config: dict[str, Any] = _load_yaml(external_links_path)
    token_colors_config: dict[str, str] = _load_yaml(token_colors_path)
    site_description: str = site_description_path.read_text()
    documents: dict[str, str] = {
        slug: path.read_text() for slug, path in document_paths.items()
    }

    df = pl.read_parquet(str(data_path))

    # Validate columns
    df_columns = set(df.columns)
    config_columns = set(config.keys())
    missing_in_config = df_columns - config_columns
    missing_in_df = config_columns - df_columns

    if missing_in_config:
        print(f"Warning: Columns in data but not in config: {missing_in_config}")  # noqa: T201
    if missing_in_df:
        print(f"Warning: Columns in config but not in data: {missing_in_df}")  # noqa: T201

    # Filter to common columns, preserving DataFrame column order
    common_columns = [col for col in df.columns if col in config_columns]
    df = df.select(common_columns)

    display_config = {col: config[col] for col in common_columns}

    # Display order is a metadata value, not the parquet's column order: group
    # every non-hidden column by `category` then `subcategory`, and order it
    # within its group by `priority`, then by label. `common_columns`
    # deliberately stays in parquet order -- that is the *data* order, which
    # `df.select` and both download paths depend on.
    visible_fields = {
        name: meta
        for name, meta in fields.items()
        if name in df_columns and meta.visibility != "hidden"
    }
    visible_columns = schema.order_fields(visible_fields, categories)
    column_groups = schema.group_fields(visible_fields, categories)
    column_order = {col: rank for rank, col in enumerate(visible_columns)}
    primary_columns = [
        col
        for col in visible_columns
        if display_config[col].get("visibility") == "primary"
    ]
    secondary_columns = [
        col
        for col in visible_columns
        if display_config[col].get("visibility") == "secondary"
    ]

    column_names = {col: display_config[col]["fullname"] for col in visible_columns}
    boolean_columns = [
        col for col in visible_columns if df.schema.get(col) == pl.Boolean
    ]
    boolean_column_names = {
        col: display_config[col]["fullname"] for col in boolean_columns
    }

    # Keyed on `common_columns` rather than on `fields`, so nothing the API
    # lists can be a column the parquet does not have. An alias whose target
    # was dropped that way goes with it.
    common_fields = {col: fields[col] for col in common_columns}
    field_aliases = {
        alias: name for name, meta in common_fields.items() for alias in meta.aliases
    }
    all_boolean_columns = [
        col for col in common_columns if df.schema.get(col) == pl.Boolean
    ]

    backgrounds, covariates = _backgrounds(root, df.columns)

    return AppConfig(
        df=df,
        display_config=display_config,
        common_columns=common_columns,
        primary_columns=primary_columns,
        secondary_columns=secondary_columns,
        visible_columns=visible_columns,
        column_order=column_order,
        boolean_columns=boolean_columns,
        column_names=column_names,
        boolean_column_names=boolean_column_names,
        badges_config=badges_config,
        external_links_config=external_links_config,
        token_colors_config=token_colors_config,
        site_description=site_description,
        documents=documents,
        roles=roles,
        categories=categories,
        column_groups=column_groups,
        fields=common_fields,
        field_aliases=field_aliases,
        all_boolean_columns=all_boolean_columns,
        resources=root,
        release=(
            common_fields[roles.primary_key].update
            if roles.primary_key in common_fields
            else ""
        ),
        backgrounds=backgrounds,
        covariates=covariates,
    )
