from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://creator@localhost:5432/raphael"
    redis_url: str = "redis://localhost:6379"

    # Google AI Studio
    google_api_key: str = ""
    imagen_model: str = "imagen-4.0-fast-generate-001"
    imagen_output_dir: Path = Path(__file__).resolve().parent.parent / "output"
    imagen_aspect_ratio: str = "square"  # square|landscape|portrait|wide

    # Google Flow (Veo — video generation)
    google_flow_model: str = "veo-2.0-generate-001"
    flow_output_dir: Path = Path(__file__).resolve().parent.parent / "output_video"

    # Ollama (Gemma — metadata generation, runs locally)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:e2b"

    # Shutterstock Contributor API
    ss_client_id: str = ""
    ss_client_secret: str = ""
    ss_username: str = ""

    # Adobe Stock Contributor API
    adobe_client_id: str = ""
    adobe_client_secret: str = ""

    parse_interval_hours: int = 6
    max_jobs_per_run: int = 10
    retry_max_attempts: int = 3
    output_keep_days: int = 14

    log_level: str = "INFO"
    log_format: str = "json"


settings = Settings()
