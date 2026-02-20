"""Tests for the file crawler."""

from pathlib import Path

from rag_mcp.crawler import discover_files, get_top_level_category


def test_discover_files(tmp_knowledge: Path):
    files = discover_files(tmp_knowledge)
    names = {f.name for f in files}
    assert "basics.md" in names
    assert "advanced.md" in names
    assert "docker.md" in names
    assert "no-cats.md" in names
    assert len(files) == 4


def test_discover_files_empty(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert discover_files(empty) == []


def test_discover_files_nonexistent(tmp_path: Path):
    assert discover_files(tmp_path / "nope") == []


def test_get_top_level_category():
    assert get_top_level_category("python") == "python"
    assert get_top_level_category("python/flask") == "python"
    assert get_top_level_category("a/b/c") == "a"
    assert get_top_level_category(".") == "_root"
