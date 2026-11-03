# genetrek

Python client and command line for [GeneTrek](https://genetrek.pasteur.fr): curated gene lists
for neurodevelopmental disorders (NDD) and per-gene annotations (constraint, brain expression,
rare-variant results) in one versioned table.

```bash
pip install genetrek            # or: uv add genetrek
pip install "genetrek[pandas]"  # to call .to_pandas() on the tables
```

Python 3.10 or newer. GeneTrek is for research use only, not for diagnostic use.

## From Python

```python
from genetrek import GeneTrek, Set

gt = GeneTrek()                                   # the public instance

hc = gt.gene_list("hc_ndd")                       # a gene list's members
table = gt.genes(["SHANK3", "KIAA1650", "85358"],  # any identifiers, any mix
                 columns=["loeuf", "hc_ndd"])       # -> polars DataFrame
gt.convert(["SHANK3", "ENSG00000100888"], to=["hgnc", "ncbi"])
gt.search("chr22:50,674,408-50,733,212")          # what the search box does

result = gt.compare([
    Set.field("hc_ndd"),
    Set.genes(["SHANK3", "CHD8", "SCN2A"], label="my genes"),
    Set.file("candidates.txt"),
])
result.pairs        # overlap, odds ratio with exact 95% CI, Fisher p, FDR, adjusted
result.methods      # the methods paragraph, with the database release
result.to_request_json("request.json")   # gt.replay("request.json") later
```

Tables are [polars](https://pola.rs) DataFrames; with the `pandas` extra, `table.to_pandas()`.
A null means "not measured" and is never turned into `False`.

Errors are exceptions you can catch: `UnknownGene`, `AmbiguousGene`, `UnknownField`,
`NotAGeneList`, and `ApiError` (with the server's `.code`) for the rest. Identifiers that
match no gene, or several, are reported in an `UnresolvedWarning` rather than dropped
silently.

## From the shell

```bash
genetrek list hc_ndd > hc_ndd.txt                    # one gene per line
genetrek genes my_genes.txt --columns loeuf,hc_ndd   # TSV to stdout
genetrek convert - --to hgnc,ensg < ids.txt
genetrek search SHANK3 chr22:50674408-50733212
genetrek compare hc_ndd my_genes.txt:mine --out results/
genetrek replay results/request.json
genetrek download -o genetrek.parquet
genetrek --help
```

Tables go to stdout as TSV (`NA` for null), messages to stderr; `--json` prints JSON.

## Which server

The public instance, `https://genetrek.pasteur.fr`, is the default. To use another one,
name it once:

```bash
genetrek config add local http://localhost:8000
genetrek config add dev https://genetrek.dev.example.org --default
genetrek info          # says which server answered, and why it was chosen
```

The names live in a small TOML file in your configuration directory (`genetrek config list`
prints where; `$GENETREK_CONFIG` moves it). The first of these that is set wins:
`GeneTrek(base_url=...)` or `--url`; `GeneTrek(server="dev")` or `--server dev`;
`$GENETREK_URL`; `$GENETREK_SERVER`; the file's default; the public instance.

## Your genes

Your identifiers travel in request bodies only, never in a URL, so they are not written to
any server's access log, and nothing you send is cached to disk. A My lists share link is
read locally: only its token is sent.

## Versions

Every result carries the database release it was computed on (`result.release`,
`gt.release()`). The client checks the server's API version: `genes()`, `search()` and the
error codes need API 1.1, which genetrek.pasteur.fr serves from GeneTrek v8; against an
older server those methods raise `ServerTooOld`, and everything else works.

## Citing and licences

How to cite GeneTrek: <https://genetrek.pasteur.fr/cite>. The code is MIT-licensed; the
data keep each source's terms, listed per column on <https://genetrek.pasteur.fr/columns>.
