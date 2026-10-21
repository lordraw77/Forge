"""`forgectl`: the command line for Forge.

    forgectl export DIR           write the project's assets as files (for Git)
    forgectl diff DIR             what applying DIR would change
    forgectl apply DIR            import what changed
    forgectl run USE_CASE         run a published use case
    forgectl chat MODEL "text"    one call through the OpenAI-compatible gateway
    forgectl whoami               the key's project and permissions

Settings: `--url` / FORGE_URL, `--api-key` / FORGE_API_KEY, `--project` / FORGE_PROJECT (optional
with a project key). Exit status: 0 success, 1 differences found (`diff --exit-code`) or items that
failed to import, 2 a usage or API error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from forge_sdk import gitops
from forge_sdk.client import ForgeClient, ForgeError

SYMBOL = {"new": "+", "changed": "~", "unchanged": "=", "removed": "-"}


def _client(args: argparse.Namespace) -> ForgeClient:
    if not args.url or not args.api_key:
        raise ForgeError(
            2, "missing_settings", "Set --url and --api-key (or FORGE_URL and FORGE_API_KEY)"
        )
    return ForgeClient(args.url, args.api_key, args.project)


def _print_json(out: TextIO, value: Any) -> None:
    out.write(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def cmd_whoami(args: argparse.Namespace, out: TextIO) -> int:
    who = _client(args).whoami()
    if args.json:
        _print_json(out, who)
    else:
        out.write(f"{who['kind']} · project {who.get('project_name')} ({who.get('project_id')})\n")
        out.write("permissions: " + ", ".join(who.get("permissions", [])) + "\n")
    return 0


def cmd_export(args: argparse.Namespace, out: TextIO) -> int:
    bundle = _client(args).export_bundle(versions=args.versions, config=args.config)
    written = gitops.write_tree(bundle, Path(args.directory))
    out.write(f"{len(bundle['items'])} items written to {args.directory} ({len(written)} files)\n")
    return 0


def _plan(args: argparse.Namespace) -> tuple[ForgeClient, dict[str, Any], list[gitops.Change]]:
    local = gitops.read_tree(Path(args.directory))
    client = _client(args)
    has_config = any(
        i["kind"] in ("model_alias", "gateway_config", "user_group", "guardrail_policy")
        for i in local["items"]
    )
    remote = client.export_bundle(versions="latest", config=has_config)
    return client, local, gitops.compare(local, remote)


def cmd_diff(args: argparse.Namespace, out: TextIO) -> int:
    _, _, changes = _plan(args)
    if args.json:
        _print_json(out, [c.__dict__ for c in changes])
    else:
        for c in changes:
            if c.status != "unchanged" or args.all:
                out.write(f"{SYMBOL[c.status]} {c.kind:<16} {c.name}  [{c.status}]\n")
        pending = sum(c.status in ("new", "changed") for c in changes)
        removed = sum(c.status == "removed" for c in changes)
        out.write(f"{pending} to apply, {removed} only in the project (never deleted)\n")
    return 1 if args.exit_code and any(c.status in ("new", "changed") for c in changes) else 0


def cmd_apply(args: argparse.Namespace, out: TextIO) -> int:
    client, local, changes = _plan(args)
    todo = gitops.only_changes(local, changes)
    if not todo["items"]:
        out.write("Nothing to apply: the project already matches the files.\n")
        return 0
    report = client.import_bundle(todo, policy=args.policy, dry_run=args.dry_run)
    if args.json:
        _print_json(out, report)
    else:
        mode = "Dry run" if args.dry_run else "Applied"
        out.write(f"{mode}:\n")
        for item in report["items"]:
            line = f"  {item['action']:<13} {item['kind']:<16} {item['name']}"
            if item.get("error"):
                line += f"  ERROR: {item['error']}"
            out.write(line + "\n")
            for w in item.get("warnings", []):
                out.write(f"                warning: {w}\n")
    failed = [i for i in report["items"] if i["action"] in ("error", "forbidden")]
    return 1 if failed else 0


def _parse_inputs(pairs: Sequence[str], json_file: str | None) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if json_file:
        loaded = json.loads(Path(json_file).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ForgeError(2, "bad_input", "--input-json must hold a JSON object")
        inputs.update(loaded)
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ForgeError(2, "bad_input", f"--input needs key=value, got '{pair}'")
        inputs[key] = value
    return inputs


def cmd_run(args: argparse.Namespace, out: TextIO) -> int:
    run = _client(args).run_use_case(
        args.use_case,
        _parse_inputs(args.input, args.input_json),
        wait=not args.no_wait,
        timeout=args.timeout,
    )
    if args.json or args.no_wait:
        _print_json(out, run)
    else:
        outputs = run.get("outputs") or {}
        out.write(f"{run['status']} · run {run['id']}\n")
        if run.get("error"):
            out.write(f"error: {run['error']}\n")
        if outputs:
            _print_json(out, outputs)
    return 0 if run["status"] in ("succeeded", "queued", "running") else 1


def cmd_chat(args: argparse.Namespace, out: TextIO) -> int:
    answer = _client(args).chat(args.model, [{"role": "user", "content": args.text}])
    if args.json:
        _print_json(out, answer)
    else:
        out.write(answer["choices"][0]["message"]["content"] + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="forgectl", description="Command line for Forge")
    p.add_argument("--url", default=os.environ.get("FORGE_URL"), help="Forge base URL")
    p.add_argument("--api-key", default=os.environ.get("FORGE_API_KEY"), help="project API key")
    p.add_argument("--project", default=os.environ.get("FORGE_PROJECT"), help="project id")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami", help="the key's project and permissions").set_defaults(func=cmd_whoami)

    e = sub.add_parser("export", help="write the project's assets as files")
    e.add_argument("directory")
    e.add_argument("--versions", choices=["latest", "published", "all"], default="latest")
    e.add_argument("--config", action="store_true", help="also aliases, limits, groups, guardrails")
    e.set_defaults(func=cmd_export)

    d = sub.add_parser("diff", help="compare a directory with the project")
    d.add_argument("directory")
    d.add_argument("--all", action="store_true", help="list unchanged items too")
    d.add_argument("--exit-code", action="store_true", help="exit 1 when there are differences")
    d.set_defaults(func=cmd_diff)

    a = sub.add_parser("apply", help="import what changed in a directory")
    a.add_argument("directory")
    a.add_argument("--policy", choices=["new_version", "rename", "skip"], default="new_version")
    a.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    a.set_defaults(func=cmd_apply)

    r = sub.add_parser("run", help="run a published use case")
    r.add_argument("use_case", help="name or id")
    r.add_argument("--input", "-i", action="append", default=[], metavar="KEY=VALUE")
    r.add_argument("--input-json", metavar="FILE")
    r.add_argument("--no-wait", action="store_true")
    r.add_argument("--timeout", type=float, default=300.0)
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("chat", help="one chat call through the gateway")
    c.add_argument("model")
    c.add_argument("text")
    c.set_defaults(func=cmd_chat)
    return p


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    try:
        return int(args.func(args, out))
    except ForgeError as exc:
        sys.stderr.write(f"forgectl: {exc}\n")
        return 2
    except gitops.TreeError as exc:
        sys.stderr.write(f"forgectl: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
