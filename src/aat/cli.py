from __future__ import annotations

import argparse
import json
import sys

from .device import doctor
from .project import Project
from .runner import Session, freeze, load_plan, run, run_directory
from .storage import Invalid, lock


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aat")
    parser.add_argument("--project", default="android-testing.yaml")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--device")
    c = sub.add_parser("cases")
    c.add_argument("action", choices=["list", "show", "search", "validate"])
    c.add_argument("query", nargs="?")
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--case", action="append", default=[])
        p.add_argument("--tag", action="append", default=[])
        p.add_argument("--changed", action="append", default=[])
        p.add_argument("--suite")
        p.add_argument("--profile", default="change")
        if name == "run":
            p.add_argument("--run-id")
            p.add_argument("--device")
    b = sub.add_parser("build")
    b.add_argument("--run-id", required=True)
    r = sub.add_parser("report")
    r.add_argument("run_id")
    args = parser.parse_args(argv)
    code = 0
    try:
        project = Project(args.project)
        if args.command == "doctor":
            result = doctor(project, args.device)
        elif args.command == "cases":
            cases = list(project.cases.values())
            if args.action == "show":
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
                if args.case or args.tag or args.suite or args.changed:
                    raise Invalid("Cannot change selection of a frozen plan")
                run_id = args.run_id
            else:
                result = freeze(
                    project, project.select(args.case, args.tag, args.suite, args.changed), args.profile
                )
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
        else:
            directory = run_directory(project, args.run_id)
            path = directory / "result.json"
            result = (
                json.loads(path.read_text())
                if path.exists()
                else {
                    "state": "INCOMPLETE" if (directory / "started.json").exists() else "PLANNED",
                    "run_id": args.run_id,
                }
            )
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
