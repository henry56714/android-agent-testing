"""Run one reproducible check and retain its output, exit code and digest."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def record(directory, name, argv, expected=0, timeout=1800, cwd=None, env=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / f"{name}.log"
    receipt = directory / f"{name}.json"
    if log.exists() or receipt.exists():
        raise ValueError(f"Evidence already exists: {name}; use a new check name")
    started = datetime.now(UTC).isoformat()
    start = time.monotonic()
    timed_out = False
    with log.open("wb") as output:
        try:
            completed = subprocess.run(
                argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=timeout, check=False
            )
            code = completed.returncode
        except subprocess.TimeoutExpired:
            code, timed_out = 124, True
    result = {
        "name": name,
        "argv": argv,
        "cwd": str(Path(cwd or ".").resolve()),
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(time.monotonic() - start, 3),
        "returncode": code,
        "expected_returncode": expected,
        "passed": code == expected and not timed_out,
        "timed_out": timed_out,
        "log": log.name,
        "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
    }
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--expect", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command required after --")
    raise SystemExit(
        0 if record(args.directory, args.name, command, args.expect, args.timeout)["passed"] else 1
    )
