## Column reference

Every column in the database, with its description and provenance, in the order
the gene table shows them. The table below is built from the database itself
each time this page loads, so it always matches the data being served.

Type in the filter box to narrow the list on any field — a column name, a source,
a word in a description. Click a header to sort; sorting by category or
subcategory keeps the order the table uses.

- **Genes** counts the genes that are *on* a gene list, and the genes carrying a
  value for any other column.
- **Measured** is shown only for columns that did not measure every gene. Outside
  those genes the column is empty because the gene was *not measured*, which is
  not the same as "no" — see [General documentation](/documentation).
- **API only** marks a column the gene table does not offer. It can still be read
  through the API, for example `/api/genes/SHANK3?columns=moeuf`.

The same information is available as a TSV download below, and to programs as
`/api/fields?include_hidden=true&include_counts=true` — see the
[API reference](/api-reference).
