import argparse
import json
from collections.abc import Sequence

from app.core.config import Settings, get_settings


def build_parser(settings: Settings | None = None) -> argparse.ArgumentParser:
    current_settings = settings or get_settings()
    parser = argparse.ArgumentParser(
        prog=current_settings.app_name,
        description="Command-line interface for the micronoc project.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("health", help="Print local health status.")

    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI app with Uvicorn.")
    serve_parser.add_argument("--host", default=current_settings.app_host, help="Bind host.")
    serve_parser.add_argument("--port", type=int, default=current_settings.app_port, help="Bind port.")
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development.",
    )

    return parser


def _run_health() -> int:
    print(json.dumps({"status": "ok"}))
    return 0


def _run_serve(host: str, port: int, reload_enabled: bool) -> int:
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=reload_enabled)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "health":
        return _run_health()
    if args.command == "serve":
        return _run_serve(args.host, args.port, args.reload)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
