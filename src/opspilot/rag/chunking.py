"""Markdown-aware chunking: split on headings, then pack paragraphs up to a size budget."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHARS = 1000
OVERLAP_PARAGRAPHS = 1


@dataclass(frozen=True)
class Chunk:
    doc_name: str
    title: str
    section: str | None
    index: int
    content: str


def _sections(markdown: str) -> tuple[str, list[tuple[str | None, str]]]:
    """Return (document title, [(section heading, body)])."""
    lines = markdown.strip().splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else "Untitled"
    body = "\n".join(lines[1:] if lines and lines[0].startswith("#") else lines)
    parts = re.split(r"^(#{2,3} .+)$", body, flags=re.MULTILINE)
    sections: list[tuple[str | None, str]] = []
    preamble = parts[0].strip()
    for i in range(1, len(parts), 2):
        heading = parts[i].lstrip("# ").strip()
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if text:
            sections.append((heading, text))
    if preamble:
        # metadata-only preambles (owner/version lines) are merged into the first section
        # instead of becoming a low-information chunk of their own
        if sections:
            sections[0] = (sections[0][0], preamble + "\n\n" + sections[0][1])
        else:
            sections.append((None, preamble))
    return title, sections


def chunk_markdown(doc_name: str, markdown: str, max_chars: int = MAX_CHARS) -> list[Chunk]:
    """Chunk one document. Each chunk is prefixed with "title > section" for retrieval context."""
    title, sections = _sections(markdown)
    chunks: list[Chunk] = []
    for heading, text in sections:
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        buf: list[str] = []
        for p in paras:
            if buf and sum(len(x) for x in buf) + len(p) > max_chars:
                chunks.append(_make(doc_name, title, heading, len(chunks), buf))
                buf = buf[-OVERLAP_PARAGRAPHS:] if OVERLAP_PARAGRAPHS else []
            buf.append(p)
        if buf:
            chunks.append(_make(doc_name, title, heading, len(chunks), buf))
    return chunks


def _make(doc: str, title: str, heading: str | None, idx: int, paras: list[str]) -> Chunk:
    header = f"{title} > {heading}" if heading else title
    return Chunk(doc, title, heading, idx, f"{header}\n\n" + "\n\n".join(paras))
