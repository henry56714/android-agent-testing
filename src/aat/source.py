"""Source enumeration and fingerprints, independent of execution engines."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .device import query
from .lifecycle import excluded
from .storage import digest, inside

SKIP_DIRS = {".git", ".aat", ".venv", "venv", "build", "dist", "node_modules", "__pycache__", ".gradle"}


def source_state(project):
    code, listing = query(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], project.root
    )
    # A standalone project may live inside an enclosing repository's ignored
    # directory (e.g. a restored snapshot); successful Git with no files is not
    # evidence that the project has no source inputs.
    if code or not listing:
        paths = []
        for parent, dirs, names in os.walk(project.root, followlinks=False):
            parent = Path(parent)
            dirs[:] = [
                d
                for d in dirs
                if d not in SKIP_DIRS
                and not (parent / d).is_symlink()
                and not (parent / d).resolve().is_relative_to(project.artifacts)
                and not excluded(project, (parent / d).relative_to(project.root).as_posix() + "/")
            ]
            paths.extend((parent / name).relative_to(project.root).as_posix() for name in names)
    else:
        paths = listing.split("\0")
    files = {}
    for name in sorted(set(paths)):
        if not name or excluded(project, name):
            continue
        p = inside(project.root, name)
        if p.is_relative_to(project.artifacts) or ".aat" in p.relative_to(project.root).parts:
            continue
        if p.is_file():
            files[name] = digest(p)
        elif not p.exists():
            files[name] = "deleted"
    code, commit = query(["git", "rev-parse", "HEAD"], project.root)
    return {
        "commit": commit.strip() if code == 0 else None,
        "files": files,
        "fingerprint": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
    }
