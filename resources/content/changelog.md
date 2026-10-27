## Changelog

What changed in the database, newest first. Each entry gives the date the change
was published, the columns added, retired or refreshed, and — where a gene list
gained a version — how it differs from the one before.

Versions of a list are kept side by side rather than overwritten, so an analysis
can be pinned to the version it was run against. The short name always points at
the current version: `hc_ndd` is whichever version is current today. Where an
older version is still in the table it is named below and appears in the
comparison table at the foot of this page; where it was retired, the entry says
so, and the only remaining copy is the published release it shipped in.

### 2026-10-27 — GeneTrek 2, with HC-NDD v7

**Archived.** The code is release
[v2.0.0](https://github.com/bourgeron-lab/genetrek/releases/tag/v2.0.0) of
<https://github.com/bourgeron-lab/genetrek>, archived on Zenodo as
[doi:10.5281/zenodo.23693065](https://doi.org/10.5281/zenodo.23693065). The database is deposited on
Zenodo, with every column's licence, as
[doi:10.5281/zenodo.23692280](https://doi.org/10.5281/zenodo.23692280).

The first public release of GeneTrek 2. It brings the features of the entries
below — gene-set comparison, your own lists, an [API](/api-reference) and
versioned gene lists — and a database of 87 columns over 45,083 genes, built
around High-confidence NDD v7 ([documentation](/documentation)).

**Added.**

- **Three trait lists**, whose union is `hc_ndd_v7`, as defined in
  [Korkmaz et al. 2026](https://doi.org/10.1016/j.biopsych.2026.10.003):
  - `autism_genes_v7` (421 genes): SFARI Gene score 1, SPARK and the PanelApp
    Australia Autism panel. This is the former `autism_combined_v7`, renamed.
  - `idd_genes_v7` (2,100 genes): SysNDD *Definitive* and the PanelApp Australia
    and Genomics England intellectual disability panels.
  - `epilepsy_genes_v7` (917 genes): the PanelApp Australia Genetic epilepsy and
    Progressive myoclonic epilepsy panels and the Genomics England Early onset or
    syndromic epilepsy panel.

  Each has a short name: `autism_genes`, `idd_genes`, `epilepsy_genes`.
  `hc_ndd_v7` is now stored as the union of the three, so HC-NDD and its trait
  lists cannot drift apart.
- **`adhd_demontis2023`**: 20 ADHD genes from Demontis et al. 2023. They are not
  part of HC-NDD v7.

**Retired.**

- **The Genomics England PanelApp columns**: `panelapp_eng_epilepsy_20260331`,
  `panelapp_eng_id_20260331` and their union `panelapp_eng_all_20260331`. Their
  terms of use do not clearly allow bulk redistribution. Their genes are still
  part of HC-NDD v7, and of the IDD and epilepsy lists.
- **`panelapp_aus_all_20260331`**, the union of the PanelApp Australia NDD
  panels. Each panel is still a column of its own.

**Renamed.**

- `inheritance` → **`inheritance_v7`**, so the mode of inheritance is versioned
  with the list it describes. The short name `inheritance` still works. Values
  are unchanged.
- `spark_20250601` → **`spark_20250617`**, the date SPARK gives for its gene
  list. The short name `spark` still works.
- `autism_combined_v7` → **`autism_genes_v7`** (above). The old name has no short
  name and no longer works.

**Changed.**

- **Column groups.** The *Gene lists* and *PanelApp* groups are replaced by:
  - **NDD gene lists**, with one subgroup per trait (autism, epilepsy, IDD),
    each led by GeneTrek's list for that trait;
  - **Other gene lists**, holding the cancer and clinical lists.
- **Candidate lists.** The candidate list was not updated for v6 or v7.
  `candidate_ndd_v1` to `candidate_ndd_v5` are no longer shown by default, and
  each description gives how many genes it shares with HC-NDD v7.
- **`hc_ndd_v7`** is dated 2026-07-10.
- **HGNC fields with several values** no longer carry literal quotes: `"A|B"` is
  now `A|B` in `hgnc_alias_symbol`, `hgnc_prev_symbol`, `refseq_accession`,
  `omim_id` and `mane_select` (32,952 values, every `mane_select` among them).
- **Documentation.** The [documentation](/documentation) now describes how
  HC-NDD v7, the candidate lists and the mode of inheritance are built, and the
  [How to cite](/cite) page what to cite for each.

### 2026-10-06 — brain expression, and "not measured" kept apart from "no"

**Added — ten columns in a new *Expression* category.** Every number below counts
the genes measured in this release.

- **Brain proteome.**
  - `jiang2020_brain_ts`: the published tissue-specificity score in brain, from
    the GTEx proteome (Jiang et al. 2020).
  - `jiang2020_brain_enriched`: the paper's call that a protein is brain-enriched
    (1,769 of the 8,911 genes quantified in brain).
- **Brain development.**
  - `brainspan_prenatal_log2fc`: a region-balanced prenatal/postnatal log2 fold
    change from BrainSpan.
  - `brainspan_prenatal_biased`: log2FC ≥ 1 and FDR < 0.05, tested over donors
    (2,254 of 37,197 genes).
- **Adult tissues, GTEx v11.** Brain is 10 regions, one per sample-duplicate pair.
  The cerebellum is included; the spinal cord is not.
  - `gtex_v11_brain_max_tpm` and `gtex_v11_brain_expressed`: the brain level
    (16,793 brain-expressed genes).
  - `gtex_v11_brain_vs_rest_log2` and `gtex_v11_tau`: brain specificity, with brain
    one tissue among about 30.
  - `gtex_v11_brain_enriched`: ≥ 4-fold above every other tissue (716 genes).
  - `gtex_v11_cerebellum_enriched`: the 159 of those that are ≥ 4-fold above the
    other brain regions as well.

How each was computed is in `notebooks/brain_data/` and in each column's
description.

**Changed — a gene that was not measured is no longer counted as "no".**

- In the table, a list's "not measured" shows as a grey "—", distinct from a
  blank "no".
- **Compare gene sets** now tests every pair within the genes that every compared
  set measured. The Statistics panel's enrichment tests a list within the genes
  that list measured. Before, an unmeasured gene counted as "not on the list".
- This changes the results for the existing partly-measured lists
  (`is_shet_constrained`, `is_loeuf_gnomad_v4_1_1`, `constraint`,
  `haploinsufficient`, `triplosensitive`). Each measured only 16,400 to 18,000 genes, so
  comparisons involving them now run on that smaller universe, and the page says
  so.
- The API gives each column's `universe` and `n_measured`. It adds
  `/api/fields/<column>/universe` and writes `NA` for "not measured" in TSV
  downloads.

### 2026-10-06 — comparison statistics revised, and a comparison API

No data changed. These changes revise how the **Compare gene sets** tab and the
Statistics panel compute their results. Most numbers move slightly; adjusted
results are new.

**Changed.** Odds ratios are now the *conditional maximum-likelihood* estimate
with its *exact* 95% confidence interval, as R's `fisher.test` reports. They
replace the sample odds ratio with a Woolf interval. The interval now always
agrees with the Fisher p-value about significance, and it still gives a bound
when a cell is zero. Expect odds ratios and intervals to differ slightly from
those computed before.

**Added.**

- **Adjustment for constraint.** Every pair is also tested within LOEUF deciles
  (Cochran–Mantel–Haenszel), shown beside the crude result. Lists rich in
  constrained genes overlap partly for that reason alone. For High-confidence NDD
  v7 against SynGO, the odds ratio goes from 2.8 to 1.7.
- **A signed heatmap.** The heatmap's FDR measure is now signed: positive for
  more shared genes than expected, negative for fewer, on a diverging scale.
- **Alt text** for every figure export.
- **`POST /api/compare`**, which reproduces any comparison from a script.
  Downloads include the `request.json` that does so. `GET /api/backgrounds`
  lists the available backgrounds and covariates. `POST /api/convert` is new,
  and keeps identifier lists out of server logs.

See [Comparing gene sets](/documentation) and [the API reference](/api-reference).

### 2026-10-06 — My lists and gene-set comparison

No data changed: the database is still the 2026-09-24 release. This entry
records new features of the site.

**My lists.** You can now keep gene sets: the current search results, pasted
genes, or an imported file. A saved list works like any gene list in the
database: as a search filter, as a table column, and in enrichment. Lists are
stored only in your browser, never on the server. You can export them as a file
or share them as a link that carries the genes itself. See
[My lists & your data](/my-lists).

**Compare gene sets.** A new tab next to the table compares 2 to 12 sets: the
current results, database gene lists and your own lists. It shows Venn or UpSet
diagrams, a pairwise heatmap and a statistics table: Fisher's exact test,
Benjamini–Hochberg FDR, odds ratio with 95% CI, fold enrichment, Jaccard index
and overlap coefficient. Click any region to list, save or download its genes.
Figures download in publication format (SVG, or PNG at 300 or 600 dpi, at
journal column widths). Tables download as TSV or XLSX, with a generated methods
paragraph. See [Comparing gene sets](/documentation).

**Changed.** Enrichment in the Statistics panel is now computed within an
explicit background, protein-coding genes by default. Before this, its label
said "protein-coding" while the test counted every gene in the database, which
overstated enrichment for lists made mostly of protein-coding genes. Results from
before this date are therefore not directly comparable. To reproduce them, choose
the *All genes* background.

### 2026-09-24 — restored list versions, reviewed metadata

Columns went from 71 to 78, and genes from 45,045 to 45,083.

**Backbone.** Rebuilt on the HGNC complete set of 2026-09-18, with coordinates
from GENCODE v50 as before. 39 genes were added and 1 withdrawn; 6 symbols
changed. The release this database was previously built on, 2026-08-28, is no
longer served from HGNC's archive, so it could not be reused.

**Added.** The six older versions of the lab's two list families that the
rebuild retired — `hc_ndd_v1`, `hc_ndd_v2` and `candidate_ndd_v1` through
`candidate_ndd_v4` — recovered from the 2024 release. They are queryable again
and appear in the comparison table at the foot of this page. Also
`haploinsufficient` (pHaplo >= 0.86) and `triplosensitive` (pTriplo >= 0.94),
the pair of dosage-sensitivity calls the 2024 release carried and the rebuild
dropped. Both keep their nulls: they were scored on 16,781 genes and are blank
on the rest, which is not the same as being called negative.

**Retired.** `constraint_combined`, the undated of the two combined-constraint
calls. No pLI scores survive in the current sources, so the criteria behind it
can no longer be reconstructed. Use `constraint`, the dated call that takes the
union of s-het and LOEUF.

**Changed.** Every column's description, source, DOI and release date was
reviewed and rewritten; no values changed. The lab's own lists now form their
own group, ahead of everyone else's, in both the table and the column selector.

### 2026-09-04 — rebuilt on a new backbone

The whole table was rebuilt rather than amended, so both its gene set and its
columns changed.

**Backbone.** HGNC complete set of 2026-08-28, with coordinates from GENCODE v50
and NCBI as a fallback, in GRCh38 and GRCh37. Genes went from 61,669 to 45,045:
the previous backbone carried entries the HGNC complete set no longer lists.
Columns went from 67 to 71, with 57 new and 53 retired — a near-total turnover,
because the rebuild also renamed columns to say who authors each value
(`hgnc_*` only for what HGNC curates).

**Added.** 46 annotation columns, from `GeneListForGeneTrek_20260904`, plus 5
derived roll-ups (`chrom_epi_tf_combined`, `panelapp_aus_all`,
`panelapp_eng_all`, `autism_combined_v7`, `cancer_combined_ncg_7_2`) which are
stored as a rule over their sources rather than as imported data. 17 gene lists
gained a stable short name, so `hc_ndd` reaches the current version without
naming its suffix.

**Retired.** `hc_ndd_v1`, `hc_ndd_v2`, `candidate_ndd_v1` through
`candidate_ndd_v4`, and the unversioned `sysndd_candidate`,
`sysndd_definitive` and `inheritance_sysndd`. The six list versions were
restored on 2026-09-24, above; the three unversioned columns were not, so for
those the only remaining copy is the release they shipped in.

**Still available.** `hc_ndd_v3`, `hc_ndd_v6` and `hc_ndd_v7` all remain, so v3
and v6 can still be queried and compared. `hc_ndd` points at `hc_ndd_v7`.

### Comparing the versions that are still here

The table below is computed from the published data every time this page is
loaded, so it cannot drift from what the database actually contains. One row per
older version that is still in the table, measured against the current version
of the same list.

*Jaccard* is the overlap: shared genes divided by the number of genes in either
version. 1.000 means the two versions are identical.
