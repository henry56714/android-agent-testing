"""Regression contracts for the November 2026 architecture review."""

import json

import pytest
import yaml
from test_discovery import configure, native

from aat.discovery import reconcile
from aat.project import Project
from aat.runner import freeze, run
from aat.storage import Invalid


@pytest.mark.parametrize(
    "path,pattern",
    [
        ("test_external.py", "test_external.py"),
        ("private/test_external.py", "private/**"),
        ("maestro/private.yaml", "maestro/**"),
    ],
)
def test_snapshot_exclusion_cannot_hide_tests(project_factory, path, pattern):
    p = configure(project_factory())
    inventory = native(p)
    candidate = p.root / path
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        "appId: example.app\n---\n- assertVisible: Private\n"
        if path.endswith(".yaml")
        else "def test_external(): assert False\n"
    )
    config = yaml.safe_load(p.path.read_text())
    config["snapshot"] = {"exclude": [pattern]}
    p.path.write_text(yaml.safe_dump(config))
    audit = reconcile(Project(p.path), inventory)
    assert any(g["item"] == path for g in audit["gaps"])
    q = Project(p.path)
    # Excluded private test content must not enter the snapshot either.
    plan = freeze(q, q.select(), "full", discovery=audit)
    assert path not in plan["assets"]
    result, code = run(q, plan["run_id"])
    assert code == 3 and not result["coverage"]["full_project_passed"]


def test_device_requirement_blocks_local_test(project_factory, monkeypatch):
    p = project_factory(case_update=lambda c: c.update(requires=["device"]))
    monkeypatch.setattr(
        "aat.runner.inspect_device", lambda *_: {"available": False, "reason": "DEVICE_UNAVAILABLE"}
    )
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 3
    assert result["cases"][0]["reason_code"] == "DEVICE_UNAVAILABLE"


def test_accessibility_requires_explicit_probe(project_factory):
    p = project_factory(case_update=lambda c: c.update(requires=["ui_accessibility"]))
    result, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 3
    assert result["cases"][0]["reason_code"] == "CAPABILITY_PROBE_REQUIRED"


@pytest.mark.parametrize("policy", ["restore_changed_settings", "allow_clear_test_package"])
def test_unimplemented_device_policy_is_rejected(project_factory, policy):
    with pytest.raises(Invalid, match="device_policy"):
        project_factory(config_update=lambda c: c.update(device_policy={policy: True}))


def test_completed_progress_is_redacted(project_factory, monkeypatch):
    token = "SYNTHETIC_REVIEW_TOKEN_123"
    monkeypatch.setenv("AAT_REVIEW_TOKEN", token)
    xml = f'<testsuite tests="1"><testcase classname="{token}" name="works"/></testsuite>'
    body = f"from pathlib import Path\np=Path('build/result.xml');p.parent.mkdir(exist_ok=True);p.write_text({xml!r})\n"
    p = project_factory(
        body=body,
        case_update=lambda c: c["implementation"].update(selector=token + "#works"),
        config_update=lambda c: c.update(privacy={"redact_env": ["AAT_REVIEW_TOKEN"]}),
    )
    plan = freeze(p, p.select())
    result, code = run(p, plan["run_id"])
    assert code == 0
    for name in ["progress.json", "result.json", "report.md", "junit.xml"]:
        assert token not in (p.artifacts / plan["run_id"] / name).read_text()
    assert "[REDACTED]" in json.dumps(result)


def test_discovery_preview_and_plan_never_modify_catalog(project_factory, monkeypatch, capsys):
    from aat.cli import main
    from aat.discovery import preview

    p = configure(project_factory())
    inventory = native(p)
    config = yaml.safe_load(p.path.read_text())
    config["discovery"]["enabled"] = True
    p.path.write_text("# Preserve this project note\n" + yaml.safe_dump(config))
    before = {str(f.relative_to(p.root)): f.read_bytes() for f in p.root.rglob("*") if f.is_file()}
    inspected = preview(Project(p.path), inventory)
    assert inspected["needs_sync"] and any(c["diff"] for c in inspected["changes"])
    monkeypatch.setattr("aat.discovery.native_inventory", lambda _: inventory)
    assert main(["--project", str(p.path), "plan"]) == 2
    assert "DISCOVERY_OUT_OF_SYNC" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert all((p.root / name).read_bytes() == data for name, data in before.items())
    assert not (p.root / "testing/discovery.json").exists()
    reconcile(Project(p.path), inventory)
    assert p.path.read_text().startswith("# Preserve this project note\n")
    synced = {
        str(f.relative_to(p.root)): f.read_bytes()
        for f in p.root.rglob("*")
        if f.is_file() and ".aat" not in f.parts
    }
    assert main(["--project", str(p.path), "plan"]) == 0
    capsys.readouterr()
    assert all((p.root / name).read_bytes() == data for name, data in synced.items())
    assert not preview(Project(p.path), inventory)["needs_sync"]


def test_explicit_sync_and_legacy_sync_are_observable(project_factory, monkeypatch, capsys):
    from aat.cli import main

    p = configure(project_factory())
    inv = native(p)
    config = yaml.safe_load(p.path.read_text())
    config["discovery"].update(enabled=True, auto_sync=True)
    p.path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr("aat.discovery.native_inventory", lambda _: inv)
    assert main(["--project", str(p.path), "run"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["warnings"]
    (p.root / "app/src/test/java/NewTest.java").write_text("class NewTest {}")
    assert main(["--project", str(p.path), "run", "--sync"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["coverage"]["full_project_passed"]
    assert not result.get("warnings")


def test_discovered_history_includes_versions_and_retirement(project_factory):
    from aat.catalog import history

    p = configure(project_factory())
    inv = native(p)
    first = reconcile(p, inv)
    case_id = first["coverage"][":app:testDebugUnitTest"][0]
    (p.root / "app/src/test/java/ExistingTest.java").write_text("class ExistingTest { /* new assertion */ }")
    reconcile(Project(p.path), inv)
    versions = history(Project(p.path), case_id)
    assert [c["revision"] for c in versions["versions"]] == [1, 2]
    assert versions["history_complete"]
    assert all(e["source"] == "discovery" and e["asset_hashes"] for e in versions["events"])
    (p.root / "app/src/test/java/ExistingTest.java").unlink()
    inv["projects"][0]["tasks"] = []
    reconcile(Project(p.path), inv)
    assert history(Project(p.path), case_id)["events"][-1]["action"] == "retired"


def test_failed_history_write_rolls_back_catalog_and_history(project_factory, monkeypatch):
    from aat import transaction

    p = configure(project_factory())
    inv = native(p)
    original = p.path.read_bytes()
    write = transaction.write_json

    def fail(path, value):
        if "catalog-history" in str(path):
            raise OSError("simulated history disk failure")
        return write(path, value)

    monkeypatch.setattr(transaction, "write_json", fail)
    with pytest.raises(OSError):
        reconcile(p, inv)
    assert p.path.read_bytes() == original
    assert not (p.root / "testing/discovery.json").exists()
    assert not list((p.artifacts / "catalog-history").rglob("*.json"))
    assert not (p.artifacts / "catalog-transaction.json").exists()


@pytest.mark.parametrize("arguments", [["missing-command"], ["run", "--unknown"], ["run", "--device"]])
def test_cli_parser_errors_are_json(arguments, capsys):
    from aat.cli import main

    assert main(arguments) == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {**output, "schema_version": 1, "ok": False, "exit_code": 2, "data": None}
    assert output["error"]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize("extra", [["--profile", "full"], ["--sync"], ["--case", "test.works"]])
def test_frozen_plan_rejects_selection_and_sync(project_factory, capsys, extra):
    from aat.cli import main

    p = project_factory()
    plan = freeze(p, p.select())
    assert main(["--project", str(p.path), "run", "--run-id", plan["run_id"], *extra]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_INPUT"
    assert not (p.artifacts / plan["run_id"] / "started.json").exists()


def test_doctor_local_does_not_require_android_tools(project_factory, monkeypatch, capsys):
    from aat.cli import main

    p = project_factory()
    monkeypatch.setattr("aat.device.query", lambda *_a, **_kw: pytest.fail("Unneeded Android probe"))
    assert main(["--project", str(p.path), "doctor", "--strict", "--layer", "local"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["ready"]
    assert not result["tools"]["adb"]["required"]
    assert not result["tools"]["maestro"]["required"]


def test_doctor_strict_missing_device_is_nonzero(project_factory, monkeypatch, capsys):
    from aat.cli import main

    p = project_factory(case_update=lambda c: c.update(requires=["device"]))
    monkeypatch.setattr("aat.device.query", lambda *_a, **_kw: (-1, "unavailable"))
    assert main(["--project", str(p.path), "doctor", "--strict"]) == 3
    result = json.loads(capsys.readouterr().out)["data"]
    assert not result["ready"] and result["issues"]
    assert main(["--project", str(p.path), "doctor"]) == 0
    assert not json.loads(capsys.readouterr().out)["data"]["ready"]


def test_init_apk_template_preserves_user_files(tmp_path, capsys):
    from aat.cli import main

    apk = tmp_path / "fixture.apk"
    apk.write_bytes(b"synthetic file; metadata test only")
    root = tmp_path / "new"
    root.mkdir()
    (root / ".gitignore").write_text("# existing\nlocal.properties\n")
    cfg = root / "android-testing.yaml"
    assert (
        main(
            [
                "--project",
                str(cfg),
                "init",
                "--id",
                "new.app",
                "--mode",
                "apk",
                "--apk",
                str(apk),
                "--application-id",
                "example.app",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["mode"] == "apk"
    assert Project(cfg).config["products"]["app"]["input_path"] == "input/app.apk"
    assert (root / ".gitignore").read_text().startswith("# existing\nlocal.properties\n")
    assert ".aat/" in (root / ".gitignore").read_text()
    assert (root / "input/app.apk").read_bytes() == apk.read_bytes()
    assert main(["--project", str(cfg), "init", "--id", "new.app"]) == 2
    capsys.readouterr()


def test_cli_legacy_output_and_unknown_profile(project_factory, capsys):
    from aat.cli import main

    p = project_factory()
    assert main(["--project", str(p.path), "--format", "legacy-json", "cases", "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert main(["--project", str(p.path), "plan", "--profile", "typo", "--case", "test.works"]) == 2
    assert "profile" in json.loads(capsys.readouterr().out)["error"]["message"]


def test_snapshot_deduplicates_only_frozen_files(project_factory):
    p = project_factory()
    plan = freeze(p, p.select())
    directory = p.artifacts / plan["run_id"]
    original = (directory / "snapshot/test_runner.py").read_bytes()
    (p.root / "test_runner.py").write_text("# modified live workspace\n")
    assert (directory / "source/test_runner.py").read_bytes() == original
    assert (directory / "snapshot/test_runner.py").read_bytes() == original
    assert (p.root / "test_runner.py").stat().st_ino != (directory / "snapshot/test_runner.py").stat().st_ino


@pytest.mark.parametrize("cancel", [False, True])
def test_progress_redaction_on_error_or_cancel(project_factory, monkeypatch, cancel):
    from aat.runner import Session

    token = "SYNTHETIC_SECRET_CASE"
    monkeypatch.setenv("AAT_REVIEW_TOKEN", token)
    p = project_factory(
        case_update=lambda c: c.update(id=token),
        config_update=lambda c: c.update(privacy={"redact_env": ["AAT_REVIEW_TOKEN"]}),
    )

    def fail(*_):
        if cancel:
            raise KeyboardInterrupt
        raise RuntimeError(token)

    monkeypatch.setattr(Session, "case", fail)
    plan = freeze(p, p.select())
    _, code = run(p, plan["run_id"])
    assert code == (130 if cancel else 4)
    for name in ["progress.json", "result.json", "report.md", "junit.xml"]:
        assert token not in (p.artifacts / plan["run_id"] / name).read_text()


def test_unknown_native_model_blocks_and_explicit_mapping_resolves(project_factory):
    from aat.discovery import scan

    p = configure(project_factory())
    inv = native(p)
    task = inv["projects"][0]["tasks"][0]
    task.update(sourceOrigin="unavailable", reports=[], reportOrigin="unavailable")
    assert any(g["reason"] == "UNKNOWN_TEST_SOURCE_MODEL" for g in scan(p, inv)["gaps"])
    config = yaml.safe_load(p.path.read_text())
    config["discovery"]["task_sources"] = {task["path"]: ["app/src/test/java"]}
    p.path.write_text(yaml.safe_dump(config))
    assert any(g["reason"] == "UNKNOWN_REPORT_MODEL" for g in scan(Project(p.path), inv)["gaps"])
    config["discovery"]["task_reports"] = {task["path"]: ["build/custom.xml"]}
    p.path.write_text(yaml.safe_dump(config))
    result = scan(Project(p.path), inv)
    assert not result["gaps"]
    entry = next(e for e in result["entries"] if e["key"] == task["path"])
    assert entry["reports"] == ["build/custom.xml"]
    assert entry["source_origin"] == entry["report_origin"] == "explicit"


def test_model_sources_do_not_use_variant_substrings(project_factory):
    from aat.discovery import scan

    p = configure(project_factory())
    inv = native(p)
    task = inv["projects"][0]["tasks"][0]
    task.update(sourceDirs=[str(p.root / "app/src/test/java")], sourceOrigin="android-variant-model")
    other = p.root / "app/src/testOtherDebug/java"
    other.mkdir(parents=True)
    (other / "OtherTest.java").write_text("class OtherTest {}")
    result = scan(p, inv)
    entry = next(e for e in result["entries"] if e["key"] == task["path"])
    assert all("OtherDebug" not in path for path in entry["sources"])
    assert any(g["item"].endswith("OtherTest.java") for g in result["gaps"])


def test_retired_discovery_id_reuses_monotonic_revision(project_factory):
    from aat.catalog import history

    p = configure(project_factory())
    inv = native(p)
    case_id = reconcile(p, inv)["coverage"][":app:testDebugUnitTest"][0]
    task = inv["projects"][0]["tasks"].pop()
    source = p.root / "app/src/test/java/ExistingTest.java"
    source.unlink()
    reconcile(Project(p.path), inv)
    source.write_text("class ExistingTest {}")
    inv["projects"][0]["tasks"].append(task)
    reconcile(Project(p.path), inv)
    q = Project(p.path)
    assert q.cases[case_id]["revision"] == 2
    assert [c["revision"] for c in history(q, case_id)["versions"]] == [1, 2]


def test_batch_save_shared_asset_is_atomic(project_factory):
    from aat.catalog import save, save_many

    p = project_factory()
    first = {k: v for k, v in p.cases["test.works"].items() if k != "_path"}
    second = {**first, "id": "test.second"}
    save(p, {"case": second}, 0)
    q = Project(p.path)
    before = (q.root / "contract.md").read_text()
    updates = [
        {
            "envelope": {"case": {**c, "revision": 2}, "files": {"contract.md": "Shared new contract\n"}},
            "expected_revision": 1,
        }
        for c in [first, second]
    ]
    updates[1]["expected_revision"] = 0
    with pytest.raises(Invalid, match="Revision conflict"):
        save_many(q, updates)
    assert (q.root / "contract.md").read_text() == before
    updates[1]["expected_revision"] = 1
    result = save_many(q, updates)
    assert len(result["saved"]) == 2
    assert all(c["revision"] == 2 for c in Project(p.path).cases.values())
    assert (q.root / "contract.md").read_text() == "Shared new contract\n"


def test_query_timeout_uses_process_group_cleanup(tmp_path):
    import sys

    from aat.process import query

    code, reason = query([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=0.05)
    assert code == -1 and reason == "TIMEOUT"
