"""`forge` command line: one image, several process roles (ADR-001)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from forge.core.network import configure_outbound
from forge.core.settings import get_settings

if TYPE_CHECKING:
    from forge.gateway.service import Gateway

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
        # Only the web service (nginx) reaches the API, on the private Compose or cluster
        # network; nginx has already decided which client address to believe. Narrow this to
        # its address if the API port is ever published (FORGE_FORWARDED_ALLOW_IPS).
        forwarded_allow_ips=os.environ.get("FORGE_FORWARDED_ALLOW_IPS", "*"),
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


def _fetch_models(args: argparse.Namespace) -> int:
    """Build time (embedded image) or worker start (`-slim` image); honours the egress proxy."""
    from forge.gateway.service import tokenizers
    from forge.rag.service import docling_models_present, download_docling_models

    if args.docling and not (args.if_missing and docling_models_present()):
        print(f"docling models -> {download_docling_models()}")
    if args.spacy:
        from forge.guardrails import ner

        ner.fetch()
        print(f"spaCy models -> {ner.MODELS_DIR}")
    for spec in args.tokenizer or tokenizers.DEFAULT_TOKENIZERS:
        tokenizers.fetch(spec)
        print(f"tokenizer {spec} -> {tokenizers.MODELS_DIR}")
    return 0


def _migrate(args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    backend_dir = _backend_dir()
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    command.upgrade(cfg, args.revision)
    return 0


def _rotate_kek(_args: argparse.Namespace) -> int:
    from forge.admin.service import secrets
    from forge.core.crypto import Envelope
    from forge.core.db import admin_scope, create_engine, session_factory

    async def run() -> int:
        settings = get_settings()
        if settings.master_key is None:
            raise SystemExit("FORGE_MASTER_KEY is required")
        engine = create_engine(settings)
        try:
            async with admin_scope(session_factory(engine)) as s:
                return await secrets.rotate_all(
                    s, Envelope.from_master_key(settings.master_key.get_secret_value())
                )
        finally:
            await engine.dispose()

    print(f"re-wrapped {asyncio.run(run())} secrets")
    return 0


def _ensure_superuser(args: argparse.Namespace) -> int:
    """Create or reset a Super Admin (break-glass recovery, automation). Needs container access."""
    from sqlalchemy import func, select

    from forge.admin.service import Eula, audit
    from forge.core.context import Actor
    from forge.core.db import admin_scope, create_engine, session_factory
    from forge.iam import passwords
    from forge.iam.models import User

    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else None
    if not password:
        raise SystemExit("provide the password on stdin with --password-stdin")

    async def run() -> str:
        engine = create_engine(get_settings())
        try:
            async with admin_scope(session_factory(engine)) as s:
                user = (
                    await s.execute(
                        select(User).where(func.lower(User.username) == args.username.lower())
                    )
                ).scalar_one_or_none()
                created = user is None
                if user is None:
                    user = User(username=args.username, email=args.email, full_name=args.full_name)
                    s.add(user)
                user.is_super_admin, user.status = True, "active"
                user.password_hash = passwords.hash_password(password)
                user.locked_until, user.failed_logins = None, 0
                user.must_change_password = not args.ready
                if args.ready:
                    user.eula_version = Eula().version  # automation accounts skip first-login steps
                await s.flush()
                await audit.record(
                    s,
                    Actor(id=None, label="cli"),
                    "user.ensure_superuser",
                    "user",
                    user.id,
                    {"username": user.username, "created": created, "ready": args.ready},
                )
                return "created" if created else "updated"
        finally:
            await engine.dispose()

    print(f"super admin {args.username} {asyncio.run(run())}")
    return 0


def _with_gateway(action: Callable[[Gateway], Awaitable[list[str]]]) -> int:
    """Run `action` with a gateway bound to the configured database and Valkey."""
    import redis.asyncio as redis

    from forge.admin.service import SettingsStore
    from forge.core.crypto import Envelope
    from forge.core.db import create_engine, session_factory
    from forge.gateway.service import Gateway

    async def run() -> list[str]:
        settings = get_settings()
        if settings.master_key is None:
            raise SystemExit("FORGE_MASTER_KEY is required")
        engine = create_engine(settings)
        client = redis.from_url(settings.redis_url)
        try:
            sessions = session_factory(engine)
            store = SettingsStore(sessions, None)
            await store.reload()
            gateway = Gateway.build(
                sessions,
                Envelope.from_master_key(settings.master_key.get_secret_value()),
                store,
                client,
            )
            return await action(gateway)
        finally:
            await client.aclose()
            await engine.dispose()

    for line in asyncio.run(run()):
        print(line)
    return 0


def _import_providers(_args: argparse.Namespace) -> int:
    from forge.gateway.seed import import_env

    return _with_gateway(lambda gw: import_env(gw.catalog))


def _test_providers(args: argparse.Namespace) -> int:
    """Test Connection for every active platform model; paid ones (cost > 0) on request."""

    async def run(gw: Gateway) -> list[str]:
        await gw.registry.load()
        lines = []
        for m in sorted(gw.registry.snapshot.models.values(), key=lambda m: m.name):
            if m.project_id is not None or m.status != "active":
                continue
            if args.model and m.name not in args.model:
                continue
            paid = m.cost_input > 0 or m.cost_output > 0
            if paid and not args.include_paid and not args.model:
                lines.append(f"skip  {m.name:32} paid model (use --include-paid)")
                continue
            r = await gw.service.test_connection(m)
            extra = f"dims={r.dimensions}" if r.dimensions else (r.sample or "")[:60]
            status = "ok   " if r.ok else "FAIL "
            detail = extra if r.ok else f"{r.error_code}: {r.detail[:160]}"
            lines.append(f"{status} {m.name:32} {r.latency_ms:6d} ms  {detail}")
        return lines

    return _with_gateway(run)


def _injection_run(args: argparse.Namespace) -> int:
    """Score a detector on the injection dataset (E1.21); exit 1 when a gate is missed."""
    import json

    from forge.guardrails.injection import detectors, runner

    data = runner.load_dataset(args.dataset_version).subset(args.set)
    report = runner.score(detectors.load(args.detector), args.detector, data, samples=args.samples)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(runner.format_report(report))
    failed = []
    if args.min_block_rate is not None and report.block_rate < args.min_block_rate:
        failed.append(f"block rate {report.block_rate:.1%} < {args.min_block_rate:.1%}")
    if args.max_fp_rate is not None and report.false_positive_rate > args.max_fp_rate:
        failed.append(
            f"false-positive rate {report.false_positive_rate:.1%} > {args.max_fp_rate:.1%}"
        )
    for line in failed:
        print(f"GATE FAILED: {line}", file=sys.stderr)
    return 1 if failed else 0


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

    secrets_cmd = sub.add_parser("secrets", help="secret store maintenance")
    secrets_sub = secrets_cmd.add_subparsers(dest="secrets_command", required=True)
    rotate = secrets_sub.add_parser("rotate-kek", help="re-wrap every secret under the current KEK")
    rotate.set_defaults(func=_rotate_kek)

    users_cmd = sub.add_parser("users", help="user maintenance")
    users_sub = users_cmd.add_subparsers(dest="users_command", required=True)
    ensure = users_sub.add_parser("ensure-superuser", help="create or reset a Super Admin")
    ensure.add_argument("--username", required=True)
    ensure.add_argument("--email", required=True)
    ensure.add_argument("--full-name", default="Administrator")
    ensure.add_argument("--password-stdin", action="store_true")
    ensure.add_argument(
        "--ready", action="store_true", help="skip first-login steps (automation accounts only)"
    )
    ensure.set_defaults(func=_ensure_superuser)

    providers_cmd = sub.add_parser("providers", help="model provider maintenance")
    providers_sub = providers_cmd.add_subparsers(dest="providers_command", required=True)
    import_env = providers_sub.add_parser(
        "import-env",
        help="register lab providers from OLLAMA_HOST, GROQ_API_KEY, ... (idempotent)",
    )
    import_env.set_defaults(func=_import_providers)
    test = providers_sub.add_parser("test", help="Test Connection for platform models")
    test.add_argument("--include-paid", action="store_true", help="also test models with a cost")
    test.add_argument("--model", action="append", help="test only this model (repeatable)")
    test.set_defaults(func=_test_providers)

    models_cmd = sub.add_parser("models", help="local model files (Docling, tokenizers)")
    models_sub = models_cmd.add_subparsers(dest="models_command", required=True)
    fetch = models_sub.add_parser(
        "fetch", help="download Docling models and tokenizers into FORGE_MODELS_DIR"
    )
    fetch.add_argument("--docling", action="store_true", help="also the Docling models")
    fetch.add_argument(
        "--spacy", action="store_true", help="also the spaCy models of PII name detection"
    )
    fetch.add_argument(
        "--tokenizer",
        action="append",
        help="tokenizer spec, e.g. hf:BAAI/bge-m3 or tiktoken:o200k_base (repeatable; "
        "default: the built-in list)",
    )
    fetch.add_argument(
        "--if-missing", action="store_true", help="skip the Docling models if already present"
    )
    fetch.set_defaults(func=_fetch_models)

    injection = sub.add_parser("injection", help="prompt-injection test set (E1.21)")
    injection_sub = injection.add_subparsers(dest="injection_command", required=True)
    inj_run = injection_sub.add_parser(
        "run", help="block rate and false-positive rate of a detector on the dataset"
    )
    inj_run.add_argument(
        "--detector",
        default="heuristic",
        help="none, heuristic, guard:<model>@<url> (key in FORGE_GUARD_API_KEY), or "
        "command:<program> (text on stdin, 'block' or 'allow' out)",
    )
    inj_run.add_argument(
        "--set",
        choices=["main", "holdout", "all"],
        default="main",
        help="main = generated, covers the techniques; holdout = hand-written, measures "
        "generalisation",
    )
    inj_run.add_argument("--dataset-version", default="1")
    inj_run.add_argument("--min-block-rate", type=float, help="fail below this block rate (0-1)")
    inj_run.add_argument("--max-fp-rate", type=float, help="fail above this false-positive rate")
    inj_run.add_argument("--samples", type=int, default=5, help="missed/false-positive examples")
    inj_run.add_argument("--json", action="store_true", help="machine-readable report")
    inj_run.set_defaults(func=_injection_run)

    migrate = sub.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("revision", nargs="?", default="head")
    migrate.set_defaults(func=_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "func", None) is _fetch_models:
        os.environ["FORGE_MODELS_FETCH"] = "1"  # fetching must be able to download
    get_settings()  # fail fast on invalid environment
    configure_outbound()  # corporate proxy and extra CA, before any client is created
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
