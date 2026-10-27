"""Tests for assembling a database out of HGNC plus coordinates.

Everything runs against the canned sources in ``fakes.py``: the build is
deterministic and offline, and the numbers asserted here are the ones the
cascade produces for those five genes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import yaml
from fakes import HGNC_ROWS, FakeFetcher, full_fetcher, gtf, hgnc_body

from genetrek import paths, schema
from genetrek.content import CONTENT_PAGES
from genetrek.db import Database
from genetrek.db import build as build_module
from genetrek.db.build import (
    PRIMARY_KEY,
    BuildError,
    BuildSpec,
    build,
    column_name_for,
    plan_build,
    write_ui_config,
)
from genetrek.db.sources import hgnc

if TYPE_CHECKING:
    from pathlib import Path

    import polars as pl

DEFAULT_COLUMNS: dict[str, str] = {
    "symbol": "primary",
    "hgnc_id": "secondary",
    "name": "secondary",
    "locus_group": "secondary",
    "location": "secondary",
    "alias_symbol": "secondary",
    "prev_symbol": "secondary",
    "entrez_id": "secondary",
    "ensembl_gene_id": "secondary",
    "omim_id": "secondary",
    "mane_select": "secondary",
}


@pytest.fixture
def spec() -> BuildSpec:
    return BuildSpec(columns=dict(DEFAULT_COLUMNS))  # type: ignore[arg-type]


@pytest.fixture
def spec_file(tmp_path: Path) -> Path:
    path = tmp_path / "hgnc_build.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "release": "latest",
                "assemblies": ["GRCh38", "GRCh37"],
                "gencode_release": None,
                "use_ncbi_fallback": True,
                "columns": DEFAULT_COLUMNS,
                "coordinates": "secondary",
            },
        ),
        encoding="utf-8",
    )
    return path


# ------------------------------------------------------------------- naming


class TestColumnNaming:
    """The prefix marks who authors a value, not which file it arrived in."""

    @pytest.mark.parametrize(
        ("source", "built"),
        [
            ("symbol", "hgnc_symbol"),
            ("name", "hgnc_name"),
            ("location", "hgnc_location"),
            # Already prefixed, and the canonical HGNC identifier.
            ("hgnc_id", "hgnc_id"),
        ],
    )
    def test_hgnc_authored_columns_are_prefixed(self, source, built):
        assert column_name_for(source) == built

    @pytest.mark.parametrize(
        ("source", "built"),
        [
            ("entrez_id", "ncbi_id"),
            ("ensembl_gene_id", "ensg"),
            ("refseq_accession", "refseq_accession"),
            ("omim_id", "omim_id"),
            ("mane_select", "mane_select"),
        ],
    )
    def test_cross_references_keep_a_source_accurate_name(self, source, built):
        """Calling these hgnc_* would credit HGNC for someone else's identifier."""
        assert column_name_for(source) == built
        assert not built.startswith("hgnc_")


# --------------------------------------------------------------------- spec


class TestBuildSpec:
    def test_load_reads_every_key(self, spec_file):
        loaded = BuildSpec.load(spec_file)
        assert loaded.release == "latest"
        assert loaded.assemblies == ("GRCh38", "GRCh37")
        assert loaded.gencode_release is None
        assert loaded.use_ncbi_fallback is True
        assert loaded.columns["symbol"] == "primary"

    def test_load_rejects_an_unknown_visibility(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            yaml.safe_dump({"columns": {"symbol": "featured"}}),
            encoding="utf-8",
        )
        with pytest.raises(BuildError, match=r"columns\.symbol is 'featured'"):
            BuildSpec.load(path)

    def test_load_rejects_an_empty_column_map(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.safe_dump({"release": "latest"}), encoding="utf-8")
        with pytest.raises(BuildError, match="at least one HGNC column"):
            BuildSpec.load(path)

    def test_load_rejects_a_gencode_release_that_is_not_a_number(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            yaml.safe_dump(
                {"columns": {"symbol": "primary"}, "gencode_release": "v50"}
            ),
            encoding="utf-8",
        )
        with pytest.raises(BuildError, match="release number or null"):
            BuildSpec.load(path)

    def test_load_names_a_file_it_cannot_read(self, tmp_path):
        with pytest.raises(BuildError, match="cannot read the build config"):
            BuildSpec.load(tmp_path / "absent.yaml")


class TestOverrides:
    def test_a_source_name_sets_the_visibility(self, spec):
        assert spec.with_overrides(hidden=["location"]).columns["location"] == "hidden"

    def test_a_built_name_works_too(self, spec):
        """--hidden hgnc_location is what someone reading the table would type."""
        updated = spec.with_overrides(hidden=["hgnc_location"])
        assert updated.columns["location"] == "hidden"

    def test_a_cross_referenced_built_name_resolves(self, spec):
        updated = spec.with_overrides(primary=["ncbi_id"])
        assert updated.columns["entrez_id"] == "primary"

    def test_drop_removes_a_column(self, spec):
        assert "mane_select" not in spec.with_overrides(drop=["mane_select"]).columns

    def test_an_override_can_add_a_column_the_config_omits(self, spec):
        updated = spec.with_overrides(secondary=["gene_group"])
        assert updated.columns["gene_group"] == "secondary"

    def test_two_flags_claiming_one_column_is_refused(self, spec):
        with pytest.raises(BuildError, match="both --primary and --hidden"):
            spec.with_overrides(primary=["symbol"], hidden=["symbol"])

    def test_repeating_one_flag_is_fine(self, spec):
        updated = spec.with_overrides(hidden=["symbol", "symbol"])
        assert updated.columns["symbol"] == "hidden"

    def test_release_pins_pass_through(self, spec):
        updated = spec.with_overrides(release="2025-04-01", gencode_release=44)
        assert (updated.release, updated.gencode_release) == ("2025-04-01", 44)

    def test_omitting_a_pin_keeps_what_the_config_said(self, spec):
        pinned = spec.with_overrides(release="2025-04-01", use_ncbi_fallback=False)
        assert pinned.with_overrides().release == "2025-04-01"
        assert pinned.with_overrides().use_ncbi_fallback is False


# --------------------------------------------------------------------- plan


class TestPlan:
    def test_columns_follow_the_release_order_not_the_config_order(self, spec):
        plan = plan_build(spec, ["hgnc_id", "symbol", "name"])
        assert list(plan.kept) == ["hgnc_id", "symbol", "name"]

    def test_a_column_the_release_lacks_is_reported_not_fatal(self, spec):
        plan = plan_build(spec, ["hgnc_id", "symbol"])
        assert "mane_select" in plan.unknown
        assert "mane_select" not in plan.kept
        assert "not built" in plan.describe()

    def test_unrequested_columns_are_listed_as_discarded(self, spec):
        plan = plan_build(spec, ["hgnc_id", "symbol", "vega_id", "cosmic"])
        assert plan.discarded == ("vega_id", "cosmic")
        assert "discarding 2 HGNC column(s)" in plan.describe()

    def test_dropping_the_primary_key_is_refused(self, spec):
        without = spec.with_overrides(drop=[PRIMARY_KEY])
        with pytest.raises(BuildError, match="must keep 'hgnc_id'"):
            plan_build(without, ["hgnc_id", "symbol"])

    def test_a_release_with_none_of_the_columns_is_refused(self, spec):
        with pytest.raises(BuildError, match="none of the requested columns"):
            plan_build(spec, ["something_else"])

    def test_a_column_colliding_with_a_derived_one_is_refused(self, spec):
        """Reachable only through the assembly names, since every other HGNC
        column is prefixed -- but a silent overwrite would be far worse."""
        colliding = replace(spec, assemblies=("HGNC",))  # derives hgnc_chr, ...
        with pytest.raises(BuildError, match="collide with derived"):
            plan_build(
                colliding.with_overrides(secondary=["chr"]),
                ["hgnc_id", "symbol", "chr"],
            )

    def test_describe_does_not_use_square_brackets(self, spec):
        """They would be swallowed as markup by the rich console that prints this."""
        text = plan_build(spec, list(HGNC_ROWS[0])).describe()
        assert "[" not in text
        assert "primary" in text

    def test_the_column_list_ends_with_the_coordinates(self, spec):
        plan = plan_build(spec, list(HGNC_ROWS[0]))
        assert plan.columns[-5:] == (
            "grch37_chr",
            "grch37_start",
            "grch37_end",
            "grch37_source",
            "strand",
        )


# -------------------------------------------------------------------- build


@pytest.fixture
def result(tmp_path: Path, spec: BuildSpec):
    return build(full_fetcher(), tmp_path, spec)


class TestBuild:
    def test_every_gene_is_kept_with_the_expected_columns(self, result):
        assert result.frame.height == len(HGNC_ROWS)
        assert result.frame.columns == list(result.plan.columns)
        # The release's own column order, so the table reads like the source.
        assert result.frame.columns[:2] == ["hgnc_id", "hgnc_symbol"]

    def test_the_cascade_labels_each_coordinate(self, result):
        rows = dict(
            zip(result.frame["hgnc_symbol"], result.frame.to_dicts(), strict=True)
        )
        assert rows["SHANK3"]["grch38_source"] == "gencode"
        assert rows["SHANK3"]["grch37_start"] == 51112836
        # lift37 keeps the GRCh38 identifier, so ACACA resolves without mapping.
        assert rows["ACACA"]["grch37_source"] == "gencode"
        # In the GRCh38 annotation but not in lift37: NCBI supplies GRCh37.
        assert rows["AKR1C8"]["grch38_source"] == "gencode"
        assert rows["AKR1C8"]["grch37_source"] == "ncbi"
        # No Ensembl ID at all.
        assert rows["ACTBP3"]["grch38_source"] == "ncbi"
        # Neither identifier: left null rather than guessed from the symbol.
        assert rows["FRA1A"]["grch38_chr"] is None
        assert rows["FRA1A"]["grch38_source"] is None

    def test_an_empty_cross_reference_becomes_null_not_an_empty_string(self, result):
        assert result.frame["ensg"].to_list()[-2:] == [None, None]
        assert result.frame["ncbi_id"].to_list()[-1] is None
        assert (result.frame["hgnc_alias_symbol"] == "").sum() == 0

    def test_hgnc_id_keeps_its_canonical_prefix(self, result):
        assert result.frame["hgnc_id"].to_list()[0] == "HGNC:14294"

    def test_the_report_names_the_release_and_the_file(self, result):
        text = result.describe()
        assert "GENCODE v50" in text
        assert "hgnc_complete_set_20260828.txt" in text
        assert result.plan.hgnc_release == "20260828"

    def test_the_build_is_a_valid_database(self, tmp_path, result):
        root = paths.make_tree(tmp_path / "resources")
        database = Database(result.frame, result.fields, resources=root)
        database.save(backup=False)
        write_ui_config(root, result.fields)
        report = Database.open(root).validate()
        assert not report.errors
        # This saves the metadata/data pair directly rather than going through
        # `write_tree`, so the webapp's companion files are genuinely absent:
        # the alias index that `write_tree` distils from the same HGNC release,
        # and the prose pages it carries from the source tree. Neither makes
        # the pair wrong, which is why both are warnings. Any *other* warning
        # would be a defect in the pair itself.
        companions = ("alias index", "prose page")
        assert [w for w in report.warnings if not any(c in w for c in companions)] == []

    def test_the_primary_key_is_hgnc_id(self, result):
        roles = schema.resolve_roles(result.fields)
        assert roles.primary_key == PRIMARY_KEY
        assert not [p for p in roles.problems if "several fields" in p]

    def test_every_role_the_webapp_needs_is_filled(self, result):
        roles = schema.resolve_roles(result.fields)
        assert roles.symbol == "hgnc_symbol"
        assert roles.ensg == "ensg"
        assert roles.ncbi == "ncbi_id"
        assert roles.searchable
        assert sorted(roles.assemblies) == ["GRCh37", "GRCh38"]

    def test_each_assembly_declares_its_own_coordinate_columns(self, result):
        roles = schema.resolve_roles(result.fields)
        assert roles.assemblies["GRCh38"].chrom == "grch38_chr"
        assert roles.assemblies["GRCh37"].start == "grch37_start"

    def test_the_ncbi_fallback_can_be_switched_off(self, tmp_path, spec):
        offline = spec.with_overrides(use_ncbi_fallback=False)
        built = build(full_fetcher(), tmp_path, offline)
        assert {step.ncbi for step in built.coordinates.steps} == {0}
        assert built.frame["grch38_source"].to_list().count(None) == 2

    def test_a_pinned_gencode_release_is_used_and_recorded(self, tmp_path, spec):
        pinned = spec.with_overrides(gencode_release=44)
        fetcher = full_fetcher(release=44)
        built = build(fetcher, tmp_path, pinned)
        assert built.coordinates.gencode_release == 44
        # Pinned, so the release listing is never requested.
        assert not [
            url for kind, url in fetcher.calls if kind == "GET" and "Gencode" in url
        ]

    def test_the_hgnc_file_is_cached_under_its_release_name(self, tmp_path, result):
        assert result.hgnc_file == tmp_path / "hgnc_complete_set_20260828.txt"
        assert result.hgnc_file.is_file()

    def test_a_second_build_reuses_the_caches(self, tmp_path, spec):
        build(full_fetcher(), tmp_path, spec)
        fetcher = full_fetcher()
        build(fetcher, tmp_path, spec)
        assert not [kind for kind, _ in fetcher.calls if kind == "STREAM"]


# ----------------------------------------------------------------- metadata


class TestGeneratedMetadata:
    def test_provenance_is_recorded_per_column(self, result):
        fields = result.fields
        assert fields["hgnc_symbol"].source == "HGNC"
        assert fields["ncbi_id"].source == "NCBI Gene, via the HGNC complete set"
        assert fields["ensg"].source == "Ensembl, via the HGNC complete set"
        assert fields["hgnc_symbol"].source_file == "hgnc_complete_set_20260828.txt"
        assert fields["grch38_chr"].source == "GENCODE v50, with NCBI as a fallback"

    def test_dates_are_yyyymmdd_stamps(self, result):
        assert result.fields["hgnc_symbol"].update == "20260828"
        assert result.fields["hgnc_symbol"].added == schema.today_stamp()

    def test_visibility_comes_from_the_spec(self, result):
        assert result.fields["hgnc_symbol"].visibility == "primary"
        assert result.fields["hgnc_name"].visibility == "secondary"
        # Provenance columns stay out of the way unless asked for.
        assert result.fields["grch38_source"].visibility == "hidden"

    def test_value_types_match_the_parquet(self, result):
        for name, meta in result.fields.items():
            assert schema.value_type_matches(result.frame.schema[name], meta.value_type)

    def test_the_grch37_description_says_it_is_a_backmapping(self, result):
        text = result.fields["grch37_start"].description
        assert "lift37" in text
        assert "not the historical" in text
        # The reference assembly gets no such caveat: it is the native annotation.
        assert "lift37" not in result.fields["grch38_start"].description

    def test_the_strand_description_says_why_there_is_only_one(self, result):
        assert "not one per assembly" in result.fields["strand"].description
        assert result.fields["strand"].assembly == ""

    def test_every_field_has_a_fullname(self, result):
        assert all(meta.fullname.strip() for meta in result.fields.values())

    def test_an_unlabelled_column_still_gets_a_readable_fullname(self, tmp_path, spec):
        extra = spec.with_overrides(secondary=["locus_type"])
        rows = tuple(
            {**row, "locus_type": "gene with protein product"} for row in HGNC_ROWS
        )
        fetcher = full_fetcher()
        fetcher.text[hgnc.LATEST_URL] = hgnc_body(rows)
        built = build(fetcher, tmp_path, extra)
        assert built.fields["hgnc_locus_type"].fullname == "Locus type"


# ---------------------------------------------------------------- ui config


class TestUiConfig:
    def test_links_use_the_role_resolved_column_names(self, tmp_path, result):
        root = paths.make_tree(tmp_path / "resources")
        written = write_ui_config(root, result.fields)
        links = yaml.safe_load(written["external_links"].read_text())["links"]
        by_name = {link["name"]: link["url"] for link in links}
        assert "{hgnc_symbol}" in by_name["GeneCards"]
        assert "{ncbi_id}" in by_name["NCBI Gene"]
        assert "{hgnc_id}" in by_name["HGNC"]

    def test_a_link_for_a_role_the_database_lacks_is_omitted(self, tmp_path, result):
        fields = {n: m for n, m in result.fields.items() if n != "omim_id"}
        root = paths.make_tree(tmp_path / "resources")
        written = write_ui_config(root, fields)
        links = yaml.safe_load(written["external_links"].read_text())["links"]
        assert "OMIM" not in {link["name"] for link in links}

    def test_badges_start_empty_because_there_is_nothing_to_render(
        self, tmp_path, result
    ):
        root = paths.make_tree(tmp_path / "resources")
        written = write_ui_config(root, result.fields)
        assert yaml.safe_load(written["badges"].read_text()) == {}

    def test_the_written_config_passes_validation(self, tmp_path, result):
        root = paths.make_tree(tmp_path / "resources")
        Database(result.frame, result.fields, resources=root).save(backup=False)
        write_ui_config(root, result.fields)
        assert not Database.open(root).validate().errors


def test_a_built_database_is_searchable_end_to_end(tmp_path, spec):
    """The whole point of the role layer: search works without a code change."""
    from genetrek.config.loader import load_config  # noqa: PLC0415
    from genetrek.services.search import filter_dataframe  # noqa: PLC0415

    result = build(full_fetcher(), tmp_path, spec)
    root = paths.make_tree(tmp_path / "resources")
    Database(result.frame, result.fields, resources=root).save(backup=False)
    write_ui_config(root, result.fields)
    (root / paths.LAYOUT["token_colors"]).write_text("symbol: '#38ada9'\n")
    (root / paths.LAYOUT["site_description"]).write_text("# Test\n")
    for page in CONTENT_PAGES:
        paths.content_path(page.slug, root).write_text(f"## {page.title}\n")

    config = load_config(root)

    def find(text: str, genome: str = "GRCh38") -> pl.DataFrame:
        return filter_dataframe(
            config.df,
            text,
            exact_match=True,
            selected_bool_cols=[],
            results_merge="union",
            annotations_merge="union",
            selected_genome=genome,
            roles=config.roles,
        )

    assert find("SHANK3")["hgnc_symbol"].to_list() == ["SHANK3"]
    assert find("ENSG00000251322")["hgnc_symbol"].to_list() == ["SHANK3"]
    assert find("85358")["hgnc_symbol"].to_list() == ["SHANK3"]
    assert find("HGNC:14294")["hgnc_symbol"].to_list() == ["SHANK3"]
    assert find("chr22:50600000-50800000")["hgnc_symbol"].to_list() == ["SHANK3"]
    # The same interval against the other assembly must not find it.
    assert find("chr22:50600000-50800000", "GRCh37").height == 0
    assert find("chr22:51100000-51200000", "GRCh37")["hgnc_symbol"].to_list() == [
        "SHANK3"
    ]


def test_build_reports_a_cross_assembly_chromosome_disagreement(tmp_path, spec):
    """Real data has 16 of these; they must be surfaced, not absorbed."""
    from fakes import GENCODE_GENES  # noqa: PLC0415

    from genetrek.db.sources import gencode  # noqa: PLC0415

    fetcher = full_fetcher()

    moved = tuple(
        (gene_id, name, "chr9" if name == "SHANK3" else chrom, start, end, strand)
        for gene_id, name, chrom, start, end, strand in GENCODE_GENES["GRCh37"]
    )
    fetcher.streams[gencode.url_for(50, "GRCh37")] = gtf(*moved)
    built = build(fetcher, tmp_path, spec)
    assert built.conflicts == ["SHANK3"]
    assert "disagree on chromosome" in built.describe()
    assert "multi-copy RNA genes" in built.describe()


def test_a_release_missing_a_required_column_is_reported(tmp_path, spec):
    fetcher = FakeFetcher(
        text={hgnc.LATEST_URL: "vega_id\tucsc_id\nOTTHUMG1\tuc001\n"},
        heads={hgnc.LATEST_URL: {"last-modified": "Fri, 28 Aug 2026 00:00:00 GMT"}},
    )
    with pytest.raises(BuildError, match="none of the requested columns"):
        build(fetcher, tmp_path, spec)


def _built_categories(result):
    """The category order a fresh build's starter file declares.

    Built from the same constants ``write_ui_config`` renders, so the expected
    order and the file that produces it cannot drift apart.
    """
    declared = [
        schema.Category(
            name=name,
            description=description,
            subcategories=(
                tuple(result.plan.spec.assemblies)
                if name == build_module.CATEGORY_COORDINATES
                else subcategories
            ),
        )
        for name, description, subcategories in build_module.STARTER_CATEGORIES
    ]
    declared.append(
        schema.Category(name=build_module.STARTER_UNCATEGORIZED, uncategorized=True),
    )
    return schema.CategoryOrder.of(declared, result.fields)


def test_a_built_database_leads_with_the_symbol_column(result):
    """The positional sticky CSS lands on whatever column sorts first.

    Ordered through the starter categories, not bare: `order_fields` defaults
    that argument, so omitting it would assert against an order the webapp
    never uses and pass regardless of what actually comes first.
    """
    fields = result.fields
    visible = {n: m for n, m in fields.items() if m.visibility != "hidden"}
    categories = _built_categories(result)
    assert (
        schema.order_fields(visible, categories)[0]
        == schema.resolve_roles(fields).symbol
    )


def test_every_built_column_declares_a_category(result):
    """Otherwise a fresh build lands entirely in the uncategorised bucket."""
    missing = [n for n, m in result.fields.items() if not m.category]
    assert not missing, f"built without a category: {missing}"


def test_built_categories_are_all_declared_by_the_starter_file(result):
    """The starter file has to cover everything the builder can produce."""
    assert not _built_categories(result).undeclared


def test_write_ui_config_writes_a_starter_categories_file(tmp_path, result):
    root = paths.make_tree(tmp_path / "resources")
    written = write_ui_config(root, result.fields, result.plan.spec.assemblies)
    assert "categories" in written
    order = schema.load_categories_yaml(written["categories"], result.fields)
    assert [c.name for c in order.categories] == [
        build_module.CATEGORY_IDENTITY,
        build_module.CATEGORY_COORDINATES,
        build_module.STARTER_UNCATEGORIZED,
    ]
    # The reference assembly first, not alphabetical: GRCh37 sorts before
    # GRCh38, which is backwards.
    assert order.get(build_module.CATEGORY_COORDINATES).subcategories == tuple(
        result.plan.spec.assemblies,
    )


#: A hand-curated categories.yaml, to check a rebuild leaves it alone.
CURATED_CATEGORIES = "categories:\n  - name: Mine\n"


def test_an_existing_categories_file_survives_a_rebuild(tmp_path, result):
    """It is hand-curated, and unlike the other config it names no columns."""
    root = paths.make_tree(tmp_path / "resources")
    curated = root / paths.LAYOUT["categories"]
    curated.write_text(CURATED_CATEGORIES, encoding="utf-8")

    written = write_ui_config(root, result.fields, result.plan.spec.assemblies)

    assert "categories" not in written
    assert curated.read_text(encoding="utf-8") == CURATED_CATEGORIES


def test_categories_is_carried_over_rather_than_regenerated():
    """So `write_tree` copies an existing one instead of overwriting it."""
    assert "categories" in build_module.CARRIED_OVER


def test_every_built_primary_column_declares_a_priority(result):
    """Otherwise `validate` warns and the default view's order is incidental."""
    missing = [
        name
        for name, meta in result.fields.items()
        if meta.visibility == "primary" and meta.priority is None
    ]
    assert not missing, f"primary without a priority: {missing}"
