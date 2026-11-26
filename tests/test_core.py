import json
import signal
import subprocess
import sys
import time

import pytest
import yaml

from aat.cli import main
from aat.device import inspect_device
from aat.process import execute
from aat.project import Project
from aat.results import gate, instrumentation, junit, lint
from aat.runner import freeze, load_plan, run
from aat.storage import Invalid, inside, lock


def test_real_subprocess_persistent_run_and_replay(project_factory):
    p = project_factory()
    plan = freeze(p, p.select())
    result, code = run(p, plan["run_id"])
    assert code == 0
    assert result["cases"][0]["outcome"] == "PASS"
    directory = p.artifacts / plan["run_id"]
    for f in ("plan.json", "result.json", "report.md", "junit.xml", "snapshot/test_runner.py"):
        assert (directory / f).is_file()
    with pytest.raises(Invalid, match="already started"):
        run(p, plan["run_id"])
    p2 = Project(p.path)
    plan2 = freeze(p2, p2.select())
    result2, code2 = run(p2, plan2["run_id"])
    assert code2 == 0 and result2["run_id"] != result["run_id"]
    assert json.loads((directory / "result.json").read_text()) == result


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.update(typo=True),
        lambda c: c.update(schema_version=2),
        lambda c: c["tasks"]["test"].update(timeout_seconds=0),
        lambda c: c["tasks"]["test"].update(depends_on=["test"]),
        lambda c: c["tasks"]["test"].update(depends_on=["missing"]),
        lambda c: c["tasks"]["test"].update(reports=["../outside.xml"]),
        lambda c: c.update(artifacts="."),
    ],
)
def test_bad_config(project_factory, change):
    with pytest.raises(Invalid):
        project_factory(config_update=change)


def test_duplicate_yaml_and_case_id(project_factory):
    p = project_factory()
    p.path.write_text(p.path.read_text() + "schema_version: 1\n")
    with pytest.raises(Invalid, match="Duplicate YAML"):
        Project(p.path)
    p = project_factory()
    (p.root / "cases" / "second.yaml").write_text((p.root / "cases" / "works.yaml").read_text())
    with pytest.raises(Invalid, match="Duplicate case"):
        Project(p.path)


def test_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").write_text("private")
    (root / "link").symlink_to(tmp_path / "secret")
    with pytest.raises(Invalid, match="escapes"):
        inside(root, "link")


def test_selection(project_factory):
    p = project_factory()
    assert p.select(tags=["smoke"])[0]["id"] == "test.works"
    assert p.select(changed=["src/Foo.java"])
    with pytest.raises(Invalid, match="zero"):
        p.select(tags=["unknown"])
    with pytest.raises(Invalid, match="Unknown case"):
        p.select(ids=["missing"])


def test_stale_plan_and_revision(project_factory):
    p = project_factory()
    plan = freeze(p, p.select())
    (p.root / "contract.md").write_text("Changed contract")
    with pytest.raises(Invalid, match="STALE_PLAN"):
        load_plan(p, plan["run_id"])
    with pytest.raises(Invalid, match="revision"):
        freeze(p, p.select())
    case_path = p.root / "cases/works.yaml"
    case = yaml.safe_load(case_path.read_text())
    case["revision"] = 2
    case_path.write_text(yaml.safe_dump(case))
    p = Project(p.path)
    assert freeze(p, p.select())["cases"][0]["revision"] == 2


@pytest.mark.parametrize(
    "xml,selector,expected",
    [
        ('<testsuite><testcase classname="X" name="ok"/></testsuite>', "X#ok", "PASS"),
        ('<testsuite><testcase classname="X" name="bad"><failure/></testcase></testsuite>', "X#bad", "FAIL"),
        ('<testsuite><testcase classname="X" name="bad"><error/></testcase></testsuite>', "X#bad", "ERROR"),
        (
            '<testsuite><testcase classname="X" name="skip"><skipped/></testcase></testsuite>',
            "X#skip",
            "SKIPPED",
        ),
        ("<testsuite/>", "*", "ERROR"),
        ('<testsuite><testcase classname="X" name="else"/></testsuite>', "X#ok", "ERROR"),
        ('<testsuite><testcase classname="X" name="ok[0]"/></testsuite>', "X#ok*", "PASS"),
        (
            '<testsuite><testcase classname="X" name="ok"/><testcase classname="X" name="bad"><failure/></testcase></testsuite>',
            "X#ok",
            "ERROR",
        ),
    ],
)
def test_junit_mapping(tmp_path, xml, selector, expected):
    path = tmp_path / "results.xml"
    path.write_text(xml)
    assert junit([path], selector)[0] == expected


@pytest.mark.parametrize(
    "body,outcome,reason",
    [
        ("pass\n", "ERROR", "REPORT_MISSING"),
        ("raise SystemExit(2)\n", "ERROR", "REPORT_MISSING"),
        (
            "from pathlib import Path\np=Path('build/result.xml');p.parent.mkdir(exist_ok=True);p.write_text('<testsuite/>')",
            "ERROR",
            "TEST_NOT_DISCOVERED",
        ),
        (
            "from pathlib import Path\np=Path('build/result.xml');p.parent.mkdir(exist_ok=True);p.write_text('<testsuite><testcase classname=\"Example\" name=\"works\"/></testsuite>');raise SystemExit(2)",
            "ERROR",
            "ENGINE_EXIT_ERROR",
        ),
    ],
)
def test_no_false_pass(project_factory, body, outcome, reason):
    p = project_factory(body=body)
    (p.root / "build").mkdir()
    (p.root / "build/stale.xml").write_text(
        '<testsuite><testcase classname="Example" name="works"/></testsuite>'
    )
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code != 0
    assert result["cases"][0]["outcome"] == outcome
    assert result["cases"][0]["reason_code"] == reason


def test_shared_task_runs_once(project_factory):
    body = """from pathlib import Path
p=Path('build');p.mkdir(exist_ok=True)
count=p/'count';count.write_text(str(int(count.read_text())+1) if count.exists() else '1')
(p/'result.xml').write_text('<testsuite><testcase classname="Example" name="works"/><testcase classname="Example" name="second"/></testsuite>')
"""
    p = project_factory(body=body)
    case = yaml.safe_load((p.root / "cases/works.yaml").read_text())
    case["id"] = "test.second"
    case["implementation"]["selector"] = "Example#second"
    (p.root / "cases/second.yaml").write_text(yaml.safe_dump(case))
    p = Project(p.path)
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 0 and len(result["cases"]) == 2
    assert (p.root / "build/count").read_text() == "1"


def test_timeout(project_factory):
    p = project_factory(
        body="import time;time.sleep(20)",
        config_update=lambda c: c["tasks"]["test"].update(timeout_seconds=0.1),
    )
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 4 and result["cases"][0]["reason_code"] == "TIMEOUT"


def test_cancel_persists_result(project_factory):
    p = project_factory(
        body="from pathlib import Path\nimport time\nPath('build').mkdir(exist_ok=True)\nPath('build/ready').touch()\ntime.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-m", "aat.cli", "--project", str(p.path), "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not (p.root / "build/ready").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (p.root / "build/ready").exists()
        child.send_signal(signal.SIGTERM)
        out, err = child.communicate(timeout=5)
        assert child.returncode == 130, err
        result = json.loads(out)["data"]
        assert result["cases"][0]["outcome"] == "CANCELLED"
        assert (p.artifacts / result["run_id"] / "result.json").exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_literal_shell_metacharacters(tmp_path):
    r = execute(
        [sys.executable, "-c", "import sys;print(sys.argv[1])", "$(touch hacked); `whoami`"],
        tmp_path,
        tmp_path / "log",
        3,
    )
    assert r["returncode"] == 0
    assert not (tmp_path / "hacked").exists()
    assert "$(touch hacked)" in (tmp_path / "log").read_text()


def test_lock_contention(tmp_path):
    with lock(tmp_path / "lease"):
        with pytest.raises(Invalid, match="in use"):
            with lock(tmp_path / "lease"):
                pass


@pytest.mark.parametrize("status", ["draft", "quarantined", "deprecated"])
def test_nonactive_not_passed(project_factory, status):
    p = project_factory(case_update=lambda c: c.update(status=status, reason="test policy"))
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 3 and result["cases"][0]["outcome"] == "BLOCKED"


def test_unknown_capability_blocks(project_factory):
    p = project_factory(case_update=lambda c: c.update(requires=["custom_probe"]))
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 3 and result["cases"][0]["reason_code"] == "UNSUPPORTED_CAPABILITY"


@pytest.mark.parametrize(
    "outcome,code",
    [("PASS", 0), ("FAIL", 1), ("ERROR", 4), ("BLOCKED", 3), ("SKIPPED", 3), ("CANCELLED", 130)],
)
def test_gate(outcome, code):
    assert gate([{"outcome": outcome}])[1] == code
    assert gate([])[1] != 0


def test_lint_and_malformed_report(tmp_path):
    path = tmp_path / "lint.xml"
    path.write_text('<issues><issue severity="Warning"/><issue severity="Error"/></issues>')
    assert lint([path], ["Error"])[0] == "FAIL"
    assert lint([path], ["Fatal"])[0] == "PASS"
    path.write_text('<!DOCTYPE x [<!ENTITY e "x">]><testsuite/>')
    with pytest.raises(ValueError, match="DTD"):
        junit([path])


def test_instrumentation_complete_and_truncated():
    text = "INSTRUMENTATION_STATUS: class=Example\nINSTRUMENTATION_STATUS: test=works\nINSTRUMENTATION_STATUS_CODE: 0\nOK (1 test)\nINSTRUMENTATION_CODE: -1\n"
    assert instrumentation(text, "Example#works")[0] == "PASS"
    assert instrumentation(text.replace("OK (1 test)", ""), "Example#works")[0] == "ERROR"
    assert instrumentation("INSTRUMENTATION_CODE: -1", "Example#works")[0] == "ERROR"
    assert instrumentation(text.replace("STATUS_CODE: 0", "STATUS_CODE: -2"), "Example#works")[0] == "FAIL"


def test_cli_invalid_and_report(project_factory, capsys):
    p = project_factory()
    assert main(["--project", str(p.path), "cases", "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["valid"]
    assert main(["--project", str(p.path), "plan", "--case", "absent"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize(
    "listing,serial,reason",
    [
        ("List of devices attached\n", None, "DEVICE_UNAVAILABLE"),
        ("a\tdevice\nb\tdevice\n", None, "DEVICE_AMBIGUOUS"),
        ("a\tunauthorized\n", "a", "DEVICE_NOT_READY"),
    ],
)
def test_device_selection(project_factory, monkeypatch, listing, serial, reason):
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    monkeypatch.setattr("aat.device.query", lambda *a, **kw: (0, listing))
    assert inspect_device(project_factory(), serial)["reason"] == reason
