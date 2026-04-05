from pathlib import Path
from pydantic_settings import BaseSettings

# Project root is one level up from backend/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    app_name: str = "Sunshine"
    database_url: str = f"sqlite+aiosqlite:///{PROJECT_ROOT / 'backend' / 'storage' / 'sunshine.db'}"
    storage_path: Path = PROJECT_ROOT / "backend" / "storage"
    cors_origins: list[str] = ["http://localhost:5173"]
    anthropic_api_key: str = ""
    playbooks_path: Path = PROJECT_ROOT / "playbooks"
    max_upload_size_mb: int = 50
    preview_row_limit: int = 20

    model_config = {"env_file": str(PROJECT_ROOT / ".env"), "env_file_encoding": "utf-8"}

    @property
    def uploads_path(self) -> Path:
        return self.storage_path / "uploads"

    @property
    def outputs_path(self) -> Path:
        return self.storage_path / "outputs"

    @property
    def temp_path(self) -> Path:
        return self.storage_path / "temp"


settings = Settings()
