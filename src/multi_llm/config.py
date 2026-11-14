from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from .models import RouteRecord


class AppConfig(BaseModel):
    live_mode_enabled: bool = False
    openai_host_id: str | None = None
    routes: list[RouteRecord] = Field(default_factory=list)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        return AppConfig.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
