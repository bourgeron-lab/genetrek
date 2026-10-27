from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from nicegui import ui

from genetrek import media_types, paths
from genetrek.api.routes import create_compat_router, create_router
from genetrek.config.loader import load_config
from genetrek.services.lookup import build_gene_index
from genetrek.ui.content import register_content_pages
from genetrek.ui.page import register_main_page

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

register_main_page(config, resolver[0])
register_content_pages(config)

ui.run_with(
    app,
    title="GeneTrek",
    dark=False,
    language="en-US",
    tailwind=True,
)
