"""Calculate synchronization changes separately from their transactional application."""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
from pathlib import Path

import yaml

from .discovery_model import gradle_argv
from .discovery_scan import scan, signature
from .project import Project
from .storage import Invalid, digest, inside, lock
from .transaction import CatalogTransaction, recover


def update_config_text(text, before, after):
    # Retain all untouched top-level sections and comments. Changed sections are
    # regenerated and their exact text diff is exposed before apply.
    node = yaml.compose(text)
    edits = []
    existing = set()
    for key, value in node.value:
        name = key.value
        existing.add(name)
        if before.get(name) != after.get(name):
            start = key.start_mark.index
            # Use the next top-level key, retaining its leading indentation.
            end = value.end_mark.index
            replacement = yaml.safe_dump({name: after[name]}, sort_keys=False, allow_unicode=True)
            edits.append((start, end, replacement))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    additions = {k: v for k, v in after.items() if k not in existing}
    if additions:
        text = text.rstrip() + "\n" + yaml.safe_dump(additions, sort_keys=False, allow_unicode=True)
    return text


def propose(project, inventory):
    found = scan(project, inventory)
    config = json.loads(json.dumps(project.config))
    settings = config.setdefault("discovery", {})
    settings["enabled"] = True
    if gradle_argv(project):
        settings["gradle_argv"] = gradle_argv(project)
    manifest_name = settings.setdefault("manifest", "testing/discovery.json")
    manifest_path = inside(project.root, manifest_name)
    previous_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if (
        manifest_path.exists()
        and not {"schema_version", "entries", "sources", "fingerprint", "coverage"}
        <= previous_manifest.keys()
    ):
        raise Invalid("Discovery manifest path already contains an unrelated file")
    generated = {c["id"]: c for c in project.cases.values() if c.get("origin", {}).get("kind") == "discovery"}
    tasks = config.setdefault("tasks", {})
    writes, keep, coverage, events = {}, set(), {}, []
    contract = "testing/contracts/discovered-tests.md"
    contract_text = (
        "# Imported native test suites\n\nRun every test selected by the native task/flow and preserve its assertions.\n"
        "Zero discovered tests, skipped tests, disabled tasks, unknown sources and missing reports cannot establish full coverage.\n"
    )
    if (
        not previous_manifest
        and inside(project.root, contract).exists()
        and inside(project.root, contract).read_text() != contract_text
    ):
        raise Invalid("Discovery contract path already contains an unrelated file")
    writes[contract] = contract_text
    registry_file = project.artifacts / "case-revisions.json"
    registry = json.loads(registry_file.read_text()) if registry_file.exists() else {}
    for entry in found["entries"]:
        key, engine = entry["key"], entry["engine"]
        if engine == "mapped":
            coverage[key] = entry["case_ids"]
            continue
        if engine == "maestro":
            existing = [
                c["id"]
                for c in project.cases.values()
                if c.get("origin", {}).get("kind") != "discovery"
                and c["implementation"].get("engine") == "maestro"
                and c["implementation"].get("path") == key
                and c["status"] == "active"
                and c.get("required", True)
            ]
            if existing:
                coverage[key] = existing
                continue
        case_id = "discovered." + engine + "." + hashlib.sha256(key.encode()).hexdigest()[:16]
        if case_id in project.cases and case_id not in generated:
            raise Invalid(f"Generated case ID conflicts with hand-written metadata: {case_id}")
        keep.add(case_id)
        metadata_path = (
            generated[case_id]["_path"]
            if case_id in generated
            else str(Path(config["catalog"]) / (case_id + ".yaml"))
        )
        impl = {"engine": engine}
        if engine == "maestro":
            impl["path"] = key
        else:
            # Reuse existing native task aliases so class-level cases share the same execution.
            alias = next(
                (
                    name
                    for name, t in tasks.items()
                    if [a for a in t["argv"] if a.startswith(":")] == [key]
                    and not any(
                        a.startswith("--tests")
                        or a in ("-x", "--exclude-task", "-m", "--dry-run")
                        or "testInstrumentationRunnerArguments" in a
                        for a in t["argv"]
                    )
                ),
                None,
            )
            alias = alias or "discovered_" + hashlib.sha256(key.encode()).hexdigest()[:16]
            if alias not in tasks:
                tasks[alias] = {
                    "argv": [*settings["gradle_argv"], key, "--console=plain"],
                    "timeout_seconds": 600,
                    "reports": entry["reports"],
                }
            # Native model output location is authoritative; a handwritten
            # one-class report glob must not truncate full-suite coverage.
            tasks[alias]["reports"] = entry["reports"]
            if engine == "gradle-device":
                tasks[alias]["device"] = True
            for pattern, deps in settings.get("task_dependencies", {}).items():
                if fnmatch.fnmatch(key, pattern):
                    tasks[alias]["depends_on"] = sorted(set(tasks[alias].get("depends_on", []) + deps))
            impl["task"] = alias
            if engine in ("junit", "gradle-device"):
                impl["selector"] = "*"
        old = generated.get(case_id)
        previous_versions = [
            int(p.stem)
            for p in (project.artifacts / "catalog-history" / case_id).glob("*.json")
            if p.stem.isdigit()
        ]
        previous_revision = max(previous_versions + [registry.get(case_id, {}).get("revision", 0)])
        case = {
            "schema_version": 1,
            "id": case_id,
            "revision": old["revision"] if old else previous_revision + 1,
            "status": old["status"] if old else "active",
            "title": key,
            "layer": {"junit": "local", "lint": "static", "gradle-device": "device", "maestro": "ui"}[engine],
            "contract": contract,
            "implementation": impl,
            "assets": entry["sources"],
            "timeout_seconds": 600,
            "required": True,
            "tags": ["discovered", "fast" if engine in ("junit", "lint") else "device"],
            "origin": {"kind": "discovery", "reference": key},
        }
        if old and old.get("reason"):
            case["reason"] = old["reason"]
        old_clean = {k: v for k, v in old.items() if k != "_path"} if old else None
        asset_changed = False
        if old:
            asset_changed = any(
                previous_manifest.get("sources", {}).get(p) != found["sources"].get(p)
                for p in entry["sources"]
            )
        if old and case_id in registry:
            try:
                hashes = {a: digest(inside(project.root, a, exists=True)) for a in project.assets(old)}
                asset_changed = asset_changed or signature(hashes) != registry[case_id]["signature"]
            except Invalid:
                asset_changed = True
            if (
                inside(project.root, contract).exists()
                and inside(project.root, contract).read_text() != contract_text
            ):
                asset_changed = True
        if old and (old_clean != case or asset_changed):
            case["revision"] += 1
        if not old or case["revision"] != old["revision"]:
            events.append(
                {
                    "case": case,
                    "source": "discovery",
                    "action": "updated" if old else "created",
                    "previous_revision": old["revision"] if old else previous_revision,
                    "reason": "native inventory or test assets changed",
                }
            )
        writes[metadata_path] = yaml.safe_dump(case, sort_keys=False, allow_unicode=True)
        coverage[key] = [case_id]
    for case_id, old in generated.items():
        if case_id not in keep:
            writes[old["_path"]] = None
            events.append(
                {
                    "case": {k: v for k, v in old.items() if k != "_path"},
                    "source": "discovery",
                    "action": "retired",
                    "previous_revision": old["revision"],
                    "reason": "native entry removed",
                }
            )
    # Existing hand-written metadata remains authoritative; changed test assets
    # only advance its revision, never rewrite its assertions or selectors.
    for case_id, c in project.cases.items():
        if case_id in generated or case_id not in registry:
            continue
        hashes = {a: digest(inside(project.root, a, exists=True)) for a in project.assets(c)}
        if (
            signature(hashes) != registry[case_id]["signature"]
            and c["revision"] <= registry[case_id]["revision"]
        ):
            updated = {k: v for k, v in c.items() if k != "_path"}
            updated["revision"] = registry[case_id]["revision"] + 1
            writes[c["_path"]] = yaml.safe_dump(updated, sort_keys=False, allow_unicode=True)
            events.append(
                {
                    "case": updated,
                    "source": "discovery",
                    "action": "updated",
                    "previous_revision": c["revision"],
                    "reason": "declared test assets changed",
                }
            )
    found["coverage"] = coverage
    writes[manifest_name] = json.dumps(found, ensure_ascii=False, indent=2) + "\n"
    try:
        config_name = str(project.path.relative_to(project.root))
    except ValueError as e:
        raise Invalid("Discovery requires project config within project root") from e
    writes[config_name] = update_config_text(project.path.read_text(), project.config, config)
    # Do not touch unchanged files: repeat discovery is idempotent.
    writes = {
        name: value
        for name, value in writes.items()
        if (
            inside(project.root, name).exists()
            if value is None
            else not inside(project.root, name).exists() or inside(project.root, name).read_text() != value
        )
    }
    changes = []
    for name, value in sorted(writes.items()):
        path = inside(project.root, name)
        before = path.read_text() if path.exists() else ""
        action = "deleted" if value is None else "updated" if path.exists() else "created"
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                (value or "").splitlines(keepends=True),
                fromfile=name,
                tofile=name,
            )
        )
        changes.append({"path": name, "action": action, "diff": diff})
    return found, writes, events, changes


def preview(project, inventory):
    found, writes, _, changes = propose(project, inventory)
    return {**found, "needs_sync": bool(writes), "changes": changes}


def reconcile(project, inventory):
    with lock(project.artifacts / ".execution.lock"), lock(project.artifacts / ".catalog.lock"):
        recover(project)
        project = Project(project.path)
        found, writes, events, _ = propose(project, inventory)
        if writes:
            CatalogTransaction(project, writes, events).commit()
        return found
