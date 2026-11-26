"""Exercise public Android samples, using copies and a user-selected test device."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml
from record_check import record


def fingerprint(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and not {".aat", ".gradle", "build"}.intersection(p.relative_to(root).parts)
    }


def main(output, device, python):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "projects"
    workspace.mkdir()
    for name in ("counter-app", "worker-sdk", "apk-only"):
        shutil.copytree(
            Path("examples") / name,
            workspace / name,
            ignore=shutil.ignore_patterns(".aat", ".gradle", "build", "input", "local.properties"),
        )
    receipts = []
    completed = False
    # Resolve the directory, not the interpreter symlink: dereferencing it would
    # leave the installed virtual environment and select the base interpreter.
    cli = [str(Path(python).absolute()), "-I", "-m", "aat.cli"]

    def invoke(name, project, args, expected=0):
        receipt = record(
            output,
            name,
            [*cli, "--project", str(workspace / project / "android-testing.yaml"), *args],
            expected=expected,
            timeout=1200,
        )
        receipts.append(receipt)
        assert receipt["passed"], name
        payload = json.loads((output / receipt["log"]).read_text())
        assert payload["exit_code"] == expected and payload["data"] is not None, payload
        return payload["data"]

    try:
        invoke("counter-discover", "counter-app", ["discover"])
        invoke("counter-apply", "counter-app", ["discover", "--apply"])
        assert invoke("counter-doctor", "counter-app", ["doctor", "--strict", "--device", device])["ready"]
        before = fingerprint(workspace / "counter-app")
        plan = invoke("counter-plan", "counter-app", ["plan"])
        assert fingerprint(workspace / "counter-app") == before, "plan changed tracked inputs"
        result = invoke(
            "counter-full", "counter-app", ["run", "--run-id", plan["run_id"], "--device", device]
        )
        assert result["coverage"]["full_project_passed"] and all(
            c["outcome"] == "PASS" for c in result["cases"]
        )
        assert any(c.get("reused_native_task") for c in result["cases"])
        invoke("worker-apply", "worker-sdk", ["discover", "--apply"])
        sdk = invoke("worker-full", "worker-sdk", ["run", "--sync", "--profile", "full", "--device", device])
        # worker-sdk starts without takeover enabled; explicit onboarding is required.
        assert sdk["coverage"]["full_project_passed"]
        apk = workspace / "apk-only/input/app.apk"
        apk.parent.mkdir()
        shutil.copy2(workspace / "counter-app/app/build/outputs/apk/debug/app-debug.apk", apk)
        ui = invoke("apk-ui", "apk-only", ["run", "--tag", "smoke", "--device", device])
        assert ui["gate"] == "passed"
        blocked = invoke("apk-internal-blocked", "apk-only", ["run", "--device", device], 3)
        assert any(c["reason_code"] == "INSTRUMENTATION_REQUIRED" for c in blocked["cases"])
        counter = workspace / "counter-app/app/src/main/java/org/example/aat/counter/Counter.java"
        original = counter.read_text()
        assert "value++;" in original
        try:
            counter.write_text(original.replace("value++;", "value += 2;"))
            failed = invoke("mutation-fails", "counter-app", ["run", "--case", "counter.increment"], 1)
            assert failed["cases"][0]["outcome"] == "FAIL"
        finally:
            counter.write_text(original)
        assert (
            invoke("mutation-restored", "counter-app", ["run", "--case", "counter.increment"])["gate"]
            == "passed"
        )
        unavailable = invoke(
            "unavailable-device",
            "counter-app",
            ["run", "--case", "counter.worker", "--device", "aat-nonexistent-device"],
            3,
        )
        assert unavailable["cases"][0]["outcome"] == "BLOCKED"
        cfg = workspace / "counter-app/android-testing.yaml"
        original_config = cfg.read_text()
        try:
            altered = yaml.safe_load(original_config)
            altered["tasks"]["local"]["reports"] = ["build/intentionally-missing/*.xml"]
            cfg.write_text(yaml.safe_dump(altered))
            missing = invoke(
                "missing-native-report", "counter-app", ["run", "--case", "counter.increment"], 4
            )
            assert missing["cases"][0]["reason_code"] == "REPORT_MISSING"
        finally:
            cfg.write_text(original_config)
        completed = True
    finally:
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "device": device,
                    "python": python,
                    "checks": receipts,
                    "passed": completed and all(r["passed"] for r in receipts),
                },
                indent=2,
            )
            + "\n"
        )
    print("Native Android checks passed; copied project run directories retain original XML and logs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    main(args.output, args.device, args.python)
