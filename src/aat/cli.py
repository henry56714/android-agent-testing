from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .bootstrap import initialize
from .catalog import history, recover, save, save_many
from .device import doctor
from .discovery import preview, reconcile
from .lifecycle import prune, read_report, restore
from .planner import freeze, load_plan, run_directory
from .project import Project
from .runner import Session, run
from .schema import CASE, CONFIG, SUITE
from .selection import resolve
from .storage import Invalid, UniqueLoader, lock, write_json


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Invalid(message)


def parser():
    root = Parser(prog="aat")
    root.add_argument("--project", default="android-testing.yaml")
    root.add_argument("--json", action="store_true", help="Use the versioned JSON envelope (default)")
    root.add_argument("--format", choices=["json", "text", "legacy-json"], default="json")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--id", required=True)
    init.add_argument("--mode", choices=["source", "apk"], default="source")
    init.add_argument("--apk")
    init.add_argument("--application-id")
    init.add_argument("--gradle-wrapper", default="./gradlew")
    schema = sub.add_parser("schema")
    schema.add_argument("kind", choices=["project", "case", "suite"])
    d = sub.add_parser("doctor")
    d.add_argument("--device")
    d.add_argument("--strict", action="store_true")
    d.add_argument("--layer", action="append", choices=["static", "local", "device", "ui"], default=[])
    discovery = sub.add_parser("discover")
    discovery.add_argument("--apply", action="store_true")
    c = sub.add_parser("cases")
    c.add_argument(
        "action", choices=["list", "show", "search", "validate", "save", "save-batch", "history", "recover"]
    )
    c.add_argument("query", nargs="?")
    c.add_argument("--expected-revision", type=int)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--case", action="append", default=[])
        p.add_argument("--tag", action="append", default=[])
        p.add_argument("--changed", action="append", default=[])
        p.add_argument("--suite")
        p.add_argument("--profile")
        p.add_argument("--base")
        if name == "run":
            p.add_argument("--run-id")
            p.add_argument("--device")
            p.add_argument(
                "--sync", action="store_true", help="Explicitly apply discovered changes before freezing"
            )
    b = sub.add_parser("build")
    b.add_argument("--run-id", required=True)
    r = sub.add_parser("report")
    r.add_argument("run_id")
    replay = sub.add_parser("replay")
    replay.add_argument("run_id")
    replay.add_argument("--destination", required=True)
    replay.add_argument("--execute", action="store_true")
    replay.add_argument("--device")
    retention = sub.add_parser("prune")
    retention.add_argument("--days", type=int)
    retention.add_argument("--apply", action="store_true")
    pin = sub.add_parser("pin")
    pin.add_argument("run_id")
    return root


def catalog_command(project, args):
    if args.action == "save-batch":
        if not args.query:
            raise Invalid("Batch envelope path required")
        return save_many(project, yaml.load(Path(args.query).read_text(), Loader=UniqueLoader))
    if args.action == "save":
        if not args.query:
            raise Invalid("Save envelope path required")
        return save(
            project, yaml.load(Path(args.query).read_text(), Loader=UniqueLoader), args.expected_revision
        )
    if args.action == "history":
        if not args.query:
            raise Invalid("Case ID required")
        return history(project, args.query)
    if args.action == "recover":
        with lock(project.artifacts / ".execution.lock"), lock(project.artifacts / ".catalog.lock"):
            return {"recovered": recover(project)}
    if args.action == "show":
        if args.query not in project.cases:
            raise Invalid("Unknown case ID")
        return project.cases[args.query]
    if args.action == "search":
        if not args.query:
            raise Invalid("Search query required")
        return [
            c
            for c in project.cases.values()
            if args.query.lower() in json.dumps(c, ensure_ascii=False).lower()
        ]
    if args.action == "validate":
        return {
            "valid": True,
            "case_count": len(project.cases),
            "note": "Metadata validation only; tests have not executed",
        }
    return list(project.cases.values())


def plan_or_run(project, args):
    warnings = []
    if args.command == "run" and args.run_id:
        if args.case or args.tag or args.suite or args.changed or args.base or args.profile or args.sync:
            raise Invalid("Cannot change selection or synchronize a frozen plan")
        run_id = args.run_id
    else:
        selection, profile, full = resolve(project, args)
        explicit_sync = args.command == "run" and args.sync
        legacy_sync = args.command == "run" and project.config.get("discovery", {}).get("auto_sync", False)
        if explicit_sync and not full:
            raise Invalid("--sync requires a full discovery run, without partial selectors")
        audit = None
        if full:
            if explicit_sync or legacy_sync:
                audit = reconcile(project)
                project = Project(project.path)
                if legacy_sync and not explicit_sync:
                    warnings.append(
                        "discovery.auto_sync is a compatibility option; prefer discover --apply or run --sync"
                    )
            else:
                inspected = preview(project)
                if inspected["needs_sync"]:
                    paths = ", ".join(c["path"] for c in inspected["changes"])
                    raise Invalid(
                        "DISCOVERY_OUT_OF_SYNC: run discover to inspect changes, then discover --apply "
                        "or run --sync. Changed paths: " + paths
                    )
                audit = {k: v for k, v in inspected.items() if k not in ("needs_sync", "changes")}
        result = freeze(project, project.select(**selection), profile, discovery=audit)
        run_id = result["run_id"]
    if args.command == "run":
        result, code = run(project, run_id, args.device)
    else:
        code = 0
    if warnings:
        result = {**result, "warnings": warnings}
    return result, code


def dispatch(args):
    if args.command == "init":
        return initialize(
            args.project, args.id, args.mode, args.apk, args.application_id, args.gradle_wrapper
        ), 0
    if args.command == "schema":
        return {"project": CONFIG, "case": CASE, "suite": SUITE}[args.kind], 0
    lightweight = args.command in ("doctor", "report", "replay", "prune", "pin") or (
        args.command == "cases" and args.action in ("save", "save-batch", "recover", "history")
    )
    project = Project(args.project, catalog=not lightweight)
    if args.command == "doctor":
        project.cases = project._catalog()
        result = doctor(project, args.device, args.layer)
        return result, 3 if args.strict and not result["ready"] else 0
    if args.command == "discover":
        result = reconcile(project) if args.apply else preview(project)
        return result, 3 if result["gaps"] else 0
    if args.command == "cases":
        return catalog_command(project, args), 0
    if args.command in ("plan", "run"):
        return plan_or_run(project, args)
    if args.command == "build":
        with lock(project.artifacts / ".execution.lock"):
            directory, _ = load_plan(project, args.run_id)
            if (directory / "products.json").exists() or (directory / "started.json").exists():
                raise Invalid("Build already recorded; create a new plan")
            session = Session(project, directory)
            try:
                result = session.build()
                if not result:
                    raise Invalid("No products configured")
            finally:
                errors = session.close()
            if errors:
                raise RuntimeError("; ".join(errors))
            return result, 0
    if args.command == "replay":
        result = restore(project, args.run_id, args.destination)
        if args.execute:
            restored = Project(result["project"])
            plan = freeze(restored, restored.select(ids=result["cases"]), "replay")
            return run(restored, plan["run_id"], args.device)
        return result, 0
    if args.command == "prune":
        return prune(project, args.days, args.apply), 0
    if args.command == "pin":
        with lock(project.artifacts / ".execution.lock"):
            directory = run_directory(project, args.run_id)
            write_json(directory / "pinned.json", {"pinned": True})
        return {"run_id": args.run_id, "pinned": True}, 0
    return read_report(project, args.run_id), 0


def emit(result, code, error, output_format):
    envelope = {"schema_version": 1, "ok": code == 0, "exit_code": code, "data": result, "error": error}
    if output_format == "legacy-json":
        value = {"error": error["code"], "message": error["message"]} if error else result
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif output_format == "text":
        print(f"{'OK' if code == 0 else 'NOT PASSED'} (exit {code})")
        if error:
            print(f"{error['code']}: {error['message']}")
        elif isinstance(result, dict):
            for key in (
                "run_id",
                "gate",
                "ready",
                "case_count",
                "created",
                "needs_sync",
                "warnings",
                "issues",
            ):
                if key in result:
                    print(f"{key}: {result[key]}")
            if not any(
                k in result for k in ("run_id", "gate", "ready", "case_count", "created", "needs_sync")
            ):
                print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    output_format = "json"
    # Even parser errors use the requested supported envelope/format.
    if "--format" in argv:
        index = argv.index("--format") + 1
        if index < len(argv) and argv[index] in ("json", "text", "legacy-json"):
            output_format = argv[index]
    result, error, code = None, None, 0
    try:
        args = parser().parse_args(argv)
        if args.json and args.format != "json":
            raise Invalid("--json cannot be combined with a different --format")
        output_format = args.format
        result, code = dispatch(args)
    except Invalid as exc:
        code, error = 2, {"code": "INVALID_INPUT", "message": str(exc)}
    except KeyboardInterrupt:
        code, error = 130, {"code": "CANCELLED", "message": "Operation cancelled"}
    except Exception as exc:
        code, error = 4, {"code": "EXECUTION_ERROR", "message": str(exc)}
    emit(result, code, error, output_format)
    return code


if __name__ == "__main__":
    sys.exit(main())
