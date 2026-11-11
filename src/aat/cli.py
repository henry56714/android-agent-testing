from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

import yaml

from .catalog import history, recover, save
from .device import doctor, query
from .lifecycle import prune, read_report, restore
from .project import Project
from .runner import Session, freeze, load_plan, run, run_directory
from .storage import Invalid, lock, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aat")
    parser.add_argument("--project", default="android-testing.yaml")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--id", required=True)
    schema = sub.add_parser("schema")
    schema.add_argument("kind", choices=["project", "case", "suite"])
    d = sub.add_parser("doctor")
    d.add_argument("--device")
    c = sub.add_parser("cases")
    c.add_argument("action", choices=["list", "show", "search", "validate", "save", "history", "recover"])
    c.add_argument("query", nargs="?")
    c.add_argument("--expected-revision", type=int)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--case", action="append", default=[])
        p.add_argument("--tag", action="append", default=[])
        p.add_argument("--changed", action="append", default=[])
        p.add_argument("--suite")
        p.add_argument("--profile", default="change")
        p.add_argument("--base")
        if name == "run":
            p.add_argument("--run-id")
            p.add_argument("--device")
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
    args = parser.parse_args(argv)
    code = 0
    try:
        if args.command == "init":
            target = Path(args.project).resolve()
            if target.exists():
                raise Invalid("Refusing to overwrite existing project config")
            config = {
                "schema_version": 1,
                "project": {"id": args.id, "mode": "source", "root": "."},
                "catalog": "testing/cases",
                "suites": "testing/suites",
                "artifacts": ".aat/runs",
                "profiles": {"fast": {"tags": ["fast"]}, "change": {"tags": ["smoke"]}, "full": {}},
            }
            import jsonschema

            from .schema import CONFIG

            jsonschema.validate(config, CONFIG)
            target.parent.mkdir(parents=True, exist_ok=True)
            (target.parent / "testing/cases").mkdir(parents=True, exist_ok=True)
            (target.parent / "testing/suites").mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.safe_dump(config, sort_keys=False))
            print(json.dumps({"created": str(target), "next": "Add project tasks/products and native cases"}))
            return 0
        if args.command == "schema":
            from .schema import CASE, CONFIG, SUITE

            print(json.dumps({"project": CONFIG, "case": CASE, "suite": SUITE}[args.kind], indent=2))
            return 0
        lightweight = args.command in ("report", "replay", "prune", "pin") or (
            args.command == "cases" and args.action in ("save", "recover", "history")
        )
        project = Project(args.project, catalog=not lightweight)
        if args.command == "doctor":
            result = doctor(project, args.device)
        elif args.command == "cases":
            cases = list(project.cases.values())
            if args.action == "save":
                if not args.query:
                    raise Invalid("Save envelope path required")
                result = save(project, yaml.safe_load(Path(args.query).read_text()), args.expected_revision)
            elif args.action == "history":
                if not args.query:
                    raise Invalid("Case ID required")
                result = history(project, args.query)
            elif args.action == "recover":
                with lock(project.artifacts / ".execution.lock"), lock(project.artifacts / ".catalog.lock"):
                    result = {"recovered": recover(project)}
            elif args.action == "show":
                if args.query not in project.cases:
                    raise Invalid("Unknown case ID")
                result = project.cases[args.query]
            elif args.action == "search":
                if not args.query:
                    raise Invalid("Search query required")
                result = [c for c in cases if args.query.lower() in json.dumps(c, ensure_ascii=False).lower()]
            elif args.action == "validate":
                result = {
                    "valid": True,
                    "case_count": len(cases),
                    "note": "Metadata validation only; tests have not executed",
                }
            else:
                result = cases
        elif args.command in ("plan", "run"):
            if args.command == "run" and args.run_id:
                if args.case or args.tag or args.suite or args.changed or args.base:
                    raise Invalid("Cannot change selection of a frozen plan")
                run_id = args.run_id
            else:
                selection = {"ids": args.case, "tags": args.tag, "suite": args.suite, "changed": args.changed}
                if args.base:
                    rc, changed = query(["git", "diff", "--name-only", args.base, "--"], project.root)
                    if rc:
                        raise Invalid("Invalid Git base")
                    _, untracked = query(["git", "ls-files", "--others", "--exclude-standard"], project.root)
                    selection["changed"] = list(
                        set(changed.splitlines() + untracked.splitlines() + args.changed)
                    )
                    # Any unmapped file (including build/shared code) requires full coverage.
                    if any(
                        not any(
                            fnmatch.fnmatch(path, rule)
                            for c in project.cases.values()
                            for rule in c.get("impact", {}).get("paths", [])
                        )
                        for path in selection["changed"]
                    ):
                        selection = {"ids": [], "tags": [], "suite": None, "changed": []}
                if not any(selection.values()) and not args.base:
                    profiles = project.config.get("profiles", {})
                    if profiles and args.profile not in profiles:
                        raise Invalid("Unknown profile")
                    selected_profile = profiles.get(args.profile, {})
                    selection.update(
                        ids=selected_profile.get("cases", []),
                        tags=selected_profile.get("tags", []),
                        suite=selected_profile.get("suite"),
                    )
                result = freeze(project, project.select(**selection), args.profile)
                run_id = result["run_id"]
            if args.command == "run":
                result, code = run(project, run_id, args.device)
        elif args.command == "build":
            directory, _ = load_plan(project, args.run_id)
            with lock(project.artifacts / ".execution.lock"):
                if (directory / "products.json").exists() or (directory / "started.json").exists():
                    raise Invalid("Build already recorded; create a new plan")
                session = Session(project, directory)
                result = session.build()
                if not result:
                    raise Invalid("No products configured")
        elif args.command == "replay":
            result = restore(project, args.run_id, args.destination)
            if args.execute:
                restored = Project(result["project"])
                plan = freeze(restored, restored.select(ids=result["cases"]), "replay")
                result, code = run(restored, plan["run_id"], args.device)
        elif args.command == "prune":
            result = prune(project, args.days, args.apply)
        elif args.command == "pin":
            directory = run_directory(project, args.run_id)
            write_json(directory / "pinned.json", {"pinned": True})
            result = {"run_id": args.run_id, "pinned": True}
        else:
            result = read_report(project, args.run_id)
    except Invalid as e:
        result, code = {"error": "INVALID_INPUT", "message": str(e)}, 2
    except KeyboardInterrupt:
        result, code = {"error": "CANCELLED"}, 130
    except Exception as e:
        result, code = {"error": "EXECUTION_ERROR", "message": str(e)}, 4
    # Always structured; --json guarantees the same stable envelope for callers.
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
