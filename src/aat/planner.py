"""Freeze and verify immutable plans; never synchronize the catalog implicitly."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime

from . import __version__
from .lifecycle import excluded, snapshot_source
from .source import source_state
from .storage import Invalid, digest, inside, lock, write_json


def freeze(project, cases, profile="change", discovery=None):
    if discovery is not None:
        # Run native device suites first; exact-method catalog entries can then
        # reuse their already completed results instead of controlling the device twice.
        cases = sorted(cases, key=lambda c: c["implementation"]["engine"] != "gradle-device")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    with lock(project.artifacts / ".catalog.lock"):
        registry_path = project.artifacts / "case-revisions.json"
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
        assets = {}
        state = source_state(project)
        for c in cases:
            names = project.assets(c)
            if any(excluded(project, name) for name in names):
                raise Invalid(
                    f"SNAPSHOT_EXCLUDED_TEST: {c['id']}; test assets cannot be stored by this policy"
                )
            hashes = {a: state["files"].get(a) or digest(inside(project.root, a, exists=True)) for a in names}
            signature = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
            old = registry.get(c["id"])
            if old and (
                c["revision"] < old["revision"]
                or (c["revision"] == old["revision"] and old["signature"] != signature)
            ):
                raise Invalid(f"Case content changed without a newer revision: {c['id']}")
            registry[c["id"]] = {"revision": c["revision"], "signature": signature}
            assets.update(hashes)
        for product in project.config.get("products", {}).values():
            if "input_path" in product:
                name = str(inside(project.root, product["input_path"]).relative_to(project.root))
                assets[name] = digest(inside(project.root, name, exists=True))
        directory = project.artifacts / run_id
        directory.mkdir(parents=True)
        snapshot_source(project, state, directory)
        for name in assets:
            dest = directory / "snapshot" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            frozen = directory / "source" / name
            if frozen.is_file() and state["files"].get(name) == assets[name]:
                # Link only between immutable copies in this run, never to the workspace.
                try:
                    os.link(frozen, dest)
                except OSError:
                    shutil.copy2(frozen, dest)
            else:
                shutil.copy2(inside(project.root, name), dest)
            if digest(dest) != assets[name]:
                raise Invalid("Test asset changed while snapshotting: " + name)
        shutil.copy2(project.path, directory / "project.yaml")
        plan = {
            "schema_version": 1,
            "run_id": run_id,
            "project_id": project.config["project"]["id"],
            "profile": profile,
            "tool_version": __version__,
            "config_sha256": digest(project.path),
            "source": state,
            "assets": assets,
            "cases": cases,
            "source_snapshot": True,
            "discovery": discovery,
            "excluded_cases": [
                {"id": c["id"], "reason": "outside_requested_selection"}
                for c in project.cases.values()
                if c["id"] not in {x["id"] for x in cases}
            ],
            "reproduction_limits": [
                "Source snapshot excludes Git-ignored and explicitly excluded files; external SDK/tools/secrets must be supplied.",
                "Dynamic helpers must be declared in assets; API/device coverage is not inferred.",
            ],
        }
        write_json(directory / "plan.json", plan)
        write_json(registry_path, registry)
        return plan


def run_directory(project, run_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise Invalid("Invalid run ID")
    directory = inside(project.artifacts, run_id)
    if not (directory / "plan.json").is_file():
        raise Invalid("Run plan not found")
    return directory


def load_plan(project, run_id):
    directory = run_directory(project, run_id)
    plan = json.loads((directory / "plan.json").read_text())
    if digest(project.path) != plan["config_sha256"]:
        raise Invalid("STALE_PLAN: project configuration changed")
    for name, sha in plan["assets"].items():
        p = inside(project.root, name, exists=True)
        if digest(p) != sha or digest(inside(directory / "snapshot", name, exists=True)) != sha:
            raise Invalid(f"STALE_PLAN: asset changed: {name}")
    if source_state(project)["fingerprint"] != plan["source"]["fingerprint"]:
        raise Invalid("STALE_PLAN: source inputs changed; create a new plan")
    # Prevent accidental edits to frozen executable metadata.
    for c in plan["cases"]:
        if c != project.cases.get(c["id"]):
            raise Invalid("STALE_PLAN: frozen case metadata mismatch")
    return directory, plan
