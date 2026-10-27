"""Build a database from scratch out of the HGNC complete set plus coordinates.

The result is a small, fully-sourced table: one row per HGNC gene record, the
identifier cross-references HGNC publishes, and genomic coordinates in every
requested assembly. It carries no annotation columns -- those are added
afterwards with ``genetrek-db add``.

Column names mark **who authors the value**, not which file it arrived in. HGNC
is the authority for symbols and nomenclature, so those get an ``hgnc_`` prefix;
it merely cross-references ``entrez_id``, ``ensembl_gene_id``,
``refseq_accession``, ``omim_id`` and ``mane_select``, so those keep names
naming their real source. Calling them ``hgnc_*`` would mislabel five columns.

The network work lives in :mod:`genetrek.db.sources`; this module is the
assembly, and everything except :func:`build` runs offline.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, get_args

import polars as pl
import yaml

from genetrek import paths, schema
from genetrek.content import CONTENT_PAGES
from genetrek.db.gene_index import GeneIndex, GeneIndexError
from genetrek.db.sources import coordinates as coords
from genetrek.db.sources import gencode, hgnc

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from genetrek.db.sources.fetcher import Fetcher

#: Prefix for the columns HGNC actually authors.
HGNC_PREFIX = "hgnc_"

#: HGNC columns whose value another body authors. They keep a name that says so.
CROSS_REFERENCED: dict[str, str] = {
    "entrez_id": "ncbi_id",
    "ensembl_gene_id": "ensg",
    "refseq_accession": "refseq_accession",
    "omim_id": "omim_id",
    "mane_select": "mane_select",
}

#: Who to credit for each cross-referenced column.
_AUTHORITY: dict[str, str] = {
    "ncbi_id": "NCBI Gene",
    "ensg": "Ensembl",
    "refseq_accession": "NCBI RefSeq",
    "omim_id": "OMIM",
    "mane_select": "MANE (NCBI and EMBL-EBI)",
}

#: The role each built column fills, so the webapp finds it by meaning.
_ROLES: dict[str, schema.ID_TYPE] = {
    "hgnc_id": "hgnc",
    "hgnc_symbol": "symbol",
    "ncbi_id": "ncbi",
    "ensg": "ensg",
    "omim_id": "omim",
}

#: Display groups for the columns a build produces. Only these two are
#: structurally known here -- everything else a database gains is annotation,
#: categorised when it is imported. Kept in code rather than in the build spec
#: because they follow from what the builder *is*, not from how it is configured.
CATEGORY_IDENTITY = "Gene identity"
CATEGORY_COORDINATES = "Coordinates"
SUBCATEGORY_NOMENCLATURE = "Nomenclature"
SUBCATEGORY_CROSS_REFERENCES = "Cross-references"


def _hgnc_subcategory(name: str) -> str:
    """Which subgroup of "Gene identity" a built HGNC column belongs to.

    The symbol column gets none, deliberately: an unset subcategory sorts
    ahead of every subgroup, which together with its priority is what keeps it
    in column 1 -- and ``ui/styles.py`` styles the first column positionally.
    """
    if _ROLES.get(name) == "symbol":
        return ""
    return (
        SUBCATEGORY_CROSS_REFERENCES
        if name in _AUTHORITY
        else (SUBCATEGORY_NOMENCLATURE)
    )


#: ``hgnc_id`` is the primary key: it is the only identifier in the complete set
#: with no nulls and no duplicates (``entrez_id`` has 640 nulls, so it cannot be).
PRIMARY_KEY = "hgnc_id"

#: Labels for the HGNC columns a default build keeps. Anything else is
#: prettified from its name.
_FULLNAMES: dict[str, str] = {
    "hgnc_id": "HGNC ID",
    "hgnc_symbol": "Gene",
    "hgnc_name": "Gene name",
    "hgnc_locus_group": "Locus group",
    "hgnc_locus_type": "Locus type",
    "hgnc_location": "Cytogenetic location",
    "hgnc_alias_symbol": "Alias symbols",
    "hgnc_prev_symbol": "Previous symbols",
    "ncbi_id": "NCBI ID",
    "ensg": "Ensembl ID",
    "refseq_accession": "RefSeq accession",
    "omim_id": "OMIM ID",
    "mane_select": "MANE Select",
}

_DESCRIPTIONS: dict[str, str] = {
    "hgnc_id": "HGNC identifier, in its canonical prefixed form (HGNC:5)",
    "hgnc_symbol": "HGNC-approved gene symbol",
    "hgnc_name": "HGNC-approved gene name",
    "hgnc_locus_group": "Broad locus classification (protein-coding gene, "
    "pseudogene, non-coding RNA, other)",
    "hgnc_locus_type": "Specific locus type within the locus group",
    "hgnc_location": "Cytogenetic band, as curated by HGNC (not derived from "
    "the coordinates in this database)",
    "hgnc_alias_symbol": "Alternative symbols in use for this gene, pipe-separated",
    "hgnc_prev_symbol": "Symbols this gene was previously approved under, "
    "pipe-separated",
    "ncbi_id": "NCBI gene identifier (formerly Entrez Gene ID), "
    "cross-referenced by HGNC",
    "ensg": "Ensembl gene identifier for the GRCh38 assembly, cross-referenced by HGNC",
    "refseq_accession": "RefSeq nucleotide accession, cross-referenced by HGNC",
    "omim_id": "OMIM gene identifier, cross-referenced by HGNC",
    "mane_select": "MANE Select transcript: the one RefSeq/Ensembl transcript "
    "pair agreed to represent this gene",
}

#: Length of an HGNC release stamp, ``YYYYMMDD``.
_STAMP_LENGTH = 8

#: How many conflicting gene symbols a report names before summarising.
_MAX_LISTED = 8

#: Coordinate columns are provenance-tagged; the tag itself stays out of the way
#: unless asked for (``genetrek-db set grch38_source --visibility secondary``).
SOURCE_VISIBILITY: schema.VISIBILITY = "hidden"


class BuildError(RuntimeError):
    """The build specification or the assembled data is unusable."""


@dataclass(frozen=True)
class BuildSpec:
    """What to build: which release, which columns, which assemblies."""

    columns: dict[str, schema.VISIBILITY]
    release: str = "latest"
    assemblies: tuple[str, ...] = gencode.ASSEMBLIES
    gencode_release: int | None = None
    use_ncbi_fallback: bool = True
    coordinates: schema.VISIBILITY = "secondary"

    @classmethod
    def load(cls, path: Path) -> BuildSpec:
        """Read a build spec from YAML.

        Raises:
            BuildError: If the file is unreadable, or a value is not allowed.
        """
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            msg = f"cannot read the build config {path}: {exc}"
            raise BuildError(msg) from exc
        if not isinstance(raw, dict):
            msg = f"{path} must contain a mapping, not {type(raw).__name__}"
            raise BuildError(msg)

        columns = raw.get("columns") or {}
        if not isinstance(columns, dict) or not columns:
            msg = f"{path} must map at least one HGNC column under 'columns'"
            raise BuildError(msg)
        parsed = {
            str(name): _visibility(value, f"columns.{name}", path)
            for name, value in columns.items()
        }
        assemblies = raw.get("assemblies") or list(gencode.ASSEMBLIES)
        if not isinstance(assemblies, list) or not assemblies:
            msg = f"{path}: 'assemblies' must be a non-empty list"
            raise BuildError(msg)

        release = raw.get("gencode_release")
        if release is not None and not isinstance(release, int):
            msg = f"{path}: 'gencode_release' must be a release number or null"
            raise BuildError(msg)

        return cls(
            columns=parsed,
            release=str(raw.get("release", "latest")),
            assemblies=tuple(str(a) for a in assemblies),
            gencode_release=release,
            use_ncbi_fallback=bool(raw.get("use_ncbi_fallback", True)),
            coordinates=_visibility(
                raw.get("coordinates", "secondary"),
                "coordinates",
                path,
            ),
        )

    def with_overrides(
        self,
        *,
        primary: Iterable[str] = (),
        secondary: Iterable[str] = (),
        hidden: Iterable[str] = (),
        drop: Iterable[str] = (),
        release: str | None = None,
        gencode_release: int | None = None,
        use_ncbi_fallback: bool | None = None,
    ) -> BuildSpec:
        """Apply per-column visibility overrides and release pins.

        An override may name the HGNC column (``location``) or the column it
        becomes (``hgnc_location``); a column the config omits is *added* by an
        override, and ``drop`` removes one.

        Raises:
            BuildError: If a name is claimed by two different flags.
        """
        columns = dict(self.columns)
        requested: dict[str, str] = {}
        for visibility, names in (
            ("primary", primary),
            ("secondary", secondary),
            ("hidden", hidden),
            ("drop", drop),
        ):
            for raw in names:
                source = self._source_name(raw)
                if (earlier := requested.get(source)) not in (None, visibility):
                    msg = (
                        f"{raw!r} is given as both --{earlier} and "
                        f"--{visibility}; pick one"
                    )
                    raise BuildError(msg)
                requested[source] = visibility
                if visibility == "drop":
                    columns.pop(source, None)
                else:
                    columns[source] = visibility  # type: ignore[assignment]

        return replace(
            self,
            columns=columns,
            release=release or self.release,
            gencode_release=(
                gencode_release if gencode_release is not None else self.gencode_release
            ),
            use_ncbi_fallback=(
                self.use_ncbi_fallback
                if use_ncbi_fallback is None
                else use_ncbi_fallback
            ),
        )

    def _source_name(self, name: str) -> str:
        """Accept either the HGNC column name or the name it is built under."""
        if name in self.columns:
            return name
        built = {column_name_for(source): source for source in self.columns}
        if name in built:
            return built[name]
        # Not in the spec at all: an override adding a column names it as HGNC
        # does, so map a built-style name back where that is unambiguous.
        if name.startswith(HGNC_PREFIX) and name != PRIMARY_KEY:
            return name.removeprefix(HGNC_PREFIX)
        return next(
            (source for source, built in CROSS_REFERENCED.items() if built == name),
            name,
        )


def _visibility(value: object, where: str, path: Path) -> schema.VISIBILITY:
    """Parse a visibility, naming the offending key when it is not one."""
    text = str(value)
    if text not in get_args(schema.VISIBILITY):
        allowed = ", ".join(get_args(schema.VISIBILITY))
        msg = f"{path}: {where} is {value!r}; expected one of {allowed}"
        raise BuildError(msg)
    return text  # type: ignore[return-value]


def column_name_for(source: str) -> str:
    """The database column an HGNC column is built under."""
    if source in CROSS_REFERENCED:
        return CROSS_REFERENCED[source]
    if source == PRIMARY_KEY or source.startswith(HGNC_PREFIX):
        return source
    return f"{HGNC_PREFIX}{source}"


#: Display priority given to a primary column. A build declares at most a
#: handful, so they all share one slot and fall back to the alphabetical
#: tie-break; `genetrek-db set <field> --priority` separates them later.
_PRIORITY_STEP = 10


@dataclass(frozen=True)
class BuildPlan:
    """The resolved shape of a build, decided before any data is assembled."""

    spec: BuildSpec
    #: HGNC column -> (built column, visibility), in build order.
    kept: dict[str, tuple[str, schema.VISIBILITY]]
    #: HGNC columns the spec named that the release does not have.
    unknown: tuple[str, ...] = ()
    #: HGNC columns present in the release and deliberately not kept.
    discarded: tuple[str, ...] = ()
    hgnc_release: str = ""
    n_genes: int = 0

    @property
    def columns(self) -> tuple[str, ...]:
        """Every column the build produces, in table order."""
        derived: list[str] = []
        for assembly in self.spec.assemblies:
            derived.extend(coords.columns_for(assembly).names())
        return (
            *(built for built, _ in self.kept.values()),
            *derived,
            coords.STRAND_COLUMN,
        )

    def describe(self) -> str:
        """The plan as a human-readable report."""
        lines = [
            f"HGNC release {self.hgnc_release or self.spec.release}"
            f"{f': {self.n_genes:,} genes' if self.n_genes else ''}",
            f"assemblies: {', '.join(self.spec.assemblies)}"
            + (
                f" (GENCODE v{self.spec.gencode_release})"
                if self.spec.gencode_release
                else " (newest complete GENCODE release)"
            ),
            "",
            f"keeping {len(self.kept)} HGNC column(s):",
        ]
        # Square brackets are deliberately absent: this text is printed through
        # rich, which would read them as markup and swallow them.
        source_width = max((len(s) for s in self.kept), default=0)
        built_width = max((len(b) for b, _ in self.kept.values()), default=0)
        for source, (built, visibility) in self.kept.items():
            arrow = "  " if built == source else "->"
            lines.append(
                f"  {source:<{source_width}} {arrow} "
                f"{built:<{built_width}}  {visibility}",
            )
        lines += [
            "",
            f"deriving {len(self.spec.assemblies) * 4 + 1} coordinate column(s), "
            f"{self.spec.coordinates} except *_source ({SOURCE_VISIBILITY}):",
            f"  {', '.join(self.columns[len(self.kept) :])}",
        ]
        if self.discarded:
            lines += [
                "",
                f"discarding {len(self.discarded)} HGNC column(s): "
                f"{', '.join(self.discarded)}",
            ]
        if self.unknown:
            lines += [
                "",
                f"NOT in this HGNC release, so not built: {', '.join(self.unknown)}",
            ]
        return "\n".join(lines)


@dataclass
class BuildResult:
    """A built database, in memory, with everything needed to record it."""

    frame: pl.DataFrame
    fields: dict[str, schema.FieldMeta]
    plan: BuildPlan
    coordinates: coords.CoordinateReport
    hgnc_file: Path
    #: Symbols the assemblies place on different chromosomes, for the report.
    conflicts: list[str] = field(default_factory=list)

    def describe(self) -> str:
        """A summary of what was built."""
        lines = [
            f"{self.frame.height:,} genes x {self.frame.width} columns",
            f"HGNC: {self.hgnc_file.name}",
            self.coordinates.describe(),
        ]
        if self.conflicts:
            shown = ", ".join(self.conflicts[:_MAX_LISTED])
            hidden = len(self.conflicts) - _MAX_LISTED
            more = f", +{hidden} more" if hidden > 0 else ""
            lines.append(
                f"  the disagreeing genes are short multi-copy RNA genes and "
                f"pseudogenes whose GRCh37 mapping landed on a near-identical "
                f"paralogue: {shown}{more}",
            )
        return "\n".join(lines)


def plan_build(spec: BuildSpec, available: Sequence[str]) -> BuildPlan:
    """Resolve a spec against the columns a release actually has.

    Args:
        spec: What to build.
        available: Column names present in the downloaded complete set.

    Raises:
        BuildError: If none of the requested columns exist, or a built name
            would collide with another.
    """
    present = list(available)
    kept: dict[str, tuple[str, schema.VISIBILITY]] = {}
    unknown: list[str] = []
    # Build in the release's own column order, so the table reads like the source.
    ordered = [c for c in present if c in spec.columns]
    for source in ordered:
        kept[source] = (column_name_for(source), spec.columns[source])
    unknown = [c for c in spec.columns if c not in present]

    if not kept:
        msg = (
            f"none of the requested columns exist in this HGNC release "
            f"({sorted(spec.columns)}). Available: {sorted(present)[:12]}..."
        )
        raise BuildError(msg)
    if PRIMARY_KEY not in {built for built, _ in kept.values()}:
        msg = (
            f"the build must keep {PRIMARY_KEY!r}: it is the only HGNC identifier "
            f"with no nulls and no duplicates, so it is the primary key"
        )
        raise BuildError(msg)

    derived = {
        name
        for assembly in spec.assemblies
        for name in coords.columns_for(assembly).names()
    } | {coords.STRAND_COLUMN}
    if clash := sorted({b for b, _ in kept.values()} & derived):
        msg = f"HGNC column(s) would collide with derived coordinate columns: {clash}"
        raise BuildError(msg)

    return BuildPlan(
        spec=spec,
        kept=kept,
        unknown=tuple(unknown),
        discarded=tuple(c for c in present if c not in spec.columns),
    )


def build(
    fetcher: Fetcher,
    cache_dir: Path,
    spec: BuildSpec,
    *,
    refresh: bool = False,
) -> BuildResult:
    """Download, assemble and describe a database. Writes nothing but the cache."""
    release = hgnc.fetch(fetcher, cache_dir, spec.release, refresh=refresh)
    raw = hgnc.read(release.cached)
    plan = replace(
        plan_build(spec, raw.columns),
        hgnc_release=release.release,
        n_genes=raw.height,
    )
    return assemble(fetcher, cache_dir, plan, raw, release, refresh=refresh)


def assemble(
    fetcher: Fetcher,
    cache_dir: Path,
    plan: BuildPlan,
    raw: pl.DataFrame,
    release: hgnc.HgncRelease,
    *,
    refresh: bool = False,
) -> BuildResult:
    """Turn a downloaded complete set into a frame plus its field metadata."""
    genes = raw.select(
        pl.col(source).alias(built) for source, (built, _) in plan.kept.items()
    )
    genes = genes.with_columns(
        # HGNC leaves an unknown cross-reference as an empty string in the TSV;
        # a missing identifier is a null, not a zero-length one.
        pl.when(pl.col(name).str.strip_chars() == "")
        .then(None)
        .otherwise(pl.col(name))
        .alias(name)
        for name in genes.columns
    )

    ensg = _built_name(plan, "ensembl_gene_id")
    frame, report = coords.build(
        fetcher,
        cache_dir,
        genes,
        release=plan.spec.gencode_release,
        assemblies=plan.spec.assemblies,
        refresh=refresh,
        use_ncbi=plan.spec.use_ncbi_fallback,
        ensg_column=ensg or _MISSING_ENSG,
        ncbi_column=_built_name(plan, "entrez_id") or _MISSING_NCBI,
    )
    combined = pl.concat([genes, frame], how="horizontal")

    symbol = _built_name(plan, "symbol")
    conflicts = (
        combined[symbol][list(report.conflict_rows)].to_list()
        if symbol and report.conflict_rows
        else []
    )
    return BuildResult(
        frame=combined,
        fields=make_fields(combined, plan, release, report),
        plan=replace(plan, hgnc_release=release.release, n_genes=combined.height),
        coordinates=report,
        hgnc_file=release.cached,
        conflicts=conflicts,
    )


#: Placeholders, so a build without an Ensembl or NCBI column reports the real
#: reason rather than a missing-column error from deep in the cascade.
_MISSING_ENSG = "ensembl_gene_id"
_MISSING_NCBI = "entrez_id"


def _built_name(plan: BuildPlan, source: str) -> str | None:
    """What an HGNC column is called in the build, if it is kept at all."""
    entry = plan.kept.get(source)
    return entry[0] if entry else None


def make_fields(
    frame: pl.DataFrame,
    plan: BuildPlan,
    release: hgnc.HgncRelease,
    report: coords.CoordinateReport,
) -> dict[str, schema.FieldMeta]:
    """Generate field metadata for every built column."""
    today = schema.today_stamp()
    # The HGNC release stamp is already the YYYYMMDD that FieldMeta requires; a
    # pinned archive date arrives as YYYY-MM-DD, so strip the separators.
    stamp = release.release.replace("-", "")
    update = stamp if len(stamp) == _STAMP_LENGTH else ""
    built_to_source = {built: source for source, (built, _) in plan.kept.items()}

    fields: dict[str, schema.FieldMeta] = {}
    for name in frame.columns:
        common: dict[str, Any] = {
            "value_type": schema.value_type_for_dtype(frame.schema[name]),
            "added": today,
        }
        if (source := built_to_source.get(name)) is not None:
            fields[name] = _hgnc_field(name, source, plan, release, update, common)
        else:
            fields[name] = _coordinate_field(name, plan, report, update, common)
    return fields


def _hgnc_field(
    name: str,
    source: str,
    plan: BuildPlan,
    release: hgnc.HgncRelease,
    update: str,
    common: dict[str, Any],
) -> schema.FieldMeta:
    """Metadata for a column taken from the complete set."""
    authority = _AUTHORITY.get(name)
    return schema.FieldMeta(
        fullname=_FULLNAMES.get(name, source.replace("_", " ").capitalize()),
        description=_DESCRIPTIONS.get(name, f"HGNC complete set column {source!r}"),
        update=update,
        visibility=plan.kept[source][1],
        priority=_PRIORITY_STEP if plan.kept[source][1] == "primary" else None,
        category=CATEGORY_IDENTITY,
        subcategory=_hgnc_subcategory(name),
        source=(f"{authority}, via the HGNC complete set" if authority else "HGNC"),
        source_file=release.cached.name,
        id_type=_ROLES.get(name),
        primary_key=name == PRIMARY_KEY,
        **common,
    )


def _coordinate_field(
    name: str,
    plan: BuildPlan,
    report: coords.CoordinateReport,
    update: str,
    common: dict[str, Any],
) -> schema.FieldMeta:
    """Metadata for a derived coordinate, strand or provenance column."""
    version = (
        f"GENCODE v{report.gencode_release}"
        if report.gencode_release is not None
        else "GENCODE"
    )
    origin = (
        f"{version}, with NCBI as a fallback"
        if plan.spec.use_ncbi_fallback
        else version
    )
    if name == coords.STRAND_COLUMN:
        return schema.FieldMeta(
            fullname="Strand",
            description=(
                "Transcription strand. One column, not one per assembly: a gene "
                "does not change strand between assemblies."
            ),
            update=update,
            visibility=plan.spec.coordinates,
            category=CATEGORY_COORDINATES,
            source=origin,
            **common,
        )

    for assembly in plan.spec.assemblies:
        columns = coords.columns_for(assembly)
        if name not in columns.names():
            continue
        if name == columns.source:
            return schema.FieldMeta(
                fullname=f"{assembly} coordinate source",
                description=(
                    f"Which source placed this gene in {assembly}: 'gencode', "
                    f"'ncbi', or null when neither could. The two annotators "
                    f"call gene boundaries differently, so this says which one "
                    f"the coordinates come from."
                ),
                update=update,
                visibility=SOURCE_VISIBILITY,
                category=CATEGORY_COORDINATES,
                subcategory=assembly,
                source="genetrek-db init",
                **common,
            )
        part = ("chromosome", "start", "end")[columns.names().index(name)]
        return schema.FieldMeta(
            fullname=f"{assembly} {part}",
            description=_coordinate_description(assembly, part, plan),
            update=update,
            visibility=plan.spec.coordinates,
            category=CATEGORY_COORDINATES,
            subcategory=assembly,
            source=origin,
            id_type=("locus_chr", "locus_start", "locus_end")[
                ("chromosome", "start", "end").index(part)
            ],
            assembly=assembly,
            **common,
        )

    msg = f"cannot describe the column {name!r}: it is neither HGNC nor derived"
    raise BuildError(msg)


def _coordinate_description(assembly: str, part: str, plan: BuildPlan) -> str:
    """A coordinate column's description, saying what the numbers really are."""
    text = f"Gene {part} in {assembly}, joined on the Ensembl gene ID."
    if assembly != plan.spec.assemblies[0]:
        text += (
            f" Taken from GENCODE's lift37 annotation -- the current gene model "
            f"backmapped onto {assembly} -- so both assemblies describe the same "
            f"gene. It is not the historical {assembly} annotation: where that "
            f"also has the gene, bounds differ by a median 15 bp and a p95 of "
            f"32 kb, because transcript models have been extended since."
        )
    return text


def write_ui_config(
    resources: Path,
    fields: Mapping[str, schema.FieldMeta],
    assemblies: Sequence[str] | None = None,
) -> dict[str, Path]:
    """Write webapp configuration matching the built schema.

    ``external_links.yaml`` and ``badges.yaml`` reference columns by name, so a
    tree carrying the old ones would fail validation against a fresh database:
    the links would point at columns that no longer exist, and the badges would
    configure annotation columns a fresh build does not have.

    Returns:
        The files written, by kind.
    """
    roles = schema.resolve_roles(dict(fields))
    written: dict[str, Path] = {}

    links = [
        (role, label, template)
        for role, label, template in (
            ("ncbi", "NCBI Gene", "https://www.ncbi.nlm.nih.gov/gene/{}"),
            ("omim", "OMIM", "https://www.omim.org/entry/{}"),
            (
                "hgnc",
                "HGNC",
                "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/{}",
            ),
            (
                "ensg",
                "Ensembl",
                "https://www.ensembl.org/Homo_sapiens/Gene/Summary?g={}",
            ),
            (
                "symbol",
                "GeneCards",
                "https://www.genecards.org/cgi-bin/carddisp.pl?gene={}",
            ),
        )
        if roles.identifiers.get(role)
    ]
    body = [
        "# External links, generated by 'genetrek-db init'.",
        "# Each entry appears in the per-gene dialog; {placeholder} is a column.",
        "links:",
    ]
    for role, label, template in links:
        body += [
            f'  - name: "{label}"',
            f'    url: "{template.format("{" + roles.identifiers[role] + "}")}"',
            "",
        ]
    path = resources / paths.LAYOUT["external_links"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
    written["external_links"] = path

    badges = resources / paths.LAYOUT["badges"]
    badges.write_text(
        "# Badge rendering, generated by 'genetrek-db init'.\n"
        "# Empty: a fresh build has no annotation columns to render as badges.\n"
        "# See docs/database-maintenance.md for adding one.\n"
        "{}\n",
        encoding="utf-8",
    )
    written["badges"] = badges

    # Only when absent: `_carry_over` brings an existing one forward, and a
    # hand-curated order must survive a rebuild. Written here rather than in
    # `write_tree` so a tree assembled by calling `write_ui_config` directly
    # also gets one.
    categories = paths.LAYOUT["categories"]
    categories_path = resources / categories
    if not categories_path.exists():
        categories_path.parent.mkdir(parents=True, exist_ok=True)
        categories_path.write_text(
            _starter_categories_yaml(assemblies or ()),
            encoding="utf-8",
        )
        written["categories"] = categories_path
    return written


#: The category declaration a fresh build starts from, covering the only two
#: groups it can produce. Annotation columns added later bring their own
#: categories, which have to be appended here to be ordered.
#:
#: A constant rather than a literal in the writer, so a test can assert the
#: built tree's column order against the same declaration that produced it.
STARTER_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        CATEGORY_IDENTITY,
        "HGNC nomenclature and cross-references to other databases",
        (SUBCATEGORY_NOMENCLATURE, SUBCATEGORY_CROSS_REFERENCES),
    ),
    (CATEGORY_COORDINATES, "Genomic position", ()),
)

#: Label for the bucket the starter file gives uncategorised columns.
STARTER_UNCATEGORIZED = "Other"


def _starter_categories_yaml(assemblies: Iterable[str]) -> str:
    """Render the starter ``categories.yaml``.

    Assemblies are listed explicitly rather than left alphabetical, because
    the reference assembly comes first in the build spec and should come first
    in the table too -- "GRCh37" sorts before "GRCh38", which is backwards.
    """
    lines = [
        "# Display order of the column categories, for the gene table and the",
        "# column selector alike. List order *is* the order shown.",
        "#",
        "# Generated by 'genetrek-db init', then hand-maintained: a rebuild",
        "# carries this file over rather than regenerating it, because category",
        "# names do not depend on the build the way external_links.yaml and",
        "# badges.yaml depend on column names.",
        "categories:",
    ]
    for name, description, subcategories in STARTER_CATEGORIES:
        subs = tuple(assemblies) if name == CATEGORY_COORDINATES else subcategories
        lines.append(f"  - name: {name}")
        lines.append(f"    description: {description}")
        if subs:
            lines.append(f"    subcategories: [{', '.join(subs)}]")
        lines.append("")
    lines += [
        "  # Where columns with no category sort, and what they show up as.",
        f"  - name: {STARTER_UNCATEGORIZED}",
        "    description: Columns not yet assigned a category",
        "    uncategorized: true",
        "",
    ]
    return "\n".join(lines)


#: Files the webapp needs that a build does not generate, so they are copied
#: from an existing tree. Without them ``load_config`` exits at startup.
#:
#: ``categories`` is here rather than regenerated because it is hand-curated:
#: overwriting it on every rebuild would silently discard that curation, and
#: unlike the other generated config it references no column names, so an
#: existing one stays correct against a fresh database.
CARRIED_OVER: tuple[str, ...] = (
    "token_colors",
    "site_description",
    "categories",
    # Hand-curated like categories. It does name a column, but an HGNC-authored
    # one the build always writes, and `validate` reports it if that changes.
    "backgrounds",
)


@dataclass(frozen=True)
class TreeReport:
    """Every file a build wrote, for reporting."""

    resources: Path
    parquet: Path
    fields_yaml: Path
    #: Webapp configuration generated from the resolved roles, by kind.
    ui_config: dict[str, Path]
    #: Files copied from ``source_tree`` because a build cannot generate them.
    carried: list[Path]
    #: The alias index, distilled from the same HGNC release. ``None`` only if
    #: writing it failed, which costs retired-symbol resolution and nothing else.
    alias_index: Path | None = None

    def files(self) -> list[Path]:
        """Every path touched, in write order."""
        return [
            self.parquet,
            self.fields_yaml,
            *self.ui_config.values(),
            *([self.alias_index] if self.alias_index else []),
            *self.carried,
        ]


def write_tree(
    result: BuildResult,
    target: Path,
    *,
    source_tree: Path | None = None,
    actor: str | None = None,
    note: str | None = None,
) -> TreeReport:
    """Write a built database, plus the webapp configuration, into a tree.

    Order matters. The generated configuration goes in *before* the pair is
    saved, because :meth:`Database.save` validates the new metadata against it: a
    tree whose ``external_links.yaml`` still names the previous schema's columns
    fails that check every time, and the build would be unwritable.

    Backups and confirmation are deliberately not handled here -- whether
    replacing an existing tree needs a copy first is a policy decision, and it
    belongs to the caller that knows what it is about to overwrite.

    Args:
        result: What :func:`build` produced.
        target: Resources tree to write into. Created if absent.
        source_tree: Where to copy :data:`CARRIED_OVER` from. Defaults to the
            resources tree the usual resolution order finds; nothing is copied
            if that cannot be located or the file is already present.
        actor: Recorded in the history log.
        note: Recorded in the history log. Defaults to the resolved releases.

    Returns:
        A :class:`TreeReport` naming every file written.

    Raises:
        ValidationFailedError: If the built database does not validate. Nothing
            is left half-written: the pair is swapped in one step.
    """
    from genetrek.db.database import Database  # noqa: PLC0415 - avoids a cycle

    paths.make_tree(target)
    carried = _carry_over(target, source_tree)
    ui_config = write_ui_config(target, result.fields, result.plan.spec.assemblies)

    # A copy, not the caller's dict: Database mutates what it is handed, so a
    # notebook that adds annotation columns afterwards would otherwise find its
    # BuildResult quietly carrying them too.
    database = Database(result.frame, dict(result.fields), resources=target)
    database.save(
        backup=False,
        actor=actor,
        note=note
        or (
            f"init from HGNC {result.plan.hgnc_release}"
            f" + GENCODE v{result.coordinates.gencode_release}"
        ),
    )

    # The complete set is in hand -- it is what the database was just built
    # from -- and it will not be in the deployed image, so distil the alias
    # index now rather than leaving a tree that resolves current symbols only.
    # Best-effort: a tree that resolves exact symbols is still a usable tree,
    # and `validate` says so.
    alias_index: Path | None = None
    try:
        index = GeneIndex.from_frame(result.frame, database.roles).with_hgnc(
            result.hgnc_file,
        )
        index.write_alias_index(paths.aliases_path(target))
        alias_index = paths.aliases_path(target)
    except (GeneIndexError, OSError) as exc:  # pragma: no cover - defensive
        print(f"Warning: could not write the alias index: {exc}")  # noqa: T201

    return TreeReport(
        resources=target,
        parquet=paths.parquet_path(target),
        fields_yaml=paths.fields_yaml_path(target),
        ui_config=ui_config,
        carried=carried,
        alias_index=alias_index,
    )


def _carry_over(target: Path, source_tree: Path | None) -> list[Path]:
    """Copy the config files a build cannot generate, skipping what exists."""
    try:
        origin = paths.resources_dir(source_tree)
    except paths.ResourcesNotFoundError:
        return []

    copied: list[Path] = []
    for kind in CARRIED_OVER:
        relative = paths.LAYOUT[kind]
        destination = target / relative
        if destination.exists() or not (origin / relative).is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin / relative, destination)
        copied.append(destination)

    # The prose pages are declared in `genetrek.content`, not in LAYOUT, so they
    # are carried as a directory -- and per file rather than wholesale, unlike
    # static/ below. A tree that already has a customised `documentation.md` but
    # no `changelog.md` still needs `changelog.md`, which a skip-if-non-empty
    # rule would never give it. Existing files are still never overwritten.
    for page in CONTENT_PAGES:
        source = paths.content_path(page.slug, origin)
        destination = paths.content_path(page.slug, target)
        if destination.exists() or not source.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination)

    # A directory rather than a LAYOUT entry, so it needs its own step. Skipped
    # wholesale if the target already has one: a rebuild must never silently
    # replace curated files that a build cannot regenerate.
    static_origin = paths.static_dir(origin)
    static_target = paths.static_dir(target)
    if static_origin.is_dir() and not any(static_target.glob("*")):
        shutil.copytree(static_origin, static_target, dirs_exist_ok=True)
        copied.append(static_target)
    return copied
