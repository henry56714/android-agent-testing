"""Query the evaluated native build; no catalog mutation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .process import execute
from .storage import Invalid
from .transaction import atomic_bytes


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
