from __future__ import annotations

import io
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from genetrek import paths
from genetrek.api.compare_routes import register_compare_routes
from genetrek.api.lookup_routes import register_lookup_routes
from genetrek.models.schemas import DataResponse
from genetrek.services import lookup

if TYPE_CHECKING:
    from genetrek.config.loader import AppConfig
    from genetrek.db.gene_index import GeneIndex


def create_router(
    config: AppConfig,
    resolver: tuple[GeneIndex | None, str] | None = None,
) -> APIRouter:
    """Create the API router with access to application config.

    ``resolver`` is a shared :func:`genetrek.services.lookup.build_gene_index`
    result; without one the lookup endpoints build their own.
    """
    router = APIRouter(prefix="/api")

    @router.get("/data", response_model=DataResponse)
    def get_data(
        page: int = Query(1, ge=1),
        per_page: int = Query(20, ge=1, le=100),
        columns: str | None = Query(
            None,
            description="Comma-separated list of columns to include",
        ),
    ) -> DataResponse:
        """Get paginated data."""
        if columns:
            selected_columns = [
                col.strip()
                for col in columns.split(",")
                if col.strip() in config.visible_columns
            ]
        else:
            selected_columns = config.primary_columns

        if not selected_columns:
            raise HTTPException(status_code=400, detail="No valid columns selected")

        filtered_df = config.df.select(selected_columns)
        start = (page - 1) * per_page
        paginated_df = filtered_df.slice(start, per_page)
        data = paginated_df.to_dicts()

        return DataResponse(
            data=data,
            total=len(config.df),
            page=page,
            per_page=per_page,
        )

    @router.get("/download/database")
    def download_database(
        file_format: str = Query(
            "tsv",
            description="File format: tsv or parquet",
        ),
    ) -> StreamingResponse:
        """Download the full NDD database in the specified format."""
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")

        if file_format == "tsv":
            buffer = io.BytesIO()
            config.df.write_csv(buffer, separator="\t")
            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="text/tab-separated-values",
                headers={
                    "Content-Disposition": f"attachment; filename=genetrek-data-{today}.tsv",
                },
            )
        if file_format == "parquet":
            buffer = io.BytesIO()
            config.df.write_parquet(buffer)
            buffer.seek(0)
            return StreamingResponse(
                buffer,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f"attachment; filename=genetrek-data-{today}.parquet",
                },
            )

        raise HTTPException(
            status_code=400,
            detail="Invalid format. Use tsv or parquet.",
        )

    # One resolver for the lookup and the compare endpoints. Built here when
    # the caller did not share one -- a test client, typically.
    resolver = resolver or lookup.build_gene_index(config)
    register_lookup_routes(router, config, resolver)
    register_compare_routes(router, config, resolver)

    @router.get("/health")
    def health() -> dict[str, str | int]:
        """Report that the process came up with its data loaded.

        Deliberately not the NiceGUI page at ``/``: a GET there builds the whole
        element tree, reads ``ui/js/*.js`` off disk, schedules a ``ui.timer`` and
        creates a server-side ``Client`` that then waits ``reconnect_timeout`` for
        a socket.io handshake a kubelet will never send.

        ``config.df`` being addressable at all is the real assertion. It is
        populated by ``load_config()`` at module import, so a missing or
        unreadable resources tree never reaches this line -- the process exits
        first, and the container fails to start rather than serving empty pages.
        """
        return {
            "status": "ok",
            "rows": config.df.height,
            "columns": config.df.width,
            "release": os.environ.get("GENETREK_RELEASE", "unknown"),
        }

    return router


def create_compat_router() -> APIRouter:
    """Routes that keep the previous GeneTrek's public URLs working.

    The Node application published these paths for years and they are cited
    off-site -- the UCSC track hub in particular is fetched by sessions we do
    not control -- so they outlive the implementation behind them. No ``prefix``:
    the whole point is that the URLs are unchanged.
    """
    router = APIRouter(include_in_schema=False)

    @router.get("/downloadVCF")
    def download_vcf() -> FileResponse:
        """Serve the Rolland et al. 2023 VCF archive."""
        archive = paths.static_dir() / "Rolland_NatMed_2023.zip"
        if not archive.is_file():
            raise HTTPException(status_code=404, detail="Archive not available.")
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=archive.name,
        )

    @router.get("/downloadAllData")
    def download_all_data(filetype: str = Query("tsv")) -> RedirectResponse:
        """Redirect the old bulk-download URL onto its current equivalent.

        The old endpoint served a pre-exported file per format; the database is
        now serialised on demand, so this is a redirect rather than a copy.

        The old URL offered ``tsv`` and ``json``, and the JSON export has since
        been removed -- it cost five times the bytes of the TSV for the same
        content. A stale ``?filetype=json`` bookmark therefore lands on the TSV
        rather than on a 400, which is the whole point of keeping this route.
        """
        del filetype  # every accepted value now maps onto the same export
        return RedirectResponse(
            url="/api/download/database?file_format=tsv",
            status_code=307,
        )

    return router
