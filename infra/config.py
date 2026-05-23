from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_SMARTOLOGY_ENV = str(Path(__file__).resolve().parent.parent.parent / "smartology" / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[_SMARTOLOGY_ENV, ".env"],  # smartology/.env первый, локальный .env переопределяет
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://creator@localhost:5432/smartology"
    db_schema: str = "leonardo"
    redis_url: str = "redis://localhost:6379"

    # Hugging Face Inference API
    hf_token: str = ""  # https://huggingface.co/settings/tokens
    imagen_output_dir: Path = Path(__file__).resolve().parent.parent / "output"

    # Google AI Studio (платная альтернатива, $0.02/фото)
    google_api_key: str = ""
    imagen_model: str = "gemini-2.5-flash-image"
    imagen_aspect_ratio: str = "square"

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
