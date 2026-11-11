import sys

import pytest
import yaml

from aat.project import Project


@pytest.fixture
def project_factory(tmp_path):
    counter = 0

    def factory(body=None, case_update=None, config_update=None):
        nonlocal counter
        counter += 1
        root = tmp_path / f"project{counter}"
        root.mkdir()
        (root / "cases").mkdir()
        (root / "contract.md").write_text("Expected: selected test asserts the required behavior.\n")
        xml = '<testsuite tests="1"><testcase classname="Example" name="works"/></testsuite>'
        body = (
            body
            if body is not None
            else f"from pathlib import Path\np=Path('build/result.xml');p.parent.mkdir(exist_ok=True);p.write_text({xml!r})\n"
        )
        (root / "test_runner.py").write_text(body)
        config = {
            "schema_version": 1,
            "project": {"id": "test.project", "mode": "source", "root": "."},
            "catalog": "cases",
            "artifacts": ".aat/runs",
            "tasks": {
                "test": {
                    "argv": [sys.executable, "test_runner.py"],
                    "reports": ["build/*.xml"],
                    "timeout_seconds": 5,
                }
            },
        }
        if config_update:
            config_update(config)
        case = {
            "schema_version": 1,
            "id": "test.works",
            "revision": 1,
            "status": "active",
            "title": "Works",
            "layer": "local",
            "contract": "contract.md",
            "implementation": {
                "engine": "junit",
                "task": "test",
                "selector": "Example#works",
                "path": "test_runner.py",
            },
            "timeout_seconds": 5,
            "tags": ["smoke"],
            "impact": {"paths": ["src/**"]},
        }
        if case_update:
            case_update(case)
        path = root / "android-testing.yaml"
        path.write_text(yaml.safe_dump(config))
        (root / "cases" / "works.yaml").write_text(yaml.safe_dump(case))
        return Project(path)

    return factory
