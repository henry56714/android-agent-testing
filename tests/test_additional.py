import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from aat.project import Project
from aat.results import junit
from aat.runner import freeze, load_plan, run
from aat.storage import Invalid


def test_source_change_does_not_require_case_revision(project_factory):
    p = project_factory()
    (p.root / "production.java").write_text("old implementation")
    old = freeze(p, p.select())
    (p.root / "production.java").write_text("new implementation")
    with pytest.raises(Invalid, match="source inputs"):
        load_plan(p, old["run_id"])
    new = freeze(p, p.select())
    assert new["cases"][0]["revision"] == 1
    assert new["source"]["fingerprint"] != old["source"]["fingerprint"]


def test_business_change_during_execution_invalidates_pass(project_factory):
    p = project_factory(
        body="from pathlib import Path\nPath('production.java').write_text('mutated')\np=Path('build/result.xml');p.parent.mkdir(exist_ok=True);p.write_text('<testsuite><testcase classname=\"Example\" name=\"works\"/></testsuite>')"
    )
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 4 and result["cases"][0]["reason_code"] == "SOURCE_CHANGED_DURING_RUN"


def test_missing_command(project_factory):
    p = project_factory(config_update=lambda c: c["tasks"]["test"].update(argv=["aat-no-such-command-123"]))
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 4 and result["cases"][0]["reason_code"] == "COMMAND_UNAVAILABLE"


def test_dependency_failure_prevents_child(project_factory):
    def config(c):
        c["tasks"]["setup"] = {"argv": [sys.executable, "-c", "raise SystemExit(9)"], "timeout_seconds": 3}
        c["tasks"]["test"]["depends_on"] = ["setup"]

    p = project_factory(config_update=config)
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 4 and result["cases"][0]["reason_code"] == "DEPENDENCY_FAILED"
    assert not (p.root / "build/result.xml").exists()


@pytest.mark.parametrize(
    "text",
    [
        '<testsuite tests="2"><testcase classname="X" name="test"/></testsuite>',
        "<testsuite><testcase",
        "<something/>",
    ],
)
def test_invalid_or_truncated_reports(tmp_path, text):
    p = tmp_path / "report.xml"
    p.write_text(text)
    with pytest.raises(Exception):
        junit([p])


def test_reported_failed_status_is_not_pass(tmp_path):
    p = tmp_path / "report.xml"
    p.write_text('<testsuite><testcase name="ui" status="FAILED"/></testsuite>')
    assert junit([p])[0] == "FAIL"


def test_instrument_selector_shell_characters_rejected(project_factory):
    def change(c):
        c["layer"] = "device"
        c["implementation"] = {"engine": "instrumentation", "selector": "Example#ok; reboot"}

    with pytest.raises(Invalid, match="exact"):
        project_factory(case_update=change)


def test_input_apk_change_invalidates_plan(project_factory):
    p = project_factory()
    (p.root / "input.apk").write_bytes(b"apk-placeholder-for-hash-test")
    config = yaml.safe_load(p.path.read_text())
    config["products"] = {"app": {"input_path": "input.apk"}}
    p.path.write_text(yaml.safe_dump(config))
    p = Project(p.path)
    plan = freeze(p, p.select())
    assert "input.apk" in plan["assets"]
    (p.root / "input.apk").write_bytes(b"changed")
    with pytest.raises(Invalid, match="asset changed"):
        load_plan(p, plan["run_id"])


def test_snapshot_corruption_is_detected(project_factory):
    p = project_factory()
    plan = freeze(p, p.select())
    (p.artifacts / plan["run_id"] / "snapshot/contract.md").write_text("corrupted")
    with pytest.raises(Invalid, match="asset changed"):
        load_plan(p, plan["run_id"])


def test_absolute_asset_within_root_is_canonicalized(project_factory):
    p = project_factory()
    case_path = p.root / "cases/works.yaml"
    case = yaml.safe_load(case_path.read_text())
    case["contract"] = str(p.root / "contract.md")
    case_path.write_text(yaml.safe_dump(case))
    p = Project(p.path)
    plan = freeze(p, p.select())
    assert "contract.md" in plan["assets"]
    assert all(not Path(name).is_absolute() for name in plan["assets"])
    assert (p.artifacts / plan["run_id"] / "snapshot/contract.md").is_file()


def test_suite_selection_and_empty_suite(project_factory):
    p = project_factory()
    directory = p.root / "testing/suites"
    directory.mkdir(parents=True)
    path = directory / "smoke.yaml"
    path.write_text("schema_version: 1\nid: smoke\ncases: [test.works]\n")
    assert p.select(suite="smoke")[0]["id"] == "test.works"
    path.write_text("schema_version: 1\nid: smoke\n")
    with pytest.raises(Invalid, match="Empty suite"):
        p.select(suite="smoke")


def test_plan_reloaded_in_fresh_process(project_factory):
    p = project_factory()
    plan = freeze(p, p.select())
    r = subprocess.run(
        [sys.executable, "-m", "aat.cli", "--project", str(p.path), "run", "--run-id", plan["run_id"]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["gate"] == "passed"


def test_timeout_kills_descendant(project_factory):
    script = """import subprocess,sys,time
from pathlib import Path
Path('build').mkdir(exist_ok=True)
subprocess.Popen([sys.executable, '-c', "import time;from pathlib import Path;time.sleep(1);Path('build/leaked').touch()"])
time.sleep(10)
"""
    p = project_factory(body=script, config_update=lambda c: c["tasks"]["test"].update(timeout_seconds=0.2))
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 4
    time.sleep(1.2)
    assert not (p.root / "build/leaked").exists()
