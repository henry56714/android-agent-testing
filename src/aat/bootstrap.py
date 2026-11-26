"""Minimal source/APK onboarding with explicit missing prerequisites."""

import shutil
from pathlib import Path

import jsonschema
import yaml

from .schema import CONFIG
from .storage import Invalid, inside
from .transaction import atomic_bytes


def initialize(path, project_id, mode="source", apk=None, application_id=None, wrapper="./gradlew"):
    target = Path(path).resolve()
    if target.exists():
        raise Invalid("Refusing to overwrite existing project config")
    config = {
        "schema_version": 1,
        "project": {"id": project_id, "mode": mode, "root": "."},
        "catalog": "testing/cases",
        "suites": "testing/suites",
        "artifacts": ".aat/runs",
        "profiles": {"fast": {"tags": ["fast"]}, "change": {}, "full": {}},
        "discovery": {"enabled": True},
    }
    if application_id:
        config["target"] = {"application_id": application_id}
    missing = []
    if mode == "source":
        if apk:
            raise Invalid("--apk is only valid with --mode apk")
        config["tools"] = {"gradle_wrapper": wrapper}
        if not (target.parent / wrapper).is_file():
            missing.append("Provide the project Gradle wrapper or configure discovery.gradle_argv")
        missing.append(
            "For UI/direct instrumentation, configure build tasks, products, application ID and runner"
        )
    else:
        source = Path(apk).resolve() if apk else None
        if source and not source.is_file():
            raise Invalid("Input APK does not exist")
        config["products"] = {"app": {"input_path": "input/app.apk"}}
        destination = inside(target.parent, "input/app.apk")
        if source and destination.exists() and source != destination:
            raise Invalid("Refusing to overwrite existing input/app.apk")
        if not source and not destination.is_file():
            missing.append("Place an APK at input/app.apk")
        if not application_id:
            missing.append("Set target.application_id to the actual APK package")
        missing.append("Add a Maestro flow with business assertions before discover --apply")
    try:
        jsonschema.validate(config, CONFIG)
    except jsonschema.ValidationError as error:
        raise Invalid(error.message) from error
    target.parent.mkdir(parents=True, exist_ok=True)
    for directory in ("testing/cases", "testing/suites", "testing/maestro"):
        (target.parent / directory).mkdir(parents=True, exist_ok=True)
    if mode == "apk" and source and source != destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    ignore = target.parent / ".gitignore"
    existing = ignore.read_text() if ignore.exists() else ""
    patterns = [".aat/"] + (["input/*.apk"] if mode == "apk" else [])
    for pattern in patterns:
        if pattern not in existing.splitlines():
            existing = existing.rstrip("\n") + ("\n" if existing else "") + pattern + "\n"
    atomic_bytes(ignore, existing.encode())
    atomic_bytes(target, yaml.safe_dump(config, sort_keys=False).encode())
    return {
        "created": str(target),
        "mode": mode,
        "missing": missing,
        "next": ["discover", "discover --apply", "doctor --strict", "run"],
    }
