# GeneTrek

Genomic data viewer: FastAPI backend + NiceGUI frontend. Data comes from a Polars DataFrame loaded from `resources/processed_data/genetrek.parquet`. Column visibility, badges, and external links are config-driven via YAML files in `resources/config/`.

## Running

```bash
uv run genetrek                     # start at http://127.0.0.1:8000
uv run genetrek --reload            # dev mode with hot reload
uv run genetrek-db --help           # maintain the parquet/YAML database
uv run genetrek-db init --dry-run   # build a database from HGNC + GENCODE, plan only
uv run genetrek-db validate         # validate YAML/parquet alignment
uv run genetrek-validate-fields     # same checks, legacy entry point
uv run pytest                       # test suite
```

## Architecture

```
src/genetrek/
  paths.py            – Resolves resources/ (arg > $GENETREK_RESOURCES > upward search)
  schema.py           – THE field-metadata contract: FieldMeta, value types, YAML I/O
  app.py              – FastAPI app factory, NiceGUI registration
  web.py              – Thin entry point (exposes `app` for uvicorn, `main()` for CLI)
  config/loader.py    – Loads YAML configs + parquet data → AppConfig dataclass
  models/schemas.py   – Pydantic response models
  api/routes.py       – FastAPI routes (/api/data, /api/download/database, /api/health)
  api/lookup_routes.py – /api/fields, /api/genes, /api/convert (same router, split for size)
  api/compare_routes.py – POST /api/compare, GET /api/backgrounds (the Compare view over HTTP)
  services/search.py  – DataFrame filtering (token search + boolean column filters)
  services/enrichment.py – Fisher exact test enrichment analysis
  services/lookup.py  – Field/gene lookups shared by the API and the UI dialogs
  services/versions.py – Compares coexisting versions of a gene list (the changelog table)
  services/column_reference.py – Every column's metadata + counts (the /columns page, its TSV)
  services/user_lists.py – My lists: gene-set model, compact codec, share links, export files
  services/compare.py – Gene-set comparison: background restriction, pairwise Fisher/BH, LOEUF
                        strata (CMH), regions
  services/odds.py    – Conditional exact OR/CI (= R fisher.test) and Mantel-Haenszel/CMH
  services/compare_layout.py – Venn geometry: proportional circles (2-3), fixed ellipses (4-5)
  services/compare_figures.py – Plotly Venn/UpSet/heatmap, screen and publication styles
  services/compare_export.py – Supplementary tables (TSV/XLSX/ZIP) and the methods text
  backgrounds.py      – Comparison universes and covariates, from config/backgrounds.yaml (optional)
  content.py          – THE prose-page table: ContentPage, CONTENT_PAGES
  ui/page.py          – @ui.page("/") main page composition
  ui/content.py       – One page per CONTENT_PAGES entry, from resources/content/*.md
  ui/styles.py        – CSS constants
  ui/components/      – gene_table, token_editor, search_drawer, stats_drawer, column_selector,
                        my_lists (every My lists dialog + the tab's list state),
                        compare_view (the Compare tab)
  ui/js/              – JavaScript files (gene_table.js, token_editor.js, drawer_toggle.js,
                        my_lists.js -- the only code touching localStorage and #lists=,
                        search_link.js -- reads and strips #search= (a search link),
                        compare.js -- publication figure export, drawn off-screen in the browser)

  db/                 – Database maintenance library (no FastAPI/NiceGUI imports)
    database.py       – Database: staged mutation, validation, atomic save
    build.py          – Assemble a new database from HGNC + coordinates (offline)
    sources/          – One module per upstream, all behind a Fetcher protocol:
                        hgnc, gencode, ncbi, coordinates (the cascade), fetcher
    field.py          – Field (metadata only) / BoundField (+ values, write-through)
    gene_index.py     – Identifier resolution onto ncbi_id, with HGNC aliases
    storage.py        – Crash-safe writes, backups, history log
  cli/                – `genetrek-db` Typer app (list/show/add/remove/set/resolve/…)
```

## Key Patterns

- Config is loaded once at startup via `load_config()` → immutable `AppConfig` dataclass.
- NiceGUI page uses a closure-based `state` dict shared across components.
- The table is rendered client-side by TanStack Table (vanilla JS, `@tanstack/table-core` via CDN).
- Python sends page data to JS via `ui.run_javascript()`; JS emits events back via NiceGUI bridge.
- Search filtering is a pure function in `services/search.py` — no UI dependencies.
- JS files under `ui/js/` are read from disk on **every page request**, by the component that
  needs them, and injected as an inline `<script>`: `gene_table.js` through
  `ui.add_head_html()` (its definitions have to reach global scope), `drawer_toggle.js` and
  `token_editor.js` through `ui.add_body_html()`. So editing one takes effect on the next
  page load with no restart -- but it is also a file read per visitor.
- `schema.py` is the single definition of the field-metadata contract — both `config/loader.py`
  and the `db/` tooling parse through `FieldMeta`. Don't duplicate the key list.
- Resource paths come from `paths.py`. Never hardcode `Path(__file__).parent.parent...`.
- **User lists (My lists) live in the visitor's browser only** -- `localStorage` key
  `genetrek.my_lists`, written by `ui/js/my_lists.js`. The server gets them over the page's
  websocket while a tab is open, keeps them in that tab's `state`, and must never write,
  cache or log them: no `app.storage`, no cookies, no `ui.upload` (it spools files over
  1 MiB to disk and caps size only client-side -- imports are read in the browser and sent
  in chunks). Share links put the genes after `#lists=`, which no server ever receives.
  Lists are null-free Boolean columns named `mylist:<id>` (`:` keeps them from colliding
  with a parquet column) on `state["base_df"]`; per-tab code starts from `base_df`, never
  from `config.df`, and `AppConfig` is never mutated. Components that offer lists subscribe
  to `state["on_lists_change"]`. The three message-size limits are pinned between
  `services/user_lists.py` and `my_lists.js` by `tests/test_user_lists.py`.
- **The main area has two views, Table and Compare**, switched by `ui.tabs` toggling
  visibility -- not `ui.tab_panels`, which unmounts the inactive panel and with it the
  TanStack container and `window._geneTableInstance`. The Compare view computes nothing
  while hidden; changes only mark it stale. Every number comes from `services/compare.py`,
  whose `fisher_2x2` is also what the drawer's enrichment uses, so the two cannot disagree.
- **Every comparison has an explicit background** (`AppConfig.backgrounds`, from
  `config/backgrounds.yaml`; the first is the default, an entry with no `column` is every
  gene). Before it existed the drawer's label said "protein-coding" while the test
  counted all 45,083 rows. The YAML names the column, so no identifier literal enters code.
- **Null is "not measured", never "no".** A boolean or numeric column computed from a
  measurement is null outside the genes its source measured. The `universe` metadata key
  says which genes those are; the count is the non-null rows and is never stored.
  - Compare runs on **one joint universe**: the background ∩ the measured genes
    (`GeneSet.measured`) of every set with partial coverage
    (`compare.effective_universe`). It reports `not_measured` separately from
    `outside_background`.
  - The drawer's enrichment drops the list's unmeasured genes.
  - The table renders such a null as a grey "—" (`markNull`).
  - TSV downloads write `NA`.
  - Derived rules use Kleene logic.
  - Never `fill_null(False)` a measured column before counting.
- **Comparison statistics are pinned to reference implementations.** `services/odds.py`
  reimplements scipy's conditional odds ratio (scipy takes ~1 s per large table, 66 pairs
  per 12-set comparison) and statsmodels' Mantel-Haenszel/CMH (not a dependency);
  `tests/test_odds.py` holds them to those libraries. Every pair is reported crude *and*
  adjusted for the first `covariates:` entry of `backgrounds.yaml` (LOEUF decile, strata
  computed within the background plus a "none" stratum) -- side by side, because the gap
  is the finding. The LOEUF strata are computed within the *analysed* universe, so a
  coverage restriction re-bins them.
- **Gene lists reach the API by POST only** (`/api/compare`, `/api/convert`): bodies are
  not in access logs, URLs are. Nothing is stored. `request.json` in every Compare
  download is the exact `POST /api/compare` body (database lists by field, anything else
  inlined as keys), and `tests/test_api_compare.py` replays one.
- **Figures are publication exports**: `compare_figures` builds one Plotly figure per kind
  from a `FigureStyle`; `export_style(width_mm)` is the same code at Arial 7 pt / 0.5 pt
  lines / Okabe-Ito, sized in mm at 96 px per inch (PNG `scale = dpi / 96`). The browser
  draws and downloads it (`compare.js`); the server renders no image and writes no file.
  Tables leave through `ui.download.content` (websocket) for the same reason.
- **Search links**: `?search=` / `#search=` (+ `genome=`) open the table filtered as if the
  tokens were typed; `services.search.parse_search_link` reads them, the drawer's
  `state["show_search_link"]` runs them and fills the token editor
  (`window.setSearchTokens`). Prefer the fragment for anything patient-derived: it never
  reaches a server. Exact symbol search resolves withdrawn/alias symbols through the gene
  index (`services.lookup.symbol_resolver`), as the list dialogs and `/api/convert` do.
- NiceGUI passes the event to any handler that takes a parameter, so `lambda i=x: f(i)`
  receives the event as `i`. Write `lambda _, i=x: f(i)`; a test scans `ui/` for it.
- The parquet and `fields_description.yaml` are one unit: mutate them via `db.Database`,
  which validates, backs up, and swaps both together. Never write one without the other.

## Development

- **Package manager**: uv (`uv sync` / `uv sync --group dev`)
- **Linting**: `uv run ruff check src/ tests/` / `uv run ruff format src/ tests/`
- **Type checking**: `uv run mypy src/genetrek/`
- **Tests**: `uv run pytest` (fixtures build a synthetic resources tree in `tmp_path`;
  `tests/test_real_database.py` guards invariants on the checked-in data)
- **Python**: 3.12+
- **Build backend**: hatchling
- `COM812` is disabled: `ruff format` owns trailing commas and the two rules fight otherwise.

## Common Tasks

- **Build a database from nothing**: `uv run genetrek-db init --output /tmp/gt-new` —
  HGNC complete set + GENCODE coordinates (GRCh38 and lift37 GRCh37, NCBI fallback), spec in
  `resources/config/hgnc_build.yaml`. No credentials needed. It replaces the live tree by
  default, so it always backs up and asks; the backup covers `external_links.yaml` and
  `badges.yaml` too, because `init` regenerates those from the resolved roles. Column names
  say who *authors* a value: `hgnc_*` only for what HGNC curates, `ncbi_id`/`ensg`/
  `omim_id`/`refseq_accession`/`mane_select` for its cross-references.
- **The notebooks are private for the v7 release.** `notebooks/` and `resources/original_file/`
  are not in the public repository (bourgeron-lab/genetrek); they live in the private GitLab
  deploy project and become public with v8. The two entries below describe them for whoever
  has that copy. Write v8's notebooks to be publishable: no committed outputs, no local
  paths or e-mail addresses, no reads of files that cannot be published.
- **Worked example of build + annotate**: `notebooks/create_20260903.ipynb` — builds the
  backbone with `genetrek.db.build`, then adds 45 annotation columns from an annotated HGNC
  complete set into `resources/processed_data/staging/resources_20260903/`. Resolution is by
  `hgnc_id`, never symbol (175 genes changed symbol between the two releases). Its markdown
  cells record which source columns were rejected and why. Three tables drive everything
  downstream: `UPDATES` (imported), `DERIVED` (a rule) and `LEGACY` — eight columns only the
  2024 export has, joined on **`ncbi_id`**, because that file's `hgnc_id` is null on 30% of its
  rows. That join is asserted, not assumed: the 2024 file also carries two columns the 2026 one
  gave us, and reproducing them exactly is what licenses the other eight.
- **Re-running the notebook applies metadata only on a fresh build**: preflight routes a field
  already in `db.fields` to `already` and skips it, so editing a description and re-running Part
  B over an existing staged tree changes nothing. `REBUILD = True` from cell 4 is the only path.
- **Add a new column**: `uv run genetrek-db add <file> --id-type <type> --dry-run`, then drop
  `--dry-run`. See [docs/database-maintenance.md](docs/database-maintenance.md). Do **not**
  hand-edit `fields_description.yaml` and re-run the notebook — the notebook is a from-scratch
  bootstrap that writes to `staging/`, not the way to add a field.
- **Change a field's metadata**: `uv run genetrek-db set <field> --visibility secondary`
- **Categorise a column**: `uv run genetrek-db set <field> --category "NDD gene lists"
  --subcategory Autism`. The category must be declared in `resources/config/categories.yaml`,
  whose **list order is the group order** — an undeclared category still sorts, but last and
  ungrouped, and `genetrek-db validate` warns. `uv run genetrek-db categories` prints the
  taxonomy with per-group counts. Subcategories are alphabetical within a category unless it
  declares an explicit `subcategories` list. Editing the order itself is a hand edit of that
  file, as for `badges.yaml`.
- **Reorder the columns**: `uv run genetrek-db set <field> --priority 25`. Column order is
  `category`, then `subcategory`, then `priority` ascending, then alphabetical by `fullname`,
  with unset sorting last — *not* the parquet's column order and not the YAML's key order
  (`Database.save()` rewrites that to match the parquet on every write, so moving an entry up
  the file does nothing). One ordering drives both the table's columns and the selector tree.
  `visibility` still decides *whether* a column shows; the rest decide *where*. `priority` is
  a position **within one subcategory**, not across the table — it restarts at 10 in each
  group, so the same value in two categories means nothing. The sort lives in
  `schema.order_fields` / `schema.group_fields` / `schema.in_display_order`.
- **Careful with the first column**: `ui/styles.py` styles the sticky, right-aligned first
  column *positionally*, so whichever column sorts first inherits it. It must be the `symbol`
  role; `validate` warns when a category or priority change takes that slot. The selector holds
  up its end: `column_selector._with_locked` puts `roles.symbol` back into every selection and
  `_tree_nodes` omits it from the tree, so no tick — not even "None" — can take that slot away.
  Both are pure and tested in `tests/test_column_tree.py`.
- **Rename a field**: `uv run genetrek-db rename <old> <new>` — `set` cannot do this; a key is
  also the parquet column, a possible badge key, a link placeholder, and sometimes a name in
  code. Identifier and coordinate columns rename freely (their `id_type` follows them, so
  search/enrichment/resolution follow too). Names in `schema.CODE_DEPENDENT_COLUMNS` are
  refused; update the code first, then `--force`.
- **Adding a column name to code**: prefer a **role** over a literal. If the column is an
  identifier or a coordinate, give it an `id_type` and read it from `config.roles` /
  `db.roles` — that is what `schema.resolve_roles()` is for. Only when a component genuinely
  needs one specific measurement by name (as the constraint plot needs `shet_mean` and
  `loeuf`) add it to
  `CODE_DEPENDENT_COLUMNS`, which is the inventory of the remaining coupling.
- **Check a gene list before adding it**: `uv run genetrek-db resolve <file>`
- **Alias a versioned family**: `uv run genetrek-db alias hc_ndd hc_ndd_v7` — an explicit
  pointer stored as `aliases: [...]` on the target field, never auto-resolved to "latest".
  Read ops (`show`/`get`/`db[...]`) accept aliases; `add`/`remove`/`rename` require the real
  name. The API's **lookup** endpoints resolve aliases and echo both names
  (`field` + `requested_as`), which was the condition set when this was deferred;
  `/api/data` keeps its exact-name contract.
- **Refresh the shipped alias index**: `uv run genetrek-db build-aliases` — distils HGNC's
  `alias_symbol`/`prev_symbol` columns into `resources/processed_data/gene_aliases.parquet`
  (~1 MB, 103k rows, committed). `resources/original_file/` is gitignored and excluded from
  the image, so this file is a **deployment's only alias coverage**: without it `KIAA1650`
  does not resolve to `SHANK3` and `/api/convert` reports `symbol_coverage: exact-only`.
  `genetrek-db init` writes it from the same HGNC release it built from. Re-run it whenever
  the parquet's gene set changes — `validate` warns, and
  `test_real_database.py` fails in CI, when the recorded gene count stops matching.
- **Brain expression columns**: `notebooks/brain_data/` holds three notebooks (Jiang 2020
  proteome TS, BrainSpan prenatal bias, GTEx v11 brain level/specificity) and their shared
  `brain_common.py`. Each downloads its source once into `resources/original_file/brain_data/`
  (with a sha256 sidecar), resolves ensg then ncbi (never symbol), stages with
  `missing="keep"`, sets `universe`, and records each flag's numeric parent as
  `derived_op: external`. They open the live tree with `DRY_RUN = True`; run headless with
  `uv run --group notebooks jupyter nbconvert --execute --ExecutePreprocessor.kernel_name=python3`.
  GTEx: one brain definition for every column -- 10 regions, one per GTEx duplicate pair
  (Frontal Cortex BA9 not Cortex, Cerebellar Hemisphere not Cerebellum), spinal cord unused;
  tau runs over ~30 pseudo-tissues (multi-site tissues collapsed by max). Collapsing does not
  change the brain-vs-rest ratio (max of maxes); which regions make up "brain" does.
- **Express a roll-up as a rule**: `uv run genetrek-db derive <name> --from a --from b` —
  a boolean union/intersection of other boolean columns, stored as `derived_op`/`derived_from`
  in the metadata. The column still lands in the parquet; the rule buys drift detection
  (`validate` errors when stored values stop matching) and `genetrek-db recompute-derived`
  after a source is refreshed. Sources must be boolean; nulls follow **Kleene** logic (a
  union is null unless some source is True or all are measured False). `save` never
  recomputes on its own. `derived_op: external` only records a parent (provenance) and is
  never evaluated (`FieldMeta.is_rule` is False for it).
- **Per-column licences**: every column declares `licence` (an SPDX id where one exists).
  Set it with `uv run genetrek-db set <field> --licence CC-BY-4.0`, then regenerate the file
  that ships with the code and the data deposits:
  `uv run genetrek-db licences --write DATA_LICENCES.md`. `test_real_database.py` fails when
  a column has none or the committed file is stale.
- **Add a badge type**: add to `resources/config/badges.yaml`, add the column to
  `schema.BADGE_RENDERED_COLUMNS`, and update rendering in `ui/js/gene_table.js`. All three:
  the YAML alone does nothing (`genetrek-db validate` warns about this). Both may name an
  **alias** instead of a column (`schema.badge_key`): `inheritance` is the alias of the current
  `inheritance_v*`, so moving the alias to a new version moves the badge with it.
- **Add an external link**: add to `resources/config/external_links.yaml` (uses `{column_name}`
  placeholders; the referenced column must exist or validation fails)
- **Query the API**: `/api/fields` lists columns, `/api/fields/{field}` adds coverage,
  `/api/fields/{field}/genes[/tsv]` gives gene→value pairs (`?drop_empty=true` on a boolean
  is a gene list's membership), `/api/genes/{gene}` takes any identifier and `?columns=`
  reaches **hidden** columns by name (the only way to read e.g. `moeuf`/`soeuf`),
  `/api/genes/{gene}/genesets` lists every boolean column true for a gene, and
  `/api/convert` resolves identifiers. `/docs` is the generated reference;
  `resources/content/api-reference.md` is the prose one, served at `/api-reference`. Endpoints are named after roles,
  never after columns — nothing here belongs in `CODE_DEPENDENT_COLUMNS`.
- **Add a prose page**: one `ContentPage` entry in `src/genetrek/content.py` and one
  `resources/content/<slug>.md`. Nothing else: the loader reads every declared slug, the
  registrar builds a page per entry, the header menu is built from the same tuple, and
  `genetrek-db init` carries the whole `content/` directory. A declared page with no file
  is a `load_config` exit at startup, a `validate` warning, and a CI failure in
  `tests/test_content_pages.py` — deliberately all three, because the failure modes differ.
  Routes must avoid `/api*` and `/docs`; `tests/test_content_pages.py` asserts that.
  A page can also draw a computed section below its prose (`_COMPUTED_SECTIONS` in
  `ui/content.py`): the changelog's version table, and `/columns`, the column reference --
  built from `config.fields`/`config.categories`/`config.df` per request (hidden columns
  included, marked "API only"), so adding or editing a column needs no edit there.
  `wide=True` on a `ContentPage` gives a table page a 1400 px card instead of 900 px.
- **Update the changelog**: edit `resources/content/changelog.md` for the narrative. Do
  **not** type the version-comparison numbers — `services/versions.py` computes them from
  the parquet on every page load, seeded from the **aliases** rather than a version regex
  (a regex mistakes `inheritance_panelapp_20260617`, which is Categorical, for a gene-list
  version and raises). Two of the five hand-written numeric claims in
  `fields_description.yaml` have already drifted, which is why.
- **Change My lists**: storage format, codec and file format are in `services/user_lists.py`
  (bump `FORMAT` only for a change an older reader would misread -- it refuses newer
  formats rather than guessing); dialogs in `ui/components/my_lists.py`; the privacy promise
  users read is `resources/content/my-lists.md`, so keep it true when behaviour changes.
- **Change the comparison backgrounds**: edit `resources/config/backgrounds.yaml` (hand
  edit; `genetrek-db validate` warns about an entry whose column is missing, and `init`
  carries the file over). **Change a statistic**: `services/compare.py` / `services/odds.py`,
  then -- all together, because papers will quote them -- the prose in
  `resources/content/documentation.md` ("Comparing gene sets"), the API's response
  description in `resources/content/api-reference.md`, the generated methods text in
  `services/compare_export.py`, and `HEATMAP_EXPLANATIONS` in `services/compare_figures.py`.
  A new `CompareRequest`/`CompareSet` key must be documented in `api-reference.md`;
  `tests/test_api_compare.py` fails until it is.
- **Modify search**: edit `services/search.py`
- **Modify table rendering**: edit `ui/js/gene_table.js` (client) or `ui/components/gene_table.py` (bridge)
