"""Tests for the markdown parser."""

import textwrap
from datetime import date as _date
from pathlib import Path

from rag_mcp.markdown_parser import parse_markdown


def test_parse_basic_document(tmp_knowledge: Path):
    doc = parse_markdown(tmp_knowledge / "python" / "basics.md")
    assert doc.meta.title == "Python Basics"
    assert doc.meta.category == "python"
    assert doc.meta.categories == ["python"]
    assert doc.meta.author == "tester"
    assert doc.meta.date == "2024-01-15"
    assert doc.meta.status == "approved"
    assert doc.meta.approved_by == "AB"
    assert len(doc.chunks) > 0


def test_parse_multi_category(tmp_knowledge: Path):
    doc = parse_markdown(tmp_knowledge / "python" / "advanced.md")
    assert doc.meta.categories == ["python", "engineering"]
    assert doc.meta.category == "python"  # primary is first


def test_parse_no_categories(tmp_knowledge: Path):
    doc = parse_markdown(tmp_knowledge / "no-cats.md")
    assert doc.meta.categories == []
    assert doc.meta.category is None
    assert doc.meta.author == "Local"
    assert doc.meta.status == "draft"
    assert doc.meta.date == _date.today().isoformat()


def test_parse_no_frontmatter_defaults(tmp_path: Path):
    f = tmp_path / "plain.md"
    f.write_text("# Plain Heading\n\nContent without frontmatter.", encoding="utf-8")
    doc = parse_markdown(f)
    assert doc.meta.title == "Plain Heading"
    assert doc.meta.category is None
    assert doc.meta.author == "Local"
    assert doc.meta.status == "draft"
    assert doc.meta.date == _date.today().isoformat()


def test_chunks_have_heading_paths(tmp_knowledge: Path):
    doc = parse_markdown(tmp_knowledge / "python" / "basics.md")
    headings = [" > ".join(c.heading_path) for c in doc.chunks if c.heading_path]
    assert any("Variables" in h for h in headings)
    assert any("Functions" in h for h in headings)


def test_chunk_ids_unique(tmp_knowledge: Path):
    doc = parse_markdown(tmp_knowledge / "python" / "basics.md")
    ids = [c.chunk_id for c in doc.chunks]
    assert len(ids) == len(set(ids))


def test_title_fallback_to_h1(tmp_path: Path):
    f = tmp_path / "test.md"
    f.write_text("---\ncategories: [test]\n---\n\n# My Title\n\nSome content.\n", encoding="utf-8")
    doc = parse_markdown(f)
    assert doc.meta.title == "My Title"


def test_title_fallback_to_filename(tmp_path: Path):
    f = tmp_path / "my-doc.md"
    f.write_text("---\ncategories: [test]\n---\n\nNo heading here.\n", encoding="utf-8")
    doc = parse_markdown(f)
    assert doc.meta.title == "my-doc"


def test_single_chunk_no_headings(tmp_path: Path):
    f = tmp_path / "flat.md"
    f.write_text(
        textwrap.dedent("""\
            ---
            title: Flat Doc
            categories: [test]
            ---

            Just plain text with no headings at all.
        """),
        encoding="utf-8",
    )
    doc = parse_markdown(f)
    assert len(doc.chunks) == 1
    assert doc.chunks[0].heading_path == []


def test_categories_as_csv_string(tmp_path: Path):
    f = tmp_path / "csv.md"
    f.write_text(
        "---\ntitle: CSV Cats\ncategories: python, devops\n---\n\nContent.\n",
        encoding="utf-8",
    )
    doc = parse_markdown(f)
    assert doc.meta.categories == ["python", "devops"]
