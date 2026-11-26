"""Native engine adapters sharing the same case result/capability contract."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .device import query, tool
from .results import instrumentation, junit, lint
from .storage import inside


def execute_case(session, c):
    result = {
        "id": c["id"],
        "revision": c["revision"],
        "required": c.get("required", True),
        "outcome": "ERROR",
        "reason_code": "NOT_EXECUTED",
        "attempts": [],
        "evidence": [],
    }

    def finish(outcome, reason, details=None):
        result.update(outcome=outcome, reason_code=reason)
        if details is not None:
            result["details"] = details
        return result

    if c["status"] != "active":
        return finish("BLOCKED", "CASE_" + c["status"].upper())
    applicability = c.get("applicability", {})
    for key, value in (
        ("modes", session.p.config["project"]["mode"]),
        ("variants", session.p.config.get("target", {}).get("variant")),
    ):
        if applicability.get(key) and value not in applicability[key]:
            return finish("SKIPPED", "NOT_APPLICABLE")
    custom = session.p.config.get("capabilities", {})
    requested = set(c.get("requires", []))
    if "device" in requested:
        device = session.prepare_device()
        if not device["available"]:
            return finish("BLOCKED", device["reason"])
    if "ui_accessibility" in requested and "ui_accessibility" not in custom:
        return finish("BLOCKED", "CAPABILITY_PROBE_REQUIRED", {"capability": "ui_accessibility"})
    unsupported = set(c.get("requires", [])) - {"device", "ui_accessibility"} - set(custom)
    if unsupported:
        return finish("BLOCKED", "UNSUPPORTED_CAPABILITY", sorted(unsupported))
    for capability in sorted(set(c.get("requires", [])) & set(custom)):
        if capability not in session.capabilities:
            spec = custom[capability]
            session.capabilities[capability] = session.command(
                spec["argv"], f"capabilities/{capability}", spec["timeout_seconds"]
            )
        probe = session.capabilities[capability]
        if probe["reason"] or probe["returncode"] != 0:
            return finish("BLOCKED", "CAPABILITY_PROBE_FAILED", {"capability": capability})
    missing = [
        v for v in session.p.config.get("environment", {}).get("references", []) if v not in os.environ
    ]
    if missing:
        return finish("BLOCKED", "MISSING_ENVIRONMENT", missing)
    impl = c["implementation"]
    engine = impl["engine"]
    if engine == "instrumentation":
        for name, completed in session.tasks.items():
            suites = [
                s
                for s in session.p.cases.values()
                if s["implementation"]["engine"] == "gradle-device"
                and s["implementation"].get("task") == name
                and impl.get("path") in s.get("assets", [])
            ]
            variant = session.p.config.get("target", {}).get("variant")
            if variant:
                suites = [
                    s
                    for s in suites
                    if s.get("origin", {})
                    .get("reference", "")
                    .lower()
                    .endswith("connected" + variant.lower() + "androidtest")
                ]
            if not suites:
                continue
            if (
                not session.p.tasks[name].get("device")
                or completed.get("reason")
                or not completed.get("reports")
            ):
                continue
            outcome, reason, details = junit(completed["reports"], impl["selector"])
            if reason == "TEST_NOT_DISCOVERED":
                continue
            if completed["returncode"] != 0 and outcome == "PASS":
                outcome, reason = "ERROR", "ENGINE_EXIT_ERROR"
            result["attempts"].append({k: v for k, v in completed.items() if k != "reports"})
            result["evidence"] = [str(p.relative_to(session.directory)) for p in completed["reports"]]
            result["reused_native_task"] = name
            return finish(outcome, reason, details)
    if engine in ("junit", "lint", "gradle-device"):
        if session.p.config["project"]["mode"] == "apk":
            return finish("BLOCKED", "SOURCE_REQUIRED")
        if engine == "gradle-device":
            device = session.prepare_device()
            if not device["available"]:
                return finish("BLOCKED", device["reason"])
        r = session.task(impl["task"])
        result["attempts"].append({k: v for k, v in r.items() if k != "reports"})
        if r["reason"]:
            return finish("ERROR", r["reason"])
        if not r["reports"]:
            return finish("ERROR", "REPORT_MISSING")
        result["evidence"] = [str(p.relative_to(session.directory)) for p in r["reports"]]
        parsed = (
            junit(r["reports"], impl["selector"])
            if engine in ("junit", "gradle-device")
            else lint(r["reports"], impl.get("fail_severities", ["Error", "Fatal"]))
        )
    else:
        if engine == "maestro" and not shutil.which(tool(session.p, "maestro")):
            return finish("BLOCKED", "MAESTRO_UNAVAILABLE")
        device = session.prepare_device()
        if not device["available"]:
            return finish("BLOCKED", device["reason"])
        if engine == "instrumentation" and (
            not session.p.config.get("instrumentation")
            or "instrumentation" not in session.p.config.get("products", {})
        ):
            return finish("BLOCKED", "INSTRUMENTATION_REQUIRED")
        install_error = session.install()
        if install_error:
            return finish("BLOCKED", install_error)
        directory = session.directory / "cases" / c["id"]
        directory.mkdir(parents=True, exist_ok=True)
        if engine == "maestro":
            report = directory / "maestro.xml"
            argv = [
                tool(session.p, "maestro"),
                "--device",
                device["serial"],
                "test",
                "--format",
                "junit",
                "--output",
                str(report),
                "--test-output-dir",
                str(directory / "maestro"),
                str(inside(session.p.root, impl["path"])),
            ]
            r = session.command(argv, f"cases/{c['id']}", c["timeout_seconds"])
            parsed = junit([report]) if report.exists() else ("ERROR", "REPORT_MISSING", [])
        else:
            spec = session.p.config["instrumentation"]
            runner = spec["test_application_id"] + "/" + spec["runner"]
            code, runners = query(
                [tool(session.p, "adb"), "-s", device["serial"], "shell", "pm", "list", "instrumentation"],
                session.p.root,
            )
            expected = f"instrumentation:{runner} (target={session.p.config['target']['application_id']})"
            if code or expected not in runners:
                return finish("BLOCKED", "INSTRUMENTATION_TARGET_MISMATCH")
            argv = [
                tool(session.p, "adb"),
                "-s",
                device["serial"],
                "shell",
                "am",
                "instrument",
                "-w",
                "-r",
                "-e",
                "class",
                impl["selector"],
                runner,
            ]
            r = session.command(argv, f"cases/{c['id']}", c["timeout_seconds"])
            parsed = instrumentation(Path(r["log"]).read_text(errors="replace"), impl["selector"])
        result["attempts"].append(r)
        result["evidence"] = [
            str(p.relative_to(session.directory)) for p in directory.rglob("*") if p.is_file()
        ]
        if r["reason"]:
            return finish("ERROR", r["reason"])
    outcome, reason, details = parsed
    if r["returncode"] != 0 and outcome not in ("FAIL", "ERROR"):
        return finish("ERROR", "ENGINE_EXIT_ERROR", details)
    return finish(outcome, reason, details)
