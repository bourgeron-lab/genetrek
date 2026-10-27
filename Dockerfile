# GeneTrek: FastAPI with NiceGUI mounted at "/", served by a single uvicorn
# worker. Built from uv.lock so the image gets the resolved dependency set.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.10.9

# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
# Debian rather than Alpine: the numpy and scipy wheels pinned in uv.lock are
# manylinux_2_28, so on musl uv would fall back to building scipy from source.
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG UV_VERSION
# Installed from PyPI rather than copied from ghcr.io/astral-sh/uv: the existing
# pipeline only ever pulled from docker.io, so ghcr reachability from the
# Pasteur runners is unproven. Pinned because uv must be at least the version
# that wrote uv.lock or `--frozen` refuses to read it.
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1

WORKDIR /app

# Dependencies first, so an application-only commit does not re-resolve and
# re-download ~450 MB of wheels.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# README.md is required: pyproject sets `readme = "README.md"` and hatchling
# reads it at build time. .gitignore matters too -- hatchling applies VCS ignore
# patterns when selecting wheel contents, so shipping it keeps the wheel built
# here identical to one built locally.
COPY README.md .gitignore ./
COPY src/ ./src/
# --no-editable so site-packages holds a real copy and nothing at runtime
# depends on /app/src still being present.
RUN uv sync --frozen --no-dev --no-editable

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="genetrek" \
      org.opencontainers.image.description="GeneTrek: genes and neurodevelopmental disorders" \
      org.opencontainers.image.source="https://github.com/bourgeron-lab/genetrek"

# PYTHONDONTWRITEBYTECODE lets the root filesystem be mounted read-only at no
# startup cost: UV_COMPILE_BYTECODE already wrote every .pyc in the builder, and
# this only stops writing new ones.
# PYTHONFAULTHANDLER turns a segfault inside a native wheel (polars, scipy) into
# a traceback in `kubectl logs` rather than a silent exit 139.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/app/.venv/bin:$PATH" \
    GENETREK_RESOURCES=/app/resources \
    HOME=/tmp

RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid 1000 --no-create-home \
            --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# The webapp's whole runtime data set. resources/original_file/ is excluded by
# .dockerignore: only `genetrek-db` reads it, and the CLI is not what this image
# runs.
COPY resources/config         /app/resources/config
COPY resources/content        /app/resources/content
COPY resources/static         /app/resources/static
COPY resources/processed_data/genetrek.parquet \
     /app/resources/processed_data/genetrek.parquet
# Retired gene symbols only resolve with an alias source, and the 34 MB HGNC
# complete set it comes from is gitignored and excluded above. This ~1 MB
# distillation of it is the deployment's only alias coverage; without it
# `/api/convert` reports `symbol_coverage: exact-only` and a search for a
# withdrawn symbol silently finds nothing.
COPY resources/processed_data/gene_aliases.parquet \
     /app/resources/processed_data/gene_aliases.parquet

# genetrek.parquet and config/fields_description.yaml are mode 0600 in git, and
# COPY preserves the mode. Unfixed, an unprivileged user gets EACCES inside
# load_config() -- which runs at module import, so the container would fail
# before uvicorn ever binds. `a=rX` rather than `COPY --chown` so the image
# still works if the cluster overrides runAsUser to something other than 1000.
RUN chmod -R a=rX /app/resources

USER 1000:1000

EXPOSE 8000

# uvicorn directly rather than the `genetrek` console script: web.py's argparse
# exposes only --host/--port and defaults to 127.0.0.1, which is unreachable
# from a Service.
#
# --workers 1 is load-bearing, not a default left in place. Every browser
# session's UI state is a Python closure inside @ui.page("/"), so a second
# worker would answer a socket.io reconnect from a process that has never seen
# that client.
#
# --forwarded-allow-ips '*' trusts any peer's X-Forwarded-* headers, which is
# acceptable only because the Service is ClusterIP: nothing but the ingress
# controller can reach this port.
CMD ["uvicorn", "genetrek.web:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--timeout-graceful-shutdown", "20"]
