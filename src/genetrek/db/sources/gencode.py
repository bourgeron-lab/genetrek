"""Gene coordinates from GENCODE, for GRCh38 and GRCh37 in one release.

GENCODE publishes the current annotation on GRCh38 and, separately, that *same*
annotation backmapped onto GRCh37 (``lift37``). The lift37 files keep the GRCh38
gene identifiers, so both assemblies join on one key -- no ID mapping, no symbol
matching, and both coordinate systems describe the same gene set.

Ensembl's own REST API can supply coordinates too, but a 1000-ID POST measured
50.8 s, which is ~70 minutes for two assemblies. Streaming the GTFs is minutes,
and the derived table is cached so a rebuild is instant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from genetrek.db.sources.fetcher import FetchError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from genetrek.db.sources.fetcher import Fetcher

_ROOT = "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human"

#: GTF path per assembly, relative to a release directory.
_PATHS = {
    "GRCh38": "gencode.v{release}.annotation.gtf.gz",
    "GRCh37": "GRCh37_mapping/gencode.v{release}lift37.annotation.gtf.gz",
}

#: Assemblies this module can supply, newest coordinate system first.
ASSEMBLIES: tuple[str, ...] = ("GRCh38", "GRCh37")

#: How many releases back to look before giving up.
_MAX_PROBES = 5

_RELEASE_RE = re.compile(r"release_(\d+)\b")
_GENE_ID_RE = re.compile(r'gene_id "([^"]+)"')
_GENE_NAME_RE = re.compile(r'gene_name "([^"]+)"')
#: A GENCODE gene_id carries a version and, in lift37, a mapping counter or a
#: ``PAR_Y`` marker: ``ENSG00000308415.1_2``, ``ENSG00000223274.1_PAR_Y``. Bare
#: ENSG accessions contain neither ``.`` nor ``_``, so cutting at the first of
#: either recovers the accession.
_ID_SUFFIX_RE = re.compile(r"[._].*$")

#: Fields a GTF line must have before it can be a gene feature.
_GTF_FIELDS = 9

#: Column layout of a cached gene table.
GENE_SCHEMA: dict[str, pl.DataType] = {
    "ensg": pl.String(),
    "gene_name": pl.String(),
    "chrom": pl.String(),
    "start": pl.Int64(),
    "end": pl.Int64(),
    "strand": pl.String(),
}


@dataclass(frozen=True)
class GencodeRelease:
    """A GENCODE release known to carry both assemblies."""

    release: int
    urls: dict[str, str]

    def url(self, assembly: str) -> str:
        """The GTF URL for one assembly."""
        if assembly not in self.urls:
            msg = f"release {self.release} has no {assembly} annotation"
            raise FetchError(msg)
        return self.urls[assembly]


def url_for(release: int, assembly: str) -> str:
    """Build the GTF URL for a release and assembly."""
    if assembly not in _PATHS:
        msg = f"unknown assembly {assembly!r}; expected one of {sorted(_PATHS)}"
        raise FetchError(msg)
    return f"{_ROOT}/release_{release}/{_PATHS[assembly].format(release=release)}"


def resolve_release(
    fetcher: Fetcher,
    release: int | None = None,
    assemblies: Iterable[str] = ASSEMBLIES,
) -> GencodeRelease:
    """Find a release that actually has every requested assembly.

    A brand-new release can appear before its ``GRCh37_mapping`` directory does,
    so candidates are probed rather than assumed: taking the newest number and
    trusting it would intermittently produce a GRCh38-only build.

    Args:
        fetcher: Where to send the listing and HEAD requests.
        release: A specific release, or ``None`` to take the newest complete one.
        assemblies: Which assemblies must be present.

    Raises:
        FetchError: If no candidate carries them all.
    """
    wanted = list(assemblies)
    candidates = [release] if release is not None else _list_releases(fetcher)

    tried: list[str] = []
    for candidate in candidates[:_MAX_PROBES]:
        urls: dict[str, str] = {}
        for assembly in wanted:
            url = url_for(candidate, assembly)
            try:
                fetcher.head(url)
            except FetchError:
                tried.append(f"release {candidate}: no {assembly}")
                break
            urls[assembly] = url
        else:
            return GencodeRelease(release=candidate, urls=urls)

    detail = "; ".join(tried) or "no releases listed"
    msg = f"no GENCODE release carries {wanted} ({detail})"
    raise FetchError(msg)


def _list_releases(fetcher: Fetcher) -> list[int]:
    """Release numbers from the GENCODE listing, newest first."""
    listing = fetcher.get_text(f"{_ROOT}/")
    found = {int(m) for m in _RELEASE_RE.findall(listing)}
    if not found:
        msg = f"could not read any release_N directory from {_ROOT}/"
        raise FetchError(msg)
    return sorted(found, reverse=True)


def fetch_genes(
    fetcher: Fetcher,
    cache_dir: Path,
    release: GencodeRelease,
    assembly: str,
    *,
    refresh: bool = False,
) -> Path:
    """Stream a GTF, keep its gene features, and cache the result.

    Only the ~1.5 MB derived table is kept, not the ~150 MB GTF: the raw file has
    no other use here, and caching it would add hundreds of megabytes per
    release to the repository.
    """
    target = cache_dir / f"gencode_v{release.release}_{assembly.lower()}.parquet"
    if target.is_file() and not refresh:
        return target

    rows = list(_gene_rows(fetcher.stream_lines(release.url(assembly), gunzip=True)))
    if not rows:
        msg = f"no gene features found in the {assembly} GTF for v{release.release}"
        raise FetchError(msg)

    cache_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=GENE_SCHEMA, orient="row").write_parquet(target)
    return target


def _gene_rows(
    lines: Iterable[str],
) -> Iterable[tuple[str, str | None, str, int, int, str]]:
    """Yield one tuple per gene feature in a GTF stream."""
    for raw in lines:
        if not raw or raw[0] == "#":
            continue
        parts = raw.split("\t", 9)
        if len(parts) < _GTF_FIELDS or parts[2] != "gene":
            continue
        match = _GENE_ID_RE.search(parts[8])
        if match is None:
            continue
        name = _GENE_NAME_RE.search(parts[8])
        yield (
            _ID_SUFFIX_RE.sub("", match.group(1)),
            name.group(1) if name else None,
            parts[0].removeprefix("chr"),
            int(parts[3]),
            int(parts[4]),
            parts[6],
        )


def read(path: Path) -> pl.DataFrame:
    """Read a cached gene table."""
    return pl.read_parquet(path)
