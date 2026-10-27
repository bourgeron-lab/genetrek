# Data licences

GeneTrek's code is under the MIT licence (see `LICENSE`). Its **data** is not:
every column comes from a source with its own terms, listed below, and no
single licence covers the whole database. When you reuse a column, follow its
source's terms and cite its source; the [How to cite](https://genetrek.pasteur.fr/cite)
page and each column's information dialog give the references.

GeneTrek is for research use only, not for diagnostic use.

This file is generated from each column's metadata by `genetrek-db licences`.
Do not edit it by hand.

## Summary

| Licence | Columns |
| --- | ---: |
| CC-BY-4.0 | 32 |
| CC0-1.0 | 14 |
| No restrictions (Ensembl/GENCODE, NCBI) | 9 |
| None stated (PanelApp Australia) | 7 |
| GTEx Portal Data License | 6 |
| CC0-1.0 (via HGNC) | 5 |
| SFARI Gene terms (reuse with citation) | 3 |
| Allen Institute Terms of Use (non-commercial) | 2 |
| Not open (Elsevier open archive; non-commercial) | 2 |
| CC-BY-SA-4.0 | 1 |
| CC-BY-SA-4.0 (includes TRRUST) | 1 |
| None stated (NCG; cite) | 1 |
| Not open (Genet Med 2025); factual gene list | 1 |
| Not open (Nat Genet 2023); factual gene list | 1 |
| SPARK Terms of Use; public display permitted by the Simons Foundation (SFARI) | 1 |
| Unclear (CC-BY-4.0 data, CC-BY-NC-SA-4.0 repository) | 1 |

## Columns

| Column | Name | Licence | Source | DOI |
| --- | --- | --- | --- | --- |
| `hgnc_symbol` | Gene | CC0-1.0 | HGNC |  |
| `hgnc_alias_symbol` | Alias symbols | CC0-1.0 | HGNC |  |
| `hgnc_location` | Cytogenetic location | CC0-1.0 | HGNC |  |
| `hgnc_name` | Gene name | CC0-1.0 | HGNC |  |
| `hgnc_id` | HGNC ID | CC0-1.0 | HGNC |  |
| `hgnc_locus_group` | Locus group | CC0-1.0 | HGNC |  |
| `hgnc_locus_type` | Locus type | CC0-1.0 | HGNC |  |
| `hgnc_prev_symbol` | Previous symbols | CC0-1.0 | HGNC |  |
| `ensg` | Ensembl ID | CC0-1.0 (via HGNC) | Ensembl, via the HGNC complete set |  |
| `mane_select` | MANE Select | CC0-1.0 (via HGNC) | MANE (NCBI and EMBL-EBI), via the HGNC complete set |  |
| `ncbi_id` | NCBI ID | CC0-1.0 (via HGNC) | NCBI Gene, via the HGNC complete set |  |
| `omim_id` | OMIM ID | CC0-1.0 (via HGNC) | OMIM, via the HGNC complete set |  |
| `refseq_accession` | RefSeq accession | CC0-1.0 (via HGNC) | NCBI RefSeq, via the HGNC complete set |  |
| `strand` | Strand | No restrictions (Ensembl/GENCODE, NCBI) | GENCODE v50, with NCBI as a fallback |  |
| `grch38_chr` | GRCh38 chromosome | No restrictions (Ensembl/GENCODE, NCBI) | GENCODE v50, with NCBI as a fallback |  |
| `grch38_source` | GRCh38 coordinate source | No restrictions (Ensembl/GENCODE, NCBI) | genetrek-db init |  |
| `grch38_end` | GRCh38 end | No restrictions (Ensembl/GENCODE, NCBI) | GENCODE v50, with NCBI as a fallback |  |
| `grch38_start` | GRCh38 start | No restrictions (Ensembl/GENCODE, NCBI) | GENCODE v50, with NCBI as a fallback |  |
| `grch37_chr` | GRCh37 chromosome | No restrictions (Ensembl/GENCODE, NCBI) | GENCODE v50, with NCBI as a fallback |  |
| `grch37_source` | GRCh37 coordinate source | No restrictions (Ensembl/GENCODE, NCBI) | genetrek-db init |  |
| `grch37_end` | GRCh37 end | No restrictions (Ensembl/GENCODE, NCBI) | GENCODE v50, with NCBI as a fallback |  |
| `grch37_start` | GRCh37 start | No restrictions (Ensembl/GENCODE, NCBI) | GENCODE v50, with NCBI as a fallback |  |
| `hc_ndd_v7` | High-confidence NDD v7 | CC-BY-4.0 | GeneTrek curation: union of autism_genes_v7, idd_genes_v7 and epilepsy_genes_v7 | 10.1016/j.biopsych.2026.10.003 |
| `candidate_ndd_v5` | Candidate NDD v5 | CC-BY-4.0 | GeneTrek curation |  |
| `hc_ndd_v6` | High-confidence NDD v6 | CC-BY-4.0 | GeneTrek curation |  |
| `hc_ndd_v3` | High-confidence NDD v3 | CC-BY-4.0 | GeneTrek curation |  |
| `hc_ndd_v2` | High-confidence NDD v2 | CC-BY-4.0 | GeneTrek curation |  |
| `hc_ndd_v1` | High-confidence NDD v1 | CC-BY-4.0 | GeneTrek curation |  |
| `candidate_ndd_v4` | Candidate NDD v4 | CC-BY-4.0 | GeneTrek curation |  |
| `candidate_ndd_v3` | Candidate NDD v3 | CC-BY-4.0 | GeneTrek curation |  |
| `candidate_ndd_v2` | Candidate NDD v2 | CC-BY-4.0 | GeneTrek curation |  |
| `candidate_ndd_v1` | Candidate NDD v1 | CC-BY-4.0 | GeneTrek curation |  |
| `autism_genes_v7` | Autism genes v7 | CC-BY-4.0 | GeneTrek curation | 10.1016/j.biopsych.2026.10.003 |
| `eagle_score` | EAGLE class | SFARI Gene terms (reuse with citation) | https://gene.sfari.org/about-gene-scoring/eagle-score/ - EAGLE score, 2026-03-22 release | 10.1038/s41576-020-0231-2 |
| `eagle_points` | EAGLE score | SFARI Gene terms (reuse with citation) | https://gene.sfari.org/about-gene-scoring/eagle-score/ - EAGLE score, 2026-03-22 release | 10.1038/s41576-020-0231-2 |
| `panelapp_aus_autism_20260331` | PanelApp AUS Autism | None stated (PanelApp Australia) | PanelApp Australia - https://panelapp-aus.org/panels/51/ v0.248 | 10.1038/s41588-019-0528-2 |
| `sfari_1_20260322` | SFARI 1 | SFARI Gene terms (reuse with citation) | https://gene.sfari.org/database/gene-scoring/ - SFARI Gene, 2026-03-22 release |  |
| `spark_20250617` | SPARK | SPARK Terms of Use; public display permitted by the Simons Foundation (SFARI) | https://sparkforautism.org/portal/page/spark-gene-list/ |  |
| `epilepsy_genes_v7` | Epilepsy genes v7 | CC-BY-4.0 | GeneTrek curation | 10.1016/j.biopsych.2026.10.003 |
| `panelapp_aus_epilepsy_20260331` | PanelApp AUS Genetic epilepsy | None stated (PanelApp Australia) | PanelApp Australia - https://panelapp-aus.org/panels/202/ v1.4 | 10.1038/s41588-019-0528-2 |
| `panelapp_aus_myoclonic_epilepsy_20260331` | PanelApp AUS Progressive myoclonic epilepsy | None stated (PanelApp Australia) | PanelApp Australia - https://panelapp-aus.org/panels/331/ v0.28 | 10.1038/s41588-019-0528-2 |
| `idd_genes_v7` | IDD genes v7 | CC-BY-4.0 | GeneTrek curation | 10.1016/j.biopsych.2026.10.003 |
| `panelapp_aus_id_20260331` | PanelApp AUS Intellectual disability | None stated (PanelApp Australia) | PanelApp Australia - https://panelapp-aus.org/panels/250/ v1.731 | 10.1038/s41588-019-0528-2 |
| `sysndd_definitive_v0_11_2` | SysNDD definitive | CC-BY-4.0 | SysNDD https://sysndd.dbmr.unibe.ch/ v0.11.2 |  |
| `adhd_demontis2023` | ADHD genes (Demontis 2023) | Not open (Nat Genet 2023); factual gene list | Demontis et al. 2023, Nat Genet 55:198-208 | 10.1038/s41588-022-01285-8 |
| `panelapp_aus_speech_apraxia_20260331` | PanelApp AUS Speech apraxia | None stated (PanelApp Australia) | PanelApp Australia - https://panelapp-aus.org/panels/4290/ v1.28 | 10.1038/s41588-019-0528-2 |
| `cancer_combined_ncg_7_2` | Cancer gene (any source) | CC-BY-4.0 | union of NCG 7.2 and the PanelApp paediatric cancer panel |  |
| `cancer_ncg_7_2` | Canonical cancer drivers | None stated (NCG; cite) | Network of Cancer Genes - http://network-cancer-genes.org/canonical_drivers.php NCG7.2 | 10.1186/s13059-022-02607-z |
| `panelapp_aus_cancer_predisposition` | PanelApp AUS Cancer Predisposition Paediatric | None stated (PanelApp Australia) | PanelApp Australia - https://panelapp-aus.org/panels/152/ v1.2 | 10.1038/s41588-019-0528-2 |
| `acmg_sf_v3_3` | ACMG SF | Not open (Genet Med 2025); factual gene list | ACMG SF v3.3 list -- Lee K et al., Genet Med 2025;27(8):101454 | 10.1016/j.gim.2025.101454 |
| `chrom_epi_tf_combined` | Chromatin, epigenetic or TF gene | CC-BY-SA-4.0 (includes TRRUST) | union of the four sources |  |
| `syngo_v1_3` | SynGO v1.3 | CC-BY-4.0 | SynGO - https://www.syngoportal.org/ v1.3 | 10.1016/j.neuron.2019.05.002 |
| `metabolic_process_go_8152` | Metabolic process | CC-BY-4.0 | https://www.ebi.ac.uk/QuickGO/ GO:0008152 |  |
| `epifactors_v2_1` | Epifactors | CC-BY-4.0 | https://epifactors.autosome.org/ - EpiFactors v2.1 | 10.1093/database/bav067 |
| `chromatin_remodeling` | Epigenetic regulators and chromatin remodellors | CC-BY-4.0 | Valencia et al., Nature Genetics 2023 | 10.1038/s41588-023-01451-6 |
| `rummagene_tf` | Rummagene transcription factor | Unclear (CC-BY-4.0 data, CC-BY-NC-SA-4.0 repository) | https://rummagene.com/ - Rummagene | 10.1038/s42003-024-06177-7 |
| `trrust_v2_tf` | TRRUST transcription factor | CC-BY-SA-4.0 | TRRUST - https://www.grnpedia.org/trrust/ v2 | 10.1093/nar/gkx1013 |
| `constraint` | Constrained genes | CC-BY-4.0 | combined constraint call |  |
| `is_loeuf_gnomad_v4_1_1` | LOEUF score < 0.45 | CC0-1.0 | https://gnomad.broadinstitute.org/data#v4-constraint - gnomAD v4.1.1 constraint metrics | 10.1038/s41586-020-2308-7 |
| `loeuf` | LOEUF | CC0-1.0 | https://gnomad.broadinstitute.org/data#v4-constraint - gnomAD v4.1.1 constraint metrics | 10.1038/s41586-020-2308-7 |
| `gnomad_constraint_flags` | gnomAD constraint flags | CC0-1.0 | https://gnomad.broadinstitute.org/data#v4-constraint - gnomAD v4.1.1 constraint metrics | 10.1038/s41586-020-2308-7 |
| `gnomad_gene_flags` | gnomAD gene flags | CC0-1.0 | https://gnomad.broadinstitute.org/data#v4-constraint - gnomAD v4.1.1 constraint metrics | 10.1038/s41586-020-2308-7 |
| `moeuf` | MOEUF | CC0-1.0 | https://gnomad.broadinstitute.org/data#v4-constraint - gnomAD v4.1.1 constraint metrics | 10.1038/s41586-020-2308-7 |
| `soeuf` | SOEUF | CC0-1.0 | https://gnomad.broadinstitute.org/data#v4-constraint - gnomAD v4.1.1 constraint metrics | 10.1038/s41586-020-2308-7 |
| `is_shet_constrained` | s-het constrained | CC-BY-4.0 | Sun et al. Nature 2024 | 10.1038/s41586-024-07556-0 |
| `shet_mean` | s-het mean | CC-BY-4.0 | Sun et al. Nature 2024 | 10.1038/s41586-024-07556-0 |
| `shet_lower` | s-het (lower bound) | CC-BY-4.0 | Sun et al. Nature 2024 | 10.1038/s41586-024-07556-0 |
| `shet_upper` | s-het (upper bound) | CC-BY-4.0 | Sun et al. Nature 2024 | 10.1038/s41586-024-07556-0 |
| `shet_sd` | s-het sd | CC-BY-4.0 | Sun et al. Nature 2024 | 10.1038/s41586-024-07556-0 |
| `haploinsufficient` | Haploinsufficient | CC-BY-4.0 | Collins et al., Cell 2022 | 10.1016/j.cell.2022.06.036 |
| `triplosensitive` | Triplosensitive | CC-BY-4.0 | Collins et al., Cell 2022 | 10.1016/j.cell.2022.06.036 |
| `jiang2020_brain_enriched` | Brain-enriched protein (Jiang 2020) | Not open (Elsevier open archive; non-commercial) | Jiang et al. 2020, Cell 183:269 -- Table S4, sheet 'A enrichment comparison' | 10.1016/j.cell.2020.08.036 |
| `jiang2020_brain_ts` | Brain protein TS (Jiang 2020) | Not open (Elsevier open archive; non-commercial) | Jiang et al. 2020, Cell 183:269 -- Table S2, sheet 'G protein TS score' | 10.1016/j.cell.2020.08.036 |
| `brainspan_prenatal_biased` | Prenatal-biased (BrainSpan) | Allen Institute Terms of Use (non-commercial) | BrainSpan Atlas of the Developing Human Brain -- RNA-Seq Gencode v10 summarized to genes | 10.1038/nature10523 |
| `brainspan_prenatal_log2fc` | Prenatal log2FC (BrainSpan) | Allen Institute Terms of Use (non-commercial) | BrainSpan Atlas of the Developing Human Brain -- RNA-Seq Gencode v10 summarized to genes | 10.1038/nature10523 |
| `gtex_v11_brain_enriched` | Brain-enriched (GTEx v11) | GTEx Portal Data License | GTEx Analysis v11 -- gene median TPM by tissue (RNASeQC v2.4.3, GENCODE v47) | 10.1126/science.aaz1776 |
| `gtex_v11_brain_max_tpm` | Brain max TPM (GTEx v11) | GTEx Portal Data License | GTEx Analysis v11 -- gene median TPM by tissue (RNASeQC v2.4.3, GENCODE v47) | 10.1126/science.aaz1776 |
| `gtex_v11_brain_vs_rest_log2` | Brain vs rest, log2 (GTEx v11) | GTEx Portal Data License | GTEx Analysis v11 -- gene median TPM by tissue (RNASeQC v2.4.3, GENCODE v47) | 10.1126/science.aaz1776 |
| `gtex_v11_brain_expressed` | Brain-expressed (GTEx v11) | GTEx Portal Data License | GTEx Analysis v11 -- gene median TPM by tissue (RNASeQC v2.4.3, GENCODE v47) | 10.1126/science.aaz1776 |
| `gtex_v11_cerebellum_enriched` | Cerebellum-enriched (GTEx v11) | GTEx Portal Data License | GTEx Analysis v11 -- gene median TPM by tissue (RNASeQC v2.4.3, GENCODE v47) | 10.1126/science.aaz1776 |
| `gtex_v11_tau` | Tissue specificity tau (GTEx v11) | GTEx Portal Data License | GTEx Analysis v11 -- gene median TPM by tissue (RNASeQC v2.4.3, GENCODE v47) | 10.1126/science.aaz1776 |
| `37365347_autism_or` | Autism odds ratio | CC-BY-4.0 |  | 10.1038/s41591-023-02408-2 |
| `37365347_number_of_carriers_among_autistic_individuals` | Number of carriers among autistic individuals | CC-BY-4.0 |  | 10.1038/s41591-023-02408-2 |
| `37365347_p_value` | p-value | CC-BY-4.0 |  | 10.1038/s41591-023-02408-2 |
| `inheritance_v7` | Mode of inheritance v7 | CC-BY-4.0 | GeneTrek curation |  |
| `inheritance_panelapp_20260617` | Mode of inheritance from PanelApp Aus | None stated (PanelApp Australia) | PanelApp Australia, 2026-06-17 | 10.1038/s41588-019-0528-2 |
| `inheritance_sysndd_definitive_v0_11_2` | Mode of inheritance from SysNDD | CC-BY-4.0 | SysNDD https://sysndd.dbmr.unibe.ch/ v0.11.2 |  |
