"""Shared pytest fixtures for rag-mcp tests."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from rag_mcp.config import Config
from rag_mcp.fts_store import FTSStore
from rag_mcp.indexer import Indexer
from rag_mcp.vector_store import VectorStore


@pytest.fixture()
def tmp_knowledge(tmp_path: Path) -> Path:
    """Create a temp knowledge directory with sample .md files."""
    kdir = tmp_path / "knowledge"

    # Category: python
    py_dir = kdir / "python"
    py_dir.mkdir(parents=True)
    (py_dir / "basics.md").write_text(
        textwrap.dedent("""\
            ---
            title: Python Basics
            date: "2024-01-15"
            author: tester
            categories: [python]
            status: approved
            approved_by: AB
            ---

            # Python Basics

            Python is a high-level programming language known for its readability.

            ## Variables

            Variables in Python are dynamically typed. You can assign values without
            declaring types explicitly.

            ```python
            x = 42
            name = "hello"
            ```

            ## Functions

            Functions are defined with the `def` keyword.

            ```python
            def greet(name):
                return f"Hello, {name}!"
            ```
        """),
        encoding="utf-8",
    )

    (py_dir / "advanced.md").write_text(
        textwrap.dedent("""\
            ---
            title: Advanced Python
            date: "2024-02-10"
            author: tester
            categories: [python, engineering]
            status: draft
            ---

            # Advanced Python

            ## Decorators

            Decorators modify the behavior of functions.

            ```python
            def timer(func):
                def wrapper(*args, **kwargs):
                    import time
                    start = time.time()
                    result = func(*args, **kwargs)
                    print(f"Took {time.time() - start:.2f}s")
                    return result
                return wrapper
            ```

            ## Context Managers

            Use `with` statements for resource management.
        """),
        encoding="utf-8",
    )

    # Category: devops
    devops_dir = kdir / "devops"
    devops_dir.mkdir(parents=True)
    (devops_dir / "docker.md").write_text(
        textwrap.dedent("""\
            ---
            title: Docker Fundamentals
            date: "2024-03-01"
            author: ops
            categories: [devops]
            status: in_review
            in_review_by: CD
            ---

            # Docker Fundamentals

            Docker containers package applications with their dependencies.

            ## Dockerfile

            ```dockerfile
            FROM python:3.12
            COPY . /app
            RUN pip install -r requirements.txt
            CMD ["python", "app.py"]
            ```

            ## Docker Compose

            Compose defines multi-container applications.
        """),
        encoding="utf-8",
    )

    # File without categories (should be skipped)
    (kdir / "no-cats.md").write_text(
        textwrap.dedent("""\
            ---
            title: No Categories
            ---

            This file has no categories field and should be skipped.
        """),
        encoding="utf-8",
    )

    return kdir


@pytest.fixture()
def config(tmp_path: Path, tmp_knowledge: Path) -> Config:
    """Config pointing at temp directories."""
    return Config(
        knowledge_dir=tmp_knowledge,
        data_dir=tmp_path / "data",
    )


@pytest.fixture()
def fts(config: Config) -> FTSStore:
    """Fresh FTS store."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    store = FTSStore(config.sqlite_path)
    yield store
    store.close()


@pytest.fixture()
def vectors(config: Config) -> VectorStore:
    """Fresh vector store."""
    store = VectorStore(config.lance_path)
    yield store
    store.close()


@pytest.fixture()
def indexer(config: Config, fts: FTSStore, vectors: VectorStore) -> Indexer:
    """Indexer wired to temp stores."""
    return Indexer(config, fts, vectors)


@pytest.fixture()
def indexed(indexer: Indexer) -> dict:
    """Run full sync and return stats. Use when tests need a populated index."""
    return indexer.run_full_sync()
