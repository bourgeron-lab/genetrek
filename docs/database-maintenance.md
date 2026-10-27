# Maintaining the GeneTrek database

The database is a **pair** of files that only make sense together:

| File | What it holds |
|---|---|
| `resources/processed_data/genetrek.parquet` | 61,669 genes × 67 fields |
| `resources/config/fields_description.yaml` | one metadata entry per parquet column |

The webapp reads both at startup. If they disagree, columns silently vanish from the UI,
external links break, or the app fails to boot. Everything below exists to keep them in
step: the tooling treats them as one transactional unit, validates before writing, backs
up what it replaces, and logs what it did.

## Two ways in

**Command line** — `genetrek-db`. Independent of the webapp; it never imports FastAPI or
NiceGUI, so a broken database can still be inspected and repaired.

```bash
uv run genetrek-db --help
```

**Library** — for notebooks.

```python
from genetrek.db import Database

db = Database.open()
db.list_fields()                       # renders as a table in Jupyter
db["loeuf"].stats()
db["loeuf"].meta.visibility = "primary"
db.diff()                              # review before writing
db.save(actor="curator", note="promote LOEUF")
```

Both resolve the resources directory the same way: an explicit argument, then
`$GENETREK_RESOURCES`, then by searching upwards for a `resources/` directory containing
`config/fields_description.yaml`. Point `$GENETREK_RESOURCES` at a copy to experiment
safely.

To create a database rather than maintain one, see
[Building a database from scratch](#building-a-database-from-scratch-init).

## The field metadata schema

Nineteen keys per field. The first six are the original schema; the rest were added so the
dtype contract is explicit, new fields carry provenance, display order is declared rather
than inherited, columns group into a browsable taxonomy, and the code can find a column by
what it means rather than by its name.

```yaml
is_haploinsufficient:
  fullname: Haploinsufficient              # the column label shown in the UI
  description: "genes n= 2,987 (pHaplo >= 0.86)"
  update: "20240116"                       # "" or YYYYMMDD, validated as a real date
  visibility: primary                      # primary | secondary | hidden
  priority: 40                             # order *within* the group below; null sorts last
  category: "Constraint"                   # top-level group; declared in categories.yaml
  subcategory: "gnomAD"                    # group within the category; "" sorts first
  source: ""
  doi: ""
  universe: ""                             # which genes were measured, in words
  value_type: boolean                      # checked against the parquet dtype
  id_type: null                            # the column's role -- see below
  assembly: ""                             # only on locus_* roles, e.g. GRCh38
  primary_key: false                       # is this what identifiers resolve onto?
  added: ""                                # YYYYMMDD, set automatically on add
  source_file: ""                          # set automatically on add
  derived_op: null                          # union | intersection | external
  derived_from: []                          # the fields it is computed from
  aliases: []                              # stable short names pointing here
```

- `visibility` drives the UI. `primary` columns are shown by default, `secondary` are
  available in the column selector, `hidden` are sent to the browser but never displayed
  (which is how `{ncbi_id}` and `{omim_id}` external links work).

- `category` and `subcategory` decide *where* a column sits, as `visibility` decides
  *whether* it shows. Non-hidden columns are sorted by category, then subcategory, then
  `priority`, then alphabetically by `fullname` — and that one ordering drives both the
  table's left-to-right column order and the tree in the column selector. See
  [Categories](#categories-grouping-the-columns).

- `priority` orders columns **within one subcategory**, smaller first; a column without one
  sorts after every column in its group that has one. It is *not* comparable across groups:
  the convention is to restart at 10 in each, so the same value in two categories means
  nothing. Set it only where alphabetical order inside a group is wrong — a summary or
  "any source" column that should lead it. A negative value goes ahead of everything already
  numbered in that group.

  Three invariants hold around it, all enforced:

  - a `primary` column must declare a `priority`, or its position in the default view is
    decided by its label (`validate` warns; `tests/test_real_database.py` fails);
  - a `hidden` column must *not*, because a hidden column never reaches the table and the
    priority would be dead metadata;
  - the first visible column must be the column filling the `symbol` role. `ui/styles.py`
    styles the sticky, right-aligned first column *positionally* (`th:first-child`), so
    whichever column sorts first inherits that treatment. `validate` warns when it is not
    the symbol — worth knowing about, because a category change anywhere can take that slot.

- `universe` says which genes a **measured** column covers, e.g. "Proteins with a TS
  score in Brain Cortex or Brain Cerebellum (Jiang et al. 2020)". Outside that universe
  the column is null, meaning *not measured*, and never `False` or `0`.
  - The count is not stored: it is the column's non-null rows, so it cannot drift.
  - Compare restricts its universe to it, and so does the drawer's enrichment.
  - The table shows a grey "—" for such a null, and `/api/fields/<f>/universe` serves it.
  - Leave it empty for a curated list, where absent means "not on it".
  - Set it with `genetrek-db set <f> --universe "..."`, or `add --universe` (with
    `--missing keep`).
- `value_type` is the **declared** type. `validate` fails if the parquet dtype no longer
  matches, which is what catches a boolean column silently becoming a string.
- `id_type`, `assembly` and `primary_key` are the **role** keys. Annotation fields leave
  `id_type` null. See below.

Key order in the YAML is kept identical to parquet column order, and the writer reproduces
the file's existing quoting style exactly — so saving an unmodified database is a no-op and
every real diff is readable.

This is also why display order is a **value** (`priority`, `category`) and not a position in
this file: `save()` re-sorts the entries to match the parquet on every write, so moving an
entry up the file is silently undone. Use `genetrek-db set <field> --priority N`.

## Categories: grouping the columns

`config/categories.yaml` declares the top-level groups and, by list position, the order they
appear in. There is no priority number here, so inserting a category never means renumbering
its neighbours.

```yaml
categories:
  - name: Gene identity
    description: HGNC nomenclature and cross-references to other databases
    subcategories: [Nomenclature, Cross-references]   # optional order override
  - name: Constraint
    description: Selection and mutational constraint metrics
  - name: Other                                       # where uncategorised columns sort
    uncategorized: true
```

- **Subcategories are alphabetical** unless the category lists them explicitly. Reach for the
  override when alphabetical is wrong: casefolded, `"cross-references"` sorts before
  `"nomenclature"` and `"grch37"` before `"grch38"`, and both of those are backwards.
- **An empty `subcategory` sorts first** within its category, so a group's headline columns
  sit above its subgroups. This is what keeps the gene symbol in column 1.
- **`uncategorized: true`** marks where columns with no `category` sort, and what they are
  labelled. A flag rather than a reserved name, so the bucket can be called anything and a
  real category may still be named "Other". At most one entry may claim it. Without one,
  uncategorised columns sort last.
- **An undeclared category is not an error.** Its columns still sort — after everything
  declared, alphabetically by category name — and `validate` warns. A declared category no
  field uses is reported as a note.
- **The file is optional.** A tree without one orders columns by `priority` alone, which is
  how ordering worked before categories existed. `genetrek-db init` writes a starter file
  covering the two groups a build can produce, and a rebuild *carries an existing one over*
  rather than regenerating it — unlike `external_links.yaml` and `badges.yaml`, category
  names do not depend on the build, so a curated file stays correct against a fresh database.

Names may not contain `/`, because the selector's tree node ids are `sub:<category>/<name>`.

Move a column between groups with `set`, and read the current taxonomy with `categories`:

```bash
uv run genetrek-db set loeuf --category Constraint --subcategory gnomAD
uv run genetrek-db set loeuf --category ""          # back to the uncategorised bucket
uv run genetrek-db categories                       # the declared order, with counts
```

Editing the order itself is a hand edit of `categories.yaml`, as it is for `badges.yaml`.

## Roles: how the code finds a column

The webapp used to hardcode column names — `search.py` looked for a column literally called
`symbol`, `enrichment.py` for `ensg`, and the locus lookup built `f"{genome}_chr"`. Renaming
any of them broke things **silently**: search returned the *entire unfiltered table* and
reported it as hits.

Columns are now found by what they mean. Two metadata keys carry that:

| key | says |
|---|---|
| `id_type` | which identifier or coordinate component this column *is* |
| `primary_key` | whether this is the column identifiers resolve onto |

They are orthogonal on purpose: `ncbi_id` is both *the NCBI ID* (`id_type: ncbi`) and *the
key* (`primary_key: true`), and one field could not say both.

```yaml
ncbi_id:
  id_type: ncbi
  primary_key: true
symbol:
  id_type: symbol
hg38_chr:
  id_type: locus_chr
  assembly: hg38
hg38_start:
  id_type: locus_start
  assembly: hg38
```

`schema.resolve_roles()` turns that into a `Roles` object the code asks instead of guessing:

```python
db = Database.open()
print(db.roles.describe())
#   primary_key: ncbi_id
#   symbol: symbol
#   ensg: ensg
#   ncbi: ncbi_id
#   hgnc: hgnc_id
#   omim: omim_id
#   hg19: hg19_chr / hg19_start / hg19_end
#   hg38: hg38_chr / hg38_start / hg38_end
```

### What this buys you

**Renaming an identifier or coordinate column no longer needs `--force`.** `rename_field`
carries the metadata across, so the `id_type` follows the column and every consumer follows
with it:

```bash
uv run genetrek-db rename symbol hgnc_symbol      # search still works
uv run genetrek-db rename hg38_chr grch38_chr     # locus search still works
```

That is why `schema.CODE_DEPENDENT_COLUMNS` shrank from 19 entries to a handful. What remains
is the genuinely name-bound residue — `inheritance`, `eagle_score`, the constraint measures
(`shet_mean`, `loeuf`, `moeuf`, plus the optional `is_shet_constrained` and the legacy `pli`
fallback) and the three `37365347_*` columns, which a UI component asks for by literal name
because they are specific measurements rather than roles.

### Coordinate roles

An assembly needs `locus_chr`, `locus_start` and `locus_end` all sharing one `assembly`
value; `locus_strand` is optional. An incomplete assembly is dropped with a warning rather
than half-working, and the assembly names themselves populate the genome selector in the
search drawer — a database using `GRCh37`/`GRCh38` offers those instead of `hg19`/`hg38`.

The bare `id_type: locus` that predates this is still accepted so an unmigrated file loads,
but it contributes no coordinates and `validate` says so.

### When a role is unfilled

A database need not carry every identifier, so an unfilled role is not an error. But an
**ambiguous** one is — two columns claiming `id_type: symbol` makes the choice arbitrary, so
`validate` reports it as an error and `save` refuses.

If no column the search could use is present at all, `filter_dataframe` raises
`SearchUnavailableError` rather than returning every row. A full table looks identical to "no
search was run", which was the most misleading thing the app could do.

## Gene identifier resolution

Gene lists arrive keyed on whatever the source used. Resolution normalises all of it onto
`ncbi_id`, the one identifier that is complete and unique across all 61,669 rows.

| Input | Handling |
|---|---|
| `SHANK3` | current HGNC symbol |
| `KIAA1650` | retired symbol, resolved via HGNC `prev_symbol` / `alias_symbol` |
| `ENSG00000251322.4` | version suffix stripped |
| `HGNC:14294` | `HGNC:` prefix stripped (the parquet stores a bare integer) |
| `85358` | NCBI ID |
| `1.0` | Entrez ID mangled by a spreadsheet round-trip |

**Always check a list before adding it.** `resolve` changes nothing and exits non-zero if
anything failed:

```bash
uv run genetrek-db resolve my_genes.txt
uv run genetrek-db resolve my_genes.txt --unmatched-only > failures.txt
cat my_genes.txt | uv run genetrek-db resolve -
```

Mixed lists work: with `--id-type auto` (the default), as soon as more than one
recognisable shape is present each value is classified on its own. A majority rule would
resolve a mostly-symbol list wholly as symbols and silently fail the few Ensembl IDs in it.

Bare integers are genuinely ambiguous — `ncbi_id`, `hgnc_id` and `omim_id` are all plain
integers in the parquet — so they are read as `ncbi_id` with a warning. Pass `--id-type`
when the source is something else.

### Why the HGNC file matters

Loading the HGNC complete set enables alias and previous-symbol matching. On 4,000 real
retired symbols, exact matching alone resolves 242; with the cascade it is 3,968. A gene
list from an older paper would otherwise lose most of its genes with no indication.

The file is found automatically: the newest `hgnc_complete_set*` in
`resources/original_file/`, chosen by modification time. Without one, resolution still works
but only matches current symbols, and says so.

Only the standard HGNC columns are read — `hgnc_id`, `symbol`, `alias_symbol`,
`prev_symbol`, `entrez_id` — so a stock complete set from
<https://www.genenames.org/download/archive/> works. The lab's own copy is a lab-extended
version with ~90 further annotation columns appended (PanelApp releases, gnomAD v4.1.1
constraint, newer gene-list versions); those extra columns are the *source* of new fields,
not part of resolution.

> **Not committed.** That file is ~34 MB and currently untracked. Decide whether to commit
> it or keep it out of git; the tooling degrades gracefully either way.

## Adding a field

```bash
uv run genetrek-db add hc_ndd_v7.tsv \
  --id-col hgnc_id --value-col "High_Confidence_NDD_genes_v7" \
  --name hc_ndd_v7 --fullname "High Confidence NDD genes v7" \
  --visibility secondary --source "HGNC GeneList" \
  --dry-run
```

Input can be a two-column table (identifier + value), a one-column gene list (header
optional — a bare first line is treated as a gene, not a column name), or, in the library,
a list/Series/DataFrame. The field name defaults to a slug of the value column.

`--dry-run` prints the resolution report and the resulting column statistics without
writing anything. Drop it, add `--yes`, and it saves.

### Numeric columns and dtype

A numeric column keeps whatever dtype the input has, so cast it deliberately before adding.
`Float32` halves a column's memory (248 KiB versus 489 KiB over 61,669 rows) and is right for
most measurements, but **it underflows anything below ~1.2e-38 to `0.0`** — which reads as the
exact opposite of a very small probability.

This is not hypothetical. The `pli` column is `Float32` and contains **91 zeros** that were
tiny positive values in the source, lost when the original bootstrap notebook ran
`shrink_dtype()` over everything. gnomAD pLI/pNull/pRec reach 7e-226, so those need `Float64`:

```python
frame = source.select(
    "hgnc_id",
    pl.col("lof.pLI_gnomad.v4.1.1").cast(pl.Float64, strict=False).alias("pli_v4_1_1"),
).drop_nulls("pli_v4_1_1")
db.add_field(frame, "pli_v4_1_1", id_col="hgnc_id", value_col="pli_v4_1_1",
             id_type="hgnc", missing="keep", allow_unmatched=True)
```

`notebooks/update_20260819.ipynb` checks every cast for underflow before staging anything, and
reports the worst relative rounding error for the casts it accepts.

### Rows the input does not mention

Controlled by `--missing` (`missing=` in the library):

- `auto` (default) — boolean fields fill `False` (absent from a curated gene list means not on
  it); every other type is left null.
- `keep` — leave them null whatever the type.
- `fill --fill VALUE` — use an explicit value.
- `error` — refuse unless every one of the 61,669 rows is covered.

**`auto` and `keep` are not interchangeable for booleans**, and the database contains both
conventions:

| shape | example | uncovered rows | why |
|---|---|---|---|
| Curated gene list | `acmg_sf` (0 nulls) | `False` | Not on the list means not on it |
| Derived flag | `gnomad_v4_is_loeuf` (43,925 nulls) | null | No upstream data means unknown, not "unconstrained" |
| Measured call | `gtex_v11_brain_enriched` | null | Not measured; `universe` says which genes were |

Importing a derived flag with `auto` would silently assert `False` for every gene the upstream
source has no data on. Use `keep` for anything computed from a measurement — a constraint
threshold, a selection-coefficient call — and `auto` for a hand-curated list.

### Identifiers that do not resolve

`add` refuses by default, because silently dropping genes is the failure this tooling
exists to prevent. The two kinds are separate, because they mean different things:

- `--allow-unmatched` — the source covers genes the database does not carry. Common and
  harmless: the HGNC complete set has 44,084 rows, 1,355 of which are not in GeneTrek.
- `--allow-ambiguous` — an identifier matches several genes, so its value cannot be
  assigned. `HGNC:12275` is both `TARP` and `TRGC1`; there are 10 such HGNC IDs and 8
  duplicated symbols in the parquet. These rows are dropped.

`--force` is shorthand for both.

## Removing a field

```bash
uv run genetrek-db remove old_field
```

Refused when the field is `ncbi_id` (everything resolves onto it) or when the webapp
configuration references it — a `badges.yaml` key or an `external_links.yaml` `{placeholder}`.
`--force` overrides the staging check, but `save` still validates: to actually drop `ensg`
you must also remove `{ensg}` from `external_links.yaml`, or the app would be left with a
dangling link.

## Renaming a field

A field key is not just metadata. It is simultaneously the parquet column name, the metadata
key, possibly a `badges.yaml` key, an `external_links.yaml` `{placeholder}`, a
`column_mapping.json` value, and possibly a name written into Python or JavaScript. So
`set` cannot do it — `set` changes metadata *values*. Use `rename`:

```bash
uv run genetrek-db rename gnomad_v4_is_loeuf is_loeuf_gnomad_v4
```

It renames the column **in place** (position preserved, so the diff is one changed key
rather than a reordering), carries the metadata across untouched, and rewrites references in
`badges.yaml`, `external_links.yaml` and `column_mapping.json` — reporting each edit.
`--keep-config` skips those rewrites, but then `save` refuses, because a stale placeholder
fails validation.

`fullname` is deliberately *not* changed: the key and the display label are independent
decisions. The command reminds you.

### Derived columns: a roll-up as a rule

A boolean column that is the union or intersection of other boolean columns can be recorded as
a rule rather than imported as data:

```bash
uv run genetrek-db derive chrom_epi_tf_combined \
  --from chromatin_remodeling --from epifactors_v2_1 \
  --from rummagene_tf --from trrust_v2_tf --dry-run
```

The rule lands in `fields_description.yaml` as two keys beside `aliases`:

```yaml
chrom_epi_tf_combined:
  ...
  derived_op: union
  derived_from: [chromatin_remodeling, epifactors_v2_1, rummagene_tf, trrust_v2_tf]
```

The column is still written to the parquet — the pair stays one unit, and search, enrichment
and the table read it like any other column. What the rule adds is that `validate` recomputes
it and **errors** when the stored values no longer match, and that
`genetrek-db recompute-derived` regenerates it after a source is refreshed. Without the rule a
drifted roll-up is invisible: it is an ordinary boolean column, and nothing about it says what
it was supposed to be.

`show` prints the rule and whether it currently holds.

Deliberate limits:

- **Sources must be boolean; nulls follow Kleene logic.** Null means "not measured":
  - a union is True if any source is True, False only when every source is measured
    False, and null otherwise;
  - an intersection is False if any source is False.

  Filling nulls with False first would quietly turn "not measured" into "no".
  `Constraint_combined_woPLI` is still not a rule: it matches the union of two constraint
  flags on TRUE membership but has *fewer* non-null rows than either input, so its nulls
  do not follow from its inputs. It is data.
- **`save` never recomputes.** Silently rewriting a column during a save would repair exactly
  the drift `validate` exists to report. Recomputing is always an explicit step.
- **Unions and intersections only.** Not an expression language; a numeric roll-up or a
  threshold belongs in the import.
- **`external` records provenance, not a rule.** A flag computed outside GeneTrek, for
  example a threshold on a numeric column whose rule needs values the database does not
  store (an FDR, a published category), names its parent with `derived_op: external`,
  `derived_from: [<parent>]`.
  - It is never evaluated or drift-checked; `validate` only checks that the parents
    exist.
  - The info dialog shows "computed from `<parent>` outside GeneTrek".
  - The `notebooks/brain_data/` notebooks set it on every flag they add.

A roll-up may itself be a source for another roll-up — `schema.derived_order` sorts the
dependency graph and refuses a cycle.

### Names that code depends on

`genetrek.schema.CODE_DEPENDENT_COLUMNS` lists every column whose *name* appears in code, and
where. Renaming one is refused by default, because the code will not follow and the failure
is quiet — `ui/components/stats_drawer.py` needs `shet_mean` (or `pli`), `loeuf` and `moeuf`,
and renders a *"Missing columns"* label instead of the constraint plot. `--force` overrides
once you have updated the code.

The label column is *not* on that list: it comes from `roles.symbol`, so a database calling it
`hgnc_symbol` plots normally. `tests/test_roles.py::TestNoIdentifierNameIsHardcoded` scans the
view layer for a literal identifier name and fails if one creeps back in — which is how the
last of them was found.

`remove` applies the same guard, and reports *every* objection at once (code dependency,
badge key, link placeholder, primary key) rather than the first one it hits.

### Worked example: gnomAD v5 arrives

Today `pli`, `loeuf` and `moeuf` are unversioned, which is the real problem — nothing records
which release they came from. When v5 lands:

```bash
# 1. What depends on the current names?
uv run genetrek-db rename loeuf loeuf_gnomad_v4        # refused, and tells you why

# 2. Update ui/components/stats_drawer.py to use the new names, and
#    schema.CODE_DEPENDENT_COLUMNS to match. Then:
uv run genetrek-db rename loeuf loeuf_gnomad_v4 --force
uv run genetrek-db rename pli pli_gnomad_v4 --force
uv run genetrek-db rename moeuf moeuf_gnomad_v4 --force

# 3. Add the v5 columns
uv run genetrek-db add gnomad_v5.tsv --id-col gene_id --value-col lof.oe_ci.upper \
    --name loeuf_gnomad_v5 --fullname "LOEUF (gnomAD v5)" --visibility primary \
    --source "gnomAD v5 constraint metrics" --allow-unmatched

# 4. Demote the old one and confirm
uv run genetrek-db set loeuf_gnomad_v4 --visibility secondary
uv run genetrek-db validate
uv run genetrek-db history
```

Step 2 is the awkward one, and it is awkward *honestly*: three plot columns are hardcoded in
the UI. The alternative — leaving `loeuf` to silently mean "whichever release we imported
last" — is worse, because a published analysis citing GeneTrek's LOEUF would no longer be
reproducible.

## Changing metadata only

```bash
uv run genetrek-db set loeuf --visibility primary --description "gnomAD v4 LOEUF"
uv run genetrek-db set loeuf --priority 35        # slide it between 30 and 40
uv run genetrek-db set loeuf --priority ""        # back to the alphabetical tail
uv run genetrek-db set loeuf --touch          # stamp update with today's date
```

## Aliases for versioned families

`hc_ndd_v1`, `hc_ndd_v2`, `hc_ndd_v3` — and `candidate_ndd_v1..v5` — are families where
analyses usually want "the current one" without pinning the version in every query. An alias
gives you a stable short name:

```bash
uv run genetrek-db alias hc_ndd hc_ndd_v3      # point it
uv run genetrek-db alias                       # list every alias
uv run genetrek-db alias hc_ndd --remove       # drop it
uv run genetrek-db show hc_ndd                 # resolves, and says so
```

An alias is stored as an `aliases` list on the **target** field, so it travels with the field:
`rename` carries it automatically, and removing the field removes the alias with it — it can
never dangle.

```yaml
hc_ndd_v3:
  fullname: HC NDD
  visibility: secondary
  aliases: []
hc_ndd_v7:
  fullname: HC NDD v7
  visibility: primary
  aliases: [hc_ndd]
```

### An alias is a pointer, never a rule

Nothing computes "the latest version". Retargeting is a deliberate act:

```bash
uv run genetrek-db alias hc_ndd hc_ndd_v7 --actor curator --note "v7 release"
#   alias hc_ndd: hc_ndd_v3 -> hc_ndd_v7
```

That shows up as a one-line YAML diff and a `set_alias` record in `history.jsonl` carrying
both the old and new target. So the question *"what did `hc_ndd` mean when that analysis
ran?"* has an answer.

Auto-resolving to the highest version was considered and rejected, for two reasons:

- **Reproducibility.** A published analysis citing GeneTrek's `hc_ndd` would silently change
  meaning on the next import, with nothing recording it.
- **Version strings do not sort.** The HGNC file already carries `v6`, `v6.2`, `v6.3` and
  `v7`; naive ordering works there by luck and breaks at `v10` vs `v9`. And "latest" is a
  curation judgement anyway — `hc_ndd_v1..v3` coexist with different gene counts, and v7 is
  not automatically the right choice for every panel.

### Where aliases resolve

| Accepts an alias | Does not |
|---|---|
| `show`, `get`, `db[...]`, `db.resolve_name()`, `set` | `add`, `remove`, `rename` |

Destructive operations require the real field name, so an alias can never be the thing that
gets deleted. `genetrek-db remove hc_ndd` tells you to use `hc_ndd_v7` instead.

`/api/data?columns=hc_ndd` still does **not** work: that endpoint was published first and
keeps its exact-name contract. The **lookup** endpoints added later do resolve aliases, on
the condition this paragraph originally set — the response must echo which real column it
resolved to. `/api/fields/hc_ndd` answers with `"field": "hc_ndd_v7"` alongside
`"requested_as": "hc_ndd"`, and `/api/fields/hc_ndd/genes/tsv` heads its column
`hc_ndd_v7`, so downloaded data is never ambiguous about its own provenance. That is the
same rule `get` follows: its TSV header names the real column, never the alias.

`validate` rejects an alias that shadows a real field name, points at its own field, or is
claimed by two fields at once.

## Inspecting

```bash
uv run genetrek-db list                          # all fields
uv run genetrek-db list --sizes                  # in-memory bytes and % of the table
uv run genetrek-db list --on-disk                # compressed parquet size (needs pyarrow)
uv run genetrek-db list --visibility primary --type boolean
uv run genetrek-db show inheritance              # metadata plus type-appropriate stats
uv run genetrek-db get is_haploinsufficient --drop-nulls
uv run genetrek-db export all_genes.tsv --gzip
uv run genetrek-db history
```

`--json` works on every reporting command, before or after the subcommand.

## Validation

```bash
uv run genetrek-db validate      # exits non-zero on any error
uv run genetrek-validate-fields  # same checks, legacy entry point
```

Errors (these block a save):

- a parquet column with no metadata entry, or vice versa
- a declared `value_type` that no longer matches the parquet dtype
- an empty `fullname`
- the primary-key column missing, null anywhere, or not unique — whichever column
  `primary_key` marks (`ncbi_id` today, `hgnc_id` in a database built by `init`)
- `badges.yaml` naming a field that does not exist
- an `external_links.yaml` `{placeholder}` that is not a field

Warnings (fixable, harmless):

- metadata key order differing from column order — `save` normalises it
- fields with no declared `value_type`
- a `badges.yaml` entry the table cannot render (see below)

### The badge coupling

The table only renders badges for the columns in `genetrek.schema.BADGE_RENDERED_COLUMNS`
(currently `inheritance` and `eagle_score`). Adding a third top-level key to `badges.yaml`
has no visible effect until that column is added to the constant too — `validate` warns
about exactly this.

## Saving, backups, and recovery

`save()` in order:

1. normalises metadata key order to column order;
2. validates, and refuses on any error;
3. copies the current pair into `resources/processed_data/.backups/<YYYYMMDD-HHMMSS>/`,
   keeping the newest 5;
4. writes both files to temporary siblings, `fsync`s them, then swaps both into place;
5. appends one record per operation to `resources/processed_data/history.jsonl`.

**How far the atomicity goes.** POSIX cannot swap two files in one step. Each file is fully
written and flushed before anything is swapped, so neither destination is ever partially
written — but the two swaps are sequential, a two-syscall window. A marker file is written
before them and removed after, so a process killed in between leaves evidence: the next
`genetrek-db` run warns that the pair may disagree. Recover from `.backups/`:

```bash
ls resources/processed_data/.backups/
cp resources/processed_data/.backups/20260806-144136/genetrek.parquet \
   resources/processed_data/genetrek.parquet
cp resources/processed_data/.backups/20260806-144136/fields_description.yaml \
   resources/config/fields_description.yaml
uv run genetrek-db validate
```

Backups and the history log are generated, and are gitignored.

## Building a database from scratch: `init`

`genetrek-db init` builds a fresh database out of the live upstreams rather than out of a
spreadsheet: the HGNC complete set for the gene records, and GENCODE (with NCBI as a
fallback) for coordinates. The result is the **gene backbone only** — 22 columns, no
annotation columns — ready for `genetrek-db add`.

```bash
uv run genetrek-db init --dry-run                      # resolve and print the plan
uv run genetrek-db init --output build/resources_new   # build somewhere safe first
uv run genetrek-db init                                # replace the live database
```

No credential is needed; every source is public. An `NCBI_API_KEY` in the environment is
honoured if you have one, and only raises the eutils rate limit from 3 to 10 requests a
second — worth nothing at this scale.

### What it keeps, and what the names mean

`resources/config/hgnc_build.yaml` is the build spec: it maps each HGNC column to a
visibility, and **anything not listed is discarded**. Flags override it per column:

```bash
uv run genetrek-db init --primary hgnc_id --hidden location --drop mane_select
uv run genetrek-db init --secondary gene_group     # a column the config omits
```

An override may name the HGNC column (`location`) or the column it becomes
(`hgnc_location`); `--drop` removes one. Two flags claiming the same column is an error.

A column's prefix says **who authors the value**, not which file it arrived in. HGNC is the
authority for symbols and nomenclature, so those become `hgnc_*`. It merely
*cross-references* the identifiers below, which therefore keep source-accurate names —
calling them `hgnc_*` would credit HGNC for someone else's identifier:

| HGNC column | Built as | Authored by |
|---|---|---|
| `symbol`, `name`, `locus_group`, `locus_type`, `location`, `alias_symbol`, `prev_symbol` | `hgnc_*` | HGNC |
| `hgnc_id` | `hgnc_id` | HGNC (already prefixed) |
| `entrez_id` | `ncbi_id` | NCBI Gene |
| `ensembl_gene_id` | `ensg` | Ensembl |
| `refseq_accession` | `refseq_accession` | NCBI RefSeq |
| `omim_id` | `omim_id` | OMIM |
| `mane_select` | `mane_select` | MANE (NCBI + EMBL-EBI) |
| — | `grch38_chr/_start/_end`, `grch37_chr/_start/_end`, `strand` | GENCODE / NCBI |
| — | `grch38_source`, `grch37_source` | this tool |

`hgnc_id` is the **primary key**, stored canonically as `HGNC:5` (a string). It is the only
identifier in the complete set with neither nulls nor duplicates — `entrez_id` has 640
nulls, so it cannot be one. Every role is written into the metadata, so the webapp finds
these columns by meaning; see [Roles](#roles-how-the-code-finds-a-column).

### How coordinates are obtained

Two steps per assembly, in order, and no heuristics:

1. join the stored Ensembl ID against that assembly's GENCODE gene table;
2. for whatever is left and has an `ncbi_id`, take NCBI's `locationhist` bounds.

Nothing matches on symbol. Symbol matching was measured to be unsafe — `AAVS1` resolves
through Ensembl's xref lookup to `ENSG00000125503`, which is `PPP1R12C`, a *different*
gene — so a gene neither step resolves is left null and counted, never guessed at.

`grch38_source` / `grch37_source` record which step produced each coordinate, because the
two annotators call gene boundaries differently: SHANK3 on GRCh37 starts at 51,112,836 per
GENCODE and 51,111,251 per NCBI. That 1.6 kb is not reconcilable, only auditable.

Measured against HGNC 20260828 and GENCODE v50 (45,045 genes):

| `*_source` | GRCh38 | GRCh37 |
|---|---|---|
| `gencode` | 42,233 (93.8%) | 41,158 (91.4%) |
| `ncbi` | 2,107 | 2,946 |
| null | 705 | 941 |

Of the 705 with no GRCh38 locus, 653 do have an `ncbi_id` — NCBI simply has no coordinates
for them — and 50 carry neither identifier (they are OMIM phenotype and fragile-site
records, not genes). The build report prints this tally every time, so a release that
shifts the numbers is visible rather than absorbed.

### GRCh37 is a backmapping, not the historical annotation

`grch37_*` comes from GENCODE's `lift37` file: the *current* GRCh38 gene model backmapped
onto GRCh37 coordinates, **retaining the GRCh38 gene identifiers**. That is what makes one
join per assembly possible.

It matters because HGNC's `ensembl_gene_id` is the GRCh38 identifier, and 2,712 of those
IDs do not exist in the *native* Ensembl GRCh37.87 annotation — including real genes:
`ACACA` is `ENSG00000278540` in GRCh38 but `ENSG00000132142` in GRCh37. A join against the
native annotation would silently null those coordinates; lift37 resolves 1,620 of them
directly by ID.

The trade-off is that `grch37_*` answers *"where is this gene in GRCh37 coordinates?"* and
not *"what would a 2014 analysis have called this gene?"*. Where the native GRCh37.87
annotation also has the gene, bounds differ by a median 15 bp but a p95 of 32 kb, because
transcript models have been extended since. The field descriptions say so.

### Genes the two assemblies disagree about

16 of 45,045 genes end up on **different chromosomes** in the two assemblies. All 16 are
short multi-copy RNA genes and pseudogenes — miRNAs, 7SK/7SL and U6/U7 pseudogenes, 15 of
them under 1 kb — whose lift37 mapping landed on a near-identical paralogue. The sources
genuinely disagree, so the build reports them by name and keeps both coordinates rather
than reconciling them with a rule:

```
WARNING: 16 genes disagree on chromosome between assemblies
  MIR548BA, MIR1268B, MIR1302-7, MIR1827, MIR3118-3, BMS1P23, RN7SKP131, RN7SL73P, +8 more
```

A locus search in GRCh37 will return such a gene at its lift37 position. `grch37_source`
says where that came from.

### Writing to the live tree

The default target *is* the live tree, and a fresh build replaces a curated 67-field
database with a 22-column one. So that path always names the loss and always asks:

```
about to REPLACE the database at /path/to/resources
  current: 67 fields, 61,669 genes
  new:     22 fields, 45,045 genes (5 field name(s) in common)
  this discards 62 field(s), every annotation column included (a backup is taken first)
Replace the database in /path/to/resources? [y/N]
```

The backup is wider than a normal `save`: it copies `external_links.yaml` and `badges.yaml`
alongside the pair, because `init` rewrites those too. It has to — they reference columns
by name, so a tree still carrying links to `{symbol}` would fail validation against a
schema whose symbol column is `hgnc_symbol`. `init` regenerates `external_links.yaml` from
the resolved roles and writes an empty `badges.yaml` (a fresh build has no annotation
columns to render as badges), and reports both. `token_colors.yaml`,
`categories.yaml`, `site_description.md` and every prose page in `content/` are copied
from the source tree, so an `--output` tree runs as-is. The prose pages are copied
per file rather than as a directory: a tree that already has a customised
`documentation.md` but no `changelog.md` still needs `changelog.md`.

Prefer building elsewhere and looking at it first:

```bash
uv run genetrek-db init --output /tmp/gt-new
GENETREK_RESOURCES=/tmp/gt-new uv run genetrek-db validate
GENETREK_RESOURCES=/tmp/gt-new uv run genetrek --port 8130
```

### Caching and reproducibility

Downloads land in `resources/original_file/` under release-stamped names:
`hgnc_complete_set_<YYYYMMDD>.txt`, plus `gencode_v<release>_grch3{8,7}.parquet` — the
*derived* gene tables (~1.1 MB each), not the ~150 MB GTFs, which have no other use here. A
second build reuses them; `--refresh` re-downloads. The HGNC file is also what
`paths.latest_hgnc_file()` picks up for alias resolution, so a build incidentally improves
identifier matching.

`release: latest` drifts by design, so the resolved release is recorded in every field's
`update` and `source` — a build is reproducible after the fact:

```bash
uv run genetrek-db init --release 2025-04-01 --gencode-release 44
```

HGNC keeps monthly files for a year and quarterly ones indefinitely; a pinned date is
looked up in both.

### What `init` deliberately does not do

It does not migrate the existing 67-field database into the new schema. Carrying the
annotation columns across would be a separate `migrate` command keyed on `hgnc_id`.
Until then, re-add them with `genetrek-db add` — which is why `hgnc_id`, `ncbi_id`, `ensg`
and `hgnc_symbol` are all present and role-tagged.

## Rebuilding from scratch

`genetrek-db init` above is the supported way to build a database from nothing.

The notebooks named below, and the raw files under `resources/original_file/` they read, are
kept privately for this release and are not in the public repository. They will be published
with the next data release; until then, the reasoning they record is summarised here.

**`notebooks/create_20260903.ipynb` is the worked example of doing it end to end**: it builds
the backbone with `genetrek.db.build`, then adds ~30 annotation columns from an annotated HGNC
complete set, and ends with a database the webapp serves with every feature working. Read it
before starting a new database — the reasoning cells record which of the source's 149 columns
were rejected and why, which is the part that does not survive being rediscovered.

It writes to `resources/processed_data/staging/resources_<date>/`, a complete resources tree
under the gitignored staging directory, so it can be reviewed with `GENETREK_RESOURCES=…` and
served by the webapp without touching the live pair. The dated suffix means successive
attempts do not overwrite each other; `create_dataset.ipynb` used a bare
`staging/resources/`, which is the same idea without the date.

`notebooks/create_dataset.ipynb` predates both: it rebuilds the parquet from the raw TSV in
`resources/original_file/`. It is documentation of how the database was originally created,
**not** the way to add a field, and not the way to start a new one either.

It writes to `resources/processed_data/staging/` and never touches the live pair;
promotion is a deliberate step documented in the notebook's last markdown cell. Its
column-mapping cell merges instead of regenerating, and metadata reconciliation goes
through `sync_fields_description()`, which cannot blank a curated value.

`sync_fields_description()` is also the right call after any out-of-band change to the
parquet: it adds a stub for each new column, reports entries with no column, and normalises
order.

```bash
uv run genetrek-db sync            # report and write
uv run genetrek-db sync --prune    # also delete orphaned entries
```
