"""Build distributions and execute the installed wheel outside the source tree."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from record_check import record


def main(directory):
    root = Path.cwd()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    packages = directory / "packages"
    environment = directory / "wheel-env"
    for name, command in [
        ("build", ["uv", "build", "--out-dir", str(packages)]),
        ("venv", ["uv", "venv", "--python", sys.executable, str(environment)]),
    ]:
        assert record(directory, name, command)["passed"], name
    wheel = next(packages.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "aat/resources/discover.gradle" in names
        assert any("/licenses/LICENSE" in name for name in names)
        assert any("PyYAML-LICENSE.txt" in name for name in names)
        assert not any("/.aat/" in name or name.endswith((".apk", ".aar")) for name in names)
    python = environment / "bin/python"
    assert record(directory, "install", ["uv", "pip", "install", "--python", str(python), str(wheel)])[
        "passed"
    ]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="aat-wheel-smoke-") as temp:
        work = Path(temp)
        check = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import aat, importlib.resources as r; from importlib.metadata import version; "
                'assert aat.__version__ == version("android-agent-testing"); '
                'assert "aatExportTestInventory" in r.files("aat").joinpath("resources/discover.gradle").read_text(); '
                "print(aat.__file__)",
            ],
            cwd=work,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        assert str(root / "src") not in check.stdout
        cli = [str(python), "-I", "-m", "aat.cli"]

        def invoke(name, args):
            receipt = record(directory, name, [*cli, *args], cwd=work, env=env)
            assert receipt["passed"], name
            return json.loads((directory / receipt["log"]).read_text())["data"]

        schema = invoke("schema", ["schema", "case"])
        assert schema["additionalProperties"] is False
        invoke("init", ["init", "--id", "wheel.smoke"])
        config = {
            "schema_version": 1,
            "project": {"id": "wheel.smoke", "mode": "source", "root": "."},
            "catalog": "testing/cases",
            "artifacts": ".aat/runs",
            "tasks": {
                "unit": {
                    "argv": [str(python), "test_runner.py"],
                    "timeout_seconds": 5,
                    "reports": ["build/report.xml"],
                }
            },
        }
        case = {
            "schema_version": 1,
            "id": "wheel.local",
            "revision": 1,
            "status": "active",
            "title": "Native XML in isolated wheel environment",
            "layer": "local",
            "contract": "contract.txt",
            "implementation": {"engine": "junit", "task": "unit", "selector": "Wheel#works"},
            "timeout_seconds": 5,
        }
        (work / "android-testing.yaml").write_text(json.dumps(config))
        (work / "testing/cases/unit.yaml").write_text(json.dumps(case))
        (work / "contract.txt").write_text("Exactly one native method executes and passes.\n")
        (work / "test_runner.py").write_text(
            'from pathlib import Path\np=Path(\'build/report.xml\');p.parent.mkdir(exist_ok=True);p.write_text(\'<testsuite tests="1"><testcase classname="Wheel" name="works"/></testsuite>\')\n'
        )
        result = invoke("run", ["run"])
        assert result["gate"] == "passed" and result["cases"][0]["outcome"] == "PASS"
        assert invoke("report", ["report", result["run_id"]])["run_id"] == result["run_id"]
    result = {
        "passed": True,
        "wheel": str(wheel),
        "python": str(python),
        "installed_module": check.stdout.strip(),
    }
    (directory / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    main(parser.parse_args().output)
