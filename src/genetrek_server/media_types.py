"""Media types for the genomics files served at ``/data``.

Python's :mod:`mimetypes` does not know the genome-browser formats, and it knows
one of them wrongly: ``.vcf`` is registered as a vCard contact card, so a
bgzf-compressed VCF is announced as ``text/x-vcard; charset=utf-8``. Declaring a
text type -- worse, a charset -- on binary invites a client or proxy to transcode
it, and every one of these files is read by range request: UCSC pulls a bigBed a
few bytes at a time, and igv.js does the same with a VCF through its tabix index.
"""

from __future__ import annotations

import mimetypes

#: Suffixes to force to ``application/octet-stream``.
#:
#: ``.vcf`` is here to displace the vCard mapping, which also fixes ``.vcf.gz``:
#: :func:`mimetypes.guess_type` reports ``.gz`` as an *encoding* and takes the
#: type from the inner suffix.
BINARY_SUFFIXES: tuple[str, ...] = (
    ".bb",
    ".bigBed",
    ".bw",
    ".bigWig",
    ".tbi",
    ".tbx",
    ".vcf",
    ".bam",
    ".bai",
)


def register() -> None:
    """Teach :mod:`mimetypes` about the formats served from ``/data``.

    Idempotent, and global: :class:`~starlette.staticfiles.StaticFiles` consults
    the process-wide database, so this has to run before a file is served rather
    than being passed to the mount.
    """
    for suffix in BINARY_SUFFIXES:
        mimetypes.add_type("application/octet-stream", suffix)
