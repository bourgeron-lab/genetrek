## How to cite

### GeneTrek

If GeneTrek itself was useful, cite the paper describing the operative gene
list it was first built around:

> Leblond CS, Le TL, Malesys S, Cliquet F, Tabet AC, Delorme R, Rolland T,
> Bourgeron T. **Operative list of genes associated with autism and
> neurodevelopmental disorders based on database review.** Mol Cell Neurosci.
> 2021 Jun;113:103623.
> [doi:10.1016/j.mcn.2021.103623](https://doi.org/10.1016/j.mcn.2021.103623).
> [PMID: 33932580](https://pubmed.ncbi.nlm.nih.gov/33932580/).

### The high-confidence NDD genes

If you used HC-NDD v7 (`hc_ndd_v7`) or its autism, IDD or epilepsy gene lists
(`autism_genes_v7`, `idd_genes_v7`, `epilepsy_genes_v7`), also cite the review
that defines them:

> Korkmaz S, De Sainte Agathe JM, Cliquet F, Martin LC, Barthome E, Fleury M,
> Segers J, Bonnot-Briey S, Grimaldi de Puget V, Zhou Y, Wolke D, Grove J,
> Børglum AD, Delorme R, Leblond CS, Bourgeron T. **The genetic orchestra of
> autism and its intersections with epilepsy, intellectual disability, and
> ADHD.** Biol Psychiatry. 2026.
> [doi:10.1016/j.biopsych.2026.10.003](https://doi.org/10.1016/j.biopsych.2026.10.003).

Please also say which version of a gene list you used — `hc_ndd_v7` rather than
`hc_ndd` — and the date you downloaded the data. Versions coexist and the short
names move, so an analysis that cites only the short name cannot be reproduced
later. The [changelog](/changelog) records what each version contains.

### The data you used

GeneTrek aggregates other people's work. Cite the source of the columns your
analysis actually depended on, alongside GeneTrek.

**Rare loss-of-function variants in autism** — the carrier counts, odds ratios
and p-values:

> Rolland T, Cliquet F, Anney RJL, et al. **Phenotypic effects of genetic
> variants associated with autism.** Nat Med. 2023.
> [doi:10.1038/s41591-023-02408-2](https://doi.org/10.1038/s41591-023-02408-2).

**Selection coefficients (s-het)** — `shet_mean` and the constrained call:

> Sun KY, Bai X, Chen S, et al. **A deep catalogue of protein-coding variation
> in 983,578 individuals.** Nature. 2024;631:583–592.
> [doi:10.1038/s41586-024-07556-0](https://doi.org/10.1038/s41586-024-07556-0).

**Chromatin remodelling genes** — `chromatin_remodeling`:

> [doi:10.1038/s41588-023-01451-6](https://doi.org/10.1038/s41588-023-01451-6)

**Loss-of-function constraint** — LOEUF, MOEUF and the gnomAD constraint flags
are from the [gnomAD](https://gnomad.broadinstitute.org/) v4.1.1 constraint
metrics; cite gnomAD's own current citation guidance.

**Nomenclature and coordinates** — gene symbols, identifiers and cross-references
are from [HGNC](https://www.genenames.org/); coordinates are from
[GENCODE](https://www.gencodegenes.org/) v50, with
[NCBI Gene](https://www.ncbi.nlm.nih.gov/gene/) as a fallback.

**The other gene lists** — PanelApp, SysNDD, SFARI, SPARK, EAGLE, ACMG SF,
SynGO, EpiFactors, TRRUST, Rummagene and NCG each have their own citation and
their own release date.

### Finding the right reference for a column

Rather than reproducing all of that here, where it would go stale: every column
carries its own provenance. Click the information icon in a column's header and
the dialog gives that column's source, release date and DOI. The same fields are
available over the API at
[`/api/fields/{field}`](/api-reference#getting-a-gene-list-or-any-column).
