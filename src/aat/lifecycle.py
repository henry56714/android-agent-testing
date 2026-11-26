from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import time
from pathlib import Path

import yaml

from .storage import Invalid, digest, inside, lock, write_json

DEFAULT_EXCLUDES = [".claude/**", ".agents/**", ".codex/**", ".idea/**", "**/__pycache__/**"]


def excluded(project, name):
    return any(
        fnmatch.fnmatch(name, pat)
        for pat in DEFAULT_EXCLUDES + project.config.get("snapshot", {}).get("exclude", [])
    )


def snapshot_source(project, state, directory):
    limit = project.config.get("snapshot", {}).get("max_bytes", 512 * 1024 * 1024)
    total = 0
    for name, sha in state["files"].items():
        if sha == "deleted":
            continue
        src = inside(project.root, name, exists=True)
        total += src.stat().st_size
        if total > limit:
            raise Invalid("Source snapshot size exceeds configured max_bytes")
        dst = inside(directory / "source", name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if digest(dst) != sha:
            raise Invalid("Source changed while snapshotting")


def restore(project, run_id, destination):
    from .runner import run_directory

    directory = run_directory(project, run_id)
    plan = json.loads((directory / "plan.json").read_text())
    if not plan.get("source_snapshot"):
        raise Invalid("Legacy run has no complete source snapshot")
    destination = Path(destination).resolve()
    if destination.exists():
        raise Invalid("Replay destination must not exist")
    if destination.is_relative_to(project.root):
        raise Invalid("Replay destination must be outside the original project")
    # Validate before creating an output directory.
    for name, sha in plan["source"]["files"].items():
        if sha != "deleted" and digest(inside(directory / "source", name, exists=True)) != sha:
            raise Invalid(f"Source snapshot damaged: {name}")
    for name, sha in plan["assets"].items():
        if digest(inside(directory / "snapshot", name, exists=True)) != sha:
            raise Invalid(f"Test snapshot damaged: {name}")
    destination.mkdir(parents=True)
    try:
        shutil.copytree(directory / "source", destination, dirs_exist_ok=True)
        shutil.copytree(directory / "snapshot", destination, dirs_exist_ok=True)
        config = yaml.safe_load((directory / "project.yaml").read_text())
        config["project"]["root"] = "."
        cfg = destination / "android-testing.yaml"
        cfg.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
        return {
            "restored": True,
            "project": str(cfg),
            "origin_run_id": run_id,
            "cases": [c["id"] for c in plan["cases"]],
            "limits": "External tools/SDK/dependencies and secret environment must be available",
        }
    except BaseException:
        shutil.rmtree(destination)
        raise


def read_report(project, run_id):
    from .runner import run_directory

    directory = run_directory(project, run_id)
    if (directory / "result.json").exists():
        return json.loads((directory / "result.json").read_text())
    return {"run_id": run_id, "state": "INCOMPLETE" if (directory / "started.json").exists() else "PLANNED"}


def prune(project, days=None, apply=False):
    policy = project.config.get("retention", {})
    days = policy.get("days", 30) if days is None else days
    if days < 0:
        raise Invalid("days cannot be negative")
    candidates = []
    with lock(project.artifacts / ".execution.lock"):
        for p in sorted(project.artifacts.glob("*/result.json")):
            r = json.loads(p.read_text())
            if (p.parent / "pinned.json").exists() or (
                policy.get("keep_failures", True)
                and (r["gate"] != "passed" or any(c["outcome"] != "PASS" for c in r["cases"]))
            ):
                continue
            if time.time() - p.stat().st_mtime < days * 86400:
                continue
            candidates.append(p.parent.name)
            if not apply:
                continue
            for name in ("source", "snapshot", "products", "cases", "tasks", "install", "capabilities"):
                child = inside(p.parent, name)
                if child.exists():
                    shutil.rmtree(child)
            for evidence in r.get("evidence_manifest", {}).values():
                evidence["state"] = "expired"
            (p.parent / "progress.json").unlink(missing_ok=True)
            r["artifacts_expired"] = True
            write_json(p, r)
        return {"dry_run": not apply, "runs": candidates, "days": days}


class Redactor:
    """Sanitize report values before persistence; snapshots stay private."""

    def __init__(self, project):
        settings = project.config.get("privacy", {})
        self.secrets = [os.environ[k] for k in settings.get("redact_env", []) if os.environ.get(k)]
        self.patterns = [re.compile(p) for p in settings.get("patterns", [])]

    def text(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        for pattern in self.patterns:
            text = pattern.sub("[REDACTED]", text)
        return text

    def values(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.values(item) for item in value]
        if isinstance(value, dict):
            return {self.text(k): self.values(v) for k, v in value.items()}
        return value


def redact(project, directory, result):
    cleaner = Redactor(project)
    for folder in ("tasks", "cases", "capabilities", "install"):
        for path in (directory / folder).rglob("*"):
            if path.is_file() and path.suffix in (".log", ".txt", ".json", ".xml", ".html"):
                path.write_text(cleaner.text(path.read_text(errors="replace")))
    return cleaner.values(result)
