"""File-based catalog transactions; interrupted writes roll back before the next writer."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

import jsonschema
import yaml

from .project import Project
from .schema import CASE, ID
from .storage import Invalid, inside, lock, write_json


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".aat-write-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def recover(project):
    journal = project.artifacts / "catalog-transaction.json"
    if not journal.exists():
        return False
    state = json.loads(journal.read_text())
    if state["state"] != "committed":
        for name, before in state["before"].items():
            target = inside(project.root, name)
            if before is None:
                target.unlink(missing_ok=True)
            else:
                atomic_bytes(target, base64.b64decode(before))
        if state.get("history"):
            inside(project.artifacts, state["history"]).unlink(missing_ok=True)
    journal.unlink()
    return True


def save(project, envelope, expected_revision=None):
    """Envelope contains a case plus optional project-relative UTF-8 file contents."""
    if not isinstance(envelope, dict) or set(envelope) - {"case", "files"}:
        raise Invalid("Save expects {case: metadata, files: {relative-path: text}}")
    c = envelope.get("case")
    try:
        jsonschema.validate(c, CASE)
    except jsonschema.ValidationError as e:
        raise Invalid(e.message) from e
    files = envelope.get("files", {})
    if not isinstance(files, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in files.items()
    ):
        raise Invalid("files must map project-relative paths to text")
    with lock(project.artifacts / ".execution.lock"), lock(project.artifacts / ".catalog.lock"):
        recover(project)
        current = Project(project.path)
        previous = current.cases.get(c["id"])
        old_revision = previous["revision"] if previous else 0
        if expected_revision is None or expected_revision != old_revision:
            raise Invalid(f"Revision conflict: expected {expected_revision}, current {old_revision}")
        if c["revision"] != old_revision + 1:
            raise Invalid("Revision must increment exactly once (new case starts at 1)")
        case_path = (
            previous["_path"] if previous else str(Path(project.config["catalog"]) / f"{c['id']}.yaml")
        )
        writes = dict(files)
        if case_path in writes:
            raise Invalid("Case metadata must be supplied through case, not files")
        allowed = set(c.get("assets", [])) | {c["contract"]}
        if c["implementation"].get("path"):
            allowed.add(c["implementation"]["path"])
        for name in writes:
            if name not in allowed or Path(name).is_absolute() or ".." in Path(name).parts:
                raise Invalid(f"Undeclared or non-relative test asset: {name}")
            target = inside(project.root, name)
            if target.is_relative_to(project.artifacts) or ".git" in target.relative_to(project.root).parts:
                raise Invalid("Cannot write repository internals or run evidence")
            for other in current.cases.values():
                if (
                    other["id"] != c["id"]
                    and name in current.assets(other)
                    and target.exists()
                    and target.read_text() != writes[name]
                ):
                    raise Invalid(f"Shared asset affects {other['id']}; update shared cases explicitly")
        writes[case_path] = yaml.safe_dump(c, allow_unicode=True, sort_keys=False)
        before = {}
        for name in writes:
            p = inside(project.root, name)
            before[name] = base64.b64encode(p.read_bytes()).decode() if p.exists() else None
        journal = project.artifacts / "catalog-transaction.json"
        history = project.artifacts / "catalog-history" / c["id"] / f"{c['revision']}.json"
        write_json(
            journal,
            {"state": "writing", "before": before, "history": str(history.relative_to(project.artifacts))},
        )
        try:
            for name, text in writes.items():
                atomic_bytes(inside(project.root, name), text.encode())
            Project(project.path, allow_pending=True)
            write_json(history, {"case": c, "files": writes})
            write_json(journal, {"state": "committed", "before": before})
            journal.unlink()
        except BaseException:
            recover(project)
            raise
        return {"id": c["id"], "revision": c["revision"], "path": case_path, "saved": True}


def history(project, case_id):
    if not jsonschema.Draft202012Validator(ID).is_valid(case_id):
        raise Invalid("Invalid case ID")
    versions = []
    for p in sorted(
        (project.artifacts / "catalog-history" / case_id).glob("*.json"), key=lambda p: int(p.stem)
    ):
        versions.append(json.loads(p.read_text())["case"])
    executions = []
    for p in sorted(project.artifacts.glob("*/result.json")):
        r = json.loads(p.read_text())
        for c in r["cases"]:
            if c["id"] == case_id:
                executions.append({"run_id": r["run_id"], **c})
    return {"id": case_id, "versions": versions, "executions": executions}
