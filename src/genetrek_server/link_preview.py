"""Link previews: the ``og:`` and ``twitter:`` tags of every page.

Slack, Teams, LinkedIn, X and mail clients read them to draw a card (title,
description, image) when a GeneTrek link is pasted. Most of them ignore a
relative image or URL, so both need the deployment's own address,
``GENETREK_PUBLIC_URL``; without it the pages carry the title and description
only.

The tags are the same for every visitor and never repeat the request: a
``?search=`` can hold patient-derived genes, and a preview bot fetching the
link must not find them echoed in the HTML. ``og:url`` is the page's route,
never the URL that was asked for.
"""

from __future__ import annotations

import os
import re
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

ENV_VAR = "GENETREK_PUBLIC_URL"

TITLE = "GeneTrek - Association of genes with neurodevelopmental disorders"
DESCRIPTION = (
    "Curated gene lists for neurodevelopmental disorders and per-gene annotations"
    " in one versioned table. Search genes or loci, and compare your own lists"
    " with known NDD genes."
)

#: Drawn from ``docs/link-preview.html``; regenerate it from there.
IMAGE = Path(__file__).parent / "assets" / "link-preview.png"
IMAGE_ROUTE = "/link-preview.png"
IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 630
IMAGE_ALT = "GeneTrek: association of genes with neurodevelopmental disorders."

#: An origin, optionally with a path prefix: nothing that could leave the
#: attribute it is written into.
_URL_RE = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?")


def public_url(environ: Mapping[str, str] = os.environ) -> str | None:
    """The deployment's public address from the environment, or ``None``.

    Raises:
        ValueError: If it is set to something that is not an http(s) URL. A
            typo should stop the deployment, not silently break the previews.
    """
    value = environ.get(ENV_VAR, "").strip()
    if not value:
        return None
    if not _URL_RE.fullmatch(value):
        msg = f"{ENV_VAR}={value!r} is not an http(s) URL"
        raise ValueError(msg)
    return value.rstrip("/")


def preview_tags(base_url: str | None, *, route: str = "/", title: str = TITLE) -> str:
    """The ``<meta>`` tags for the page at ``route``.

    Args:
        base_url: From :func:`public_url`. ``None`` leaves out the URL and the
            image, which previews only honour as absolute addresses.
        route: The page's route, as registered -- never the request's URL.
        title: The page's own title; the main page uses :data:`TITLE`.
    """
    tags = [
        ("name", "description", DESCRIPTION),
        ("property", "og:type", "website"),
        ("property", "og:site_name", "GeneTrek"),
        ("property", "og:title", title),
        ("property", "og:description", DESCRIPTION),
        ("name", "twitter:title", title),
        ("name", "twitter:description", DESCRIPTION),
    ]
    if base_url is not None:
        image = base_url + IMAGE_ROUTE
        tags += [
            ("property", "og:url", base_url + route),
            ("property", "og:image", image),
            ("property", "og:image:type", "image/png"),
            ("property", "og:image:width", str(IMAGE_WIDTH)),
            ("property", "og:image:height", str(IMAGE_HEIGHT)),
            ("property", "og:image:alt", IMAGE_ALT),
            ("name", "twitter:card", "summary_large_image"),
            ("name", "twitter:image", image),
            ("name", "twitter:image:alt", IMAGE_ALT),
        ]
    else:
        tags.append(("name", "twitter:card", "summary"))
    return "\n".join(
        f'<meta {kind}="{key}" content="{escape(value)}">' for kind, key, value in tags
    )
