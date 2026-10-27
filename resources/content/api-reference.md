## The API

Everything the table shows is also available over HTTP, read-only and without a
key. Responses are JSON unless stated otherwise. The generated reference, with
every parameter and an in-page console, is at [`/docs`](/docs).

A gene is addressed by **any identifier it carries** — an approved symbol, an
`HGNC:` id, an Ensembl gene id, an NCBI gene id or an OMIM id — and GeneTrek works
out which is which. Withdrawn and alternative symbols resolve too: `KIAA1650`
answers as `SHANK3`.

Columns are addressed by name, and a versioned gene list can be addressed by its
stable short name: `hc_ndd` reaches `hc_ndd_v7`. The response always names the
real column it used, so a file you download is never ambiguous about which
version it holds.

### Listing the columns

```
GET /api/fields
GET /api/fields?value_type=boolean&category=Gene%20lists
GET /api/fields?include_counts=true
```

Columns come back in the order the table shows them. Some columns are not shown
in the table by default; add `include_hidden=true` to list those as well. They
are always retrievable by name whether or not they are listed.

`include_counts=true` adds `n_genes` — for a gene list, how many genes are on it.

One column's metadata and coverage, including how many genes have a value and the
range of those values:

```
GET /api/fields/loeuf
GET /api/fields/hc_ndd
```

**Licences.** Each column's metadata carries `licence`: the terms its values are
redistributed under, an SPDX id where one exists (`CC0-1.0`, `CC-BY-4.0`). The
sources' terms differ, so no single licence covers the database; reuse a column on
its own terms and cite its source. `DATA_LICENCES.md` in the source repository lists
every column.

**Measured, not measured.** A column computed from a measurement (an expression
level, a constraint score, a call made from either) only covers the genes its
source measured. For every other gene the value is `null`, which means *not
measured*: it is neither `false` nor `0`. The metadata's `universe` says which genes
were measured, in words, and `coverage.n_measured` says how many.

For a yes/no column, `n_true + n_false == n_measured`.

### Getting a gene list, or any column

```
GET /api/fields/hc_ndd/genes?drop_empty=true
GET /api/fields/hc_ndd/genes/tsv?drop_empty=true
```

`drop_empty=true` omits genes with no value. For a yes/no column that also omits
the genes where the answer is no — so what you get back is the list's membership,
which is usually the point. Without it you get all 45,045 genes and their values.

Add `/tsv` for a two-column tab-separated file instead of JSON. A gene that was not
measured is written `NA` there (and `null` in JSON), never left blank, so it
cannot be read as "no".

Genes are named by their symbol by default. Ask for a different identifier with
`id_type`, one of `symbol`, `hgnc`, `ensg`, `ncbi` or `omim`:

```
GET /api/fields/hc_ndd/genes?drop_empty=true&id_type=ensg
```

Genes that have no value for the identifier you chose cannot be named, so they are
not returned; the count of them is reported as `n_without_identifier`.

Use `limit` and `offset` to page through a large column.

### Getting the universe of a list

```
GET /api/fields/gtex_v11_brain_enriched/universe
GET /api/fields/gtex_v11_brain_enriched/universe/tsv?id_type=ensg
```

The genes the column measured: every gene with a value, whether that value is
`true`, `false` or `0`. The response gives the `universe` text, `total`, and
`n_not_measured`.

If you test a GeneTrek list for enrichment anywhere else, use this as the
background. A gene the source never measured cannot be counted as "not on the list".

### Getting one gene

```
GET /api/genes/SHANK3
GET /api/genes/HGNC:14294
GET /api/genes/85358
GET /api/genes/SHANK3?columns=loeuf,inheritance,eagle_score
```

Without `columns` you get every column the table can show. Naming columns
explicitly also reaches the ones the table does not show, which is how measures
like `moeuf` and `soeuf` are retrieved. The [Column reference](/columns) marks
those columns "API only".

Which gene lists a gene is on:

```
GET /api/genes/SHANK3/genesets
```

Every yes/no column that is true for that gene, each with its category. Two things
to expect. Versions of a list coexist, so a gene on the current high-confidence
NDD list is usually on the previous ones too — the entry carrying an alias is the
current one. And some columns are roll-ups of others; those report what they were
combined from in `derived_from`.

### Converting identifiers

```
GET /api/convert?ids=SHANK3,KIAA1650,ENSG00000251322,85358
GET /api/convert?ids=SHANK3&to=hgnc,ensg
GET /api/convert?ids=1,2,3&from=ncbi
```

Up to 1000 identifiers per call, separated by commas, spaces or newlines. For more
than that, download the whole table from
[`/api/download/database`](/api/download/database?file_format=tsv) and join
locally.

Every input comes back in the order you sent it, with `matched_by` saying how it
was recognised, so nothing is silently dropped:

- `symbol`, `hgnc_id`, `ensg`, `ncbi_id`, `omim_id` — matched directly
- `alias_symbol`, `prev_symbol` — an alternative or withdrawn symbol; `note` names
  the current one
- `unmatched` — not in GeneTrek
- `ambiguous` — the identifier names more than one gene, so no answer is given
  rather than an arbitrary one

Bare numbers are assumed to be NCBI gene ids, since they could equally be HGNC or
OMIM ids; pass `from=hgnc` or `from=omim` to be certain. Anything worth knowing
about a result appears in `warnings`.

**Keeping a list out of the logs.** With `GET` the identifiers are part of the URL,
and URLs are written to the server's and the proxy's access logs. `POST
/api/convert` takes the same request in the body, which is not logged, and accepts
up to 50,000 identifiers:

```
POST /api/convert
{"ids": ["SHANK3", "KIAA1650", "85358"], "from": "auto", "to": ["hgnc", "ensg"]}
```

The response is identical to the `GET` form's.

### Comparing gene sets

```
POST /api/compare
GET  /api/backgrounds
```

`POST /api/compare` runs the comparison the **Compare gene sets** tab runs, with
the same statistics, so anything done in the browser can be reproduced from a
script. Every *Download tables* bundle from that tab includes `request.json`, the
exact body that regenerates it:

```
curl -X POST "$GENETREK/api/compare" \
     -H "Content-Type: application/json" \
     -d @request.json
```

where `$GENETREK` is this site's address.

**The request.** Between 2 and 12 `sets`, each one of:

- `field`: a gene list in the database, by name or short name (`"hc_ndd"`);
- `genes`: identifiers of any kind, resolved as `/api/convert` does. `id_type`
  (default `auto`) pins how they are read. Up to 50,000 per set;
- `lists_file`: a file exported from **My lists**, pasted in as JSON;
- `share`: a My lists share token, the part of a share link after `#lists=`.

For `lists_file` and `share`, `list_index` (default 0) picks which list of
several. Each set can take a `label`, used in the response and the methods text.
Then:

- `background`: the universe of genes, by name from `GET /api/backgrounds`.
  Omitted means the default, protein-coding genes;
- `adjust_for`: the covariate to stratify by, from `GET /api/backgrounds`.
  Omitted means every configured covariate (LOEUF decile); `[]` means crude
  results only. One covariate at a time;
- `include_genes`: also list the genes in each region, not just count them.

Two database lists, adjusted for constraint by default:

```
{"sets": [{"field": "hc_ndd"}, {"field": "syngo_v1_3"}]}
```

A list of your own against one, crude only, within all genes. Identifiers can be
mixed: `KIAA1650` is a former symbol of SHANK3, and `ENSG00000171862` is PTEN:

```
{
  "sets": [
    {"label": "Candidates", "genes": ["KIAA1650", "MECP2", "CHD8", "ENSG00000171862", "SCN2A"]},
    {"field": "sfari_1_20260322"}
  ],
  "background": "All genes",
  "adjust_for": []
}
```

**The response:**

- `release` and `key`: the database release and the key column genes are named
  by;
- `background`: the name, definition and `size` of the background. It also gives
  `effective_size`, the N actually tested: the background less every gene that some
  set did not measure. `restricted_by` names the sets responsible (see
  [Comparing gene sets](/documentation)). When every set covers the whole
  background, the two sizes are equal;
- `sets`: what each set resolved to:
  - every set gives `n_genes`, then `n_in_background` (left in the analysed
    universe), `n_outside_background`, and `n_not_measured` (inside the background
    but not measured by some set);
  - pasted identifiers also report `unmatched` and `ambiguous`;
  - a list file or link reports `n_not_in_database`: genes withdrawn since the list
    was made;
- `pairs`, one per pair of sets:
  - `overlap`, `expected_overlap` and `fold_enrichment`;
  - `odds_ratio` with `ci95`: the conditional maximum-likelihood estimate and its
    exact 95% interval, as R's `fisher.test` reports;
  - `p_value` from the two-sided Fisher's exact test, and `fdr` from
    Benjamini–Hochberg across all pairs;
  - `jaccard` and `overlap_coefficient`;
  - `overlap_by_construction`, which names the reason when the overlap is built
    in;
  - `adjusted`, when a covariate applies: the Mantel–Haenszel `odds_ratio` and
    `ci95` (Robins–Breslow–Greenland), and the Cochran–Mantel–Haenszel `p_value`
    with its own `fdr`. See [Comparing gene sets](/documentation) for what
    these mean;
- `regions`: every non-empty exclusive region (genes in exactly those sets), with
  its `size`, and its `genes` if asked for;
- `methods`: the methods paragraph the browser's downloads carry. It names any
  coverage restriction and the resulting N;
- `warnings`: anything to read before quoting the numbers, for instance that
  coverage removed more than a fifth of the background.

JSON has no infinity: an odds ratio or interval end that is infinite, because a
cell of the table is zero, is the string `"Inf"`. A value that is undefined is
`null`. A `p_value` of 0 means below about 1e-300.

**Errors.** A request that cannot be answered as asked returns 422 with the
reason: fewer than 2 or more than 12 sets, a set naming more than one source, an
unknown field or background, a column that is not a gene list, a damaged link or
file, or a list made for a database keyed differently.

**Your lists are not kept.** The API stores nothing. A list sent in the body of
`POST /api/compare` or `POST /api/convert` is used to compute the response and
then discarded. It is not written to disk or logged; access logs record the URL,
not the body. This is the same promise **My lists** makes in the browser. See
[My lists & your data](/my-lists).

`GET /api/backgrounds` lists the backgrounds, with their definitions and sizes,
and the covariates, with how their strata are built.

### Downloading everything

```
GET /api/download/database?file_format=tsv
GET /api/download/database?file_format=parquet
```

The whole table, every column, serialised on request.
