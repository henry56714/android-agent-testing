from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


def execute(argv, cwd, log, timeout, env=None):
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
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={**os.environ, **(env or {})},
            )
        except OSError as e:
            result.update(reason="COMMAND_UNAVAILABLE", message=str(e))
            return result
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
    result["duration_seconds"] = round(time.monotonic() - start, 3)
    return result
