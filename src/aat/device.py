from __future__ import annotations

import os
import shutil
from pathlib import Path

from .process import query as query


def tool(project, name):
    value = project.config.get("tools", {}).get(name, name)
    return str(project.root / value) if "/" in value and not value.startswith("/") else value


def inspect_device(project, serial=None):
    adb = tool(project, "adb")
    code, output = query([adb, "devices"], project.root)
    if code:
        return {"available": False, "reason": "ADB_UNAVAILABLE"}
    devices = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] in ("device", "offline", "unauthorized"):
            devices[fields[0]] = fields[1]
    settings = project.config.get("target", {}).get("device", {})
    serial = serial or os.environ.get(settings.get("serial_env", "ANDROID_SERIAL"))
    if not serial:
        if len(devices) != 1:
            return {"available": False, "reason": "DEVICE_UNAVAILABLE" if not devices else "DEVICE_AMBIGUOUS"}
        serial = next(iter(devices))
    if devices.get(serial) != "device":
        return {"available": False, "reason": "DEVICE_NOT_READY", "serial": serial}
    code, api = query([adb, "-s", serial, "shell", "getprop", "ro.build.version.sdk"], project.root)
    code2, abis = query([adb, "-s", serial, "shell", "getprop", "ro.product.cpu.abilist"], project.root)
    if code or code2 or not api.strip().isdigit():
        return {"available": False, "reason": "DEVICE_QUERY_FAILED", "serial": serial}
    if int(api.strip()) < settings.get("min_api", 1):
        return {"available": False, "reason": "API_INCOMPATIBLE", "serial": serial}
    supported = abis.strip().split(",")
    required = settings.get("required_abis", [])
    if required and not set(required).intersection(supported):
        return {"available": False, "reason": "ABI_INCOMPATIBLE", "serial": serial}
    return {"available": True, "serial": serial, "api": int(api.strip()), "abis": supported}


def find_aapt(project):
    found = shutil.which("aapt")
    if found:
        return found
    adb = shutil.which(tool(project, "adb"))
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk and adb:
        sdk = str(Path(adb).resolve().parent.parent)
    candidates = list(Path(sdk).glob("build-tools/*/aapt")) if sdk else []

    def version(path):
        return tuple(int(x) if x.isdigit() else 0 for x in path.parent.name.split("."))

    return str(max(candidates, key=version)) if candidates else None


def doctor(project, serial=None, layers=()):
    from .discovery_model import gradle_argv

    selected = [c for c in project.cases.values() if not layers or c["layer"] in layers]
    engines = {c["implementation"]["engine"] for c in selected}
    requested = {r for c in selected for r in c.get("requires", [])}
    need_device = bool(engines & {"gradle-device", "instrumentation", "maestro"} or "device" in requested)
    need_install = bool(engines & {"instrumentation", "maestro"})
    gradle = gradle_argv(project)
    task_names = [c["implementation"]["task"] for c in selected if c["implementation"].get("task")]
    if need_install:
        task_names += [
            v["from_task"] for v in project.config.get("products", {}).values() if "from_task" in v
        ]
    ordered = project.order(task_names)
    need_gradle = bool(gradle and (ordered or project.config["project"]["mode"] == "source" and not selected))
    need_device = need_device or any(project.tasks[n].get("device") for n in ordered)
    required = {
        "adb": need_device,
        "maestro": "maestro" in engines,
        "java": need_gradle or "maestro" in engines,
        "gradle": need_gradle,
        "aapt": need_install,
    }
    tools, issues = {}, []
    commands = {
        "adb": [tool(project, "adb"), "version"],
        "maestro": [tool(project, "maestro"), "--version"],
        "java": ["java", "-version"],
        "gradle": [*gradle, "--version"] if gradle else [],
        "aapt": [find_aapt(project) or "aapt", "version"],
    }
    for name, argv in commands.items():
        found = shutil.which(argv[0], path=os.environ.get("PATH")) if argv else None
        if argv and "/" in argv[0] and not argv[0].startswith("/"):
            found = str(project.root / argv[0]) if (project.root / argv[0]).is_file() else None
        record = {"required": required[name], "available": bool(found), "path": found, "usable": False}
        if found and required[name]:
            code, output = query(argv, project.root, timeout=60)
            record.update(returncode=code, version=output.strip()[:1500], usable=code == 0)
        elif found:
            record["usable"] = None  # Not probed because this layer does not need it.
        tools[name] = record
        if required[name] and not record["usable"]:
            issues.append(
                {
                    "item": name,
                    "reason": "TOOL_UNAVAILABLE",
                    "fix": f"Install/configure {name} for the selected layers",
                }
            )
    for name in ordered:
        argv = project.tasks[name]["argv"]
        executable = argv[0]
        exists = (project.root / executable).is_file() if "/" in executable else shutil.which(executable)
        if not exists:
            issues.append(
                {"item": name, "reason": "TASK_COMMAND_UNAVAILABLE", "fix": f"Configure tasks.{name}.argv"}
            )
    device = inspect_device(project, serial) if need_device else {"available": None, "required": False}
    if need_device and not device["available"]:
        issues.append(
            {"item": "device", "reason": device["reason"], "fix": "Connect/select a compatible adb device"}
        )
    missing = [v for v in project.config.get("environment", {}).get("references", []) if v not in os.environ]
    issues.extend(
        {"item": v, "reason": "MISSING_ENVIRONMENT", "fix": "Set the required environment variable"}
        for v in missing
    )
    if not selected:
        issues.append(
            {
                "item": "catalog",
                "reason": "NO_SELECTED_CASES",
                "fix": "Add native tests, then discover --apply; check layer selection",
            }
        )
    if need_install:
        for key, present in (
            ("target.application_id", project.config.get("target", {}).get("application_id")),
            ("products.app", project.config.get("products", {}).get("app")),
        ):
            if not present:
                issues.append({"item": key, "reason": "CONFIGURATION_REQUIRED", "fix": f"Configure {key}"})
        for name, product in project.config.get("products", {}).items():
            if "input_path" in product and not (project.root / product["input_path"]).is_file():
                issues.append(
                    {"item": name, "reason": "APK_INPUT_MISSING", "fix": "Provide the configured input APK"}
                )
    if "instrumentation" in engines and (
        not project.config.get("instrumentation")
        or "instrumentation" not in project.config.get("products", {})
    ):
        issues.append(
            {
                "item": "instrumentation",
                "reason": "CONFIGURATION_REQUIRED",
                "fix": "Configure runner and test APK",
            }
        )
    probes = {}
    custom = project.config.get("capabilities", {})
    for name in sorted(requested - {"device"}):
        if name not in custom:
            issues.append(
                {"item": name, "reason": "CAPABILITY_PROBE_REQUIRED", "fix": f"Configure capabilities.{name}"}
            )
            continue
        spec = custom[name]
        code, _ = query(spec["argv"], project.root, spec["timeout_seconds"])
        probes[name] = {"available": code == 0, "returncode": code}
        if code:
            issues.append(
                {"item": name, "reason": "CAPABILITY_PROBE_FAILED", "fix": "Resolve the failed project probe"}
            )
    return {
        "project_id": project.config["project"]["id"],
        "ready": not issues,
        "layers": list(layers),
        "tools": tools,
        "device": device,
        "missing_environment": missing,
        "issues": issues,
        "capabilities": probes,
        "case_count": len(selected),
    }
