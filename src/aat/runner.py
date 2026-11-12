from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import signal
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .device import inspect_device, query, tool
from .lifecycle import excluded, redact, snapshot_source
from .process import execute
from .results import export_report, gate, instrumentation, junit, lint
from .storage import Invalid, digest, inside, lock, write_json


def source_state(project):
    code, listing = query(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], project.root
    )
    # A standalone project may live inside an enclosing repository's ignored
    # directory (e.g. a restored snapshot); successful Git with no files is not
    # evidence that the project has no source inputs.
    if code or not listing:
        paths = [
            p.relative_to(project.root).as_posix()
            for p in project.root.rglob("*")
            if p.is_file()
            and not set(p.relative_to(project.root).parts).intersection(
                {".git", ".aat", ".venv", "build", "__pycache__", ".gradle"}
            )
        ]
    else:
        paths = listing.split("\0")
    files = {}
    for name in sorted(set(paths)):
        if not name or excluded(project, name):
            continue
        p = inside(project.root, name)
        if p.is_relative_to(project.artifacts) or ".aat" in p.relative_to(project.root).parts:
            continue
        if p.is_file():
            files[name] = digest(p)
        elif not p.exists():
            files[name] = "deleted"
    code, commit = query(["git", "rev-parse", "HEAD"], project.root)
    return {
        "commit": commit.strip() if code == 0 else None,
        "files": files,
        "fingerprint": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
    }


def freeze(project, cases, profile="change", discovery=None):
    if discovery is not None:
        # Run native device suites first; exact-method catalog entries can then
        # reuse their already completed results instead of controlling the device twice.
        cases = sorted(cases, key=lambda c: c["implementation"]["engine"] != "gradle-device")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    with lock(project.artifacts / ".catalog.lock"):
        registry_path = project.artifacts / "case-revisions.json"
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
        assets = {}
        for c in cases:
            hashes = {a: digest(inside(project.root, a, exists=True)) for a in project.assets(c)}
            signature = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
            old = registry.get(c["id"])
            if old and (
                c["revision"] < old["revision"]
                or (c["revision"] == old["revision"] and old["signature"] != signature)
            ):
                raise Invalid(f"Case content changed without a newer revision: {c['id']}")
            registry[c["id"]] = {"revision": c["revision"], "signature": signature}
            assets.update(hashes)
        for product in project.config.get("products", {}).values():
            if "input_path" in product:
                name = str(inside(project.root, product["input_path"]).relative_to(project.root))
                assets[name] = digest(inside(project.root, name, exists=True))
        state = source_state(project)
        directory = project.artifacts / run_id
        directory.mkdir(parents=True)
        snapshot_source(project, state, directory)
        for name in assets:
            dest = directory / "snapshot" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(inside(project.root, name), dest)
        shutil.copy2(project.path, directory / "project.yaml")
        plan = {
            "schema_version": 1,
            "run_id": run_id,
            "project_id": project.config["project"]["id"],
            "profile": profile,
            "tool_version": __version__,
            "config_sha256": digest(project.path),
            "source": state,
            "assets": assets,
            "cases": cases,
            "source_snapshot": True,
            "discovery": discovery,
            "excluded_cases": [
                {"id": c["id"], "reason": "outside_requested_selection"}
                for c in project.cases.values()
                if c["id"] not in {x["id"] for x in cases}
            ],
            "reproduction_limits": [
                "Source snapshot excludes Git-ignored and explicitly excluded files; external SDK/tools/secrets must be supplied.",
                "Dynamic helpers must be declared in assets; API/device coverage is not inferred.",
            ],
        }
        write_json(directory / "plan.json", plan)
        write_json(registry_path, registry)
        return plan


def run_directory(project, run_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise Invalid("Invalid run ID")
    directory = inside(project.artifacts, run_id)
    if not (directory / "plan.json").is_file():
        raise Invalid("Run plan not found")
    return directory


def load_plan(project, run_id):
    directory = run_directory(project, run_id)
    plan = json.loads((directory / "plan.json").read_text())
    if digest(project.path) != plan["config_sha256"]:
        raise Invalid("STALE_PLAN: project configuration changed")
    for name, sha in plan["assets"].items():
        p = inside(project.root, name, exists=True)
        if digest(p) != sha or digest(inside(directory / "snapshot", name, exists=True)) != sha:
            raise Invalid(f"STALE_PLAN: asset changed: {name}")
    if source_state(project)["fingerprint"] != plan["source"]["fingerprint"]:
        raise Invalid("STALE_PLAN: source inputs changed; create a new plan")
    # Prevent accidental edits to frozen executable metadata.
    for c in plan["cases"]:
        if c != project.cases.get(c["id"]):
            raise Invalid("STALE_PLAN: frozen case metadata mismatch")
    return directory, plan


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
        found = shutil.which("aapt")
        if found:
            return found
        adb = shutil.which(tool(self.p, "adb"))
        sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if not sdk and adb:
            sdk = str(Path(adb).resolve().parent.parent)
        candidates = sorted(Path(sdk).glob("build-tools/*/aapt")) if sdk else []
        return str(candidates[-1]) if candidates else None

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
            ("modes", self.p.config["project"]["mode"]),
            ("variants", self.p.config.get("target", {}).get("variant")),
        ):
            if applicability.get(key) and value not in applicability[key]:
                return finish("SKIPPED", "NOT_APPLICABLE")
        custom = self.p.config.get("capabilities", {})
        unsupported = set(c.get("requires", [])) - {"device", "ui_accessibility"} - set(custom)
        if unsupported:
            return finish("BLOCKED", "UNSUPPORTED_CAPABILITY", sorted(unsupported))
        for capability in sorted(set(c.get("requires", [])) & set(custom)):
            if capability not in self.capabilities:
                spec = custom[capability]
                self.capabilities[capability] = self.command(
                    spec["argv"], f"capabilities/{capability}", spec["timeout_seconds"]
                )
            probe = self.capabilities[capability]
            if probe["reason"] or probe["returncode"] != 0:
                return finish("BLOCKED", "CAPABILITY_PROBE_FAILED", {"capability": capability})
        missing = [
            v for v in self.p.config.get("environment", {}).get("references", []) if v not in os.environ
        ]
        if missing:
            return finish("BLOCKED", "MISSING_ENVIRONMENT", missing)
        impl = c["implementation"]
        engine = impl["engine"]
        if engine == "instrumentation":
            for name, completed in self.tasks.items():
                suites = [
                    s
                    for s in self.p.cases.values()
                    if s["implementation"]["engine"] == "gradle-device"
                    and s["implementation"].get("task") == name
                    and impl.get("path") in s.get("assets", [])
                ]
                variant = self.p.config.get("target", {}).get("variant")
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
                    not self.p.tasks[name].get("device")
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
                result["evidence"] = [str(p.relative_to(self.directory)) for p in completed["reports"]]
                result["reused_native_task"] = name
                return finish(outcome, reason, details)
        if engine in ("junit", "lint", "gradle-device"):
            if self.p.config["project"]["mode"] == "apk":
                return finish("BLOCKED", "SOURCE_REQUIRED")
            if engine == "gradle-device":
                device = self.prepare_device()
                if not device["available"]:
                    return finish("BLOCKED", device["reason"])
            r = self.task(impl["task"])
            result["attempts"].append({k: v for k, v in r.items() if k != "reports"})
            if r["reason"]:
                return finish("ERROR", r["reason"])
            if not r["reports"]:
                return finish("ERROR", "REPORT_MISSING")
            result["evidence"] = [str(p.relative_to(self.directory)) for p in r["reports"]]
            parsed = (
                junit(r["reports"], impl["selector"])
                if engine in ("junit", "gradle-device")
                else lint(r["reports"], impl.get("fail_severities", ["Error", "Fatal"]))
            )
        else:
            if engine == "maestro" and not shutil.which(tool(self.p, "maestro")):
                return finish("BLOCKED", "MAESTRO_UNAVAILABLE")
            device = self.prepare_device()
            if not device["available"]:
                return finish("BLOCKED", device["reason"])
            if engine == "instrumentation" and (
                not self.p.config.get("instrumentation")
                or "instrumentation" not in self.p.config.get("products", {})
            ):
                return finish("BLOCKED", "INSTRUMENTATION_REQUIRED")
            install_error = self.install()
            if install_error:
                return finish("BLOCKED", install_error)
            directory = self.directory / "cases" / c["id"]
            directory.mkdir(parents=True, exist_ok=True)
            if engine == "maestro":
                report = directory / "maestro.xml"
                argv = [
                    tool(self.p, "maestro"),
                    "--device",
                    device["serial"],
                    "test",
                    "--format",
                    "junit",
                    "--output",
                    str(report),
                    "--test-output-dir",
                    str(directory / "maestro"),
                    str(inside(self.p.root, impl["path"])),
                ]
                r = self.command(argv, f"cases/{c['id']}", c["timeout_seconds"])
                parsed = junit([report]) if report.exists() else ("ERROR", "REPORT_MISSING", [])
            else:
                spec = self.p.config["instrumentation"]
                runner = spec["test_application_id"] + "/" + spec["runner"]
                code, runners = query(
                    [tool(self.p, "adb"), "-s", device["serial"], "shell", "pm", "list", "instrumentation"],
                    self.p.root,
                )
                expected = f"instrumentation:{runner} (target={self.p.config['target']['application_id']})"
                if code or expected not in runners:
                    return finish("BLOCKED", "INSTRUMENTATION_TARGET_MISMATCH")
                argv = [
                    tool(self.p, "adb"),
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
                r = self.command(argv, f"cases/{c['id']}", c["timeout_seconds"])
                parsed = instrumentation(Path(r["log"]).read_text(errors="replace"), impl["selector"])
            result["attempts"].append(r)
            result["evidence"] = [
                str(p.relative_to(self.directory)) for p in directory.rglob("*") if p.is_file()
            ]
            if r["reason"]:
                return finish("ERROR", r["reason"])
        outcome, reason, details = parsed
        if r["returncode"] != 0 and outcome not in ("FAIL", "ERROR"):
            return finish("ERROR", "ENGINE_EXIT_ERROR", details)
        return finish(outcome, reason, details)


def run(project, run_id, serial=None):
    directory, plan = load_plan(project, run_id)
    with lock(project.artifacts / ".execution.lock"):
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
                write_json(directory / "progress.json", result)
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
