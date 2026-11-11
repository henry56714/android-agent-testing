from __future__ import annotations

import os
import shutil
import subprocess


def query(argv, cwd, timeout=15):
    try:
        p = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
        )
        return p.returncode, p.stdout + p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


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


def doctor(project, serial=None):
    tools = {}
    for name in ("adb", "maestro", "java"):
        exe = tool(project, name) if name != "java" else "java"
        found = shutil.which(exe)
        record = {"available": bool(found), "path": found}
        if found:
            code, text = query(
                [exe, "-version" if name == "java" else "version" if name == "adb" else "--version"],
                project.root,
            )
            record.update(returncode=code, version=text.strip()[:1500])
        tools[name] = record
    return {
        "project_id": project.config["project"]["id"],
        "tools": tools,
        "device": inspect_device(project, serial),
        "missing_environment": [
            v for v in project.config.get("environment", {}).get("references", []) if v not in os.environ
        ],
        "case_count": len(project.cases),
    }
