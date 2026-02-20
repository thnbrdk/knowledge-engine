"""Markdown parsing: frontmatter extraction and heading-based chunking."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

import frontmatter


@dataclass
class DocumentMeta:
    file_path: str
    category: str | None  # primary category (first in categories list)
    title: str
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    frontmatter: dict = field(default_factory=dict)
    date: str = field(default_factory=lambda: _date.today().isoformat())
    author: str = "Local"
    approved_by: str = ""
    in_review_by: str = ""
    status: str = "draft"
    revision: int = 1

    @property
    def quality(self) -> str:
        return self.status

    @quality.setter
    def quality(self, value: str) -> None:
        self.status = value

    @property
    def verified_by(self) -> str:
        return self.approved_by

    @property
    def flagged_by(self) -> str:
        return self.in_review_by


@dataclass
class Chunk:
    chunk_id: str
    content: str
    heading_path: list[str]
    file_path: str
    category: str
    title: str


@dataclass
class ParsedDocument:
    meta: DocumentMeta
    content: str  # full body (without frontmatter)
    chunks: list[Chunk]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def parse_markdown(file_path: Path) -> ParsedDocument:
    """Parse a markdown file: extract frontmatter and split into heading-based chunks.

    Categories MUST be present in the frontmatter 'categories' field.
    If missing, categories will be empty (caller should warn).
    """
    raw = file_path.read_text(encoding="utf-8")
    try:
        post = frontmatter.loads(raw)
    except Exception:
        # Frontmatter is malformed — treat entire file as body with no metadata
        post = frontmatter.Post(raw, metadata={})

    fm = dict(post.metadata)
    body: str = post.content

    # Determine title: frontmatter > first H1 > filename
    title = fm.get("title", "")
    if not title:
        m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = m.group(1).strip() if m else file_path.stem

    # Extract categories from frontmatter — no fallback
    cats = fm.get("categories", [])
    if isinstance(cats, str):
        cats = [c.strip() for c in cats.split(",") if c.strip()]

    primary_category = cats[0] if cats else None

    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    fp_str = file_path.as_posix()

    date_value = str(fm.get("date", "")).strip() if fm.get("date") else ""
    if not date_value:
        date_value = _date.today().isoformat()

    author_value = str(fm.get("author", "")).strip() if fm.get("author") else ""
    if not author_value:
        author_value = "Local"

    meta = DocumentMeta(
        file_path=fp_str,
        category=primary_category,
        title=title,
        categories=cats,
        tags=tags,
        frontmatter=fm,
        date=date_value,
        author=author_value,
        approved_by=str(fm.get("approved_by", fm.get("verified_by", ""))),
        in_review_by=str(fm.get("in_review_by", fm.get("flagged_by", ""))),
        status=str(fm.get("status", fm.get("quality", "draft"))) if (fm.get("status") or fm.get("quality")) else "draft",
        revision=int(fm.get("revision", 1)) if str(fm.get("revision", "")).strip().isdigit() else 1,
    )

    chunks = _split_by_headings(body, fp_str, primary_category, title)

    return ParsedDocument(meta=meta, content=body, chunks=chunks)


def _split_by_headings(
    body: str, file_path: str, category: str | None, doc_title: str
) -> list[Chunk]:
    """Split markdown body by headings, preserving heading hierarchy context."""
    headings: list[tuple[int, str, int]] = []
    for m in _HEADING_RE.finditer(body):
        level = len(m.group(1))
        text = m.group(2).strip()
        headings.append((level, text, m.start()))

    if not headings:
        # No headings — whole document is one chunk
        chunk_id = f"{file_path}::_root"
        return [
            Chunk(
                chunk_id=chunk_id,
                content=body.strip(),
                heading_path=[],
                file_path=file_path,
                category=category or "",
                title=doc_title,
            )
        ]

    chunks: list[Chunk] = []

    # Content before the first heading (preamble)
    preamble = body[: headings[0][2]].strip()
    if preamble:
        chunks.append(
            Chunk(
                chunk_id=f"{file_path}::_preamble",
                content=preamble,
                heading_path=[],
                file_path=file_path,
                category=category or "",
                title=doc_title,
            )
        )

    # Build heading hierarchy tracker
    heading_stack: list[tuple[int, str]] = []

    for i, (level, heading_text, start) in enumerate(headings):
        # Determine end of this section
        end = headings[i + 1][2] if i + 1 < len(headings) else len(body)
        section_content = body[start:end].strip()

        # Update heading stack: pop everything at same or deeper level
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, heading_text))

        heading_path = [h[1] for h in heading_stack]
        chunk_id = f"{file_path}::{'::'.join(heading_path)}"

        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                content=section_content,
                heading_path=heading_path,
                file_path=file_path,
                category=category or "",
                title=doc_title,
            )
        )

    return chunks
