import os
from pathlib import Path
from pydantic_settings import BaseSettings

# Project root is one level up from backend/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# Pydantic-settings resolves values in this order:
#     init args > environment variables > .env file > field defaults
# An environment variable that is *set but empty* therefore silently
# shadows whatever is in .env, which almost never matches the user's
# intent and produces confusing failures — e.g. the Anthropic SDK
# rejecting an empty api_key even though .env contains a valid one.
# Wipe empty values for known config keys so .env takes effect. Only
# empty strings are touched; real values are left alone.
_EMPTY_SHADOWING_KEYS = ("ANTHROPIC_API_KEY",)
for _key in _EMPTY_SHADOWING_KEYS:
    if os.environ.get(_key, None) == "":
        del os.environ[_key]


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
