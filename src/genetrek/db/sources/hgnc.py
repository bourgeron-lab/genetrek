"""The HGNC complete set: one gene record per approved symbol.

HGNC publishes the whole set as a single TSV, so there is no reason to use its
REST API for a full build. No credential is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from genetrek.db.sources.fetcher import FetchError

if TYPE_CHECKING:
    from pathlib import Path

    from genetrek.db.sources.fetcher import Fetcher

_BUCKET = "https://storage.googleapis.com/public-download-files/hgnc"

#: Always the newest release.
LATEST_URL = f"{_BUCKET}/tsv/tsv/hgnc_complete_set.txt"

#: A specific release. HGNC keeps monthly files for a year and quarterly ones
#: indefinitely, so a pinned date may live under either.
ARCHIVE_URLS = (
    f"{_BUCKET}/archive/archive/monthly/tsv/hgnc_complete_set_{{date}}.txt",
    f"{_BUCKET}/archive/archive/quarterly/tsv/hgnc_complete_set_{{date}}.txt",
)

#: Columns resolution needs, whatever the build config selects.
REQUIRED_COLUMNS = ("hgnc_id", "symbol")

_RELEASE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HTTP_DATE_RE = re.compile(
    r"(?P<day>\d{2}) (?P<mon>\w{3}) (?P<year>\d{4})",
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ],
        start=1,
    )
}


@dataclass(frozen=True)
class HgncRelease:
    """Where a complete set came from, for the record."""

    url: str
    #: ``YYYYMMDD``, from the pinned date or the server's ``Last-Modified``.
    release: str
    cached: Path

    @property
    def filename(self) -> str:
        """The cache filename, matching the convention in ``original_file/``."""
        return f"hgnc_complete_set_{self.release}.txt"


def _stamp_from_last_modified(headers: object) -> str | None:
    """Turn an HTTP ``Last-Modified`` header into a ``YYYYMMDD`` stamp."""
    value = str(dict(headers).get("last-modified", ""))  # type: ignore[call-overload]
    match = _HTTP_DATE_RE.search(value)
    if match is None or match["mon"] not in _MONTHS:
        return None
    return f"{match['year']}{_MONTHS[match['mon']]:02d}{match['day']}"


def resolve_release(
    fetcher: Fetcher,
    release: str = "latest",
) -> tuple[str, str]:
    """Return the URL and ``YYYYMMDD`` stamp for a release.

    Args:
        fetcher: Where to send the HEAD request.
        release: ``"latest"``, or a ``YYYY-MM-DD`` date present in the archive.

    Raises:
        FetchError: If a pinned date is malformed or absent from the archive.
    """
    if release == "latest":
        stamp = _stamp_from_last_modified(fetcher.head(LATEST_URL))
        if stamp is None:
            msg = f"{LATEST_URL} gave no usable Last-Modified date"
            raise FetchError(msg)
        return LATEST_URL, stamp

    if not _RELEASE_DATE_RE.match(release):
        msg = f"release must be 'latest' or YYYY-MM-DD, got {release!r}"
        raise FetchError(msg)

    for template in ARCHIVE_URLS:
        url = template.format(date=release)
        try:
            fetcher.head(url)
        except FetchError:
            continue
        return url, release.replace("-", "")

    msg = (
        f"no HGNC complete set for {release} in the monthly or quarterly archive. "
        f"Monthly files older than a year are deleted; try a quarterly date."
    )
    raise FetchError(msg)


def fetch(
    fetcher: Fetcher,
    cache_dir: Path,
    release: str = "latest",
    *,
    refresh: bool = False,
) -> HgncRelease:
    """Download a complete set into ``cache_dir``, or reuse what is there.

    The cache filename carries the release date, which is also what
    ``paths.latest_hgnc_file()`` looks for -- so a build incidentally supplies the
    file alias resolution wants.
    """
    url, stamp = resolve_release(fetcher, release)
    target = cache_dir / f"hgnc_complete_set_{stamp}.txt"

    if refresh or not target.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(fetcher.get_text(url), encoding="utf-8")

    return HgncRelease(url=url, release=stamp, cached=target)


def read(path: Path, columns: list[str] | None = None) -> pl.DataFrame:
    """Read a complete set as strings, keeping the requested columns.

    Everything is read as text: HGNC's numeric-looking fields carry meaning as
    identifiers, not numbers, and inferring dtypes here is how ``entrez_id``
    turns into ``1.0``.

    Raises:
        FetchError: If a requested column, or one resolution needs, is absent.
    """
    frame = pl.read_csv(
        path,
        separator="\t",
        infer_schema_length=0,
        truncate_ragged_lines=True,
        quote_char=None,
    )
    # HGNC wraps every multi-valued field in double quotes ("A|B"). Read with
    # quote_char=None, those quotes would land in the values -- they did, in
    # every multi-valued alias, previous symbol, RefSeq, OMIM and MANE entry --
    # so strip the wrapping pair here, and only a wrapping pair.
    frame = frame.with_columns(
        pl.col(column).str.replace(r'^"(.*)"$', "${1}") for column in frame.columns
    )
    wanted = list(columns) if columns else frame.columns
    for needed in REQUIRED_COLUMNS:
        if needed not in wanted and needed in frame.columns:
            wanted.append(needed)

    if absent := [c for c in wanted if c not in frame.columns]:
        msg = (
            f"{path.name} has no column(s) {absent}. "
            f"Available: {sorted(frame.columns)[:12]}..."
        )
        raise FetchError(msg)
    return frame.select(wanted)
