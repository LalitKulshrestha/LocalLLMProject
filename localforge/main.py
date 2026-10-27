import asyncio
import re
import shutil
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import (
    cancel_job,
    create_job,
    delete_job,
    get_job,
    initialize,
    list_jobs,
    list_jobs_by_status,
    retry_job,
    set_inputs_and_queue,
)
from .engines import cancel_engine_job, health
from .formats import generation_size
from .worker import worker_loop


def _safe_name(name: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name or "media").name)
    return cleaned[:180] or "media"


def _output_roots() -> tuple[Path, ...]:
    return (settings.localforge_output_dir.resolve(), (settings.localforge_data_dir / "outputs").resolve())


def _job_output(job: dict, index: int) -> Path:
    if index < 0 or index >= len(job["outputs"]):
        raise HTTPException(404, "Output not found")
    path = Path(job["outputs"][index]).resolve()
    if not path.is_file() or not any(root in path.parents for root in _output_roots()):
        raise HTTPException(404, "Output not found")
    return path


def _remove_job_files(job: dict) -> None:
    job_id = job["id"]
    for raw_path in job["outputs"]:
        path = Path(raw_path).resolve()
        if any(root in path.parents for root in _output_roots()):
            path.unlink(missing_ok=True)

    output_root = settings.localforge_output_dir.resolve()
    prefix = job_id[:12]
    for path in output_root.iterdir():
        if path.is_file() and (path.name == f"face-swap-{prefix}.mp4" or path.name.startswith(f"{job['kind']}-{prefix}-")):
            path.unlink()

    for root in _output_roots():
        legacy_dir = (root / job_id).resolve()
        if legacy_dir.parent == root and legacy_dir.is_dir():
            shutil.rmtree(legacy_dir)

    data_root = settings.localforge_data_dir.resolve()
    for sub in ("uploads", "tmp"):
        folder = (data_root / sub / job_id).resolve()
        if folder.parent == data_root / sub and folder.is_dir():
            shutil.rmtree(folder)
    (data_root / "logs" / f"{job_id}.log").unlink(missing_ok=True)


def _directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def _old_completed_jobs() -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    return [
        job for job in list_jobs_by_status("completed")
        if datetime.fromisoformat(job["updated_at"]) < cutoff
    ]


def _clear_stale_temp() -> None:
    temp_root = (settings.localforge_data_dir / "tmp").resolve()
    cutoff = time.time() - 24 * 60 * 60
    for path in temp_root.iterdir():
        if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
            shutil.rmtree(path)


def _open_in_finder(path: Path, reveal: bool = False) -> None:
    if sys.platform != "darwin":
        raise HTTPException(501, "Finder actions are available on macOS")
    command = ["/usr/bin/open", "-R", str(path)] if reveal else ["/usr/bin/open", str(path)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise HTTPException(500, "Could not open Finder")


async def _save_upload(job_id: str, item: UploadFile, label: str) -> str:
    folder = (settings.localforge_data_dir / "uploads" / job_id).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{label}-{_safe_name(item.filename)}"
    with path.open("wb") as target:
        while chunk := await item.read(1024 * 1024):
            target.write(chunk)
    return str(path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.prepare()
    initialize()
    _clear_stale_temp()
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()


app = FastAPI(title="LocalForge", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="web"), name="static")


@app.get("/")
async def home():
    return FileResponse("web/index.html")


@app.get("/api/health")
async def api_health():
    return await health()


@app.get("/api/storage")
async def api_storage():
    data_root = settings.localforge_data_dir.resolve()
    output_root = settings.localforge_output_dir.resolve()
    return {
        "output_dir": str(output_root),
        "output_bytes": _directory_bytes(output_root),
        "upload_bytes": _directory_bytes(data_root / "uploads"),
        "temp_bytes": _directory_bytes(data_root / "tmp"),
        "model_bytes": _directory_bytes(settings.facefusion_dir / ".assets" / "models"),
        "old_completed_count": len(_old_completed_jobs()),
    }


@app.post("/api/outputs/reveal")
async def api_reveal_output_folder():
    _open_in_finder(settings.localforge_output_dir.resolve())
    return {"opened": True}


@app.get("/api/jobs")
async def api_jobs():
    return list_jobs()


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/jobs/{job_id}/logs")
async def api_job_logs(job_id: str):
    log_path = (settings.localforge_data_dir / "logs" / f"{job_id}.log").resolve()
    if not log_path.exists():
        return PlainTextResponse("No log recorded yet for this job.", status_code=200)
    return PlainTextResponse(log_path.read_text(encoding="utf-8", errors="replace"))


@app.post("/api/jobs/{job_id}/retry")
async def api_job_retry(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "failed":
        raise HTTPException(409, "Only failed jobs can be retried")
    updated = retry_job(job_id)
    return updated


@app.post("/api/jobs/{job_id}/cancel")
async def api_job_cancel(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    await cancel_engine_job(job_id)
    updated = cancel_job(job_id)
    return updated


@app.delete("/api/jobs/{job_id}")
async def api_job_delete(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in {"running", "queued", "staging"}:
        raise HTTPException(409, "Cancel the job before deleting it")
    _remove_job_files(job)
    delete_job(job_id)
    return {"deleted": True, "id": job_id}


@app.post("/api/jobs/cleanup-failed")
async def api_cleanup_failed():
    failed = list_jobs_by_status("failed")
    for job in failed:
        _remove_job_files(job)
        delete_job(job["id"])
    return {"deleted": len(failed)}


@app.post("/api/jobs/cleanup-old")
async def api_cleanup_old():
    old_jobs = _old_completed_jobs()
    for job in old_jobs:
        _remove_job_files(job)
        delete_job(job["id"])
    return {"deleted": len(old_jobs)}


@app.get("/api/jobs/{job_id}/output/{index}")
async def api_output(job_id: str, index: int):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Output not found")
    path = _job_output(job, index)
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@app.post("/api/jobs/{job_id}/output/{index}/reveal")
async def api_reveal_output(job_id: str, index: int):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Output not found")
    _open_in_finder(_job_output(job, index), reveal=True)
    return {"opened": True}


@app.post("/api/jobs/face-swap")
async def face_swap(
    face: UploadFile = File(...),
    video: UploadFile = File(...),
    quality: int = Form(90),
    profile: str = Form("high"),
    face_enhancer: bool = Form(True),
    face_swapper_model: str = Form("inswapper_128_fp16"),
    enhancer_blend: int = Form(80),
    face_selector_mode: str = Form("one"),
    pixel_boost: str = Form("512x512"),
    video_encoder: str = Form("h264_videotoolbox"),
):
    params = {
        "quality": max(1, min(100, quality)),
        "profile": profile,
        "face_enhancer": face_enhancer,
        "face_swapper_model": face_swapper_model,
        "enhancer_blend": max(0, min(100, enhancer_blend)),
        "face_selector_mode": face_selector_mode,
        "pixel_boost": pixel_boost,
        "video_encoder": video_encoder,
        "enhancer_model": settings.facefusion_face_enhancer_model,
    }
    job = create_job("face_swap", params, {}, status="staging")
    face_path = await _save_upload(job["id"], face, "face")
    video_path = await _save_upload(job["id"], video, "video")
    set_inputs_and_queue(job["id"], {"face": face_path, "video": video_path})
    return get_job(job["id"])


@app.post("/api/jobs/generate")
async def generate(
    kind: str = Form(...),
    prompt: str = Form(""),
    negative_prompt: str = Form(""),
    seed: int = Form(0),
    output_format: str = Form("shorts"),
    image: UploadFile | None = File(None),
):
    if kind != "text_to_image":
        raise HTTPException(400, "A compatible video generation model is not configured")
    try:
        width, height, ratio = generation_size(kind, output_format)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    params = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "seed": seed,
        "output_format": output_format if kind != "text_to_image" else "square",
        "aspect_ratio": ratio,
        "width": width,
        "height": height,
        "frames": 49,
    }
    job = create_job(kind, params, {}, status="staging")
    inputs = {}
    if image:
        inputs["image"] = await _save_upload(job["id"], image, "image")
    set_inputs_and_queue(job["id"], inputs)
    return get_job(job["id"])
