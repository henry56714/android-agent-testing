"""One recoverable transaction for catalog files and their audit history."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime

from .storage import Invalid, digest, inside, write_json


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".aat-write-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def before_image(path):
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else None


def recover(project):
    journal = project.artifacts / "catalog-transaction.json"
    if not journal.exists():
        return False
    state = json.loads(journal.read_text())
    if state["state"] != "committed":
        for root, changes in (
            (project.root, state["before"]),
            (project.artifacts, state.get("artifact_before", {})),
        ):
            for name, before in changes.items():
                target = inside(root, name)
                if before is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_bytes(target, base64.b64decode(before))
        # Backward compatibility with interrupted 0.1/0.2 save transactions.
        if state.get("history"):
            inside(project.artifacts, state["history"]).unlink(missing_ok=True)
    journal.unlink()
    return True


class CatalogTransaction:
    """Caller owns execution/catalog locks; every write is journaled before mutation."""

    def __init__(self, project, writes, events=()):
        self.project, self.writes, self.events = project, writes, list(events)

    def commit(self):
        from .project import Project

        p = self.project
        journal = p.artifacts / "catalog-transaction.json"
        if journal.exists():
            raise Invalid("Incomplete catalog transaction; recover before writing")
        event_writes = {}
        for event in self.events:
            case = event["case"]
            if event["action"] == "retired":
                name = f"catalog-history/{case['id']}/events/{uuid.uuid4().hex}.json"
            else:
                name = f"catalog-history/{case['id']}/{case['revision']}.json"
            if inside(p.artifacts, name).exists():
                raise Invalid(f"History revision already exists: {case['id']}@{case['revision']}")
            event_writes[name] = {**event, "recorded_at": datetime.now(UTC).isoformat()}
        state = {
            "state": "writing",
            "before": {name: before_image(inside(p.root, name)) for name in self.writes},
            "artifact_before": {name: before_image(inside(p.artifacts, name)) for name in event_writes},
        }
        write_json(journal, state)
        try:
            for name, value in self.writes.items():
                target = inside(p.root, name)
                if value is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_bytes(target, value.encode())
            current = Project(p.path, allow_pending=True)
            for name, event in event_writes.items():
                case = current.cases.get(event["case"]["id"])
                event["asset_hashes"] = (
                    {asset: digest(inside(p.root, asset, exists=True)) for asset in current.assets(case)}
                    if case and event["action"] != "retired"
                    else {}
                )
                write_json(inside(p.artifacts, name), event)
            write_json(journal, {**state, "state": "committed"})
            journal.unlink()
        except BaseException:
            recover(p)
            raise
