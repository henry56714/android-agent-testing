"""Reproducible checks with immutable receipts and an optional native Android matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from record_check import record


def main(output, device=None):
    root = Path.cwd()
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    files = [
        p
        for directory in ("src", "tests", "tools", "docs", ".github")
        for p in (root / directory).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    files += [root / p for p in ("README.md", "pyproject.toml", "uv.lock")]
    sources = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    (output / "source-manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=str(path.relative_to(root)))
    diff = subprocess.run(["git", "diff", "--binary"], capture_output=True, check=True).stdout
    (output / "working-tree.patch").write_bytes(diff)
    baseline = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    receipts = []
    completed = False
    checks = [
        ("ruff", ["uv", "run", "--frozen", "ruff", "check", "src", "tests", "tools"]),
        ("format", ["uv", "run", "--frozen", "ruff", "format", "--check", "src", "tests", "tools"]),
        ("core", ["uv", "run", "--frozen", "pytest", "-q", f"--junitxml={output / 'core.junit.xml'}"]),
        ("docs", [sys.executable, "tools/validate_docs.py"]),
        ("package", [sys.executable, "tools/package_smoke.py", "--output", str(output / "package")]),
        ("benchmark", [sys.executable, "tools/benchmark_snapshot.py"]),
    ]
    try:
        for name, command in checks:
            receipt = record(output, name, command)
            receipts.append(receipt)
            if not receipt["passed"]:
                raise RuntimeError(f"Check failed: {name}; see {output / receipt['log']}")
        if device:
            receipt = record(
                output,
                "native",
                [
                    sys.executable,
                    "tools/native_selftest.py",
                    "--output",
                    str(output / "native"),
                    "--device",
                    device,
                    "--python",
                    str(output / "package/wheel-env/bin/python"),
                ],
                timeout=1800,
            )
            receipts.append(receipt)
            if not receipt["passed"]:
                raise RuntimeError("Native Android check failed")
        changed = [
            name
            for name, sha in sources.items()
            if not (root / name).exists() or hashlib.sha256((root / name).read_bytes()).hexdigest() != sha
        ]
        if changed:
            raise RuntimeError(f"Source changed during verification: {changed}")
        completed = True
    finally:
        summary = {
            "passed": completed,
            "completed_at": datetime.now(UTC).isoformat(),
            "baseline_commit": baseline,
            "python": sys.version,
            "platform": platform.platform(),
            "checks": receipts,
            "android_device": device,
            "not_run": ["remote CI", "physical devices"] + ([] if device else ["Android emulator"]),
            "source_manifest_sha256": hashlib.sha256(
                (output / "source-manifest.json").read_bytes()
            ).hexdigest(),
        }
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        manifest = {}
        for path in output.rglob("*"):
            if (
                path.is_file()
                and "wheel-env" not in path.parts
                and path.suffix in (".json", ".log", ".xml", ".patch", ".whl", ".gz")
            ):
                manifest[str(path.relative_to(output))] = hashlib.sha256(path.read_bytes()).hexdigest()
        (output / "evidence-sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"passed": completed, "evidence": str(output / "summary.json")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", required=True, help="New evidence directory; existing directories are refused"
    )
    parser.add_argument("--device", help="Explicit test-device serial for native Android checks")
    args = parser.parse_args()
    main(args.output, args.device)
