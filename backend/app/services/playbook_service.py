from pathlib import Path
import re
import unicodedata

import yaml

from app.config import settings


class PlaybookConfig:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        with open(path / "playbook.yaml") as f:
            self._config = yaml.safe_load(f)

    @property
    def display_name(self) -> str:
        return self._config.get("display_name", self.name)

    @property
    def description(self) -> str:
        return self._config.get("description", "")

    @property
    def version(self) -> str:
        return self._config.get("version", "1.0")

    @property
    def file_types(self) -> list[str]:
        return self._config.get("file_types", [])

    @property
    def detection_rules(self) -> list[dict]:
        return self._config.get("detection_rules", [])

    def get_file_type_config(self, file_type: str) -> dict:
        config_path = self.path / f"{file_type}.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"No config for file type '{file_type}' in playbook '{self.name}'")
        with open(config_path) as f:
            return yaml.safe_load(f)


def list_playbooks() -> list[PlaybookConfig]:
    playbooks_dir = settings.playbooks_path
    result = []
    for path in sorted(playbooks_dir.iterdir()):
        if path.is_dir() and not path.name.startswith("_") and (path / "playbook.yaml").exists():
            result.append(PlaybookConfig(path))
    return result


def get_playbook(name: str) -> PlaybookConfig:
    path = settings.playbooks_path / name
    if not path.exists() or not (path / "playbook.yaml").exists():
        raise FileNotFoundError(f"Playbook '{name}' not found")
    return PlaybookConfig(path)


def detect_file_type(playbook: PlaybookConfig, column_headers: list[str], filename: str) -> tuple[str | None, float]:
    headers_lower = [_normalize_indicator(h) for h in column_headers]
    filename_lower = filename.lower()

    best_match = None
    best_score = 0.0

    for rule in playbook.detection_rules:
        file_type = rule["file_type"]
        indicators = rule.get("indicators", {})

        # Check column signatures
        for signature in indicators.get("column_signatures", []):
            sig_lower = [_normalize_indicator(s) for s in signature]
            matches = sum(1 for s in sig_lower if s in headers_lower)
            score = matches / len(sig_lower) if sig_lower else 0
            if score > best_score:
                best_score = score
                best_match = file_type

        # Check filename patterns
        import fnmatch
        for pattern in indicators.get("filename_patterns", []):
            if fnmatch.fnmatch(filename_lower, pattern.lower()):
                if best_score < 0.5:
                    best_score = 0.5
                    best_match = file_type

    return (best_match, best_score) if best_score >= 0.5 else (None, 0.0)


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize_indicator(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _NON_ALNUM.sub(" ", text.lower()).strip()
