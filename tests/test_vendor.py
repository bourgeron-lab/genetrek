"""Nothing the page runs comes from a third-party host, and the vendored file is
the published one."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

import genetrek_server
from genetrek_server.ui.components import gene_table

SRC = Path(genetrek_server.__file__).parent
ROOT = SRC.parents[1]


def test_the_vendored_file_is_the_published_build():
    readme = (gene_table.VENDOR_DIR / "README.md").read_text(encoding="utf-8")
    recorded = re.search(
        rf"`{re.escape(gene_table.TANSTACK_FILE)}`.*`([0-9a-f]{{64}})`", readme
    )
    assert recorded, "README.md records no sha256 for the vendored file"
    data = (gene_table.VENDOR_DIR / gene_table.TANSTACK_FILE).read_bytes()
    assert hashlib.sha256(data).hexdigest() == recorded.group(1)
    assert b".TableCore=" in data


def test_its_licence_ships_with_it():
    licence = gene_table.VENDOR_DIR / "LICENSE-tanstack-table-core.txt"
    assert "MIT License" in licence.read_text(encoding="utf-8")


def test_the_route_serves_it():
    app = FastAPI()
    app.mount(gene_table.VENDOR_ROUTE, StaticFiles(directory=gene_table.VENDOR_DIR))
    response = TestClient(app).get(
        f"{gene_table.VENDOR_ROUTE}/{gene_table.TANSTACK_FILE}"
    )
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "app.mount(VENDOR_ROUTE" in (SRC / "app.py").read_text(encoding="utf-8")


def test_no_script_is_loaded_from_a_cdn():
    offenders = [
        f"{path.relative_to(SRC)}: {host}"
        for path in SRC.rglob("*")
        if path.suffix in {".py", ".js"} and "vendor" not in path.parts
        for host in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "esm.sh")
        if host in path.read_text(encoding="utf-8")
    ]
    assert not offenders


def test_python_version_matches_the_image():
    pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    image = re.search(r"ARG PYTHON_VERSION=(\S+)", (ROOT / "Dockerfile").read_text())
    assert image is not None
    assert pinned == image.group(1)
