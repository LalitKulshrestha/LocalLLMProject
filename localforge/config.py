from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    localforge_host: str = "127.0.0.1"
    localforge_port: int = 8787
    localforge_data_dir: Path = Path("data")
    localforge_output_dir: Path = Path("data/outputs")
    localforge_workflow_dir: Path = Path("workflows")
    localforge_allow_remote: bool = False

    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_dir: Path = Path("engines/ComfyUI")
    comfyui_python: Path = Path("engines/ComfyUI/.venv/bin/python")
    comfyui_checkpoint: str = "v1-5-pruned-emaonly-fp16.safetensors"

    facefusion_dir: Path = Path("engines/facefusion")
    facefusion_python: Path = Path("engines/facefusion/.venv/bin/python")
    facefusion_execution_providers: str = "coreml,cpu"
    facefusion_execution_thread_count: int = 8
    facefusion_workflow_strategy: str = "memory"
    facefusion_video_memory_strategy: str = "tolerant"
    facefusion_face_swapper_model: str = "inswapper_128_fp16"
    facefusion_face_enhancer_model: str = "gfpgan_1.4"
    facefusion_face_swapper_pixel_boost: str = "512x512"
    facefusion_output_video_encoder: str = "h264_videotoolbox"
    facefusion_output_video_preset: str = "fast"

    def prepare(self) -> None:
        for child in ("uploads", "logs", "tmp"):
            (self.localforge_data_dir / child).mkdir(parents=True, exist_ok=True)
        self.localforge_output_dir.mkdir(parents=True, exist_ok=True)
        self.localforge_workflow_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
