"""What can go wrong talking to a GeneTrek server.

Every error the server answers carries a ``code`` (API 1.1); each code that a
caller would want to catch on its own has an exception here, and the rest
arrive as :class:`ApiError` with ``.code`` set.
"""

from __future__ import annotations


class GeneTrekError(Exception):
    """Base class of every error this package raises."""


class ConfigError(GeneTrekError):
    """The configuration names a server that is not defined, or cannot be read."""


class ConnectionFailed(GeneTrekError):  # noqa: N818 - reads as what happened
    """The server could not be reached, even after retrying."""


class ServerTooOld(GeneTrekError):  # noqa: N818
    """The server speaks an API version older than a method needs."""

    def __init__(self, url: str, found: str, needed: str, what: str) -> None:
        self.url, self.found, self.needed = url, found, needed
        super().__init__(
            f"{what} needs GeneTrek API {needed}, but {url} speaks {found}. "
            f"genetrek.pasteur.fr serves API {needed} from GeneTrek v8; to use it "
            f"now, point the client at a newer server (base_url=, server= or "
            f"$GENETREK_URL)."
        )


class ApiError(GeneTrekError):
    """The server answered with an error.

    Attributes:
        status: The HTTP status.
        code: The server's error code (``unknown_field``, ``ambiguous_gene``,
            ...), or ``""`` from a server older than API 1.1.
        detail: The server's message, or its list of validation problems.
    """

    def __init__(self, status: int, code: str, detail: object) -> None:
        self.status, self.code, self.detail = status, code, detail
        label = f" [{code}]" if code else ""
        super().__init__(f"HTTP {status}{label}: {detail}")


class UnknownField(ApiError):  # noqa: N818
    """No column has this name or alias."""


class UnknownGene(ApiError):  # noqa: N818
    """No gene matches the identifier."""


class AmbiguousGene(ApiError):  # noqa: N818
    """The identifier matches several genes."""


class NotAGeneList(ApiError):  # noqa: N818
    """The column is not a gene list (a Boolean column)."""


#: Which exception each server code raises; any other code is an ApiError.
BY_CODE: dict[str, type[ApiError]] = {
    "unknown_field": UnknownField,
    "unknown_gene": UnknownGene,
    "ambiguous_gene": AmbiguousGene,
    "not_a_gene_list": NotAGeneList,
}

#: How an API 1.0 server's errors map, by status, where it sent no code.
_LEGACY_BY_STATUS: dict[int, type[ApiError]] = {409: AmbiguousGene}


def from_response(status: int, body: object) -> ApiError:
    """The exception for an error response's status and JSON body."""
    code, detail = "", body
    if isinstance(body, dict):
        code = str(body.get("code") or "")
        detail = body.get("detail", body)
    cls = BY_CODE.get(code) or (_LEGACY_BY_STATUS.get(status) if not code else None)
    return (cls or ApiError)(status, code, detail)
