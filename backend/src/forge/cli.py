"""`forge` command line: one image, several process roles (ADR-001)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from forge.core.settings import get_settings

SOURCE_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _backend_dir() -> Path:
    """Directory holding alembic.ini: the working directory in the image, else the source tree."""
    for candidate in (Path.cwd(), SOURCE_BACKEND_DIR):
        if (candidate / "alembic.ini").is_file():
            return candidate
    raise FileNotFoundError("alembic.ini not found in the working directory or source tree")


def _api(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "forge.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        access_log=False,
        log_config=None,
    )
    return 0


def _worker(args: argparse.Namespace) -> int:
    from forge.workers.runner import run_worker

    asyncio.run(run_worker(args.queue))
    return 0


def _worker_health(_args: argparse.Namespace) -> int:
    from forge.workers.runner import worker_is_alive

    return 0 if worker_is_alive() else 1


def _migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    backend_dir = _backend_dir()
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    command.upgrade(cfg, args.revision)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge")
    sub = parser.add_subparsers(dest="command", required=True)

    api = sub.add_parser("api", help="run the HTTP API")
    api.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container entrypoint
    api.add_argument("--port", type=int, default=8000)
    api.set_defaults(func=_api)

    worker = sub.add_parser("worker", help="run a Temporal worker")
    worker.add_argument("queue", choices=["runs", "ingest"])
    worker.set_defaults(func=_worker)

    health = sub.add_parser("worker-health", help="exit 0 if the local worker is alive")
    health.set_defaults(func=_worker_health)

    migrate = sub.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("revision", nargs="?", default="head")
    migrate.set_defaults(func=_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    get_settings()  # fail fast on invalid environment
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
