# GeneTrek

GeneTrek brings together curated gene lists for neurodevelopmental disorders (NDD) and
per-gene annotations — gene constraint, brain expression, rare-variant results in autism —
in one versioned table. Search genes or loci, compare your own lists with known NDD genes
against an explicit background, and download the whole table.

**Use it at <https://genetrek.pasteur.fr>.** This repository is the application's source code
and its database. GeneTrek is for research use only, not for diagnostic use.

## Quick start

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Clone this repository and install the dependencies:

   ```bash
   uv sync
   ```

3. Run the application, then open <http://127.0.0.1:8000/> (API reference at `/docs`):

   ```bash
   uv run genetrek                          # 127.0.0.1:8000
   uv run genetrek --host 0.0.0.0 --port 5000
   uv run genetrek --reload                 # development, with auto-reload
   ```

To run it in a container or on Kubernetes, see [docs/deploying.md](docs/deploying.md).

## Project structure

```
genetrek/
├── resources/          # The database and its configuration; the app will not start without it
│   ├── config/         # fields_description.yaml (column metadata), categories, badges, links
│   ├── content/        # site_description.md and one .md per prose page
│   ├── processed_data/ # genetrek.parquet (the database) and the gene-alias index
│   └── static/         # companion files served at /data (see below)
├── src/genetrek/       # The Python package: web app, API, services, genetrek-db
├── tests/              # pytest suite
├── chart/              # Helm chart
├── docs/               # Database maintenance and deployment guides
└── DATA_LICENCES.md    # Every column's licence
```

`resources/` is found by searching upwards for a directory containing
`config/fields_description.yaml`. Set `$GENETREK_RESOURCES` to run against a copy.

## Maintaining the database

The database is a pair of files that must agree: `resources/processed_data/genetrek.parquet`
and `resources/config/fields_description.yaml`. `genetrek-db` treats them as one unit —
validating before writing, backing up what it replaces, and logging every change.

```bash
uv run genetrek-db list --sizes                 # every field, with memory footprint
uv run genetrek-db show haploinsufficient       # metadata plus statistics
uv run genetrek-db resolve my_genes.txt         # check a gene list before adding it
uv run genetrek-db add my_genes.txt --dry-run   # preview, write nothing
uv run genetrek-db rename old_name new_name     # data + metadata + config together
uv run genetrek-db validate                     # exits non-zero on any error
uv run genetrek-db licences                     # every column's licence
uv run genetrek-db --help
```

Identifiers resolve from current symbols, retired and alias symbols, Ensembl IDs with or
without version suffixes, `HGNC:`-prefixed IDs and NCBI IDs, mixed freely in one list.
Anything that fails to resolve is reported, and `add` refuses rather than silently dropping
genes. The same functionality is available as a library:

```python
from genetrek.db import Database

db = Database.open()
db.list_fields()
db["loeuf"].stats()
```

See [docs/database-maintenance.md](docs/database-maintenance.md) for the metadata schema,
resolution rules, the add/remove runbook and backup recovery.

## Data and terms

GeneTrek republishes values from many sources, each with its own terms: some are CC0, some
CC BY, some non-commercial. No single licence covers the database. Every column states its
licence in its metadata — in the column's information dialog, on the site's
[Column reference](https://genetrek.pasteur.fr/columns), over the API — and
[DATA_LICENCES.md](DATA_LICENCES.md) lists them all. When you reuse a column, follow its
source's terms and cite its source.

**Not in this repository:**

- the Rolland et al. 2023 variant files and the UCSC track hub that the public site serves
  at `/data` and `/downloadVCF`; they are added when the site is deployed. Without them
  GeneTrek runs normally and those two paths answer 404.
- the notebooks that built this release of the database, and the raw files they read. They
  will be published with the next data release.

## How to cite

- **GeneTrek:** Leblond CS, Le TL, Malesys S, Cliquet F, Tabet AC, Delorme R, Rolland T,
  Bourgeron T. *Operative list of genes associated with autism and neurodevelopmental
  disorders based on database review.* Mol Cell Neurosci. 2021;113:103623.
  [doi:10.1016/j.mcn.2021.103623](https://doi.org/10.1016/j.mcn.2021.103623)
- **The high-confidence NDD genes** (`hc_ndd_v7`) and their autism, IDD and epilepsy lists:
  Korkmaz S et al. *The genetic orchestra of autism and its intersections with epilepsy,
  intellectual disability, and ADHD.* Biol Psychiatry. 2026.
  [doi:10.1016/j.biopsych.2026.10.003](https://doi.org/10.1016/j.biopsych.2026.10.003)
- **Every source you rely on:** see [How to cite](https://genetrek.pasteur.fr/cite) and each
  column's metadata. Name the list version you used (`hc_ndd_v7`, not `hc_ndd`).

[CITATION.cff](CITATION.cff) carries the same information for citation tools.

## Licence

The code is under the [MIT licence](LICENSE), © 2026 Institut Pasteur. The data under
`resources/` are not: each column keeps its source's terms (see
[DATA_LICENCES.md](DATA_LICENCES.md)).

## Development

```bash
uv sync --group dev            # ruff, mypy, pytest
uv run pytest
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run mypy src/genetrek/
```

## Contact

Human Genetics and Cognitive Functions, Institut Pasteur —
[ghfcgenetics@pasteur.fr](mailto:ghfcgenetics@pasteur.fr).
