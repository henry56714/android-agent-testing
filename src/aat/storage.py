from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import jsonschema
import yaml


class Invalid(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for k, v in node.value:
        key = loader.construct_object(k, deep=deep)
        if not isinstance(key, str):
            raise Invalid("YAML mapping keys must be strings")
        if key in result:
            raise Invalid(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(v, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def read_yaml(path, schema):
    try:
        data = yaml.load(Path(path).read_text(), Loader=UniqueLoader)
        jsonschema.Draft202012Validator(schema).validate(data)
        return data
    except (OSError, yaml.YAMLError, jsonschema.ValidationError) as e:
        raise Invalid(f"{path}: {e.message if isinstance(e, jsonschema.ValidationError) else e}") from e


def inside(root: Path, value: str, *, exists=False) -> Path:
    p = (root / value).resolve()
    if not p.is_relative_to(root.resolve()):
        raise Invalid(f"Path escapes project root: {value}")
    if exists and not p.is_file():
        raise Invalid(f"Missing file: {value}")
    return p


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise Invalid(f"Resource already in use: {path}") from e
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
