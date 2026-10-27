## My lists

**My lists** keeps gene lists you want to come back to: the current search
results (**Save as list**), genes you paste (**My lists → New from genes**), or
a file you import. A saved list works like a gene list from the database: you can
search with it in the left panel, show it as a column (**Select Columns → My
lists**), and test the current results for enrichment in it in the right panel.

A list is a set of genes and nothing more. It has no values, scores or other
columns. Each list keeps the name you give it and the date and time it was
created, so two lists with the same name can still be told apart.

### Where your lists are kept

**Only in your browser**, in its local storage for this site. GeneTrek has no
accounts, does not know who you are, and **does not store your lists on its
server**.

To use a list, the page has to send it to the GeneTrek server while the tab is
open: searching, enrichment and the table are computed there. The server keeps it
in memory for that connection only. It never writes the list to disk, never logs
it, and forgets it when you close the tab.

Because the browser holds the only copy, a list disappears when:

- you clear this site's data, cookies or history;
- you use a private or incognito window, once that window is closed;
- Safari removes data for sites you have not visited for 7 days;
- you switch to another browser or another computer, because lists are never
  synced between them.

**Export anything important.** Lists that have never been exported are marked
*not backed up* in My lists.

### Exporting and importing

**Export** (one list, a selection, or **Export all**) downloads a `.json` file
that you can read: each gene is listed with its identifier and symbol. **Import
file** reads it back. It also reads a plain text, TSV or CSV file with one gene
per line, using the first column, and checks those genes the same way as pasted
ones: former and alias symbols are mapped to the current gene, and anything
ambiguous or unknown is listed and left out.

Your browser reads the file and sends only its text over the page's connection.
The server does not keep it. Files are limited to 8 MB. An export of every gene
in the database is about 3 MB, and a list of 2,000 genes about 130 kB.

### Sharing a list

**Share** creates a link that contains the list itself. The genes are packed
after the `#` in the link, and browsers never send that part to a server, so the
link does not appear in any server log and GeneTrek stores nothing.

- Anyone who has the link can see the genes. Treat a link to a list built from
  one person's variants with the same care as the list itself.
- Links of up to about 2,000 characters work everywhere (a list of roughly 2,000
  genes). Longer ones may be cut off by some mail or chat clients, and links
  over 8,000 characters are not offered at all. **Download file** always works.
- When someone opens your link, GeneTrek asks them before saving anything. They
  can add the lists to their own, use them for that visit only, or discard them.

### Using your lists through the API

A list can be compared or converted from a script with `POST /api/compare` and
`POST /api/convert` (see [API reference](/api-reference)). You can send it as a
list of genes, as an exported My lists file, or as the token from a share link.
The list goes in the body of the request. It is used to compute the response and
then discarded: never stored, never logged.

Do not put a list you would not want recorded in a URL, for example the `GET`
form of `/api/convert`. Web servers and proxies log URLs.

When you download tables from the **Compare gene sets** tab, the file
`request.json` contains the genes of every list you compared. It lets you, or a
reviewer, reproduce the comparison exactly. Share it as you would the list.

### When the database changes

Lists store stable gene identifiers (HGNC IDs) rather than symbols, so a renamed
gene stays in your list. If a later release withdraws a gene, the list keeps it
and says how many of its genes are *not in this database*. An export still
includes them.
