import asyncio
import shutil

from .db import get_job, next_queued_job, update_job
from .engines import run_job
from .config import settings


def _clear_completed_uploads(job_id: str) -> None:
    upload_root = (settings.localforge_data_dir / "uploads").resolve()
    upload_dir = (upload_root / job_id).resolve()
    if upload_dir.parent == upload_root:
        shutil.rmtree(upload_dir, ignore_errors=True)


def _clear_partial_outputs(job: dict) -> None:
    output_root = settings.localforge_output_dir.resolve()
    prefix = job["id"][:12]
    if not output_root.is_dir():
        return
    for path in output_root.iterdir():
        if path.is_file() and (path.name == f"face-swap-{prefix}.mp4" or path.name.startswith(f"{job['kind']}-{prefix}-")):
            path.unlink(missing_ok=True)


async def worker_loop() -> None:
    while True:
        job = next_queued_job()
        if not job:
            await asyncio.sleep(1)
            continue
        try:
            outputs = await run_job(job)
            current = get_job(job["id"])
            if current and current.get("status") == "failed":
                _clear_partial_outputs(job)
                continue
            _clear_completed_uploads(job["id"])
            update_job(job["id"], status="completed", stage="Completed", progress=1.0, inputs={}, outputs=outputs)
        except asyncio.CancelledError:
            update_job(job["id"], status="queued")
            raise
        except Exception as exc:
            _clear_partial_outputs(job)
            current = get_job(job["id"])
            if current and "Cancelled" in str(current.get("error", "")):
                continue
            update_job(job["id"], status="failed", stage="Failed", error=str(exc))
