"""Response models for the HTTP API.

Two conventions run through the lookup models, both to keep a client's parsing
code stable across databases:

* A gene identifier is addressed by its **role** -- ``symbol``, ``hgnc``,
  ``ensg``, ``ncbi``, ``omim`` -- not by the column name, because the real
  database's symbol lives in ``hgnc_symbol`` and one built by ``genetrek-db
  init`` calls it something else. Each response reports the role-to-column
  mapping it used, so the column names are never guessed at.
* A field requested by an alias is echoed back as both ``field`` (the real
  column, which is what the values came from) and ``requested_as`` (what was
  asked for). Downloaded data is then never ambiguous about its own provenance.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DataResponse(BaseModel):
    data: list[dict[str, Any]]
    total: int
    page: int
    per_page: int


class FieldSummary(BaseModel):
    """One column's metadata.

    ``name`` is injected by the API: ``FieldMeta`` has no such key, because a
    column's name is its mapping key in ``fields_description.yaml``.
    """

    name: str
    fullname: str
    description: str
    category: str
    subcategory: str
    visibility: str
    value_type: str | None
    id_type: str | None
    assembly: str
    source: str
    doi: str
    #: The terms the column's values are redistributed under (see
    #: ``DATA_LICENCES.md``). Empty only for a column nobody has reviewed.
    licence: str = ""
    #: Which genes were measured, in words. Empty for a curated list or a
    #: column with no notion of coverage.
    universe: str
    update: str
    aliases: list[str]
    derived_op: str | None
    derived_from: list[str]
    #: How many genes carry a value, present only when asked for -- it costs a
    #: pass over the frame.
    n_genes: int | None = None


class FieldListResponse(BaseModel):
    total: int
    fields: list[FieldSummary]


class FieldCoverage(BaseModel):
    """Only the block matching the column's type is populated."""

    dtype: str
    n_rows: int
    n_null: int
    #: Rows with a value -- the column's universe. Null means "not measured",
    #: so ``n_true + n_false == n_measured`` for a boolean column.
    n_measured: int
    n_unique: int
    n_true: int | None = None
    n_false: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    top_values: list[tuple[str, int]] | None = None
    #: Values equal to the literal string ``"None"``, a missing-value marker
    #: that came in with the imported data.
    n_missing_marker: int | None = None


class FieldDetailResponse(BaseModel):
    field: str
    requested_as: str
    metadata: FieldSummary
    coverage: FieldCoverage


class GenePair(BaseModel):
    gene: str
    value: Any


class FieldGenesResponse(BaseModel):
    field: str
    requested_as: str
    id_type: str
    id_column: str
    value_type: str | None
    drop_empty: bool
    #: Rows in this response.
    total: int
    #: Rows dropped because the gene has no value for the chosen identifier --
    #: a pair with a null gene names nothing, so it is never returned.
    n_without_identifier: int
    genes: list[GenePair]


class FieldUniverseResponse(BaseModel):
    """The genes a column measured: where its value is not null."""

    field: str
    requested_as: str
    universe: str
    id_type: str
    id_column: str
    #: Genes in this response.
    total: int
    #: Genes the column did not measure (null).
    n_not_measured: int
    #: Measured genes left out because they have no value for ``id_type``.
    n_without_identifier: int
    genes: list[str]


class GeneMatch(BaseModel):
    """How a user-supplied identifier was resolved onto one gene."""

    input: str
    matched_by: str
    note: str
    key: str
    key_column: str


class GeneResponse(BaseModel):
    gene: GeneMatch
    id_columns: dict[str, str]
    identifiers: dict[str, Any]
    values: dict[str, Any]


class GeneGenesetsResponse(BaseModel):
    gene: GeneMatch
    total: int
    genesets: list[FieldSummary]


class ConversionResult(BaseModel):
    input: str
    matched_by: str
    note: str
    ids: dict[str, Any]


class ConvertResponse(BaseModel):
    #: What the caller asked for, which may be ``"auto"``.
    requested_id_type: str
    #: The identifier type actually used, or ``"mixed"`` when the inputs did
    #: not agree on one and each was classified on its own.
    id_type: str
    #: Whether ``id_type`` was guessed rather than specified.
    detected: bool
    n_input: int
    n_resolved: int
    counts: dict[str, int]
    #: How far symbol resolution reaches: ``hgnc`` (the complete set itself),
    #: ``alias-index`` (the precomputed distillation of it, which is what a
    #: deployment ships) or ``exact-only`` (no alias source, so a retired
    #: symbol resolves to nothing).
    symbol_coverage: str
    #: Verbatim from the resolver. This is where a caller learns that bare
    #: integers were assumed to be NCBI ids, or that retired symbols cannot
    #: resolve because no alias source is loaded.
    warnings: list[str]
    id_columns: dict[str, str]
    results: list[ConversionResult]


# -- POST bodies -------------------------------------------------------------------
#
# Gene lists reach the API in request *bodies*, never in a URL: query strings
# are written to the server's and the proxy's access logs, bodies are not. A
# visitor's list sent here is used for the response and kept nowhere -- the
# same promise My lists makes in the browser.

#: Most identifiers one set, or one POST /api/convert, may carry. Every gene in
#: the database is ~45,000.
MAX_LIST_IDS = 50_000


class ConvertRequest(BaseModel):
    """``POST /api/convert``: the GET form's parameters, with the ids in the body."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    ids: list[str] = Field(min_length=1, max_length=MAX_LIST_IDS)
    from_id_type: str = Field("auto", alias="from")
    to: list[str] | None = None


class CompareSet(BaseModel):
    """One set to compare. Exactly one of ``field``, ``genes``, ``lists_file``,
    ``share`` says where its genes come from."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(None, max_length=60)
    #: A database gene list (a Boolean column), by name or alias.
    field: str | None = None
    #: Identifiers of any kind -- symbols, HGNC, NCBI, Ensembl, OMIM, mixed.
    genes: list[str] | None = Field(None, max_length=MAX_LIST_IDS)
    #: How to read ``genes``: ``auto`` (default), or one identifier type.
    id_type: str = "auto"
    #: A My lists export file, as downloaded from GeneTrek.
    lists_file: dict[str, Any] | None = None
    #: A My lists share token: the part after ``#lists=`` in a share link.
    share: str | None = None
    #: Which list of a file or share token, counting from 0.
    list_index: int = Field(0, ge=0)

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        given = [
            name
            for name in ("field", "genes", "lists_file", "share")
            if getattr(self, name) is not None
        ]
        if len(given) != 1:
            msg = (
                "each set needs exactly one of 'field', 'genes', 'lists_file', "
                f"'share' (got {given or 'none'})"
            )
            raise ValueError(msg)
        return self


class CompareRequest(BaseModel):
    """``POST /api/compare``."""

    model_config = ConfigDict(extra="forbid")

    sets: list[CompareSet] = Field(min_length=2, max_length=12)
    #: A background name from ``GET /api/backgrounds``; the default when omitted.
    background: str | None = None
    #: Covariates to stratify by. Omitted: every configured covariate. ``[]``:
    #: crude results only. One covariate at a time is supported.
    adjust_for: list[str] | None = Field(None, max_length=1)
    #: Whether each region in the response lists its genes, not just counts them.
    include_genes: bool = False


# -- compare responses -------------------------------------------------------------

#: A number that may be infinite: JSON has no infinity, so it is the string
#: ``"Inf"`` (an odds ratio or interval end with a zero cell); ``null`` when
#: undefined.
Ratio = float | str | None


class BackgroundInfo(BaseModel):
    name: str
    definition: str
    description: str = ""
    size: int
    #: In a comparison: the genes actually analysed -- the background less those
    #: some set did not measure. Equal to ``size`` when every set covers it.
    effective_size: int | None = None
    #: In a comparison: the sets whose coverage shrank the background.
    restricted_by: list[str] = Field(default_factory=list)


class CovariateInfo(BaseModel):
    name: str
    column: str
    bins: int
    description: str = ""
    definition: str


class BackgroundsResponse(BaseModel):
    backgrounds: list[BackgroundInfo]
    covariates: list[CovariateInfo]


class CompareSetResult(BaseModel):
    label: str
    name: str
    #: ``database``, ``genes``, ``lists_file`` or ``share``.
    source: str
    field: str | None = None
    n_input: int
    #: Distinct genes the set resolved to.
    n_genes: int
    #: Genes left in the analysed universe.
    n_in_background: int
    n_outside_background: int
    #: Genes inside the background that some set in the comparison did not
    #: measure, so they are left out of every pair.
    n_not_measured: int = 0
    #: Keys a list file or share token carries that this release does not have.
    n_not_in_database: int = 0
    unmatched: list[str] = Field(default_factory=list)
    ambiguous: list[str] = Field(default_factory=list)


class AdjustedPairResult(BaseModel):
    adjusted_for: str
    odds_ratio: Ratio
    ci95: list[Ratio]
    p_value: float | None
    fdr: float | None
    strata_used: int


class ComparePairResult(BaseModel):
    set_a: str
    set_b: str
    n_a: int
    n_b: int
    overlap: int
    expected_overlap: float | None
    fold_enrichment: float | None
    #: Conditional maximum-likelihood estimate, with the exact 95% interval.
    odds_ratio: Ratio
    ci95: list[Ratio]
    #: Two-sided Fisher's exact test. 0 means below ~1e-300.
    p_value: float | None
    fdr: float | None
    jaccard: float | None
    overlap_coefficient: float | None
    #: Why the overlap is expected by construction, or empty.
    overlap_by_construction: str = ""
    adjusted: AdjustedPairResult | None = None


class CompareRegionResult(BaseModel):
    #: Labels of the sets the region's genes are in -- and in no other.
    sets: list[str]
    size: int
    genes: list[str] | None = None


class CompareResponse(BaseModel):
    release: str
    #: The column gene keys are values of, e.g. ``hgnc_id``.
    key: str
    background: BackgroundInfo
    adjusted_for: list[str]
    sets: list[CompareSetResult]
    pairs: list[ComparePairResult]
    regions: list[CompareRegionResult]
    #: The methods paragraph the Compare view's downloads carry.
    methods: str
    #: Things a reader should know before quoting the numbers -- e.g. that
    #: one set's coverage removed a large share of the background.
    warnings: list[str] = Field(default_factory=list)
