"""`forgectl`: the command line for Forge.

    forgectl export DIR           write the project's assets as files (for Git)
    forgectl diff DIR             what applying DIR would change
    forgectl apply DIR            import what changed
    forgectl run USE_CASE         run a published use case
    forgectl eval DATASET TARGET  evaluate a prompt or RAG pipeline on a dataset (CI gate)
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

SYMBOL = {"new": "+", "changed": "~", "unchanged": "=", "differs": "?", "removed": "-"}


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
    zipped = _client(args).export_files(versions=args.versions, config=args.config)
    written = gitops.extract(zipped, Path(args.directory))
    out.write(f"{len(written) - 1} items written to {args.directory} ({len(written)} files)\n")
    return 0


def _plan(args: argparse.Namespace) -> tuple[ForgeClient, dict[str, Any], dict[str, Any]]:
    client = _client(args)
    bundle = client.read_files(gitops.zip_directory(Path(args.directory)))
    return client, bundle, client.plan(bundle)


def cmd_diff(args: argparse.Namespace, out: TextIO) -> int:
    _, _, plan = _plan(args)
    if args.json:
        _print_json(out, plan)
    else:
        for c in plan["items"]:
            if c["status"] != "unchanged" or args.all:
                extra = (
                    f" +{c['versions_to_send']} version(s)"
                    if c["status"] == "changed" and c["versions_to_send"]
                    else ""
                )
                out.write(
                    f"{SYMBOL[c['status']]} {c['kind']:<16} {c['name']}  [{c['status']}]{extra}\n"
                )
        s = plan["summary"]
        pending = s.get("new", 0) + s.get("changed", 0)
        out.write(f"{pending} to apply, {s.get('removed', 0)} only in the project (never deleted)")
        differs = s.get("differs", 0)
        out.write(f", {differs} differ outside their versions (not applied)\n" if differs else "\n")
    s = plan["summary"]
    return 1 if args.exit_code and (s.get("new", 0) or s.get("changed", 0)) else 0


def cmd_apply(args: argparse.Namespace, out: TextIO) -> int:
    client, bundle, _ = _plan(args)
    result = client.sync(bundle, policy=args.policy, dry_run=args.dry_run)
    report = result["report"]
    if args.json:
        _print_json(out, result)
    elif report is None:
        out.write("Nothing to apply: the project already matches the files.\n")
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
    items = report["items"] if report else []
    failed = [i for i in items if i["action"] in ("error", "forbidden")]
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


def cmd_eval(args: argparse.Namespace, out: TextIO) -> int:
    evaluators = json.loads(Path(args.evaluators).read_text(encoding="utf-8"))
    client = _client(args)
    run = client.eval_run(
        args.dataset,
        args.target,
        evaluators,
        version=args.version,
        model=args.model,
        name=args.name,
        timeout=args.timeout,
    )
    summary = run.get("summary") or {}
    rate = summary.get("pass_rate")
    comparison = client.compare_eval_runs(args.baseline, run["id"]) if args.baseline else None
    if args.json:
        _print_json(
            out,
            {
                "run": {k: v for k, v in run.items() if k != "results"},
                "failed": [r for r in run.get("results", []) if not r["passed"]],
                "comparison": comparison,
            },
        )
    else:
        out.write(
            f"{run['status']} · run {run['id']} · {run['target_name']} v{run['target_version']}"
            f" on {run['dataset_name']}\n"
            f"passed {summary.get('passed')}/{summary.get('cases')} (pass rate {rate})"
            f" · errors {summary.get('errors')} · cost {summary.get('cost')}\n"
        )
        for name, e in (summary.get("evaluators") or {}).items():
            out.write(f"  {name}: mean {e['mean']} ({e['passed']}/{e['scored']} passed)\n")
        for r in run.get("results", []):
            if not r["passed"]:
                why = r.get("error") or ", ".join(
                    f"{k}: {v['detail'] or v['score']}"
                    for k, v in r["scores"].items()
                    if not v["passed"]
                )
                out.write(f"  FAIL case {r['position']}: {why}\n")
        if comparison:
            out.write(
                f"vs {args.baseline}: pass rate {comparison['pass_rate_delta']:+}, "
                f"{comparison['regressions']} regressed, {comparison['improvements']} improved\n"
            )
    if run["status"] != "done":
        return 1
    if args.fail_under is not None and (rate is None or rate < args.fail_under):
        return 1
    if comparison and args.no_regressions and comparison["regressions"]:
        return 1
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
    e.add_argument("--versions", choices=["latest", "published", "all"], default="all")
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

    ev = sub.add_parser("eval", help="evaluate a prompt or RAG pipeline on a dataset")
    ev.add_argument("dataset", help="dataset name or id")
    ev.add_argument("target", help="prompt or RAG pipeline name or id (the dataset's kind decides)")
    ev.add_argument("--evaluators", required=True, metavar="FILE", help="JSON list of evaluators")
    ev.add_argument("--version", type=int, help="target version (default: the latest)")
    ev.add_argument("--model", help="prompt only: override the version's model")
    ev.add_argument("--name", default="", help="label for the run")
    ev.add_argument("--fail-under", type=float, metavar="RATE", help="exit 1 below this pass rate")
    ev.add_argument("--baseline", metavar="RUN_ID", help="compare with an earlier run")
    ev.add_argument("--no-regressions", action="store_true", help="with --baseline: exit 1 on any")
    ev.add_argument("--timeout", type=float, default=1800.0)
    ev.set_defaults(func=cmd_eval)

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
