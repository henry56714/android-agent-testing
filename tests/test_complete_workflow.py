import base64
import json
import os

import pytest
import yaml

from aat.catalog import history, recover, save
from aat.cli import main
from aat.lifecycle import prune, read_report, restore
from aat.project import Project
from aat.runner import freeze, run
from aat.storage import Invalid, write_json


def envelope(p):
    c = dict(p.cases["test.works"])
    c.pop("_path")
    c["revision"] += 1
    c["title"] = "Updated contract title"
    return {"case": c}


def test_save_revision_and_history(project_factory):
    p = project_factory()
    result = save(p, envelope(p), 1)
    assert result["revision"] == 2
    q = Project(p.path)
    assert q.cases["test.works"]["title"] == "Updated contract title"
    assert history(q, "test.works")["versions"][0]["revision"] == 2
    with pytest.raises(Invalid, match="conflict"):
        save(q, envelope(q), 1)


def test_new_case_atomic_assets(project_factory):
    p = project_factory()
    e = envelope(p)
    e["case"].update(id="test.new", revision=1, contract="new.md")
    e["files"] = {"new.md": "New requirement"}
    save(p, e, 0)
    assert len(Project(p.path).cases) == 2
    assert (p.root / "new.md").read_text() == "New requirement"


def test_save_validation_failure_rolls_back(project_factory):
    p = project_factory()
    original = (p.root / "cases/works.yaml").read_bytes()
    e = envelope(p)
    e["case"]["implementation"]["task"] = "missing"
    with pytest.raises(Invalid):
        save(p, e, 1)
    assert (p.root / "cases/works.yaml").read_bytes() == original
    assert not (p.artifacts / "catalog-transaction.json").exists()


def test_interrupted_transaction_recovery(project_factory):
    p = project_factory()
    original = (p.root / "contract.md").read_bytes()
    write_json(
        p.artifacts / "catalog-transaction.json",
        {"state": "writing", "before": {"contract.md": base64.b64encode(original).decode()}},
    )
    (p.root / "contract.md").write_text("half written")
    with pytest.raises(Invalid, match="recover"):
        Project(p.path)
    assert recover(p)
    assert (p.root / "contract.md").read_bytes() == original


def test_test_assets_cannot_write_unrelated_file(project_factory):
    p = project_factory()
    e = envelope(p)
    e["files"] = {"unrelated.txt": "overwrite"}
    with pytest.raises(Invalid, match="Undeclared"):
        save(p, e, 1)


def test_restore_old_source_after_current_changes(project_factory, tmp_path):
    p = project_factory()
    (p.root / "business.java").write_text("old")
    plan = freeze(p, p.select())
    (p.root / "business.java").write_text("new")
    out = restore(p, plan["run_id"], tmp_path / "restored")
    q = Project(out["project"])
    assert (q.root / "business.java").read_text() == "old"
    assert (p.root / "business.java").read_text() == "new"
    result, code = run(q, freeze(q, q.select())["run_id"])
    assert code == 0
    assert result["gate"] == "passed"
    with pytest.raises(Invalid, match="not exist"):
        restore(p, plan["run_id"], tmp_path / "restored")


def test_restore_damaged_source_refused(project_factory, tmp_path):
    p = project_factory()
    plan = freeze(p, p.select())
    (p.artifacts / plan["run_id"] / "source/test_runner.py").write_text("bad")
    with pytest.raises(Invalid, match="damaged"):
        restore(p, plan["run_id"], tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_prune_dry_run_pin_failure_and_expiration(project_factory):
    p = project_factory()
    plan = freeze(p, p.select())
    result, _ = run(p, plan["run_id"])
    directory = p.artifacts / plan["run_id"]
    assert prune(p, 0)["runs"] == [plan["run_id"]]
    assert (directory / "source").exists()
    write_json(directory / "pinned.json", {"pinned": True})
    assert prune(p, 0, True)["runs"] == []
    (directory / "pinned.json").unlink()
    prune(p, 0, True)
    assert not (directory / "source").exists()
    assert read_report(p, plan["run_id"])["artifacts_expired"]
    assert all(e["state"] == "expired" for e in read_report(p, plan["run_id"])["evidence_manifest"].values())
    q = project_factory(body="pass")
    failed = freeze(q, q.select())
    run(q, failed["run_id"])
    assert prune(q, 0, True)["runs"] == []


def test_configured_capability_and_redaction(project_factory, monkeypatch):
    monkeypatch.setenv("AAT_SECRET_TEST", "private-test-value")

    def config(c):
        c["capabilities"] = {
            "test_probe": {
                "argv": [os.sys.executable, "-c", "print('private-test-value')"],
                "timeout_seconds": 5,
            }
        }
        c["privacy"] = {"redact_env": ["AAT_SECRET_TEST"]}

    p = project_factory(config_update=config, case_update=lambda c: c.update(requires=["test_probe"]))
    plan = freeze(p, p.select())
    _, code = run(p, plan["run_id"])
    assert code == 0
    log = p.artifacts / plan["run_id"] / "capabilities/test_probe/process.log"
    assert "private-test-value" not in log.read_text()
    assert "[REDACTED]" in log.read_text()


def test_failed_capability_blocks(project_factory):
    def config(c):
        c["capabilities"] = {
            "probe": {"argv": [os.sys.executable, "-c", "raise SystemExit(1)"], "timeout_seconds": 5}
        }

    p = project_factory(config_update=config, case_update=lambda c: c.update(requires=["probe"]))
    r, code = run(p, freeze(p, p.select())["run_id"])
    assert code == 3 and r["cases"][0]["reason_code"] == "CAPABILITY_PROBE_FAILED"


def test_profiles_and_read_history_without_current_cases(project_factory, capsys):
    p = project_factory(config_update=lambda c: c.update(profiles={"fast": {"tags": ["smoke"]}}))
    assert main(["--project", str(p.path), "run", "--profile", "fast"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    (p.root / "contract.md").unlink()
    assert main(["--project", str(p.path), "report", result["run_id"]]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["gate"] == "passed"


def test_nested_maestro_dependencies(project_factory):
    p = project_factory()
    (p.root / "flow.yaml").write_text("appId: sample.app\n---\n- runFlow: sub.yaml\n")
    (p.root / "sub.yaml").write_text("appId: sample.app\n---\n- runScript: helper.js\n")
    (p.root / "helper.js").write_text("output.value = 1")
    c = yaml.safe_load((p.root / "cases/works.yaml").read_text())
    c.update(layer="ui", implementation={"engine": "maestro", "path": "flow.yaml"})
    (p.root / "cases/works.yaml").write_text(yaml.safe_dump(c))
    p = Project(p.path)
    assert {"sub.yaml", "helper.js"} <= set(p.assets(p.cases["test.works"]))


def test_init_and_schema_without_existing_project(tmp_path, capsys):
    cfg = tmp_path / "project/android-testing.yaml"
    assert main(["--project", str(cfg), "init", "--id", "new.app"]) == 0
    capsys.readouterr()
    assert Project(cfg).cases == {}
    assert main(["--project", str(cfg), "init", "--id", "new.app"]) == 2
    capsys.readouterr()
    assert main(["schema", "case"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["additionalProperties"] is False


def test_history_rejects_path_traversal(project_factory):
    with pytest.raises(Invalid, match="case ID"):
        history(project_factory(), "../catalog-history")


def test_log_limit_keeps_draining_large_process(tmp_path):
    from aat.process import execute

    log = tmp_path / "process.log"
    result = execute(
        [os.sys.executable, "-c", "import sys;sys.stdout.write('x' * 1000000)"],
        tmp_path,
        log,
        5,
        max_log_bytes=1024,
    )
    assert result["returncode"] == 0
    assert result["log_truncated"]
    assert log.stat().st_size < 1100
    assert "truncated" in log.read_text()


def test_bad_redaction_regex_rejected_before_run(project_factory):
    with pytest.raises(Invalid, match="regex|pattern"):
        project_factory(config_update=lambda c: c.update(privacy={"patterns": ["["]}))


def test_unmapped_diff_expands_to_full_catalog(project_factory, monkeypatch, capsys):
    p = project_factory()
    e = envelope(p)
    e["case"].update(id="test.other", revision=1, impact={"paths": ["lib/**"]})
    save(p, e, 0)
    monkeypatch.setattr(
        "aat.selection.query",
        lambda argv, cwd: (0, "src/test.java\nbuild.gradle\n") if "diff" in argv else (0, ""),
    )
    assert main(["--project", str(p.path), "plan", "--base", "HEAD"]) == 0
    plan = json.loads(capsys.readouterr().out)["data"]
    assert len(plan["cases"]) == 2


def test_final_source_check_error_persists_nonpassing_result(project_factory, monkeypatch):
    p = project_factory()
    plan = freeze(p, p.select())
    from aat.runner import Session

    original = Session.close

    def close(session):
        answer = original(session)
        monkeypatch.setattr("aat.runner.source_state", lambda _: (_ for _ in ()).throw(OSError("gone")))
        return answer

    monkeypatch.setattr(Session, "close", close)
    result, code = run(p, plan["run_id"])
    assert code == 4
    assert result["cases"][0]["reason_code"] == "SOURCE_VERIFICATION_FAILED"
    assert read_report(p, plan["run_id"])["exit_code"] == 4


def test_snapshot_in_ignored_parent_git_directory(project_factory):
    import subprocess

    from aat.runner import source_state

    p = project_factory()
    subprocess.run(["git", "init", "-q", str(p.root.parent)], check=True)
    (p.root.parent / ".gitignore").write_text(p.root.name + "/\n")
    assert "test_runner.py" in source_state(p)["files"]
    plan = freeze(p, p.select())
    assert (p.artifacts / plan["run_id"] / "source/test_runner.py").exists()


def test_maestro_assertion_is_failure_despite_error_status(tmp_path):
    from aat.results import junit

    report = tmp_path / "maestro.xml"
    report.write_text(
        '<testsuite tests="1" failures="1"><testcase name="flow" status="ERROR">'
        "<failure>Assertion is false: button is visible</failure></testcase></testsuite>"
    )
    outcome, _, _ = junit([report])
    assert outcome == "FAIL"


def test_retention_keeps_optional_failure_even_if_gate_passed(project_factory):
    p = project_factory()
    plan = freeze(p, p.select())
    result, _ = run(p, plan["run_id"])
    result["cases"].append({"id": "optional", "required": False, "outcome": "FAIL"})
    write_json(p.artifacts / plan["run_id"] / "result.json", result)
    assert prune(p, 0, True)["runs"] == []
