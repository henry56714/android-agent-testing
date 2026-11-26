"""Resolve CLI selectors without mutating project configuration or test metadata."""

import fnmatch

from .device import query
from .storage import Invalid


def resolve(project, args):
    explicit = bool(args.case or args.tag or args.suite or args.changed or args.base)
    takeover = project.config.get("discovery", {}).get("enabled", False)
    profile = args.profile or ("full" if takeover and not explicit else "change")
    profiles = project.config.get("profiles", {})
    if profile not in {"full", "change"} | set(profiles):
        raise Invalid(f"Unknown or unconfigured profile: {profile}")
    if args.profile == "full" and explicit:
        raise Invalid("Full profile cannot be combined with partial selectors")
    selection = {"ids": args.case, "tags": args.tag, "suite": args.suite, "changed": args.changed}
    if args.base:
        rc, changed = query(["git", "diff", "--name-only", args.base, "--"], project.root)
        if rc:
            raise Invalid("Invalid Git base")
        rc, untracked = query(["git", "ls-files", "--others", "--exclude-standard"], project.root)
        if rc:
            raise Invalid("Unable to enumerate untracked source")
        selection["changed"] = sorted(set(changed.splitlines() + untracked.splitlines() + args.changed))
    if selection["changed"] and any(
        not any(
            fnmatch.fnmatch(path, pattern)
            for c in project.cases.values()
            for pattern in c.get("impact", {}).get("paths", [])
        )
        for path in selection["changed"]
    ):
        selection = {"ids": [], "tags": [], "suite": None, "changed": []}
    if not any(selection.values()) and not args.base and not args.changed:
        configured = {} if profile == "full" else profiles.get(profile, {})
        selection.update(
            ids=configured.get("cases", []), tags=configured.get("tags", []), suite=configured.get("suite")
        )
    return selection, profile, takeover and profile == "full" and not explicit
