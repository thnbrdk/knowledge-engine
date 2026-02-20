"""YAML config loader with sensible defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    admin_token: str | None = None


@dataclass
class Config:
    knowledge_dir: Path = field(default_factory=lambda: Path("./knowledge"))
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    web: WebConfig = field(default_factory=WebConfig)

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "rag.db"

    @property
    def lance_path(self) -> Path:
        return self.data_dir / "lancedb"


def load_config(config_path: Path | None = None) -> Config:
    """Load config from YAML file. Falls back to defaults if missing or empty."""
    if config_path is None:
        config_path = Path("rag-config.yaml")

    if not config_path.exists():
        return Config()

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw:
        return Config()

    base_dir = config_path.parent

    cfg = Config()

    if "knowledge_dir" in raw:
        cfg.knowledge_dir = (base_dir / raw["knowledge_dir"]).resolve()
    else:
        cfg.knowledge_dir = (base_dir / cfg.knowledge_dir).resolve()

    if "data_dir" in raw:
        cfg.data_dir = (base_dir / raw["data_dir"]).resolve()
    else:
        cfg.data_dir = (base_dir / cfg.data_dir).resolve()

    if "web" in raw and isinstance(raw["web"], dict):
        web_raw = raw["web"]
        cfg.web = WebConfig(
            enabled=web_raw.get("enabled", True),
            host=web_raw.get("host", "127.0.0.1"),
            port=web_raw.get("port", 8765),
            admin_token=web_raw.get("admin_token"),
        )

    return cfg
