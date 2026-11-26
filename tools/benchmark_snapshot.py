"""Fixed synthetic source/asset workload; report wall time and physical copy volume."""

from __future__ import annotations

import inspect
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import yaml

from aat.project import Project
from aat.runner import freeze, source_state


def main():
    with tempfile.TemporaryDirectory(prefix="aat-benchmark-") as temporary:
        root = Path(temporary)
        (root / "cases").mkdir()
        (root / "fixtures").mkdir()
        (root / "contract.md").write_text("A synthetic performance workload.\n")
        assets = []
        for i in range(256):
            name = f"fixtures/input-{i:04}.txt"
            (root / name).write_bytes((f"{i:04}".encode() * 16384))
            assets.append(name)
        for i in range(100):
            directory = root / "build" / str(i)
            directory.mkdir(parents=True)
            for j in range(20):
                (directory / str(j)).write_bytes(b"ignored build output\n")
        config = {
            "schema_version": 1,
            "project": {"id": "benchmark", "mode": "source", "root": "."},
            "catalog": "cases",
            "artifacts": ".aat/runs",
            "tasks": {
                "test": {
                    "argv": [sys.executable, "-c", "pass"],
                    "timeout_seconds": 5,
                    "reports": ["build/report.xml"],
                }
            },
        }
        case = {
            "schema_version": 1,
            "id": "benchmark.test",
            "revision": 1,
            "status": "active",
            "title": "Snapshot workload",
            "layer": "local",
            "contract": "contract.md",
            "implementation": {"engine": "junit", "task": "test", "selector": "*"},
            "assets": assets,
            "timeout_seconds": 5,
        }
        (root / "android-testing.yaml").write_text(yaml.safe_dump(config))
        (root / "cases/test.yaml").write_text(yaml.safe_dump(case))
        project = Project(root / "android-testing.yaml")
        times = []
        for _ in range(3):
            start = time.perf_counter()
            source_state(project)
            times.append(time.perf_counter() - start)
        copied = {"calls": 0, "bytes": 0}
        original = shutil.copy2

        def copy(src, dest, *args, **kwargs):
            copied["calls"] += 1
            copied["bytes"] += Path(src).stat().st_size
            return original(src, dest, *args, **kwargs)

        start = time.perf_counter()
        with patch("shutil.copy2", copy):
            plan = freeze(project, project.select())
        elapsed = time.perf_counter() - start
        directory = project.artifacts / plan["run_id"]
        files = [path for path in directory.rglob("*") if path.is_file()]
        physical = {}
        for path in files:
            stat = path.stat()
            physical[(stat.st_dev, stat.st_ino)] = stat.st_size
        print(
            json.dumps(
                {
                    "workload": {"asset_files": 256, "asset_bytes": 16777216, "ignored_build_files": 2000},
                    "source_scan_seconds": times,
                    "freeze_seconds": elapsed,
                    "copy2": copied,
                    "run_logical_bytes": sum(p.stat().st_size for p in files),
                    "run_unique_inode_bytes": sum(physical.values()),
                    "source_module": inspect.getmodule(source_state).__name__,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
