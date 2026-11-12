"""Strict v1 schemas. Native test steps remain in their original files."""


def obj(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


S = {"type": "string", "minLength": 1}
SS = {"type": "array", "items": S, "uniqueItems": True}
ID = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"}
BOOL = {"type": "boolean"}
TIME = {"type": "number", "exclusiveMinimum": 0, "maximum": 86400}
VERSION = {"const": 1, "type": "integer"}
TASK = obj(
    {
        "argv": {"type": "array", "items": S, "minItems": 1},
        "depends_on": SS,
        "reports": SS,
        "timeout_seconds": TIME,
        "device": BOOL,
    },
    ["argv", "timeout_seconds"],
)
PRODUCT = obj({"from_task": S, "path": S, "input_path": S})
CONFIG = obj(
    {
        "schema_version": VERSION,
        "project": obj({"id": ID, "mode": {"enum": ["source", "apk"]}, "root": S}, ["id", "mode", "root"]),
        "catalog": S,
        "suites": S,
        "artifacts": S,
        "tools": obj({"gradle_wrapper": S, "adb": S, "maestro": S}),
        "target": obj(
            {
                "application_id": {
                    "type": "string",
                    "pattern": r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$",
                },
                "variant": S,
                "device": obj(
                    {"serial_env": S, "min_api": {"type": "integer", "minimum": 1}, "required_abis": SS}
                ),
            }
        ),
        "tasks": {"type": "object", "propertyNames": ID, "additionalProperties": TASK},
        "products": obj({"app": PRODUCT, "instrumentation": PRODUCT}),
        "instrumentation": obj({"test_application_id": S, "runner": S}, ["test_application_id", "runner"]),
        "environment": obj({"references": SS}),
        "device_policy": obj({"allow_clear_test_package": BOOL, "restore_changed_settings": BOOL}),
        "profiles": {
            "type": "object",
            "propertyNames": ID,
            "additionalProperties": obj({"cases": SS, "tags": SS, "suite": S}),
        },
        "capabilities": {"type": "object", "propertyNames": ID, "additionalProperties": TASK},
        "snapshot": obj({"exclude": SS, "max_bytes": {"type": "integer", "minimum": 1}}),
        "privacy": obj({"redact_env": SS, "patterns": SS}),
        "retention": obj({"days": {"type": "integer", "minimum": 0}, "keep_failures": BOOL}),
        "discovery": obj(
            {
                "enabled": BOOL,
                "gradle_argv": SS,
                "manifest": S,
                "exclude": {
                    "type": "array",
                    "items": obj({"pattern": S, "reason": S}, ["pattern", "reason"]),
                },
                "task_dependencies": {"type": "object", "additionalProperties": SS},
                "task_cases": {"type": "object", "additionalProperties": SS},
                "source_cases": {"type": "object", "additionalProperties": SS},
            }
        ),
    },
    ["schema_version", "project", "catalog", "artifacts"],
)

CASE = obj(
    {
        "schema_version": VERSION,
        "id": ID,
        "revision": {"type": "integer", "minimum": 1},
        "status": {"enum": ["draft", "active", "quarantined", "deprecated"]},
        "title": S,
        "layer": {"enum": ["static", "local", "device", "ui"]},
        "tags": SS,
        "requirements": SS,
        "contract": S,
        "impact": obj({"paths": SS, "symbols": SS}),
        "implementation": obj(
            {
                "engine": {"enum": ["junit", "lint", "maestro", "instrumentation", "gradle-device"]},
                "task": S,
                "selector": S,
                "path": S,
                "fail_severities": SS,
            },
            ["engine"],
        ),
        "assets": SS,
        "applicability": obj(
            {"modes": {"type": "array", "items": {"enum": ["source", "apk"]}}, "variants": SS}
        ),
        "requires": SS,
        "priority": {"enum": ["critical", "normal", "low"]},
        "timeout_seconds": TIME,
        "origin": obj({"kind": S, "reference": S}, ["kind", "reference"]),
        "required": BOOL,
        "reason": S,
        "replacement": S,
    },
    [
        "schema_version",
        "id",
        "revision",
        "status",
        "title",
        "layer",
        "contract",
        "implementation",
        "timeout_seconds",
    ],
)

SUITE = obj({"schema_version": VERSION, "id": ID, "cases": SS, "tags": SS}, ["schema_version", "id"])
