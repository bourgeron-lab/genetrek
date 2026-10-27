"""Shared fixtures: a miniature resources tree that mirrors the real database.

The synthetic parquet deliberately reproduces the awkward properties of
``genetrek.parquet`` rather than an idealised table: ``symbol`` is not unique and
the secondary identifiers are sparse. It additionally plants a literal ``"None"``
string in ``note`` -- the real database does not have that problem, but the
marker check exists to catch a future import that introduces it, so something
must exercise it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import polars as pl
import pytest
import yaml

from genetrek import schema
from genetrek.cli import _common
from genetrek.content import CONTENT_PAGES
from genetrek.schema import FieldMeta

if TYPE_CHECKING:
    from pathlib import Path

N_ROWS = 20


@dataclass(frozen=True)
class SyntheticResources:
    """A complete resources tree on disk, ready to open as a Database."""

    root: Path
    parquet: Path
    fields_yaml: Path
    frame: pl.DataFrame


def _build_frame() -> pl.DataFrame:
    ncbi = list(range(1, N_ROWS + 1))
    symbols = [f"GENE{i:02d}" for i in ncbi]
    # A duplicated symbol, as in the real database, so ambiguity is reachable.
    symbols[-1] = symbols[0]
    return pl.DataFrame(
        {
            # Primary key: never null, unique.
            "ncbi_id": pl.Series(ncbi, dtype=pl.Int32),
            # Sparse secondary identifiers.
            "ensg": [f"ENSG{i:011d}" if i % 3 else None for i in ncbi],
            "symbol": symbols,
            "hgnc_id": pl.Series(
                [i * 10 if i % 2 else None for i in ncbi],
                dtype=pl.Int32,
            ),
            "omim_id": pl.Series(
                [600000 + i if i % 5 == 0 else None for i in ncbi],
                dtype=pl.Int32,
            ),
            "gene_type": pl.Series(
                ["protein-coding" if i % 2 else "ncRNA" for i in ncbi],
                dtype=pl.Categorical,
            ),
            "hg38_chr": [str((i % 22) + 1) for i in ncbi],
            "hg38_start": pl.Series([i * 1000 for i in ncbi], dtype=pl.Int32),
            "hg38_end": pl.Series([i * 1000 + 500 for i in ncbi], dtype=pl.Int32),
            # Booleans, as most gene-list annotations are.
            "flag_a": [i % 2 == 0 for i in ncbi],
            "flag_b": [i <= 3 for i in ncbi],
            "score": pl.Series([i / 4 for i in ncbi], dtype=pl.Float32),
            # In schema.CODE_DEPENDENT_COLUMNS: the constraint plot asks for it
            # by literal name, so renaming it is guarded.
            "loeuf": pl.Series([i / 10 for i in ncbi], dtype=pl.Float32),
            "small_count": pl.Series([i % 7 for i in ncbi], dtype=pl.Int8),
            # Literal "None" as a missing marker, to exercise the guard.
            "note": ["None" if i % 4 else f"note-{i}" for i in ncbi],
        },
    )


def _build_fields(frame: pl.DataFrame) -> dict[str, FieldMeta]:
    id_types: dict[str, str] = {
        "ncbi_id": "ncbi",
        "ensg": "ensg",
        "symbol": "symbol",
        "hgnc_id": "hgnc",
        "omim_id": "omim",
        "hg38_chr": "locus_chr",
        "hg38_start": "locus_start",
        "hg38_end": "locus_end",
    }
    # Coordinate roles need to say which assembly they describe.
    assemblies: dict[str, str] = {
        "hg38_chr": "hg38",
        "hg38_start": "hg38",
        "hg38_end": "hg38",
    }
    visibility: dict[str, str] = {
        "symbol": "primary",
        "flag_a": "primary",
        "flag_b": "secondary",
        "score": "secondary",
        "gene_type": "secondary",
        "note": "secondary",
        "small_count": "secondary",
    }
    # Display priority, in the same shape as the tables above. Chosen so the
    # fixture leads with `symbol` (all-unset would sort "Flag A" first) and so
    # four columns stay unset, exercising the null-last path.
    priorities: dict[str, int] = {"symbol": 10, "flag_a": 20, "flag_b": 30}
    return {
        name: FieldMeta(
            fullname=name.replace("_", " ").title(),
            value_type=schema.value_type_for_dtype(frame.schema[name]),
            id_type=id_types.get(name),  # type: ignore[arg-type]
            assembly=assemblies.get(name, ""),
            primary_key=name == "ncbi_id",
            visibility=visibility.get(name, "hidden"),  # type: ignore[arg-type]
            priority=priorities.get(name),
        )
        for name in frame.columns
    }


@pytest.fixture
def resources(tmp_path: Path) -> SyntheticResources:
    """Write a miniature resources tree into ``tmp_path`` and return its paths."""
    root = tmp_path / "resources"
    (root / "config").mkdir(parents=True)
    (root / "content").mkdir()
    (root / "processed_data").mkdir()
    (root / "original_file").mkdir()

    frame = _build_frame()
    parquet = root / "processed_data" / "genetrek.parquet"
    frame.write_parquet(parquet)

    fields_yaml = root / "config" / "fields_description.yaml"
    schema.write_fields_yaml(fields_yaml, _build_fields(frame))

    (root / "config" / "badges.yaml").write_text(
        yaml.safe_dump({"gene_type": {"ncRNA": {"label": "nc", "color": "#000000"}}}),
        encoding="utf-8",
    )
    (root / "config" / "external_links.yaml").write_text(
        yaml.safe_dump(
            {
                "links": [
                    {"name": "NCBI Gene", "url": "https://example.org/g/{ncbi_id}"},
                    {"name": "Ensembl", "url": "https://example.org/e/{ensg}"},
                ],
            },
        ),
        encoding="utf-8",
    )
    (root / "config" / "token_colors.yaml").write_text(
        yaml.safe_dump({"symbol": "#38ada9"}),
        encoding="utf-8",
    )
    (root / "content" / "site_description.md").write_text(
        "# Test\n",
        encoding="utf-8",
    )
    # One per declared prose page: `load_config` requires them all, so a page
    # added to CONTENT_PAGES without a file here fails every test that loads a
    # config -- which is the intended signal, not a fixture to keep patching.
    for page in CONTENT_PAGES:
        (root / "content" / f"{page.slug}.md").write_text(
            f"## Test {page.slug}\n",
            encoding="utf-8",
        )

    return SyntheticResources(
        root=root,
        parquet=parquet,
        fields_yaml=fields_yaml,
        frame=frame,
    )


#: Categories for the ``categorised_resources`` tree. Deliberately not applied
#: to the default ``resources`` fixture: that tree's lack of a
#: ``categories.yaml`` is what exercises the optional-load path through
#: ``load_config``, and ``test_column_order`` relies on its priorities being
#: comparable across the whole table rather than only within a group.
FIXTURE_CATEGORIES: dict[str, tuple[str, str]] = {
    "symbol": ("Identity", ""),
    "ncbi_id": ("Identity", "Cross-references"),
    "ensg": ("Identity", "Cross-references"),
    "hgnc_id": ("Identity", "Nomenclature"),
    "omim_id": ("Identity", "Cross-references"),
    "hg38_chr": ("Coordinates", "hg38"),
    "hg38_start": ("Coordinates", "hg38"),
    "hg38_end": ("Coordinates", "hg38"),
    "flag_a": ("Annotations", ""),
    "flag_b": ("Annotations", ""),
    "score": ("Annotations", "Scores"),
    "loeuf": ("Annotations", "Scores"),
    # `gene_type`, `note` and `small_count` are left uncategorised on purpose,
    # so the bucket has something in it.
}

#: The declaration the tree writes. "Nomenclature" is listed after
#: "Cross-references" here even though it sorts before it alphabetically, so a
#: test can tell an honoured override from a coincidence.
FIXTURE_CATEGORIES_YAML = {
    "categories": [
        {
            "name": "Identity",
            "description": "Who the gene is",
            "subcategories": ["Nomenclature", "Cross-references"],
        },
        {"name": "Coordinates", "description": "Where the gene is"},
        {"name": "Annotations"},
        {"name": "Unsorted", "uncategorized": True},
    ],
}


#: Aliases for the ``aliased_resources`` tree. The base tree has none, and
#: adding them there would change ``display_config`` under every module built
#: on it. Both point at columns of different types so a test can tell that
#: resolution is name-based and not type-based.
FIXTURE_ALIASES: dict[str, list[str]] = {"flag_a": ["flag"], "loeuf": ["constraint"]}


@pytest.fixture
def aliased_resources(resources: SyntheticResources) -> SyntheticResources:
    """The synthetic tree with field aliases, one of them on a hidden column.

    ``loeuf`` is hidden in this tree exactly as it is in the real one, so
    ``constraint`` exercises the two policies together: an alias resolving to
    a column that listings omit but names still reach.
    """
    fields = schema.load_fields_yaml(resources.fields_yaml)
    for name, aliases in FIXTURE_ALIASES.items():
        fields[name].aliases = list(aliases)
    schema.write_fields_yaml(resources.fields_yaml, fields)
    return resources


@pytest.fixture
def infinite_resources(resources: SyntheticResources) -> SyntheticResources:
    """The synthetic tree with one infinite float value in ``loeuf``.

    A value, not a column: adding a column would move the field count and the
    frame width, which other modules pin. The real database has 37 infinities
    in ``37365347_autism_or``, from odds ratios with a zero cell, and they are
    not representable in JSON -- so an endpoint that can reach a float column
    needs a test that one of them does not become a 500.
    """
    frame = resources.frame.with_columns(
        pl.when(pl.col("ncbi_id") == 1)
        .then(pl.lit(float("inf")))
        .otherwise(pl.col("loeuf"))
        .cast(pl.Float32)
        .alias("loeuf"),
    )
    frame.write_parquet(resources.parquet)
    return replace(resources, frame=frame)


@pytest.fixture
def categorised_resources(resources: SyntheticResources) -> SyntheticResources:
    """The synthetic tree with a category taxonomy laid over it.

    Separate from ``resources`` rather than folded into it: the two encode
    different contracts, and giving every tree categories would change what
    ``test_column_order`` is testing.
    """
    fields = schema.load_fields_yaml(resources.fields_yaml)
    for name, (category, subcategory) in FIXTURE_CATEGORIES.items():
        fields[name].category = category
        fields[name].subcategory = subcategory
    schema.write_fields_yaml(resources.fields_yaml, fields)
    (resources.root / "config" / "categories.yaml").write_text(
        yaml.safe_dump(FIXTURE_CATEGORIES_YAML, sort_keys=False),
        encoding="utf-8",
    )
    return resources


#: ``flag_m``: measured for genes 1-12 only -- True on 1-4, False on 5-12,
#: null ("not measured") on 13-20.
MEASURED_UNIVERSE = "Genes 1 to 12 (synthetic)"


@pytest.fixture
def measured_resources(resources: SyntheticResources) -> SyntheticResources:
    """The synthetic tree plus one gene list that did not measure every gene.

    A separate fixture, because adding a column to the base tree would move the
    field count and the frame width that other modules pin.
    """
    frame = resources.frame.with_columns(
        flag_m=pl.when(pl.col("ncbi_id") <= 4)
        .then(pl.lit(value=True))
        .when(pl.col("ncbi_id") <= 12)
        .then(pl.lit(value=False))
        .otherwise(pl.lit(None, dtype=pl.Boolean)),
    )
    frame.write_parquet(resources.parquet)
    fields = schema.load_fields_yaml(resources.fields_yaml)
    fields["flag_m"] = FieldMeta(
        fullname="Flag M",
        visibility="secondary",
        value_type="boolean",
        universe=MEASURED_UNIVERSE,
    )
    schema.write_fields_yaml(resources.fields_yaml, fields)
    return replace(resources, frame=frame)


@pytest.fixture
def hgnc_file(resources: SyntheticResources) -> Path:
    """A miniature HGNC complete set with the real file's ID quirks.

    ``hgnc_id`` carries the ``HGNC:`` prefix and ``entrez_id`` is written as a
    float string, exactly as the downloaded release does after its spreadsheet
    round-trip. ``GENE01`` gains an alias and ``GENE02`` a previous symbol.
    """
    path = resources.root / "original_file" / "hgnc_complete_set_20260101.txt"
    rows = [
        {
            "hgnc_id": f"HGNC:{i * 10}",
            "symbol": f"GENE{i:02d}",
            "alias_symbol": "OLDALIAS|SECONDALIAS" if i == 1 else "",
            "prev_symbol": "KIAA0002" if i == 2 else "",
            "entrez_id": f"{float(i)}",
            "ensembl_gene_id": f"ENSG{i:011d}",
            "status": "Approved",
        }
        for i in range(1, N_ROWS + 1)
    ]
    pl.DataFrame(rows).write_csv(path, separator="\t")
    return path


@pytest.fixture(autouse=True)
def _fixed_console_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin rich's line width so CLI assertions do not depend on the terminal.

    ``genetrek-db`` reports through rich, which wraps to the real terminal width.
    A narrow window therefore splits a message mid-phrase, and any test that
    asserts on that phrase fails for a reason unrelated to the code.
    """
    monkeypatch.setenv("COLUMNS", "200")
    for console in (_common.out, _common.err):
        monkeypatch.setattr(console, "_width", 200, raising=False)
