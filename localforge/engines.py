import asyncio
import codecs
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .config import settings
from .db import update_job


def _replace_tokens(value: Any, tokens: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: _replace_tokens(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_tokens(v, tokens) for v in value]
    if isinstance(value, str):
        if value in tokens:
            return tokens[value]
        for token, replacement in tokens.items():
            value = value.replace(token, str(replacement))
    return value


active_processes: dict[str, asyncio.subprocess.Process] = {}


def _facefusion_progress(line: str) -> tuple[str, float] | None:
    match = re.search(r"\b(extracting|processing|merging):\s*(\d+)%", line, re.IGNORECASE)
    if match:
        phase = match.group(1).lower()
        fraction = min(100, int(match.group(2))) / 100
        if phase == "extracting":
            return "Extracting frames", fraction * 0.1
        if phase == "processing":
            return "Swapping faces", 0.1 + fraction * 0.8
        return "Encoding video", 0.9 + fraction * 0.09
    if "extracting frames with a resolution" in line:
        return "Extracting frames", 0.0
    if "merging video with a resolution" in line:
        return "Encoding video", 0.9
    if "restoring audio" in line or "replacing audio" in line:
        return "Finishing audio", 0.99
    return None


def _split_progress_messages(pending: str, chunk: str) -> tuple[list[str], str]:
    pending += chunk
    parts = re.split(r"[\r\n]+", pending)
    if pending.endswith(("\r", "\n")):
        return parts, ""
    return parts[:-1], parts[-1]


async def cancel_engine_job(job_id: str) -> None:
    process = active_processes.get(job_id)
    if process and process.returncode is None:
        try:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        except ProcessLookupError:
            pass


async def run_face_swap(job: dict[str, Any]) -> list[str]:
    job_id = job["id"]
    output_dir = settings.localforge_output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"face-swap-{job_id[:12]}.mp4"
    temp_root = (settings.localforge_data_dir / "tmp" / job_id).resolve()
    log_dir = (settings.localforge_data_dir / "logs").resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job_id}.log"

    face_path = Path(job["inputs"]["face"]).resolve()
    video_path = Path(job["inputs"]["video"]).resolve()
    facefusion_dir = settings.facefusion_dir.resolve()
    facefusion_python = (Path.cwd() / settings.facefusion_python).absolute()

    if not face_path.exists():
        raise FileNotFoundError(f"Source face image not found: {face_path}")
    if not video_path.exists():
        raise FileNotFoundError(f"Target video not found: {video_path}")
    if not facefusion_python.exists():
        raise FileNotFoundError(f"FaceFusion Python environment not found: {facefusion_python}")

    params = job.get("params", {})
    profile = str(params.get("profile", "high")).lower()  # standard, high, ultra
    quality = max(1, min(100, int(params.get("quality", 90 if profile in {"high", "ultra"} else 80))))
    encoder = str(params.get("video_encoder", settings.facefusion_output_video_encoder))
    face_swapper_model = str(params.get("face_swapper_model", settings.facefusion_face_swapper_model))
    if face_swapper_model not in {"inswapper_128_fp16", "alphaface_256"}:
        raise ValueError(f"Unsupported face swapper model: {face_swapper_model}")
    model_dir = facefusion_dir / ".assets" / "models"
    if not all((model_dir / f"{face_swapper_model}.{ext}").exists() for ext in ("onnx", "hash")):
        raise FileNotFoundError(f"Face swapper model is not installed: {face_swapper_model}")
    face_selector_mode = str(params.get("face_selector_mode", "one"))
    if face_selector_mode not in {"one", "reference"}:
        raise ValueError(f"Unsupported face selector mode: {face_selector_mode}")
    enhancer_blend = max(0, min(100, int(params.get("enhancer_blend", 80))))

    processors = ["face_swapper"]
    use_enhancer = bool(params.get("face_enhancer", profile in {"high", "ultra"}))
    if use_enhancer:
        enhancer_model = str(params.get("enhancer_model", settings.facefusion_face_enhancer_model))
        if not all((model_dir / f"{enhancer_model}.{ext}").exists() for ext in ("onnx", "hash")):
            raise FileNotFoundError(f"Face enhancer model is not installed: {enhancer_model}")
        processors.append("face_enhancer")

    command = [
        str(facefusion_python), "facefusion.py", "headless-run",
        "--source-paths", str(face_path),
        "--target-path", str(video_path),
        "--output-path", str(output),
        "--temp-path", str(temp_root),
        "--processors", *processors,
        "--face-swapper-model", face_swapper_model,
        "--face-selector-mode", face_selector_mode,
        "--execution-providers", *[p.strip() for p in settings.facefusion_execution_providers.split(",") if p.strip()],
        "--execution-thread-count", str(settings.facefusion_execution_thread_count),
        "--workflow-strategy", settings.facefusion_workflow_strategy,
        "--video-memory-strategy", settings.facefusion_video_memory_strategy,
        "--download-providers", "huggingface", "github",
        "--output-video-encoder", encoder,
        "--output-video-preset", settings.facefusion_output_video_preset,
        "--output-video-quality", str(quality),
        "--output-audio-encoder", "aac",
    ]

    if use_enhancer:
        enhancer_model = str(params.get("enhancer_model", settings.facefusion_face_enhancer_model))
        command.extend(["--face-enhancer-model", enhancer_model, "--face-enhancer-blend", str(enhancer_blend)])

    pixel_boost = params.get("pixel_boost")
    if not pixel_boost and profile in {"high", "ultra"}:
        pixel_boost = "1024x1024" if profile == "ultra" else "512x512"
    if pixel_boost:
        command.extend(["--face-swapper-pixel-boost", str(pixel_boost)])

    recent_lines: list[str] = []
    last_progress = 0.0
    last_stage = "Preparing"

    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
            log_file.write(f"=== LocalForge Face Swap Job: {job_id} ===\n")
            log_file.write(f"Command: {' '.join(command)}\n\n")
            log_file.flush()

            env = dict(os.environ)
            venv_dir = (facefusion_dir / ".venv").absolute()
            env["VIRTUAL_ENV"] = str(venv_dir)
            env["PATH"] = f"{venv_dir / 'bin'}:{env.get('PATH', '')}"
            env["PYTHONUNBUFFERED"] = "1"

            output.unlink(missing_ok=True)
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(facefusion_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            active_processes[job_id] = process

            try:
                assert process.stdout is not None
                decoder = codecs.getincrementaldecoder("utf-8")("replace")
                pending = ""

                def record_line(raw_line: str) -> None:
                    nonlocal last_progress, last_stage
                    line = raw_line.strip()
                    if not line:
                        return
                    log_file.write(line + "\n")
                    log_file.flush()
                    recent_lines.append(line)
                    if len(recent_lines) > 40:
                        recent_lines.pop(0)
                    progress_update = _facefusion_progress(line)
                    if progress_update:
                        stage, progress = progress_update
                        progress = max(last_progress, min(0.99, progress))
                        if stage != last_stage or int(progress * 100) != int(last_progress * 100):
                            update_job(job_id, stage=stage, progress=progress)
                            last_stage, last_progress = stage, progress

                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    parts, pending = _split_progress_messages(pending, decoder.decode(chunk))
                    for line in parts:
                        record_line(line)

                pending += decoder.decode(b"", final=True)
                if pending:
                    record_line(pending)

                code = await process.wait()
            except asyncio.CancelledError:
                await cancel_engine_job(job_id)
                raise
            finally:
                active_processes.pop(job_id, None)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    if code != 0 or not output.exists():
        error_context = "\n".join([ln for ln in recent_lines if "processing:" not in ln and "downloading:" not in ln][-5:])
        error_msg = f"FaceFusion exited with code {code}"
        if error_context:
            error_msg += f": {error_context}"
        raise RuntimeError(error_msg)

    update_job(job_id, stage="Completed", progress=1.0)
    return [str(output)]


async def _upload_to_comfy(path: str) -> str:
    source = Path(path)
    unique_name = f"localforge-{uuid4().hex}-{source.name}"
    async with httpx.AsyncClient(timeout=120) as client:
        with source.open("rb") as stream:
            response = await client.post(
                f"{settings.comfyui_url}/upload/image",
                files={"image": (unique_name, stream, "application/octet-stream")},
                data={"overwrite": "true", "type": "input"},
            )
        response.raise_for_status()
        return response.json()["name"]


async def run_comfy(job: dict[str, Any]) -> list[str]:
    workflow_path = settings.localforge_workflow_dir / f"{job['kind']}.json"
    if not workflow_path.exists():
        raise RuntimeError(f"Missing API workflow: {workflow_path}")
    workflow = json.loads(workflow_path.read_text())
    input_image = ""
    if job["inputs"].get("image"):
        input_image = await _upload_to_comfy(job["inputs"]["image"])
    params = job["params"]
    update_job(job["id"], stage="Generating media")
    tokens = {
        "{{PROMPT}}": params.get("prompt", ""),
        "{{NEGATIVE_PROMPT}}": params.get("negative_prompt", ""),
        "{{INPUT_IMAGE}}": input_image,
        "{{SEED}}": int(params.get("seed", 0)),
        "{{WIDTH}}": int(params.get("width", 512)),
        "{{HEIGHT}}": int(params.get("height", 512)),
        "{{FRAMES}}": int(params.get("frames", 49)),
        "{{CHECKPOINT}}": settings.comfyui_checkpoint,
    }
    workflow = _replace_tokens(workflow, tokens)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{settings.comfyui_url}/prompt", json={"prompt": workflow})
        response.raise_for_status()
        prompt_id = response.json()["prompt_id"]
        for elapsed in range(3600):
            await asyncio.sleep(2)
            history = (await client.get(f"{settings.comfyui_url}/history/{prompt_id}")).json()
            if prompt_id not in history:
                continue
            record = history[prompt_id]
            if record.get("status", {}).get("status_str") == "error":
                raise RuntimeError("ComfyUI workflow failed; inspect the ComfyUI console")
            output_dir = settings.localforge_output_dir.resolve()
            update_job(job["id"], stage="Saving output")
            output_dir.mkdir(parents=True, exist_ok=True)
            results = []
            for node in record.get("outputs", {}).values():
                for group in ("images", "gifs", "videos", "audio"):
                    for artifact in node.get(group, []):
                        params_out = {
                            "filename": artifact["filename"],
                            "subfolder": artifact.get("subfolder", ""),
                            "type": artifact.get("type", "output"),
                        }
                        filename = Path(artifact["filename"]).name[:120]
                        destination = output_dir / f"{job['kind']}-{job['id'][:12]}-{len(results) + 1:02d}-{filename}"
                        async with client.stream("GET", f"{settings.comfyui_url}/view", params=params_out) as download:
                            download.raise_for_status()
                            with destination.open("wb") as target:
                                async for chunk in download.aiter_bytes(1024 * 1024):
                                    target.write(chunk)
                        results.append(str(destination))
            if not results:
                raise RuntimeError("ComfyUI completed but returned no downloadable media")
            return results
    raise TimeoutError("ComfyUI job exceeded the two-hour local timeout")


async def run_job(job: dict[str, Any]) -> list[str]:
    if job["kind"] == "face_swap":
        return await run_face_swap(job)
    return await run_comfy(job)


async def health() -> dict[str, Any]:
    model_dir = settings.facefusion_dir / ".assets" / "models"
    model_name = settings.facefusion_face_swapper_model
    facefusion = (
        settings.facefusion_python.exists()
        and (settings.facefusion_dir / "facefusion.py").exists()
        and all((model_dir / f"{model_name}.{ext}").exists() for ext in ("onnx", "hash"))
    )
    comfy = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            comfy = (await client.get(f"{settings.comfyui_url}/system_stats")).is_success
    except httpx.HTTPError:
        pass
    image_ready = (
        (settings.comfyui_dir / "models" / "checkpoints" / settings.comfyui_checkpoint).is_file()
        and (settings.localforge_workflow_dir / "text_to_image.json").is_file()
    )
    modes = {
        "text_to_image": comfy and image_ready,
        "image_to_video": False,
        "text_to_video": False,
    }
    return {
        "facefusion": facefusion,
        "comfyui": comfy,
        "generation": any(modes.values()),
        "generation_modes": modes,
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }
