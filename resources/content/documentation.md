## GeneTrek documentation

How the database is built, what each kind of column means, and how the curated
gene lists are decided.

Every column, with its description, source and coverage, is listed in the
[Column reference](/columns). For the HTTP interface see [API
reference](/api-reference); for what changed and when, [Changelog](/changelog);
for references, [How to cite](/cite).

### What is in the table

One row per gene, keyed on its HGNC id. The backbone (identifiers, gene type and
coordinates in both GRCh38 and GRCh37) comes from the HGNC complete set with
coordinates from GENCODE, falling back to NCBI where GENCODE has none.
Everything else is an annotation column layered on top of that backbone.

Columns are grouped into categories, and every column carries its own
provenance: click a column header's information icon to see its description,
source, release date and DOI, and whether it is derived from other columns. To
review them all at once (including the few only the API exposes) see the [Column
reference](/columns).

Every source was matched to the backbone by a stable identifier (HGNC id,
Ensembl or NCBI gene id), never by gene symbol: 175 genes changed symbol between
two recent HGNC releases alone.

**An empty cell means "not measured", not "no".** A column computed from a
measurement (a constraint score, an expression level) is blank for the genes its
source did not measure. The table shows those as a grey "—", downloads write
`NA`, and statistics leave them out rather than counting them as negatives. A
gene list is different: a gene that is not on the list is simply not on it.

### Versions of the curated lists

The lab's two lists, high-confidence NDD genes (`hc_ndd`) and candidate NDD
genes (`candidate_ndd`), are versioned. Versions coexist rather than replace one
another, so an analysis can be pinned to the version it was run against: cite
`hc_ndd_v7`, not `hc_ndd`. The short name `hc_ndd` always points at the current
version; the [changelog](/changelog) records how each version differs from the
one before it.

The current version is **v7**, dated 2026-07-10. Versions v1, v2, v3 and v6 are
still in the table for comparison.

Until v6, the two lists were numbered independently, so a candidate list could
change without HC-NDD changing. From v6 they share one number: HC-NDD went from
v3 straight to v6, which is why there is no `hc_ndd_v4` or `hc_ndd_v5`. No
candidate list has been released with that shared number yet. Each candidate
list was built against the HC-NDD version on the same row below, and shares no
gene with it:

| HC-NDD | Candidate built against it |
| --- | --- |
| v1 | v1 |
| v2 | v2, v3, v4 |
| v3 | v5 |
| v6 | — |
| v7 | — (not updated; see [Candidate NDD genes](#candidate-ndd-genes-candidate_ndd)) |

### High-confidence NDD genes (`hc_ndd`)

High-confidence neurodevelopmental disorder (NDD) genes are genes whose
association with neurodevelopmental conditions is robust, i.e. supported by
strong, replicated genetic evidence. This robustness also reflects the nature of
the variants involved: pathogenic variants in these genes have large effects and
high penetrance, so that a single pathogenic genotype (one heterozygous variant
for a dominant gene, two variants, one on each allele, for a recessive one) is
generally sufficient to explain the NDD in its carrier.

Several international efforts started compiling such gene lists for epilepsy,
autism and intellectual disability, and keep expanding, updating and curating
them. GeneTrek integrates five of them: PanelApp Australia, Genomics England
PanelApp, SysNDD, SFARI Gene and SPARK. In each, panels of experts curate the
lists. Each resource applies its own evaluation criteria and evidence tiers, but
these criteria broadly converge, so their high-confidence genes largely overlap.

The overlap is not complete, and this is not only a matter of curation
thresholds: the resources do not all target the same phenotype. SFARI Gene and
SPARK focus on autism. SysNDD, which grew out of SysID, was initially centred on
intellectual disability before broadening to NDD in general. PanelApp (Australia
and England) is designed for diagnostic use in clinical genetics, and so gives
more weight to syndromic presentations and to genes that are actionable in a
diagnostic setting. These differences in scope explain part of the disagreement
between lists, and are why GeneTrek combines them.

#### How the list is built

**HC-NDD is the union of the high-evidence gene sets of five curated resources.
For each resource, only the highest-confidence tier is kept.** A gene is
high-confidence as soon as it meets at least one of these criteria:

- **SysNDD** v0.11.2, 2026-03-23: genes whose association with NDD is labelled
  *Definitive* ([curation
  criteria](https://berntpopp.github.io/sysndd/05-curation-criteria.html)).
- **PanelApp Australia**, 2026-03-31: green genes of five NDD panels
  ([guidelines](https://panelapp-aus.org/#!Guidelines)):
  - [Autism](https://panelapp-aus.org/panels/51/) v0.248
  - [Intellectual disability syndromic and
    non-syndromic](https://panelapp-aus.org/panels/250/) v1.731
  - [Genetic Epilepsy](https://panelapp-aus.org/panels/202/) v1.4
  - [Progressive Myoclonic Epilepsy](https://panelapp-aus.org/panels/331/) v0.28
  - [Speech Apraxia](https://panelapp-aus.org/panels/4290/) v1.28
- **Genomics England PanelApp**, 2026-03-31: green genes of two NDD panels
  ([guidelines](https://panelapp.genomicsengland.co.uk/#!Guidelines)):
  - Early onset or syndromic epilepsy v8.170
  - Intellectual disability v9.355
- **SFARI Gene**, release of 2026-03-22: genes with a gene score of 1, "high
  confidence" ([gene scoring](https://gene.sfari.org/about-gene-scoring/)).
- **SPARK**, version of 17 June 2025 ([gene
  list](https://sparkforautism.org/portal/page/spark-gene-list/)): the whole
  SPARK gene list. Every gene on it has already been expert-curated as having a
  strong, clinically relevant association with autism or NDD, and is used to
  return genetic results to participants.

Each source is a column of its own, so why a gene is on the list can be read in
the table. In the column selector, the *NDD gene lists* category has one group
per trait (autism, epilepsy, IDD), each starting with GeneTrek's list for that
trait and followed by its sources. The exception is Genomics England (below).

| Source | Genes on the source list | Of which, on no other source |
| --- | ---: | ---: |
| SysNDD Definitive | 1,815 | 196 |
| PanelApp Australia (5 panels) | 1,898 | 170 |
| Genomics England (2 panels) | 1,589 | 30 (24 recessive, 6 dominant) |
| SFARI Gene score 1 | 244 | 39 |
| SPARK | 306 | 0 |
| **HC-NDD v7 (union)** | **2,200** | |

**Genomics England PanelApp.** Its terms of use do not clearly allow its panels
to be redistributed in bulk, so this release does not include the two Genomics
England panels as columns, although their genes are part of HC-NDD v7. As a
result, the 30 genes that only Genomics England contributes appear in the IDD or
epilepsy list, but in none of their source columns.

#### Autism, IDD and epilepsy gene lists

HC-NDD v7 is the union of three gene lists, one per trait, as defined in
[Korkmaz et al. 2026](https://doi.org/10.1016/j.biopsych.2026.10.003) (Figure
3). Each is a column of its own:

| List | Column | Sources | Genes |
| --- | --- | --- | ---: |
| Autism | `autism_genes_v7` | SFARI Gene score 1, SPARK, PanelApp Australia Autism | 421 |
| Intellectual developmental disorder (IDD) | `idd_genes_v7` | SysNDD *Definitive*, PanelApp Australia and Genomics England intellectual disability | 2,100 |
| Epilepsy | `epilepsy_genes_v7` | PanelApp Australia Genetic epilepsy and Progressive myoclonic epilepsy, Genomics England Early onset or syndromic epilepsy | 917 |
| **HC-NDD v7 (union)** | `hc_ndd_v7` | | **2,200** |

The three lists overlap: 211 genes are on all three, 653 on the IDD and epilepsy
lists only, 163 on the autism and IDD lists only, and none on the autism and
epilepsy lists alone. 1,073 genes are on the IDD list only, 53 on the epilepsy
list only and 47 on the autism list only. Comparing the three lists in the
*Compare gene sets* tab, with the *All genes* background, draws this Venn
diagram; the default protein-coding background leaves out the few non-coding
genes, such as the mitochondrial tRNA genes. Every gene of the
PanelApp Australia Speech apraxia panel is already on one of the three lists.

The review also discusses ADHD. Its 20 ADHD genes, from Demontis et al. 2023,
are in `adhd_demontis2023`, but are not part of HC-NDD v7: the evidence is still
preliminary, and they may join HC-NDD in a later version.

#### Updates

Because all these resources are actively curated, the list is regenerated at
each GeneTrek update, and each regeneration is a new version. v7 uses the same
sources and the same tiers as v6. It takes newer releases of all of them: SysNDD
*Definitive*, SFARI Gene score 1, SPARK and every PanelApp panel. The list went
from 2,064 to 2,200 genes: 139 added and 3 removed. The [changelog](/changelog)
gives the overlap between all versions.

### Candidate NDD genes (`candidate_ndd`)

**The candidate list was not updated for v6 or v7, and will be updated soon.**
The current version, `candidate_ndd_v5`, was built against HC-NDD v3, with which
it shares no gene. It therefore shares 385 genes with HC-NDD v7, whereas a
candidate list should by definition contain no high-confidence gene. Older
versions (v1–v4) are kept for comparison. None of the candidate lists is shown
by default; add them from the column selector.

### Mode of inheritance

The mode of inheritance (`inheritance`, currently `inheritance_v7`) is given for
the genes of HC-NDD, and is versioned with it: `inheritance_v7` describes the
genes of `hc_ndd_v7`. It is derived from two resources only, **PanelApp
Australia** and **SysNDD**. Their own calls are in two separate columns,
`inheritance_panelapp_20260617` and `inheritance_sysndd_definitive_v0_11_2`. The
consolidated `inheritance_v7` is built from them as described below.

The two PanelApp Australia snapshots in v7 are from different dates. The panels
that define HC-NDD are from 2026-03-31, while the modes of inheritance were read
on 2026-06-17. Version 8 will take both from a single date.

#### Extraction rules

- **SysNDD** — the mode of inheritance is read at the level of each
  gene–inheritance–disease entity, keeping only *Definitive* entities associated
  with NDD. Modes reported for the gene in other conditions are not taken into
  account.
- **PanelApp Australia** — the mode of inheritance is taken from the gene's
  entry in the NDD panels used to build HC-NDD (green genes only; see [How the
  list is built](#how-the-list-is-built)).

PanelApp's labels are mapped to GeneTrek's categories as follows:

| PanelApp Australia label | GeneTrek category |
| --- | --- |
| MONOALLELIC, autosomal or pseudoautosomal, … | Autosomal Dominant |
| BIALLELIC, autosomal or pseudoautosomal | Autosomal Recessive |
| BOTH monoallelic and biallelic, autosomal or pseudoautosomal | Autosomal Dominant |
| MONOALLELIC, … imprinted (paternally / maternally) | Autosomal Dominant |
| X-LINKED: hemizygous mutation in males, biallelic mutations in females | X-linked Recessive |
| X-LINKED: hemizygous mutation in males, monoallelic mutations in females may cause disease | X-linked Dominant |
| MITOCHONDRIAL | Mitochondrial |
| Other | *no label* |
| Unknown | *no label* |

#### When the resources disagree, or report several modes

Many NDD genes are reported with both dominant and recessive forms. A gene gets
a single label:

- if only a recessive mode of transmission is reported for it, the gene is
  **recessive** (autosomal recessive or X-linked recessive);
- otherwise, it takes the **dominant** mode reported (autosomal dominant or
  X-linked dominant), which therefore takes precedence when both dominant and
  recessive forms are described.

The same rule settles disagreements between the two resources. In v7, 35 genes
reported as dominant by PanelApp and recessive by SysNDD are dominant, and so is
the one gene in the opposite case. The 5 X-linked genes reported as dominant by
PanelApp and recessive by SysNDD are X-linked dominant.

**X-linked other.** SysNDD labels some X-linked genes "X-linked other", without
saying whether the pattern is dominant or recessive. In v7, this label wins
whenever SysNDD gives it, including over a more specific call from PanelApp
Australia (57 genes PanelApp calls X-linked dominant, 32 it calls X-linked
recessive). Version 8 will change this: a PanelApp X-linked dominant call will
take precedence, and the 32 genes PanelApp calls X-linked recessive will stay
"X-linked other", since "other" could hide either pattern.

**Special cases.**

- **Pseudoautosomal genes** follow PanelApp's "autosomal or pseudoautosomal"
  label, not the chromosome they sit on. *e.g.* DHRSX, in the pseudoautosomal
  region of chromosome X, is autosomal recessive.
- **Imprinting** is not kept in v7: the 5 genes PanelApp reports as paternally
  or maternally imprinted are autosomal dominant. Version 8 will keep imprinting
  as a separate flag.
- **Somatic** variants are not a category: PIK3CA, which SysNDD reports as
  somatic, takes PanelApp's autosomal dominant call.
- **Mitochondrial** genes are the 17 genes encoded by the mitochondrial genome.

#### Categories

After the labels from both resources are harmonised, each gene gets one of six
categories. In v7:

| Mode of inheritance | Genes | % |
| --- | ---: | ---: |
| Autosomal Recessive | 1,204 | 56.0 |
| Autosomal Dominant | 778 | 36.2 |
| X-linked other | 94 | 4.4 |
| X-linked Recessive | 33 | 1.5 |
| X-linked Dominant | 23 | 1.1 |
| Mitochondrial | 17 | 0.8 |
| **Total** | **2,149** | |

51 genes of HC-NDD v7 have no mode of inheritance. 42 are on the list only
through SFARI Gene or SPARK, which do not report one. The other 9 are not in
SysNDD's *Definitive* set, and PanelApp Australia gives them no usable mode of
inheritance.

#### Limitations — to be interpreted with caution

This variable should be regarded as an indication, not as a definitive
annotation. An increasing number of NDD genes are described with both recessive
and dominant modes of transmission, sometimes with different mechanisms,
different variant types and distinct clinical presentations. Forcing a single
label per gene necessarily simplifies this reality: the GeneTrek field is
designed for descriptive and comparative analyses across large gene sets (e.g.
comparing gene length, constraint or expression between dominant and recessive
NDD genes), not for the interpretation of an individual gene or variant.

For any gene-specific question, and in particular in a diagnostic or clinical
context, go back to the primary resources, PanelApp Australia and SysNDD, where
the full set of reported inheritance patterns, the associated phenotypes and the
supporting evidence are detailed.

### Constraint metrics

The loss-of-function metrics are taken from their sources rather than
recomputed: LOEUF, MOEUF and the gnomAD constraint flags from gnomAD v4.1.1, and
the selection coefficient s-het from Sun et al. 2024. The constrained calls are
the published calls, not thresholds applied here, which is why the constraint
plot colours points by the published call instead of splitting them at a cutoff.

### Autism variants

Prevalence and odds ratios for rare loss-of-function variants come from Rolland
et al. 2023. An odds ratio can be unbounded when a cell of the contingency table
is zero; those genes are drawn at the top of the plot with their own marker
rather than being dropped.

### Searching

The search box accepts gene symbols, HGNC ids, Ensembl gene ids, NCBI gene ids
and genomic loci in either assembly, mixed freely and separated by commas,
spaces or newlines. A locus is an interval (`chr22:50674408-50733212`) or a
single position (`chr22:50700000`); it finds every gene overlapping it.
With exact matching on (the default), withdrawn and alternative symbols resolve
to the current gene: `KIAA1109` finds `BLTP1`. Coordinates pasted from a genome
browser work with or without thousands separators.

### Linking to GeneTrek

Other tools can open GeneTrek on a gene or a search:

- `/?gene=SHANK3` opens one gene's details (any identifier GeneTrek knows).
- `/?search=SHANK3,KMT2A,chr22:50000000-51000000` opens the table filtered to
  those genes and loci, exactly as if they had been typed in the search box,
  which shows them. Add `&genome=GRCh37` to read the loci in GRCh37 (GRCh38
  otherwise). The count above the table says how many entries the link had and
  names any that found nothing.
- `/#search=...&genome=...` does the same from the part of the address after
  `#`, which browsers never send to a server. Prefer it for a list of genes or
  a locus from a person's data: unlike `?search=`, it leaves no trace in any
  server log.

A link carries at most 500 entries; for longer lists, use
[My lists](/my-lists) or the API.

### Comparing gene sets

The **Compare gene sets** tab, next to the table, compares 2 to 12 sets: the
current search results, any gene list in the database, and your own lists (see
[My lists](/my-lists)). Click any region of the Venn diagram, any bar of the
UpSet plot, any heatmap cell or any row of the statistics table to see its
genes. From there you can show them in the table, save them as a list, or
download them.

**Figures.** Two or three sets are drawn as an Euler diagram: circle areas are
proportional to set sizes. With three sets, the overlap areas are only
approximate, because three circles generally cannot match every overlap exactly.
The numbers printed in the regions are always exact. When no arrangement of
circles leaves room for every non-empty region (three lists sharing only a small
common core, say), three sets are drawn as three equal circles instead, and the
figure says areas are not proportional. Four or five sets use a
fixed ellipse layout in which every region exists but areas mean nothing; the
figure says so. From three sets on, an UpSet plot shows every non-empty
intersection, largest first, and a heatmap shows every pair.

**Background.** Every set is first restricted to a background, the universe of
genes the tests count. By default this is the protein-coding genes (the page
shows how many), and it can be switched to all genes. The choice matters: most gene lists are
protein-coding, and testing them against all genes, a third of which are
pseudogenes, makes nearly any two lists look enriched in each other. Genes
outside the background are left out, and the page says how many were dropped
from each set. The enrichment in the **Statistics** panel uses the same
backgrounds, with its own selector. Its **Open in Compare** button opens this tab
with the current results and the chosen list already selected.

**Measured, not measured.** Some lists come from a measurement that did not cover
every gene. For example, "brain-enriched protein" can only be called for the
proteins the proteome quantified. Such a list has three answers:

- *yes*;
- *no* (measured, not called);
- *not measured* — shown as a grey "—" in the table.

A gene that was not measured is unknown, not absent. Counting it as "not on the
list" would make the list look depleted in everything it never looked at.

So a comparison runs on **one joint universe**: the background less every gene that
some set did not measure. One universe serves all pairs, so the Venn and UpSet
counts match the statistics exactly. The cost is that one set with low coverage
shrinks every pair. The page reports this separately from the background loss:

- how many genes each set lost because another set did not measure them;
- the N actually tested;
- a warning when coverage removed more than a fifth of the background.

The **Statistics** panel's enrichment applies the same rule to the list it tests.
Each column's information dialog states its universe and how many genes it
measured. `/api/fields/<column>/universe` downloads that universe, so a list can be
tested correctly outside GeneTrek too.

**Statistics.** For every pair of sets *A* and *B* within the analysed universe of
*N* genes:

- **overlap** |A ∩ B|, and the **expected** overlap |A|·|B| / N if the two
  sets were unrelated;
- **fold enrichment**: the overlap divided by its expectation;
- **p-value** from a two-sided Fisher's exact test on the 2×2 table of
  membership, and the **odds ratio** reported with it. This is the *conditional
  maximum-likelihood* estimate with its *exact* 95% confidence interval, the
  pair R's `fisher.test` reports. The interval comes from the same distribution
  as the test, so it excludes 1 exactly when the test is significant at 5%. When
  a cell of the table is zero, for example when one set lies entirely inside the
  other, the odds ratio is infinite or zero, but the interval still gives a
  bound on the other side;
- **FDR**: the p-values adjusted with the Benjamini–Hochberg procedure across
  all the pairs in the comparison;
- **Jaccard index**: |A ∩ B| / |A ∪ B|, how similar the two sets are;
- **overlap coefficient**: |A ∩ B| / min(|A|, |B|), which is 1 when the smaller
  set sits entirely inside the larger, whatever their sizes.

Very small p-values cannot be computed below about 10⁻³⁰⁰; they are reported as
"< 1e-300", never as 0.

**Adjusting for constraint.** Restricting to protein-coding genes removes one
bias, and the same problem remains one level down. Neurodevelopmental gene lists
are dominated by genes intolerant to loss of function, and so are synaptic,
chromatin and many other brain-related lists. Two such lists overlap more than
two random sets of protein-coding genes would, partly for that reason alone. In
the release built on HGNC 2026-09-18, High-confidence NDD v7 and SynGO v1.3 have
an odds ratio of 2.8 within protein-coding genes, but 1.7 once constraint is
accounted for.

So every pair is also tested **within strata of LOEUF**, the gnomAD constraint
metric:

- the genes of the analysed universe (the background, less any gene a set did not
  measure) are split into ten deciles of LOEUF. The deciles are computed within
  that universe, so they stay balanced when coverage shrinks it. The genes with no
  LOEUF form one more stratum;
- the pair is then tested with the **Cochran–Mantel–Haenszel** test (with
  continuity correction), which compares the sets within each stratum and pools
  the evidence;
- this gives a **Mantel–Haenszel common odds ratio** with a
  Robins–Breslow–Greenland 95% interval, and its own Benjamini–Hochberg FDR.

The statistics table shows the crude and the adjusted result side by side.
Compare them:

- A pair whose adjusted odds ratio stays well above 1 shares genes beyond what
  constraint explains.
- A pair whose association largely disappears was mostly two lists of
  constrained genes.

The adjustment is only as good as LOEUF as a measure of constraint, and genes
without a LOEUF value are compared among themselves.

**Reading the heatmap.** The heatmap shows one of three measures for every pair,
chosen above it. Each answers a different question, and a short explanation of
the selected one is printed under the heatmap:

- **Jaccard index**: *how similar are the two sets?* 0 means no gene in common
  and 1 means identical sets. A small list lying entirely inside a large one
  still scores low, because most of the large list is not shared.
- **Overlap coefficient**: *is one set contained in the other?* 1 means the
  smaller set lies entirely inside the larger, whatever their sizes. It cannot
  tell a 10-gene list inside a 20-gene list from one inside a 5,000-gene list.
- **Signed −log10 FDR**: *is the overlap more, or less, than chance would
  give?* This is the FDR on a log scale, positive (blue) when the sets share
  **more** genes than expected and negative (orange) when they share **fewer**:
  ±1.3 is FDR 0.05, ±2 is 0.01, ±3 is 0.001. ±>300 means beyond what can be
  computed. The scale is diverging and centred on zero, so depletion is never
  mistaken for enrichment. By default it shows the result **adjusted for
  constraint**; a checkbox above the figures switches it to the crude result.
  A large overlap between two large lists can be highly significant and still
  be modest in size, so read it alongside the Jaccard index or the fold
  enrichment.

Cells marked † are pairs that overlap by construction (see below).

**Overlap by construction.** Some pairs overlap because of how they were made,
not because of biology: a list derived from another, directly or through other
lists (HC-NDD v7 is built from the autism list, which is built from SFARI Gene),
or two versions of the same curated list. These pairs are marked (with †
in the heatmap) and named in the methods text. Their p-values are still shown,
but they are not evidence of association.

**Publication exports.** *Download figure* produces SVG (vector, with text kept
editable) or PNG at 300 or 600 dpi, at 89, 120 or 183 mm wide (single, 1.5 and
double journal column). Labels use Arial at 7 pt, lines are 0.5 pt, and the
colours are the colour-blind-safe Okabe–Ito palette. Your browser draws the
figure.

- **Alt text.** The export dialog shows an *alt text* for the figure, ready to
  copy into a journal's submission form. It is generated from the numbers drawn:
  the figure type, every set and its size, the background, and the region counts
  or the value for every pair. It states what is shown, not what it means.
- **Colour space.** Figures are RGB. Most online journals accept RGB. If yours
  asks for CMYK, convert the SVG in Illustrator, Affinity Designer or Inkscape;
  the Okabe–Ito colours survive the conversion with only slight shifts in the
  yellow and the sky blue.

*Download tables* produces:

- a gene-by-set membership table (`genesets`);
- a description of every set: source, version, DOI, and size before and after
  the background (`sets`);
- the pairwise statistics, crude and adjusted (`statistics`);
- a generated methods paragraph naming every choice above;
- the alt text of every figure;
- `request.json`, the exact request that reproduces the comparison through the
  API.

You can download them as separate TSV files, as one XLSX workbook, or as a ZIP
of TSVs with the methods paragraph as a README. Nothing is stored on the server
in either case.

**Reproducing a comparison.** Everything this tab computes is also available
from `POST /api/compare` (see [API reference](/api-reference)). Send the
`request.json` from a download and you get the same numbers back. Database lists
are named in it. Anything else, such as the current search results or one of
your lists, is written out as its genes, because the server keeps no state to
refer to.
