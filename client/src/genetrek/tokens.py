"""Splitting typed or pasted text into gene and locus tokens.

Shared by the client and the server (``genetrek_server.services.search``
imports it), so a list read by ``genetrek`` on the command line, by the API and
by the search box is cut the same way.
"""

from __future__ import annotations

import re

#: A locus that still carries thousands separators, so they can be stripped
#: before the token split gets to them.
#:
#: Genome browsers print coordinates with separators -- UCSC, which this
#: application links out to, shows ``chr22:50,674,408-50,733,212`` -- so pasting
#: one back in is an obvious thing to do. Left alone, the split on commas below
#: turns that into ``22:50`` (dropped) plus ``674``, ``733`` and ``212``, which
#: are then read as *NCBI gene IDs* and return two unrelated genes on other
#: chromosomes. A wrong answer offered confidently, so it is worth pre-empting.
#:
#: The span deliberately cannot end on a comma: ``\d(?:[\d,]*\d)?`` starts and
#: ends on a digit, so ``22:100-200,TP53`` keeps its boundary instead of the
#: strip swallowing the following token.
_COMMA_LOCUS_RE = re.compile(
    r"(?:chr)?(?:\d+|X|Y|MT|M):\d(?:[\d,]*\d)?-\d(?:[\d,]*\d)?",
    re.IGNORECASE,
)

#: The constructors a gene list copied out of code is wrapped in: R's ``c(...)``
#: and ``list(...)``, Python's ``set(...)``, ``tuple(...)``, ``frozenset(...)``.
#: Only the name and its parenthesis go; the brackets go with the quotes below.
#: Case-sensitive, so a symbol such as ``C3`` is never mistaken for one.
_LIST_CALL = r"(?<![\w.])(?:c|list|set|frozenset|tuple)\s*\("
_LIST_CALL_RE = re.compile(_LIST_CALL, re.ASCII)

#: The name a pasted list was assigned to (``genes = [...]``,
#: ``genes <- c(...)``). Only removed directly before a list, where it cannot
#: be an identifier; left in, it would be searched as a gene symbol.
_LIST_ASSIGNMENT_RE = re.compile(
    rf"(?<![\w.])[A-Za-z_.][\w.]*\s*(?:=|<-)\s*(?=[\[({{]|{_LIST_CALL})",
    re.ASCII,
)

#: Brackets and quotes around a pasted list and its elements. No identifier or
#: locus contains one, and left in they stick to the first and last tokens:
#: ``['SHANK3', 'CHD8']`` used to search for ``[SHANK3`` and ``CHD8]``, so the
#: genes at either end silently found nothing.
_LIST_WRAPPER_RE = re.compile(r"""[\[\](){}"'`]""")

#: What separates two tokens.
_DELIMITER_RE = re.compile(r"[,;\s]+")


def split_tokens(text: str) -> list[str]:
    """The search tokens in typed or pasted text, in order.

    Tokens are separated by commas, semicolons or whitespace. A list copied out
    of Python or R code -- ``['SHANK3', 'CHD8']``, ``c("SHANK3", "CHD8")``, even
    with its assignment -- reads as the genes it lists. ``splitTokens`` in
    ``ui/js/token_editor.js`` (in the server) mirrors this, so the badges show what is searched;
    the server's ``tests/test_search.py`` runs both over the same cases.
    """
    # Separators inside a locus go before the split, never after: once the text
    # is in pieces the digits are indistinguishable from gene IDs.
    cleaned = _COMMA_LOCUS_RE.sub(lambda m: m.group(0).replace(",", ""), text)
    cleaned = _LIST_ASSIGNMENT_RE.sub(" ", cleaned)
    cleaned = _LIST_CALL_RE.sub(" ", cleaned)
    cleaned = _LIST_WRAPPER_RE.sub(" ", cleaned)
    return [token for token in _DELIMITER_RE.split(cleaned) if token]
