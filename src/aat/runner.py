from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import signal
from contextlib import ExitStack
from pathlib import Path

from .device import find_aapt, inspect_device, query, tool
from .engines import execute_case
from .lifecycle import Redactor, redact
from .planner import freeze as freeze
from .planner import load_plan
from .planner import run_directory as run_directory
from .process import execute
from .results import export_report, gate
from .source import source_state as source_state
from .storage import Invalid, digest, inside, lock, write_json


class Session:
    def __init__(self, project, directory, serial=None):
        self.p, self.directory, self.serial = project, directory, serial
        self.tasks = {}
        self.device = None
        self.installed = False
        self.installed_packages = []
        self.capabilities = {}
        self.stack = ExitStack()

    def command(self, argv, name, timeout=120, env=None):
        return execute(argv, self.p.root, self.directory / name / "process.log", timeout, env)

    def task(self, name):
        if name in self.tasks:
            return self.tasks[name]
        spec = self.p.tasks[name]
        for dep in spec.get("depends_on", []):
            r = self.task(dep)
            if r["reason"] or r["returncode"] != 0:
                self.tasks[name] = {"returncode": None, "reason": "DEPENDENCY_FAILED", "reports": []}
                return self.tasks[name]
        # Quarantine old reports, forcing fresh results even for Gradle UP-TO-DATE tasks.
        directory = self.directory / "tasks" / name
        directory.mkdir(parents=True, exist_ok=True)
        for pattern in spec.get("reports", []):
            for old in glob.glob(str(inside(self.p.root, pattern)), recursive=True):
                old = inside(self.p.root, old, exists=True)
                dest = directory / "previous" / old.relative_to(self.p.root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(old, dest)
        env = None
        if spec.get("device"):
            device = self.prepare_device()
            if not device["available"]:
                return {"returncode": None, "reason": device["reason"], "reports": []}
            env = {"ANDROID_SERIAL": device["serial"]}
            for package in (
                self.p.config.get("target", {}).get("application_id"),
                self.p.config.get("instrumentation", {}).get("test_application_id"),
            ):
                if package and package not in self.installed_packages:
                    self.installed_packages.append(package)
        r = self.command(spec["argv"], f"tasks/{name}", spec["timeout_seconds"], env=env)
        reports = []
        for pattern in spec.get("reports", []):
            for path in sorted(glob.glob(str(inside(self.p.root, pattern)), recursive=True)):
                source = inside(self.p.root, path, exists=True)
                dest = directory / "reports" / source.relative_to(self.p.root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
                reports.append(dest)
        r["reports"] = reports
        self.tasks[name] = r
        return r

    def build(self):
        products = {}
        for name, spec in self.p.config.get("products", {}).items():
            if "from_task" in spec:
                r = self.task(spec["from_task"])
                if r["reason"] or r["returncode"] != 0:
                    raise RuntimeError("BUILD_FAILED")
            path = inside(self.p.root, spec.get("input_path", spec.get("path")), exists=True)
            products[name] = {"path": str(path.relative_to(self.p.root)), "sha256": digest(path)}
        write_json(self.directory / "products.json", products)
        return products

    def prepare_device(self):
        if self.device is None:
            self.device = inspect_device(self.p, self.serial)
            if self.device["available"]:
                serial = self.device["serial"]
                key = hashlib.sha256(serial.encode()).hexdigest()
                self.stack.enter_context(
                    lock(Path.home() / ".cache" / "android-agent-testing" / f"device-{key}.lock")
                )
        return self.device

    def aapt(self):
        return find_aapt(self.p)

    def install(self):
        if self.installed:
            return None
        target = self.p.config.get("target", {})
        if not target.get("application_id") or "app" not in self.p.config.get("products", {}):
            return "APP_PRODUCT_REQUIRED"
        aapt = self.aapt()
        if not aapt:
            return "APK_INSPECTOR_UNAVAILABLE"
        products = self.build()
        adb = tool(self.p, "adb")
        for name, product in products.items():
            expected = (
                target["application_id"]
                if name == "app"
                else self.p.config.get("instrumentation", {}).get("test_application_id")
            )
            dest = self.directory / "products" / f"{name}.apk"
            dest.parent.mkdir(exist_ok=True)
            shutil.copy2(self.p.root / product["path"], dest)
            if digest(dest) != product["sha256"]:
                return "APK_CHANGED_DURING_INSTALL"
            path = dest
            code, info = query([aapt, "dump", "badging", str(path)], self.p.root)
            package = re.search(r"package: name='([^']+)'", info)
            if code or not expected or not package or package[1] != expected:
                return "APK_PACKAGE_MISMATCH"
            r = self.command(
                [adb, "-s", self.device["serial"], "install", "-r", str(path)], f"install/{name}"
            )
            if r["reason"] or r["returncode"] != 0:
                return "APK_INSTALL_FAILED"
            self.installed_packages.append(expected)
            code, output = query(
                [adb, "-s", self.device["serial"], "shell", "pm", "path", expected], self.p.root
            )
            if code or "package:" not in output:
                return "INSTALLED_PACKAGE_NOT_FOUND"
        self.installed = True
        return None

    def close(self):
        errors = []
        try:
            for package in self.installed_packages:
                code, _ = query(
                    [tool(self.p, "adb"), "-s", self.device["serial"], "shell", "am", "force-stop", package],
                    self.p.root,
                )
                if code:
                    errors.append("CLEANUP_FAILED:" + package)
        finally:
            self.stack.close()
        return errors

    def case(self, c):
        return execute_case(self, c)


def run(project, run_id, serial=None):
    with lock(project.artifacts / ".execution.lock"):
        directory, plan = load_plan(project, run_id)
        if (directory / "started.json").exists():
            raise Invalid("Run already started; create a new plan to retry without overwriting history")
        write_json(directory / "started.json", {"pid": os.getpid(), "state": "RUNNING"})
        result = {
            "schema_version": 1,
            "run_id": run_id,
            "project_id": plan["project_id"],
            "profile": plan["profile"],
            "gate": "not_passed",
            "cases": [],
            "gaps": [],
        }
        session = Session(project, directory, serial)
        redactor = Redactor(project)
        old_signal = signal.getsignal(signal.SIGTERM)

        def cancel(*_):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, cancel)
        try:
            for c in plan["cases"]:
                try:
                    r = session.case(c)
                except Exception as e:
                    r = {
                        "id": c["id"],
                        "revision": c["revision"],
                        "required": c.get("required", True),
                        "outcome": "ERROR",
                        "reason_code": "EXECUTION_ERROR",
                        "message": str(e),
                    }
                result["cases"].append(r)
                write_json(directory / "progress.json", redactor.values(result))
        except KeyboardInterrupt:
            completed = {c["id"] for c in result["cases"]}
            for c in plan["cases"]:
                if c["id"] not in completed:
                    result["cases"].append(
                        {
                            "id": c["id"],
                            "revision": c["revision"],
                            "required": c.get("required", True),
                            "outcome": "CANCELLED",
                            "reason_code": "USER_CANCELLED",
                        }
                    )
        finally:
            signal.signal(signal.SIGTERM, old_signal)
            try:
                cleanup_errors = session.close()
            except Exception as e:
                cleanup_errors = [f"CLEANUP_FAILED: {e}"]
            if cleanup_errors:
                result["gaps"].extend(cleanup_errors)
                for c in result["cases"]:
                    if c["outcome"] == "PASS":
                        c.update(outcome="ERROR", reason_code="CLEANUP_FAILED")
        try:
            source_error = (
                "SOURCE_CHANGED_DURING_RUN"
                if source_state(project)["fingerprint"] != plan["source"]["fingerprint"]
                else None
            )
            if any(
                digest(inside(project.root, name, exists=True)) != sha for name, sha in plan["assets"].items()
            ):
                source_error = "TEST_ASSETS_CHANGED_DURING_RUN"
        except Exception as e:
            source_error = "SOURCE_VERIFICATION_FAILED"
            result["gaps"].append(str(e))
        if source_error:
            result["gaps"].append(source_error)
            for c in result["cases"]:
                if c["outcome"] == "PASS":
                    c.update(outcome="ERROR", reason_code=source_error)
        result["device"] = session.device
        audit = plan.get("discovery")
        result["coverage"] = {
            "scope": "full" if audit is not None else "partial",
            "discovery_fingerprint": audit.get("fingerprint") if audit else None,
            "entries": audit.get("coverage", {}) if audit else {},
            "unresolved": audit.get("gaps", []) if audit else [],
            "excluded": audit.get("excluded", []) if audit else [],
        }
        explicit_exclusions = (
            [e for e in audit["excluded"] if e["reason"] != "NO_TEST_SOURCE_IN_VARIANT"] if audit else []
        )
        if audit and (audit["gaps"] or explicit_exclusions):
            result["cases"].append(
                {
                    "id": "discovery.coverage",
                    "revision": 1,
                    "required": True,
                    "outcome": "BLOCKED",
                    "reason_code": "UNMANAGED_TESTS" if audit["gaps"] else "EXCLUDED_TESTS",
                    "details": audit["gaps"] + explicit_exclusions,
                }
            )
        result = redact(project, directory, result)
        write_json(directory / "progress.json", result)
        result["evidence_manifest"] = {}
        for c in result["cases"]:
            for relative in c.get("evidence", []):
                try:
                    path = inside(directory, relative, exists=True)
                    result["evidence_manifest"][relative] = {
                        "sha256": digest(path),
                        "bytes": path.stat().st_size,
                    }
                except (OSError, Invalid):
                    result["evidence_manifest"][relative] = {"state": "missing"}
                    c.update(outcome="ERROR", reason_code="EVIDENCE_MISSING")
        result["gate"], code = gate(result["cases"])
        outcomes = {c["id"]: c["outcome"] for c in result["cases"]}
        result["coverage"]["entry_outcomes"] = {
            key: {case_id: outcomes.get(case_id, "NOT_EXECUTED") for case_id in case_ids}
            for key, case_ids in result["coverage"]["entries"].items()
        }
        result["coverage"]["full_project_passed"] = bool(
            audit is not None
            and code == 0
            and not audit["gaps"]
            and not any(e["reason"] != "NO_TEST_SOURCE_IN_VARIANT" for e in audit["excluded"])
        )
        result["exit_code"] = code
        result["gaps"].extend(
            f"{c['id']}: {c['reason_code']}"
            for c in result["cases"]
            if c["outcome"] in ("BLOCKED", "ERROR", "SKIPPED", "CANCELLED")
        )
        export_report(directory, result)
        return result, code
