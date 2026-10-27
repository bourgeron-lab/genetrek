"""GeneTrek web application entry point.

Exposes ``app`` for uvicorn (``uvicorn genetrek.web:app``) and
a ``main()`` function for the ``genetrek`` CLI command.
"""

from __future__ import annotations

from genetrek.app import app  # noqa: F401 - required for uvicorn discovery


def main() -> None:
    import argparse  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="GeneTrek Web App")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    args = parser.parse_args()

    uvicorn.run(
        "genetrek.web:app",
        reload=args.reload,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
