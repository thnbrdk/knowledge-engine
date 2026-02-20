"""Tests for config loading."""

from pathlib import Path

from rag_mcp.config import Config, WebConfig, load_config


def test_default_config():
    cfg = Config()
    assert cfg.knowledge_dir == Path("./knowledge")
    assert cfg.data_dir == Path("./data")
    assert cfg.sqlite_path == Path("./data/rag.db")
    assert cfg.lance_path == Path("./data/lancedb")


def test_default_web_config():
    cfg = Config()
    assert cfg.web.enabled is True
    assert cfg.web.host == "127.0.0.1"
    assert cfg.web.port == 8765
    assert cfg.web.admin_token is None


def test_load_config_missing_file(tmp_path: Path):
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg.knowledge_dir == Path("./knowledge")


def test_load_config_empty_file(tmp_path: Path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("", encoding="utf-8")
    cfg = load_config(yaml_path)
    assert cfg.knowledge_dir == Path("./knowledge")


def test_load_config_custom_values(tmp_path: Path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "knowledge_dir: my_knowledge\ndata_dir: my_data\n"
        "web:\n  port: 9000\n  admin_token: secret\n",
        encoding="utf-8",
    )
    cfg = load_config(yaml_path)
    assert cfg.knowledge_dir == (tmp_path / "my_knowledge").resolve()
    assert cfg.data_dir == (tmp_path / "my_data").resolve()
    assert cfg.web.port == 9000
    assert cfg.web.admin_token == "secret"
