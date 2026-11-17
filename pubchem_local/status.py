"""
Build status shared between the build process and the web server through a small JSON file.
"""
import json
import os
import time
from datetime import datetime, timezone

from pubchem_local import config


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def status_path():
    return config.state_dir() / "build_status.json"


def read_status():
    try:
        with open(status_path(), "r") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"state": "idle"}


def write_status(status):
    config.state_dir().mkdir(parents=True, exist_ok=True)
    tmp = str(status_path()) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(status, fh, indent=1)
    os.replace(tmp, status_path())


class StatusReporter(object):
    """Keeps the status file up to date while a build runs and appends to the build log."""

    def __init__(self, datasets):
        self._last_write = 0.0
        self.status = {
            "state": "running",
            "pid": os.getpid(),
            "started_at": now_iso(),
            "finished_at": None,
            "datasets": list(datasets),
            "current": None,
            "progress": None,
            "error": None,
            "log": [],
        }
        config.log_dir().mkdir(parents=True, exist_ok=True)
        self._log_fh = open(config.log_dir() / "build.log", "a", buffering=1)
        self.flush(force=True)

    def log(self, message):
        line = "%s  %s" % (now_iso(), message)
        print(line, flush=True)
        self._log_fh.write(line + "\n")
        self.status["log"] = (self.status["log"] + [line])[-200:]
        self.flush(force=True)

    def step(self, dataset, message):
        self.status["current"] = {"dataset": dataset, "message": message}
        self.status["progress"] = None
        self.log("[%s] %s" % (dataset, message))

    def progress(self, done, total, unit="", detail=None):
        self.status["progress"] = {"done": done, "total": total, "unit": unit, "detail": detail}
        self.flush()

    def flush(self, force=False):
        now = time.time()
        if force or now - self._last_write > 1.0:
            self._last_write = now
            write_status(self.status)

    def finish(self, state, error=None):
        self.status["state"] = state
        self.status["error"] = error
        self.status["finished_at"] = now_iso()
        self.status["progress"] = None
        self.flush(force=True)
        self._log_fh.close()
