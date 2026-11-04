"""The API's version and its machine-readable errors.

Every ``/api`` error body is ``{"detail": <message>, "code": <code>}``. The
message is for people and may change; the code is for programs (the
``genetrek`` Python client maps each to an exception) and does not. ``detail``
stays what it always was -- a string, or FastAPI's list of validation errors
for a 422 -- so a caller written before the codes existed still reads it.

Both are applied by :class:`VersionedRoute`, the ``/api`` router's route class,
rather than by an application-level exception handler, so they hold wherever
the router is mounted: in the application and in the tests' bare FastAPI apps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from fastapi import Request, Response

#: The API's version. A minor bump adds endpoints, fields or codes and breaks
#: no caller; a major bump would break one. Reported by ``/api/health`` and on
#: every ``/api`` response as :data:`VERSION_HEADER`.
API_VERSION = "1.2"
VERSION_HEADER = "X-GeneTrek-API-Version"

#: Every code an ``/api`` error can carry. ``api-reference.md`` documents each
#: and ``tests/test_api.py`` pins the two together.
ERROR_CODES: dict[str, str] = {
    "unknown_field": "No column has this name or alias.",
    "not_a_gene_list": "The column is not a gene list (a Boolean column).",
    "unknown_gene": "No gene matches the identifier.",
    "ambiguous_gene": "The identifier matches several genes.",
    "unknown_id_type": "The identifier type is not one this database carries.",
    "no_identifiers": "The request named no identifiers.",
    "too_many_ids": "More identifiers than one request accepts.",
    "no_columns": "None of the requested columns exists.",
    "unknown_format": "The download format is not offered.",
    "unknown_genome": "The genome assembly is not one this database carries.",
    "unknown_background": "No comparison background has this name.",
    "invalid_background": "A supplied background cannot be used (too small, or unreadable).",
    "unknown_covariate": "No covariate has this name.",
    "invalid_set": "A comparison set cannot be read (see detail).",
    "invalid_request": "The request body or parameters are malformed.",
    "unavailable": "The server cannot do this now (see detail).",
}

#: The code an error gets when the code raising it named none.
_BY_STATUS = {
    400: "invalid_request",
    404: "unknown_field",
    409: "ambiguous_gene",
    422: "invalid_request",
    503: "unavailable",
}


class ApiError(HTTPException):
    """An ``/api`` error with its code."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        if code not in ERROR_CODES:  # a typo here would ship an undocumented code
            msg = f"undeclared error code {code!r}"
            raise ValueError(msg)
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def _error(status_code: int, code: str, detail: Any) -> JSONResponse:
    return JSONResponse(
        {"detail": jsonable_encoder(detail), "code": code},
        status_code=status_code,
    )


class VersionedRoute(APIRoute):
    """Adds the version header to every response and a code to every error."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handle = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                response = await handle(request)
            except ApiError as exc:
                response = _error(exc.status_code, exc.code, exc.detail)
            except HTTPException as exc:
                code = _BY_STATUS.get(exc.status_code, "invalid_request")
                response = _error(exc.status_code, code, exc.detail)
            except RequestValidationError as exc:
                response = _error(422, "invalid_request", exc.errors())
            response.headers[VERSION_HEADER] = API_VERSION
            return response

        return route_handler
