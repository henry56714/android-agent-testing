from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .schema import CASE, CONFIG, SUITE
from .storage import Invalid, inside, read_yaml


class Project:
    def __init__(self, path, *, catalog=True, allow_pending=False):
        self.path = Path(path).resolve()
        self.config = read_yaml(self.path, CONFIG)
        self.root = (self.path.parent / self.config["project"]["root"]).resolve()
        if not self.root.is_dir():
            raise Invalid("Project root does not exist")
        self.artifacts = inside(self.root, self.config["artifacts"])
        if self.artifacts == self.root:
            raise Invalid("Artifacts cannot be project root")
        if catalog and not allow_pending and (self.artifacts / "catalog-transaction.json").exists():
            raise Invalid("Incomplete catalog transaction; run cases recover before reading")
        self.tasks = self.config.get("tasks", {})
        for pattern in self.config.get("privacy", {}).get("patterns", []):
            try:
                re.compile(pattern)
            except re.error as e:
                raise Invalid(f"Invalid redaction pattern: {e}") from e
        instrumentation = self.config.get("instrumentation", {})
        for value in instrumentation.values():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", value):
                raise Invalid("Invalid instrumentation package/runner")
        self.order(list(self.tasks))
        for task in self.tasks.values():
            for pattern in task.get("reports", []):
                inside(self.root, pattern)
        for name, product in self.config.get("products", {}).items():
            if "input_path" in product:
                if set(product) != {"input_path"}:
                    raise Invalid(f"{name}: input_path is exclusive")
                inside(self.root, product["input_path"], exists=catalog)
            else:
                if set(product) != {"from_task", "path"} or product["from_task"] not in self.tasks:
                    raise Invalid(f"{name}: product needs path and existing from_task")
                inside(self.root, product["path"])
        self.cases = self._catalog() if catalog else {}
        for spec in self.config.get("capabilities", {}).values():
            if spec.get("depends_on") or spec.get("reports"):
                raise Invalid("Capability probes use argv/timeout only")

    def order(self, names):
        done, visiting, result = set(), set(), []

        def visit(name):
            if name not in self.tasks:
                raise Invalid(f"Unknown task: {name}")
            if name in visiting:
                raise Invalid(f"Task dependency cycle: {name}")
            if name in done:
                return
            visiting.add(name)
            for dep in self.tasks[name].get("depends_on", []):
                visit(dep)
            visiting.remove(name)
            done.add(name)
            result.append(name)

        for name in names:
            visit(name)
        return result

    def _catalog(self):
        directory = inside(self.root, self.config["catalog"])
        if not directory.is_dir():
            raise Invalid("Catalog directory missing")
        cases = {}
        for path in sorted(list(directory.rglob("*.yaml")) + list(directory.rglob("*.yml"))):
            inside(self.root, str(path), exists=True)
            c = read_yaml(path, CASE)
            if c["id"] in cases:
                raise Invalid(f"Duplicate case ID: {c['id']}")
            c["_path"] = str(path.relative_to(self.root))
            impl = c["implementation"]
            engine = impl["engine"]
            if (
                c["layer"]
                != {"junit": "local", "lint": "static", "maestro": "ui", "instrumentation": "device"}[engine]
            ):
                raise Invalid(f"{c['id']}: layer does not match engine")
            if engine in ("junit", "lint"):
                if impl.get("task") not in self.tasks:
                    raise Invalid(f"{c['id']}: missing task")
                if not self.tasks[impl["task"]].get("reports"):
                    raise Invalid(f"{c['id']}: reports required")
            if engine in ("junit", "instrumentation") and not impl.get("selector"):
                raise Invalid(f"{c['id']}: selector required")
            if engine == "instrumentation" and not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.$]*#[A-Za-z_][A-Za-z0-9_]*", impl["selector"]
            ):
                raise Invalid("Instrumentation v1 requires an exact Class#method selector")
            if engine == "maestro" and not impl.get("path"):
                raise Invalid(f"{c['id']}: flow path required")
            if c["status"] in ("quarantined", "deprecated") and not c.get("reason"):
                raise Invalid(f"{c['id']}: status reason required")
            for asset in self.assets(c):
                inside(self.root, asset, exists=True)
            cases[c["id"]] = c
        return cases

    def assets(self, c):
        files = [c["_path"], c["contract"], *c.get("assets", [])]
        if c["implementation"].get("path"):
            files.append(c["implementation"]["path"])
        # Statically discover nested Maestro flows/scripts; dynamic paths stay explicit assets.
        if c["implementation"]["engine"] == "maestro":
            import yaml

            pending = [inside(self.root, c["implementation"]["path"], exists=True)]
            visited = set()
            while pending:
                path = pending.pop()
                if path in visited:
                    continue
                visited.add(path)
                files.append(str(path.relative_to(self.root)))

                def walk(node):
                    if isinstance(node, list):
                        for item in node:
                            walk(item)
                    if not isinstance(node, dict):
                        return
                    for key, value in node.items():
                        if key in ("runFlow", "runScript"):
                            value_path = (
                                value
                                if isinstance(value, str)
                                else value.get("file")
                                if isinstance(value, dict)
                                else None
                            )
                            if value_path and "${" not in value_path:
                                dep = inside(self.root, str(path.parent / value_path), exists=True)
                                files.append(str(dep.relative_to(self.root)))
                                if key == "runFlow":
                                    pending.append(dep)
                        walk(value)

                for document in yaml.safe_load_all(path.read_text()):
                    walk(document)
        return sorted({str(inside(self.root, name).relative_to(self.root)) for name in files})

    def select(self, ids=(), tags=(), suite=None, changed=()):
        ids, tags = set(ids), set(tags)
        if suite:
            directory = inside(self.root, self.config.get("suites", "testing/suites"))
            matches = [read_yaml(p, SUITE) for p in sorted(directory.glob("*.yaml"))]
            matches = [s for s in matches if s["id"] == suite]
            if len(matches) != 1:
                raise Invalid(f"Suite must resolve exactly once: {suite}")
            ids.update(matches[0].get("cases", []))
            tags.update(matches[0].get("tags", []))
            if not ids and not tags:
                raise Invalid("Empty suite")
        if ids - self.cases.keys():
            raise Invalid(f"Unknown case IDs: {sorted(ids - self.cases.keys())}")
        selected = []
        for c in self.cases.values():
            match = (
                (not ids and not tags and not changed)
                or c["id"] in ids
                or tags.intersection(c.get("tags", []))
            )
            match = match or any(
                fnmatch.fnmatch(p, pat) for p in changed for pat in c.get("impact", {}).get("paths", [])
            )
            if match:
                selected.append(c)
        if not selected:
            raise Invalid("Plan contains zero cases")
        return selected
