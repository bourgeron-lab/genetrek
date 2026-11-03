# Vendored browser code

Served at `/vendor/` (see `genetrek_server/app.py`), so the table loads nothing
from a third-party host at runtime.

| File | Package | Version | Source | sha256 |
|------|---------|---------|--------|--------|
| `tanstack-table-core-8.21.3.umd.min.js` | `@tanstack/table-core` | 8.21.3 | `build/umd/index.production.js` of the npm tarball (registry integrity `sha512-ldZXEhOB…gCftg==`, checked) | `a0d1d4531c0c4ae7cdaec01f217c724aa969cacaff7f7d8971c334ac97500379` |

The file is byte-identical to the published build: it defines `window.TableCore`.
Licence: MIT, `LICENSE-tanstack-table-core.txt`.

**Pinned to 8.x on purpose.** table-core 9 replaced `createTable` with a
`tableFeatures` API, and `ui/js/gene_table.js` is written against v8. To update
within 8.x: download the tarball from the npm registry, check it against the
registry's `dist.integrity`, copy `build/umd/index.production.js` here under the
new version's name, update `TANSTACK_FILE` in `ui/components/gene_table.py` and
the table above. `tests/test_vendor.py` checks the hash.
