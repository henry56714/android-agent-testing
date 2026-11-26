from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def execute(argv, cwd, log, timeout, env=None, max_log_bytes=16 * 1024 * 1024):
    """No shell; output goes to disk; cancellation kills the process group."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    result = {"argv": list(argv), "log": str(log), "returncode": None, "reason": None}
    with log.open("wb") as out:
        try:
            p = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={**os.environ, **(env or {})},
            )
        except OSError as e:
            result.update(reason="COMMAND_UNAVAILABLE", message=str(e))
            return result

        def drain():
            written = 0
            for data in iter(lambda: p.stdout.read(65536), b""):
                if written < max_log_bytes:
                    chunk = data[: max_log_bytes - written]
                    out.write(chunk)
                    written += len(chunk)
                if written >= max_log_bytes:
                    result["log_truncated"] = True
            if result.get("log_truncated"):
                out.write(b"\n[AAT: process log truncated]\n")

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            result["returncode"] = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result["reason"] = "TIMEOUT"
        finally:
            # Also terminate descendants left behind after the group leader exits.
            try:
                os.killpg(p.pid, signal.SIGTERM)
                try:
                    p.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            reader.join(timeout=5)
            p.stdout.close()
    result["duration_seconds"] = round(time.monotonic() - start, 3)
    return result


def query(argv, cwd, timeout=15):
    """Bounded short query using the same cancellation/process-tree contract as tests."""
    with tempfile.TemporaryDirectory(prefix="aat-query-") as temporary:
        log = Path(temporary) / "query.log"
        result = execute(argv, cwd, log, timeout)
        text = log.read_text(errors="replace")
        if result["reason"] or result.get("log_truncated"):
            return -1, result["reason"] or "QUERY_OUTPUT_TRUNCATED"
        return result["returncode"], text
