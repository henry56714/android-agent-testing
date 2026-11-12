"""Discover native execution entries and reconcile them with the project catalog."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import yaml

from .catalog import atomic_bytes, recover
from .lifecycle import excluded as snapshot_excluded
from .process import execute
from .project import Project
from .storage import Invalid, digest, inside, lock, write_json

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


def gradle_argv(project):
    configured = project.config.get("discovery", {}).get("gradle_argv")
    if configured:
        return configured
    wrapper = project.config.get("tools", {}).get("gradle_wrapper", "./gradlew")
    if (project.root / wrapper).is_file():
        return [wrapper]
    for spec in project.tasks.values():
        argv = spec["argv"]
        for i, arg in enumerate(argv):
            if arg.startswith(":"):
                return argv[:i]
    return []


def native_inventory(project):
    argv = gradle_argv(project)
    if not argv:
        return {
            "projects": [],
            "includedBuilds": [],
            "unavailable": project.config["project"]["mode"] != "apk",
        }
    project.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="discovery-", dir=project.artifacts) as temp:
        output = Path(temp) / "inventory.json"
        script = Path(__file__).parent / "resources/discover.gradle"
        result = execute(
            [
                *argv,
                "--no-configuration-cache",
                "-I",
                str(script),
                f"-Daat.inventory.output={output}",
                "aatExportTestInventory",
                "--console=plain",
            ],
            project.root,
            Path(temp) / "process.log",
            300,
        )
        # Preserve diagnostics outside the temporary directory.
        atomic_bytes(project.artifacts / "discovery-gradle.log", (Path(temp) / "process.log").read_bytes())
        if result["reason"] or result["returncode"] != 0 or not output.exists():
            raise Invalid("Gradle discovery failed; inspect artifacts/discovery-gradle.log")
        return json.loads(output.read_text())


def scan(project, inventory=None):
    from .runner import source_state

    inventory = native_inventory(project) if inventory is None else inventory
    state = source_state(project)
    files = {p: sha for p, sha in state["files"].items() if sha != "deleted"}
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
            and not snapshot_excluded(project, str((parent / d).relative_to(project.root)) + "/")
        ]
        for name in names:
            path = parent / name
            relative_name = str(path.relative_to(project.root))
            if path.suffix not in extensions or snapshot_excluded(project, relative_name):
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
            try:
                reports = [relative(p) for p in spec["reports"]]
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
                }
            )
    # Maestro entrypoints are discovered from native YAML, with child-flow graph deduplication.
    flows, children = {}, set()
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
                    children.add(relative(str(Path(parent).parent / target)))
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
    result = {
        "schema_version": 1,
        "entries": sorted(entries, key=lambda e: e["key"]),
        "sources": dict(sorted(candidates.items())),
        "gaps": gaps,
        "excluded": excluded,
    }
    result["fingerprint"] = signature(result)
    return result


def reconcile(project, inventory=None):
    """Create/update only generated metadata; journal all writes as one catalog transaction."""
    with lock(project.artifacts / ".execution.lock"), lock(project.artifacts / ".catalog.lock"):
        recover(project)
        project = Project(project.path)
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
        generated = {
            c["id"]: c for c in project.cases.values() if c.get("origin", {}).get("kind") == "discovery"
        }
        tasks = config.setdefault("tasks", {})
        writes, keep, coverage = {}, set(), {}
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
            case = {
                "schema_version": 1,
                "id": case_id,
                "revision": old["revision"] if old else 1,
                "status": old["status"] if old else "active",
                "title": key,
                "layer": {"junit": "local", "lint": "static", "gradle-device": "device", "maestro": "ui"}[
                    engine
                ],
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
            registry_file = project.artifacts / "case-revisions.json"
            registry = json.loads(registry_file.read_text()) if registry_file.exists() else {}
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
            writes[metadata_path] = yaml.safe_dump(case, sort_keys=False, allow_unicode=True)
            coverage[key] = [case_id]
        for case_id, old in generated.items():
            if case_id not in keep:
                writes[old["_path"]] = None
        # Existing hand-written metadata remains authoritative; changed test assets
        # only advance its revision, never rewrite its assertions or selectors.
        registry_file = project.artifacts / "case-revisions.json"
        registry = json.loads(registry_file.read_text()) if registry_file.exists() else {}
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
        found["coverage"] = coverage
        writes[manifest_name] = json.dumps(found, ensure_ascii=False, indent=2) + "\n"
        try:
            config_name = str(project.path.relative_to(project.root))
        except ValueError as e:
            raise Invalid("Discovery requires project config within project root") from e
        writes[config_name] = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
        # Do not touch unchanged files: repeat discovery is idempotent.
        writes = {
            name: value
            for name, value in writes.items()
            if (
                inside(project.root, name).exists()
                if value is None
                else not inside(project.root, name).exists()
                or inside(project.root, name).read_text() != value
            )
        }
        if writes:
            before = {
                name: base64.b64encode(inside(project.root, name).read_bytes()).decode()
                if inside(project.root, name).exists()
                else None
                for name in writes
            }
            journal = project.artifacts / "catalog-transaction.json"
            write_json(journal, {"state": "writing", "before": before})
            try:
                for name, value in writes.items():
                    p = inside(project.root, name)
                    if value is None:
                        p.unlink(missing_ok=True)
                    else:
                        atomic_bytes(p, value.encode())
                Project(project.path, allow_pending=True)
                write_json(journal, {"state": "committed", "before": before})
                journal.unlink()
            except BaseException:
                recover(project)
                raise
        return found
