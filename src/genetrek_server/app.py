from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from nicegui import ui

from genetrek_server import link_preview, media_types, paths
from genetrek_server.api.routes import (
    create_compat_router,
    create_downloads_router,
    create_router,
)
from genetrek_server.config.loader import load_config
from genetrek_server.services.lookup import build_gene_index
from genetrek_server.ui.components.gene_table import VENDOR_DIR, VENDOR_ROUTE
from genetrek_server.ui.content import register_content_pages
from genetrek_server.ui.page import register_main_page

config = load_config()
# One resolver for the API and the UI's list dialogs: it costs ~25 ms and a
# few MB to build, and both must resolve an identifier the same way.
resolver = build_gene_index(config)

app = FastAPI(title="GeneTrek API", description="Read-only API for genomic data")
app.include_router(create_router(config, resolver))

# Everything below must be registered before ``ui.run_with``: it mounts NiceGUI
# at "/", and Starlette matches routes in registration order, so anything added
# afterwards is shadowed by that catch-all and never reached.
app.include_router(create_compat_router())
app.include_router(create_downloads_router())

# Must precede the mount below: StaticFiles reads the process-wide mimetypes
# database when it serves a file, so the registration has to already have run.
media_types.register()

# Warn rather than exit: the companion files are not needed to browse genes, so
# a tree without them should still serve the application. StaticFiles raises at
# construction on a missing directory, hence the guard rather than a try.
_static = paths.static_dir()
if _static.is_dir():
    app.mount("/data", StaticFiles(directory=_static), name="data")
else:
    print(f"Warning: no static directory at {_static}; /data will 404")  # noqa: T201

# Browser code the page would otherwise fetch from a CDN (ui/js/vendor/README.md).
app.mount(VENDOR_ROUTE, StaticFiles(directory=VENDOR_DIR), name="vendor")


# HEAD as well: some crawlers check the image before fetching it, and a HEAD
# that `@app.get` does not answer falls through to NiceGUI's 404.
@app.api_route(
    link_preview.IMAGE_ROUTE, methods=["GET", "HEAD"], include_in_schema=False
)
def link_preview_image() -> FileResponse:
    """The image a shared link's preview card shows (``og:image``)."""
    return FileResponse(
        link_preview.IMAGE,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


register_main_page(config, resolver[0])
register_content_pages(config)

ui.run_with(
    app,
    title="GeneTrek",
    dark=False,
    language="en-US",
    tailwind=True,
)
