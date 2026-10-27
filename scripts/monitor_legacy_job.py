"""Bridge progress for a face-swap job started before live parsing was fixed.

This process exits automatically when that job finishes. New jobs use the
FaceFusion output parser in localforge.engines instead.
"""

import sys
import time
import os
import signal
import socket
import subprocess
from pathlib import Path

from localforge.config import settings
from localforge.db import get_job, initialize, list_jobs, update_job


def restart_after_idle(server_pid: int) -> None:
    while any(job["status"] in {"staging", "queued", "running"} for job in list_jobs()):
        time.sleep(10)

    os.kill(server_pid, signal.SIGTERM)
    for _ in range(100):
        with socket.socket() as connection:
            connection.settimeout(0.2)
            if connection.connect_ex(("127.0.0.1", settings.localforge_port)) != 0:
                break
        time.sleep(0.2)
    else:
        raise RuntimeError("Old LocalForge listener did not stop")

    project_dir = Path(__file__).resolve().parent.parent
    log_path = settings.localforge_data_dir / "logs" / "localforge-server.log"
    with log_path.open("a") as output:
        subprocess.Popen(
            ["/bin/zsh", "scripts/start.sh"],
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def main(job_id: str, server_pid: int | None = None) -> None:
    initialize()
    temp_dir = settings.localforge_data_dir / "tmp" / job_id / "facefusion"
    log_path = settings.localforge_data_dir / "logs" / f"{job_id}.log"
    extract_cutoff = log_path.stat().st_mtime_ns

    while True:
        job = get_job(job_id)
        if not job:
            return
        if job["status"] != "running":
            update_job(job_id, stage="Completed" if job["status"] == "completed" else "Failed")
            if server_pid is not None:
                restart_after_idle(server_pid)
            return

        frames = list(temp_dir.rglob("*.png")) if temp_dir.exists() else []
        processed = 0
        for frame in frames:
            try:
                processed += frame.stat().st_mtime_ns > extract_cutoff
            except FileNotFoundError:
                pass
        if processed and frames:
            progress = min(0.9, 0.1 + 0.8 * processed / len(frames))
            if progress > job["progress"]:
                update_job(job_id, stage="Swapping faces", progress=progress)

        log = log_path.read_text(errors="replace") if log_path.exists() else ""
        if "merging video with a resolution" in log:
            update_job(job_id, stage="Encoding video", progress=max(0.9, job["progress"]))
        time.sleep(10)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
