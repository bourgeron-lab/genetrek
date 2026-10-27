# Companion files served under `/data`

The application serves this directory at `/data`: the Rolland et al. 2023
variant files (`/downloadVCF` and the `Rolland_NatMed_2023_*.vcf.gz` tracks)
and the UCSC track hub linked from each gene's dialog.

These files are not distributed with the source code. The public instance at
https://genetrek.pasteur.fr adds them when it is deployed. Without them
GeneTrek runs normally: `/data` and `/downloadVCF` answer 404 and nothing else
changes.
