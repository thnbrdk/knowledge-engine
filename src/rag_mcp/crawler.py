"""Recursive file crawler for knowledge discovery."""

from __future__ import annotations

from pathlib import Path


def discover_files(knowledge_dir: Path) -> list[Path]:
    """Walk knowledge_dir recursively and return all .md and .mdx files."""
    if not knowledge_dir.is_dir():
        return []
    return sorted(list(knowledge_dir.rglob("*.md")) + list(knowledge_dir.rglob("*.mdx")))


def discover_categories(knowledge_dir: Path) -> dict[str, list[Path]]:
    """Legacy: discover files grouped by folder path. Used for backward compat."""
    categories: dict[str, list[Path]] = {}

    if not knowledge_dir.is_dir():
        return categories

    for dirpath in sorted(knowledge_dir.rglob("*")):
        if not dirpath.is_dir():
            continue
        md_files = sorted(dirpath.glob("*.md"))
        if not md_files:
            continue
        rel = dirpath.relative_to(knowledge_dir).as_posix()
        categories[rel] = md_files

    root_md = sorted(knowledge_dir.glob("*.md"))
    if root_md:
        categories["."] = root_md

    return categories


def get_top_level_category(category: str) -> str:
    """Extract the top-level category from a nested path."""
    if category == ".":
        return "_root"
    return category.split("/")[0]
