import json

import pytest
import yaml

from aat.cli import main
from aat.discovery import reconcile, scan
from aat.project import Project
from aat.runner import freeze, run


def native(p, kind="junit", name="testDebugUnitTest"):
    directory = p.root / "app/src/test/java"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ExistingTest.java").write_text("class ExistingTest { @Test void works() {} }")
    return {
        "projects": [
            {
                "path": ":app",
                "directory": str(p.root / "app"),
                "android": True,
                "sources": [{"name": "test", "dirs": [str(directory)]}],
                "tasks": [
                    {
                        "path": ":app:" + name,
                        "name": name,
                        "kind": kind,
                        "enabled": True,
                        "reports": [str(p.root / "build/result.xml")],
                        "filters": {},
                        "xmlEnabled": True,
                    }
                ],
            }
        ],
        "includedBuilds": [],
    }


def configure(p):
    c = yaml.safe_load(p.path.read_text())
    c["discovery"] = {"gradle_argv": ["./gradlew"], "source_cases": {"test_runner.py": ["test.works"]}}
    c["tasks"]["test"]["argv"].append(":app:testDebugUnitTest")
    p.path.write_text(yaml.safe_dump(c))
    return Project(p.path)


def test_import_native_task_and_idempotence(project_factory):
    p = configure(project_factory())
    inventory = native(p)
    found = reconcile(p, inventory)
    q = Project(p.path)
    c = q.cases[found["coverage"][":app:testDebugUnitTest"][0]]
    assert c["implementation"] == {"engine": "junit", "selector": "*", "task": "test"}
    assert c["assets"] == ["app/src/test/java/ExistingTest.java"]
    before = {f: f.read_bytes() for f in p.root.rglob("*.yaml")}
    again = reconcile(q, inventory)
    assert again == found
    assert all(f.read_bytes() == value for f, value in before.items())
    result, code = run(q, freeze(q, q.select(), "full", discovery=found)["run_id"])
    assert code == 0 and result["coverage"]["scope"] == "full"


def test_new_source_auto_registered_and_revision_advanced(project_factory):
    p = configure(project_factory())
    inv = native(p)
    first = reconcile(p, inv)
    q = Project(p.path)
    freeze(q, q.select())
    (p.root / "app/src/test/java/NewTest.java").write_text("class NewTest {}")
    second = reconcile(q, inv)
    cid = first["coverage"][":app:testDebugUnitTest"][0]
    assert Project(p.path).cases[cid]["revision"] == 2
    assert len(second["sources"]) == 3
    freeze(Project(p.path), Project(p.path).select())


def test_modified_shared_native_test_advances_manual_metadata(project_factory):
    p = configure(project_factory())
    inv = native(p)
    reconcile(p, inv)
    q = Project(p.path)
    freeze(q, q.select())
    (p.root / "test_runner.py").write_text((p.root / "test_runner.py").read_text() + "\n# updated test\n")
    reconcile(q, inv)
    q = Project(p.path)
    assert q.cases["test.works"]["revision"] == 2
    freeze(q, q.select())


def test_unmanaged_source_blocks_full_success(project_factory):
    p = configure(project_factory())
    inv = native(p)
    (p.root / "test_external.py").write_text("def test_extra(): pass")
    audit = reconcile(p, inv)
    assert any(g["reason"] == "UNMANAGED_TEST_SOURCE" for g in audit["gaps"])
    q = Project(p.path)
    result, code = run(q, freeze(q, q.select(), "full", discovery=audit)["run_id"])
    assert code == 3
    assert result["cases"][-1]["reason_code"] == "UNMANAGED_TESTS"


def test_disabled_filtered_and_unknown_tasks_reported(project_factory):
    p = configure(project_factory())
    inv = native(p)
    task = inv["projects"][0]["tasks"][0]
    task.update(enabled=False, filters={"include": ["OneTest"]})
    inv["projects"][0]["tasks"].append(
        {"path": ":app:customCheck", "name": "customCheck", "kind": "verification"}
    )
    reasons = {g["reason"] for g in scan(p, inv)["gaps"]}
    assert reasons == {"DISABLED_TEST_TASK", "FILTERED_OR_UNREPORTED_TEST_TASK", "UNMAPPED_VERIFICATION_TASK"}


def test_nested_flows_only_register_entrypoint(project_factory):
    p = configure(project_factory())
    inv = native(p)
    (p.root / "flow.yaml").write_text("appId: org.test\n---\n- runFlow: child.yaml\n")
    (p.root / "child.yaml").write_text("appId: org.test\n---\n- assertVisible: Hello\n")
    entries = [e for e in scan(p, inv)["entries"] if e["engine"] == "maestro"]
    assert [e["key"] for e in entries] == ["flow.yaml"]


def test_removed_discovered_task_retires_metadata(project_factory):
    p = configure(project_factory())
    inv = native(p)
    audit = reconcile(p, inv)
    cid = audit["coverage"][":app:testDebugUnitTest"][0]
    (p.root / "app/src/test/java/ExistingTest.java").unlink()
    inv["projects"][0]["tasks"] = []
    reconcile(Project(p.path), inv)
    assert cid not in Project(p.path).cases


def test_default_takeover_run_scans_all_and_partial_is_labelled(project_factory, monkeypatch, capsys):
    p = configure(project_factory())
    inv = native(p)
    reconcile(p, inv)
    monkeypatch.setattr("aat.discovery.native_inventory", lambda _: inv)
    assert main(["--project", str(p.path), "run"]) == 0
    full = json.loads(capsys.readouterr().out)
    assert full["coverage"]["scope"] == "full" and len(full["cases"]) == 2
    assert main(["--project", str(p.path), "run", "--case", "test.works"]) == 0
    partial = json.loads(capsys.readouterr().out)
    assert partial["coverage"]["scope"] == "partial"
    assert main(["--project", str(p.path), "run", "--profile", "full", "--case", "test.works"]) == 2


def test_failed_discovery_transaction_restores_config(project_factory, monkeypatch):
    p = configure(project_factory())
    inv = native(p)
    original = p.path.read_bytes()
    from aat.discovery import atomic_bytes

    def fail(path, data):
        if str(path).endswith("discovery.json"):
            raise OSError("disk failure")
        atomic_bytes(path, data)

    monkeypatch.setattr("aat.discovery.atomic_bytes", fail)
    with pytest.raises(OSError):
        reconcile(p, inv)
    assert p.path.read_bytes() == original
    assert len(Project(p.path).cases) == 1


def test_discovery_external_report_rejected(project_factory):
    p = project_factory()
    inv = native(p)
    inv["projects"][0]["tasks"][0]["reports"] = ["/outside/results/*.xml"]
    assert scan(p, inv)["gaps"][0]["reason"] == "EXTERNAL_REPORT_PATH"


def test_import_uses_all_native_reports_not_manual_subset(project_factory):
    p = configure(project_factory())
    inv = native(p)
    inv["projects"][0]["tasks"][0]["reports"] = [str(p.root / "build/**/*.xml")]
    reconcile(p, inv)
    assert Project(p.path).tasks["test"]["reports"] == ["build/**/*.xml"]


def test_quarantined_discovered_case_not_silently_reenabled(project_factory):
    p = configure(project_factory())
    inv = native(p)
    audit = reconcile(p, inv)
    cid = audit["coverage"][":app:testDebugUnitTest"][0]
    q = Project(p.path)
    path = q.root / q.cases[cid]["_path"]
    case = yaml.safe_load(path.read_text())
    case.update(status="quarantined", reason="unstable existing suite")
    path.write_text(yaml.safe_dump(case))
    reconcile(Project(p.path), inv)
    assert Project(p.path).cases[cid]["status"] == "quarantined"


def test_git_ignored_native_test_is_discovered(project_factory):
    import subprocess

    p = configure(project_factory())
    inv = native(p)
    subprocess.run(["git", "init", "-q", str(p.root)], check=True)
    (p.root / ".gitignore").write_text("app/src/test/\n")
    found = reconcile(p, inv)
    assert "app/src/test/java/ExistingTest.java" in found["sources"]
    q = Project(p.path)
    plan = freeze(q, q.select(), "full", discovery=found)
    assert "app/src/test/java/ExistingTest.java" in plan["assets"]


def test_device_runner_selection_filter_blocks_completeness(project_factory):
    p = configure(project_factory())
    inv = native(p, kind="gradle-device", name="connectedDebugAndroidTest")
    inv["projects"][0]["sources"][0]["name"] = "androidTest"
    inv["projects"][0]["tasks"][0]["filters"] = {"runner": ["class"]}
    assert any(g["reason"] == "FILTERED_OR_UNREPORTED_TEST_TASK" for g in scan(p, inv)["gaps"])


def test_unknown_action_in_check_aggregate_requires_mapping(project_factory):
    p = configure(project_factory())
    inv = native(p)
    inv["projects"][0]["tasks"].append(
        {"path": ":app:check", "name": "check", "kind": "verification", "hasActions": True}
    )
    assert any(g["item"] == ":app:check" for g in scan(p, inv)["gaps"])


def test_exact_device_case_reuses_completed_full_suite(project_factory, tmp_path):
    from aat.runner import Session

    p = project_factory(config_update=lambda c: c["tasks"]["test"].update(device=True))
    directory = p.artifacts / "synthetic"
    directory.mkdir(parents=True)
    report = directory / "device.xml"
    report.write_text('<testsuite tests="1"><testcase classname="Example" name="works"/></testsuite>')
    session = Session(p, directory)
    session.tasks["test"] = {"returncode": 0, "reason": None, "reports": [report]}
    c = dict(p.cases["test.works"])
    c["implementation"] = {"engine": "instrumentation", "selector": "Example#works", "path": "test_runner.py"}
    p.cases["suite"] = {
        "implementation": {"engine": "gradle-device", "task": "test"},
        "assets": ["test_runner.py"],
    }
    result = session.case(c)
    assert result["outcome"] == "PASS" and result["reused_native_task"] == "test"
    assert session.device is None


def test_explicit_exclusion_cannot_claim_full_gate_passed(project_factory):
    p = configure(project_factory())
    inv = native(p)
    (p.root / "test_external.py").write_text("def test_external(): pass")
    c = yaml.safe_load(p.path.read_text())
    c["discovery"]["exclude"] = [{"pattern": "test_external.py", "reason": "separate runtime"}]
    p.path.write_text(yaml.safe_dump(c))
    audit = reconcile(Project(p.path), inv)
    q = Project(p.path)
    result, code = run(q, freeze(q, q.select(), "full", discovery=audit)["run_id"])
    assert code == 3 and not result["coverage"]["full_project_passed"]
    assert result["cases"][-1]["reason_code"] == "EXCLUDED_TESTS"


def test_discovery_does_not_overwrite_existing_contract(project_factory):
    from aat.storage import Invalid

    p = configure(project_factory())
    inv = native(p)
    path = p.root / "testing/contracts/discovered-tests.md"
    path.parent.mkdir(parents=True)
    path.write_text("Existing project requirements")
    with pytest.raises(Invalid, match="unrelated"):
        reconcile(p, inv)
    assert path.read_text() == "Existing project requirements"


def test_discovery_does_not_overwrite_existing_manifest(project_factory):
    from aat.storage import Invalid

    p = configure(project_factory())
    inv = native(p)
    path = p.root / "testing/discovery.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"manual": true}')
    with pytest.raises(Invalid, match="unrelated"):
        reconcile(p, inv)
    assert json.loads(path.read_text()) == {"manual": True}


def test_device_case_does_not_reuse_same_selector_from_other_module(project_factory, monkeypatch):
    from aat.runner import Session

    p = project_factory(config_update=lambda c: c["tasks"]["test"].update(device=True))
    directory = p.artifacts / "synthetic"
    directory.mkdir(parents=True)
    report = directory / "device.xml"
    report.write_text('<testsuite tests="1"><testcase classname="Example" name="works"/></testsuite>')
    session = Session(p, directory)
    session.tasks["test"] = {"returncode": 0, "reason": None, "reports": [report]}
    p.cases["suite"] = {
        "implementation": {"engine": "gradle-device", "task": "test"},
        "assets": ["other/Test.java"],
    }
    c = dict(p.cases["test.works"])
    c["implementation"] = {"engine": "instrumentation", "selector": "Example#works", "path": "test_runner.py"}
    monkeypatch.setattr(session, "prepare_device", lambda: {"available": False, "reason": "DEVICE_NOT_READY"})
    assert session.case(c)["outcome"] == "BLOCKED"
