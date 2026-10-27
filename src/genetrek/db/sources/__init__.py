"""Fetchers for the public sources a database is built from.

Every module here talks to exactly one upstream and caches what it derives under
``resources/original_file/``, so a rebuild is fast and a review is offline. All
of them go through :class:`Fetcher`, which tests replace -- nothing in the test
suite touches the network.

None of these sources needs a credential. ``NCBI_API_KEY`` is honoured if set,
purely to lift the eutils rate limit from 3 to 10 requests a second.
"""

from __future__ import annotations

from genetrek.db.sources import coordinates, gencode, hgnc, ncbi
from genetrek.db.sources.fetcher import Fetcher, FetchError, HttpFetcher

__all__ = [
    "FetchError",
    "Fetcher",
    "HttpFetcher",
    "coordinates",
    "gencode",
    "hgnc",
    "ncbi",
]
