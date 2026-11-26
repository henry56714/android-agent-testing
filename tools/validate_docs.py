"""Check the two project manuals, local links, examples and workflow syntax."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import jsonschema
import yaml

from aat.project import Project
from aat.schema import CASE, CONFIG


def main():
    root = Path.cwd()
    documents = [root / "README.md", root / "docs/technical-design.md"]
    assert list(root.glob("*.md")) == [root / "README.md"]
    assert list((root / "docs").glob("*.md")) == [root / "docs/technical-design.md"]
    links = 0
    for document in documents:
        text = document.read_text()
        assert text.count("```") % 2 == 0, document
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if "://" in target:
                continue
            name, _, anchor = target.partition("#")
            path = (document.parent / name).resolve() if name else document
            assert path.exists(), (document, target)
            if anchor.startswith("L") and anchor[1:].isdigit():
                assert int(anchor[1:]) <= len(path.read_text().splitlines()), target
            elif anchor:
                assert f'id="{anchor}"' in path.read_text(), (document, target)
            links += 1
        for language, block in re.findall(r"```(\w+)\n(.*?)```", text, re.S):
            if language == "bash":
                subprocess.run(["bash", "-n"], input=block, text=True, check=True)
            elif language == "yaml":
                value = yaml.safe_load(block)
                if "case" in value:
                    jsonschema.validate(value["case"], CASE)
                else:
                    config = {
                        "schema_version": 1,
                        "project": {"id": "example", "mode": "source", "root": "."},
                        "catalog": "testing/cases",
                        "artifacts": ".aat/runs",
                    }
                    jsonschema.validate({**config, **value}, CONFIG)
    for sample in ("counter-app", "worker-sdk"):
        project = Project(root / "examples" / sample / "android-testing.yaml")
        print(sample, len(project.cases), "case assets valid")
    Project(root / "examples/apk-only/android-testing.yaml", catalog=False)
    for path in (root / ".github/workflows").glob("*.yml"):
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        assert "on" in workflow and "jobs" in workflow, path
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                if "run" in step and "${{" not in step["run"]:
                    subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)
    print(f"{links} local links, two manuals, shell/YAML examples and workflow syntax checked")


if __name__ == "__main__":
    main()
