"""Catalog validation and versioned edits; storage lives in transaction.py."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

from .project import Project
from .schema import CASE, ID
from .storage import Invalid, inside, lock
from .transaction import CatalogTransaction, recover  # public compatibility exports


def save(project, envelope, expected_revision=None):
    result = save_many(project, [{"envelope": envelope, "expected_revision": expected_revision}])
    return result["saved"][0]


def save_many(project, updates):
    """Atomically update related cases, including explicitly shared assets."""
    if not isinstance(updates, list) or not updates:
        raise Invalid("Batch expects a non-empty list of {envelope, expected_revision}")
    validated = []
    for update in updates:
        if not isinstance(update, dict) or set(update) != {"envelope", "expected_revision"}:
            raise Invalid("Each batch entry needs envelope and expected_revision")
        envelope = update["envelope"]
        if not isinstance(envelope, dict) or set(envelope) - {"case", "files"}:
            raise Invalid("Save expects {case: metadata, files: {relative-path: text}}")
        case, files = envelope.get("case"), envelope.get("files", {})
        try:
            jsonschema.validate(case, CASE)
        except jsonschema.ValidationError as error:
            raise Invalid(error.message) from error
        if not isinstance(files, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in files.items()
        ):
            raise Invalid("files must map project-relative paths to text")
        validated.append((case, files, update["expected_revision"]))
    ids = {c["id"] for c, _, _ in validated}
    if len(ids) != len(validated):
        raise Invalid("Duplicate case ID in batch")
    with lock(project.artifacts / ".execution.lock"), lock(project.artifacts / ".catalog.lock"):
        recover(project)
        current = Project(project.path)
        writes, events, saved = {}, [], []
        for case, files, expected in validated:
            previous = current.cases.get(case["id"])
            revision = previous["revision"] if previous else 0
            if expected is None or expected != revision:
                raise Invalid(f"Revision conflict: expected {expected}, current {revision}")
            if case["revision"] != revision + 1:
                raise Invalid("Revision must increment exactly once (new case starts at 1)")
            case_path = (
                previous["_path"] if previous else str(Path(project.config["catalog"]) / f"{case['id']}.yaml")
            )
            allowed = set(case.get("assets", [])) | {case["contract"]}
            if case["implementation"].get("path"):
                allowed.add(case["implementation"]["path"])
            if case_path in files:
                raise Invalid("Case metadata must be supplied through case, not files")
            for name, value in files.items():
                if name not in allowed or Path(name).is_absolute() or ".." in Path(name).parts:
                    raise Invalid(f"Undeclared or non-relative test asset: {name}")
                target = inside(project.root, name)
                if (
                    target.is_relative_to(project.artifacts)
                    or ".git" in target.relative_to(project.root).parts
                ):
                    raise Invalid("Cannot write repository internals or run evidence")
                for other in current.cases.values():
                    if (
                        other["id"] not in ids
                        and name in current.assets(other)
                        and target.exists()
                        and target.read_text() != value
                    ):
                        raise Invalid(
                            f"Shared asset affects {other['id']}; include all affected cases in save-batch"
                        )
                if name in writes and writes[name] != value:
                    raise Invalid("Conflicting batch file contents: " + name)
                writes[name] = value
            if case_path in writes:
                raise Invalid("Asset conflicts with case metadata: " + case_path)
            writes[case_path] = yaml.safe_dump(case, sort_keys=False, allow_unicode=True)
            events.append(
                {
                    "case": case,
                    "files": files,
                    "source": "save",
                    "action": "updated" if previous else "created",
                    "previous_revision": revision,
                    "reason": "explicit batch save" if len(validated) > 1 else "explicit case save",
                }
            )
            saved.append({"id": case["id"], "revision": case["revision"], "path": case_path, "saved": True})
        CatalogTransaction(project, writes, events).commit()
        return {"saved": saved}


def history(project, case_id):
    if not jsonschema.Draft202012Validator(ID).is_valid(case_id):
        raise Invalid("Invalid case ID")
    versions, events = [], []
    for p in sorted(
        (project.artifacts / "catalog-history" / case_id).glob("*.json"), key=lambda p: int(p.stem)
    ):
        event = json.loads(p.read_text())
        versions.append(event["case"])
        events.append(event)
    executions = []
    for p in sorted(project.artifacts.glob("*/result.json")):
        r = json.loads(p.read_text())
        for c in r["cases"]:
            if c["id"] == case_id:
                executions.append({"run_id": r["run_id"], **c})
    for path in sorted((project.artifacts / "catalog-history" / case_id / "events").glob("*.json")):
        events.append(json.loads(path.read_text()))
    revisions = {c["revision"] for c in versions}
    current = project.cases.get(case_id)
    if not current:
        directory = inside(project.root, project.config["catalog"])
        for path in list(directory.glob("*.yaml")) + list(directory.glob("*.yml")):
            data = yaml.safe_load(path.read_text())
            if isinstance(data, dict) and data.get("id") == case_id:
                current = data
                break
    latest = max([c["revision"] for c in versions] + ([current["revision"]] if current else [0]))
    return {
        "id": case_id,
        "versions": versions,
        "executions": executions,
        "events": events,
        "history_complete": bool(latest) and len(revisions) == latest,
        "history_note": "Only recorded revisions are known; missing historical revisions are not reconstructed.",
    }
