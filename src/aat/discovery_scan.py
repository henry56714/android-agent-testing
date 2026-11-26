"""Map a native inventory and workspace candidates to auditable execution entries."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from pathlib import Path

import yaml

from .discovery_model import native_inventory
from .lifecycle import excluded as snapshot_excluded
from .storage import Invalid, digest, inside

MAINTENANCE = {
    "check",
    "test",
    "lint",
    "connectedCheck",
    "connectedAndroidTest",
    "deviceCheck",
    "deviceAndroidTest",
    "lintFix",
    "updateLintBaseline",
    "checkJetifier",
}


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def scan(project, inventory=None):
    inventory = native_inventory(project) if inventory is None else inventory
    files = {}
    settings = project.config.get("discovery", {})
    gaps, excluded, entries, candidates, covered = [], [], [], {}, set()
    # Existing tests can be Git-ignored. Scan the workspace too, pruning build
    # outputs/caches rather than treating Git's tracked-file list as completeness.
    skip_dirs = {".git", ".aat", ".gradle", ".venv", "venv", "build", "dist", "node_modules", "__pycache__"}
    extensions = {
        ".java",
        ".kt",
        ".groovy",
        ".scala",
        ".yaml",
        ".yml",
        ".py",
        ".js",
        ".ts",
        ".sh",
        ".cpp",
        ".cc",
        ".c",
        ".feature",
    }
    for root, dirs, names in os.walk(project.root, followlinks=False):
        parent = Path(root)
        dirs[:] = [
            d
            for d in dirs
            if d not in skip_dirs
            and not (parent / d).is_symlink()
            and not (parent / d).resolve().is_relative_to(project.artifacts)
        ]
        for name in names:
            path = parent / name
            relative_name = str(path.relative_to(project.root))
            if path.suffix not in extensions:
                continue
            try:
                files[relative_name] = digest(inside(project.root, relative_name, exists=True))
            except Invalid:
                gaps.append({"item": relative_name, "reason": "EXTERNAL_TEST_CANDIDATE"})

    def ignored(name):
        for item in settings.get("exclude", []):
            if fnmatch.fnmatch(name, item["pattern"]):
                excluded.append({"item": name, "reason": item["reason"]})
                return True
        return False

    def relative(value):
        return str(inside(project.root, value).relative_to(project.root))

    def source_files(dirs):
        prefixes = []
        for directory in dirs:
            try:
                prefixes.append(relative(directory).rstrip("/") + "/")
                # Explicit native source roots may be generated/ignored and thus
                # absent from the ordinary repository walk.
                for path in inside(project.root, directory).rglob("*"):
                    if path.is_file() and path.suffix in (".java", ".kt", ".groovy", ".scala"):
                        name = relative(str(path))
                        if name not in files:
                            files[name] = digest(inside(project.root, name, exists=True))
            except Invalid:
                gaps.append({"item": directory, "reason": "EXTERNAL_SOURCE_ROOT"})
        return sorted(
            p
            for p in files
            if p.endswith((".java", ".kt", ".groovy", ".scala"))
            and any(p.startswith(prefix) for prefix in prefixes)
        )

    # Find native source files even when a module/source set was omitted by the build.
    for path, sha in files.items():
        parts = Path(path).parts
        conventional = any(
            x.lower().startswith(("test", "androidtest")) and not x.lower().startswith("testfixtures")
            for x in parts[:-1]
        )
        named = bool(
            re.search(
                r"(^test_.*\.py$|.*[._](test|spec)\.[cm]?[jt]sx?$|.*_test\.(cpp|cc|c)$|.*\.feature$)",
                Path(path).name,
            )
        )
        if (
            conventional
            and path.endswith((".java", ".kt", ".groovy", ".scala", ".py", ".js", ".ts", ".sh", ".cpp"))
        ) or named:
            candidates[path] = sha

    if inventory.get("unavailable"):
        gaps.append({"item": "gradle", "reason": "BUILD_DISCOVERY_UNAVAILABLE"})
    for included in inventory.get("includedBuilds", []):
        if not ignored(included):
            gaps.append({"item": included, "reason": "INCLUDED_BUILD_NEEDS_SEPARATE_ADAPTER"})
    for module in inventory["projects"]:
        for spec in module["tasks"]:
            key, kind = spec["path"], spec["kind"]
            if ignored(key):
                continue
            if kind == "verification":
                if (spec["name"] not in MAINTENANCE and not spec["name"].startswith("lintVital")) or (
                    spec["name"] in ("check", "test") and spec.get("hasActions")
                ):
                    mapped = settings.get("task_cases", {}).get(key, [])
                    if mapped and all(
                        x in project.cases
                        and project.cases[x]["status"] == "active"
                        and project.cases[x].get("required", True)
                        for x in mapped
                    ):
                        entries.append({"key": key, "engine": "mapped", "case_ids": mapped, "sources": []})
                    else:
                        gaps.append({"item": key, "reason": "UNMAPPED_VERIFICATION_TASK"})
                continue
            sources = []
            if kind in ("junit", "gradle-device"):
                prefix = "androidTest" if kind == "gradle-device" else "test"
                explicit_dirs = settings.get("task_sources", {}).get(key)
                if explicit_dirs is not None:
                    sources = source_files(explicit_dirs)
                elif spec.get("sourceOrigin") == "unavailable":
                    gaps.append({"item": key, "reason": "UNKNOWN_TEST_SOURCE_MODEL"})
                    continue
                elif spec.get("sourceDirs") is not None:
                    sources = source_files(spec["sourceDirs"])
                else:
                    # Compatibility with v1 inventories; new native queries always
                    # identify source directories through the evaluated model.
                    for s in module["sources"]:
                        name = s["name"]
                        if name.startswith("testFixtures"):
                            continue
                        if module["android"]:
                            variant = spec["name"][9:-11] if kind == "gradle-device" else spec["name"][4:-8]
                            matches = name == prefix or (
                                name.startswith(prefix) and name[len(prefix) :].lower() in variant.lower()
                            )
                        else:
                            matches = name != "main" and (
                                name == "test" or "test" in name.lower() or name == spec["name"]
                            )
                        if matches:
                            sources.extend(source_files(s["dirs"]))
                sources = sorted(set(sources))
                candidates.update({p: files[p] for p in sources})
                if not sources:
                    # No source in the conventional set can still mean a custom/generated Test task.
                    if module["android"]:
                        excluded.append({"item": key, "reason": "NO_TEST_SOURCE_IN_VARIANT"})
                        continue
                    if spec["name"] == "test" and not any(
                        "test" in s["name"].lower() for s in module["sources"]
                    ):
                        continue
                covered.update(sources)
            if not spec["enabled"]:
                gaps.append({"item": key, "reason": "DISABLED_TEST_TASK"})
            if kind in ("junit", "gradle-device") and (
                any(spec.get("filters", {}).values()) or not spec.get("xmlEnabled", True)
            ):
                gaps.append({"item": key, "reason": "FILTERED_OR_UNREPORTED_TEST_TASK"})
            report_paths = settings.get("task_reports", {}).get(key, spec.get("reports", []))
            if not report_paths:
                gaps.append({"item": key, "reason": "UNKNOWN_REPORT_MODEL"})
                continue
            if kind == "lint" and not spec.get("xmlEnabled", True):
                gaps.append({"item": key, "reason": "FILTERED_OR_UNREPORTED_TEST_TASK"})
            try:
                reports = [relative(p) for p in report_paths]
            except Invalid:
                gaps.append({"item": key, "reason": "EXTERNAL_REPORT_PATH"})
                continue
            entries.append(
                {
                    "key": key,
                    "engine": kind,
                    "sources": sources,
                    "reports": reports,
                    "enabled": spec["enabled"],
                    "filters": spec.get("filters", {}),
                    "report_origin": "explicit"
                    if key in settings.get("task_reports", {})
                    else spec.get("reportOrigin", "legacy-inventory"),
                    "source_origin": "explicit"
                    if key in settings.get("task_sources", {})
                    else spec.get("sourceOrigin", "legacy-inventory"),
                }
            )
    # Maestro entrypoints are discovered from native YAML, with child-flow graph deduplication.
    flows, children, edges = {}, set(), {}
    for path in files:
        if not path.endswith((".yaml", ".yml")):
            continue
        try:
            docs = list(yaml.safe_load_all(inside(project.root, path).read_text()))
        except yaml.YAMLError:
            if ".maestro" in Path(path).parts or "maestro" in Path(path).parts:
                gaps.append({"item": path, "reason": "INVALID_MAESTRO_YAML"})
            continue
        if len(docs) >= 2 and isinstance(docs[0], dict) and "appId" in docs[0] and isinstance(docs[1], list):
            flows[path] = docs
            candidates[path] = files[path]

    def dependencies(node, parent):
        if isinstance(node, list):
            for item in node:
                dependencies(item, parent)
        elif isinstance(node, dict):
            flow = node.get("runFlow")
            target = flow if isinstance(flow, str) else flow.get("file") if isinstance(flow, dict) else None
            if target and "${" not in target:
                try:
                    child = relative(str(Path(parent).parent / target))
                    children.add(child)
                    edges.setdefault(parent, set()).add(child)
                except Invalid:
                    gaps.append({"item": parent, "reason": "EXTERNAL_MAESTRO_FLOW"})
            elif target:
                gaps.append({"item": parent, "reason": "DYNAMIC_FLOW_REFERENCE"})
            for value in node.values():
                dependencies(value, parent)

    for path, docs in flows.items():
        dependencies(docs[1:], path)
    roots = set(flows) - children
    if flows and not roots:
        gaps.append({"item": "maestro", "reason": "FLOW_GRAPH_HAS_NO_ENTRYPOINT"})
    reachable, pending = set(), list(roots)
    while pending:
        flow = pending.pop()
        if flow not in reachable:
            reachable.add(flow)
            pending.extend(edges.get(flow, set()))
    for path in sorted(set(flows) - reachable):
        gaps.append({"item": path, "reason": "UNREACHABLE_FLOW"})
    for path in sorted(roots):
        if not ignored(path):
            entries.append({"key": path, "engine": "maestro", "sources": [path], "reports": []})
    covered.update(flows)
    for path in sorted(candidates):
        if ignored(path):
            covered.add(path)
        elif path not in covered:
            mapped = settings.get("source_cases", {}).get(path, [])
            if mapped and all(
                x in project.cases
                and project.cases[x]["status"] == "active"
                and project.cases[x].get("required", True)
                for x in mapped
            ):
                entries.append({"key": path, "engine": "mapped", "case_ids": mapped, "sources": [path]})
            else:
                gaps.append({"item": path, "reason": "UNMANAGED_TEST_SOURCE"})
    # Privacy controls storage, never the set of tests that must be audited.
    private_tests = {p for p in candidates if snapshot_excluded(project, p)}
    for path in sorted(private_tests):
        gaps.append({"item": path, "reason": "SNAPSHOT_EXCLUDED_TEST"})
    blocked_flows = set(private_tests)
    changed = True
    while changed:
        parents = {parent for parent, deps in edges.items() if deps & blocked_flows}
        changed = not parents <= blocked_flows
        blocked_flows.update(parents)
    entries = [e for e in entries if not set(e["sources"]) & private_tests and e["key"] not in blocked_flows]
    result = {
        "schema_version": 1,
        "native_model": {
            "version": inventory.get("model_version", 1),
            "gradle": inventory.get("gradleVersion"),
            "android_plugins": {
                m["path"]: m.get("androidPluginVersion") for m in inventory["projects"] if m["android"]
            },
        },
        "entries": sorted(entries, key=lambda e: e["key"]),
        "sources": dict(sorted(candidates.items())),
        "gaps": gaps,
        "excluded": excluded,
    }
    result["fingerprint"] = signature(result)
    return result
