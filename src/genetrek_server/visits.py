"""Visit counting: each page load reported to a self-hosted GoatCounter.

Off unless ``GENETREK_GOATCOUNTER_URL`` names the instance, so local runs, the
tests and anyone else deploying the public chart count nothing.

What leaves the browser is the page's **path and nothing else**. GoatCounter's
own script is not used, because it cannot be made to stop sending the query
string (``q: location.search``, unconditionally) and the referrer (for a move
within GeneTrek, the previous page's full URL) -- and ``?search=`` can hold
patient-derived genes. Ours sends ``p=<location.pathname>``: no query, no
fragment (``#lists=``, ``#search=``), no referrer (neither as a parameter nor
as the ``Referer`` header), no title, no screen size, no cookie. GoatCounter
adds the country from the IP and forgets the IP; see
``resources/content/my-lists.md`` for what visitors are told.

Page loads only. Nothing here or anywhere else reports searches, downloads,
dialogs or API calls; ``tests/test_visits.py`` scans for that.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

ENV_VAR = "GENETREK_GOATCOUNTER_URL"

#: Where GoatCounter runs under its ``-base-path``: a path on this host
#: (``/stats``, the deployed form, same-origin) or an absolute ``https://``
#: URL. Nothing that could close the ``<script>`` the URL is written into.
_URL_RE = re.compile(r"(?:/|https?://)[A-Za-z0-9._~:/-]*")


def counter_url(environ: Mapping[str, str] = os.environ) -> str | None:
    """The GoatCounter base URL from the environment, or ``None`` when unset.

    Raises:
        ValueError: If it is set to something that is not a path or URL. A
            typo should stop the deployment, not silently count nothing.
    """
    value = environ.get(ENV_VAR, "").strip()
    if not value:
        return None
    if not _URL_RE.fullmatch(value):
        msg = f"{ENV_VAR}={value!r} is neither a path (/stats) nor an http(s) URL"
        raise ValueError(msg)
    return value.rstrip("/")


def counter_snippet(url: str | None) -> str:
    """The ``<script>`` reporting one page load to ``url``; empty without one.

    ``mode: no-cors`` lets the same snippet reach an instance on another
    origin; the answer is never read, so an opaque response costs nothing.
    """
    if url is None:
        return ""
    endpoint = json.dumps(f"{url}/count")
    return (
        "<script>\n"
        "(function () {\n"
        "  if (!window.fetch) return;\n"
        f"  fetch({endpoint} + '?p=' + encodeURIComponent(location.pathname), {{\n"
        "    method: 'POST', mode: 'no-cors', credentials: 'omit',\n"
        "    referrerPolicy: 'no-referrer', keepalive: true,\n"
        "  });\n"
        "})();\n"
        "</script>"
    )
