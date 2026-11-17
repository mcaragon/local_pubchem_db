"""
Start, monitor and cancel the build process from the web server. The build runs as a separate process
('pubchem-local build ...') so that it survives slow requests and can use all CPU cores.
"""
import os
import signal
import subprocess
import sys

from pubchem_local import config
from pubchem_local.status import read_status, write_status, now_iso


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # Reap zombies of builds we started ourselves
    try:
        done_pid, _ = os.waitpid(pid, os.WNOHANG)
        return done_pid == 0
    except ChildProcessError:
        return True


def current_status():
    status = read_status()
    if status.get("state") == "running" and not _pid_alive(status.get("pid")):
        status["state"] = "interrupted"
        status["error"] = status.get("error") or "The build process stopped unexpectedly (container restart?)."
        status["finished_at"] = status.get("finished_at") or now_iso()
        write_status(status)
    return status


def is_running():
    return current_status().get("state") == "running"


def start_build(datasets, fresh=False):
    if is_running():
        raise RuntimeError("A build is already running")
    datasets = [d for d in config.DATASETS if d in datasets]
    if not datasets:
        raise ValueError("No valid datasets given (choose from %s)" % ", ".join(config.DATASETS))
    config.ensure_dirs()
    cmd = [sys.executable, "-m", "pubchem_local.cli", "build", "--datasets", ",".join(datasets)]
    if fresh:
        cmd.append("--fresh")
    log = open(config.log_dir() / "build.stdout.log", "ab")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    # Write a provisional status so that the UI shows the build immediately
    write_status({"state": "running", "pid": proc.pid, "started_at": now_iso(), "datasets": datasets,
                  "current": {"dataset": datasets[0], "message": "Starting"}, "progress": None, "log": [],
                  "error": None, "finished_at": None})
    return proc.pid


def cancel_build():
    status = current_status()
    if status.get("state") != "running":
        return False
    try:
        os.killpg(os.getpgid(status["pid"]), signal.SIGTERM)
    except OSError:
        return False
    return True
