from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://creator@localhost:5432/raphael"
    redis_url: str = "redis://localhost:6379"

    comfyui_url: str = "ws://localhost:8188"
    comfyui_output_dir: Path = Path(__file__).resolve().parent.parent / "output"

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:e2b"

    ss_client_id: str = ""
    ss_client_secret: str = ""
    ss_username: str = ""

    adobe_client_id: str = ""
    adobe_client_secret: str = ""

    parse_interval_hours: int = 6
    max_jobs_per_run: int = 10
    retry_max_attempts: int = 3

    log_level: str = "INFO"
    log_format: str = "json"


settings = Settings()
