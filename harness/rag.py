"""Small local retrieval layer for project and Blender 4.5 knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True, slots=True)
class ContextChunk:
    source: Path
    heading: str
    text: str
    score: int


class LocalRAG:
    def __init__(self, roots: tuple[Path, ...]):
        self.roots = roots
        self._chunks: list[ContextChunk] | None = None

    def retrieve(self, query: str, *, limit: int = 6) -> list[ContextChunk]:
        chunks = self._load_chunks()
        terms = _terms(query)
        scored: list[ContextChunk] = []
        for chunk in chunks:
            haystack = f"{chunk.heading}\n{chunk.text}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append(ContextChunk(chunk.source, chunk.heading, chunk.text, score))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def format_context(self, query: str, *, limit: int = 6, max_chars: int = 12000) -> str:
        parts: list[str] = []
        budget = max_chars
        for chunk in self.retrieve(query, limit=limit):
            text = chunk.text.strip()
            if not text:
                continue
            entry = f"Source: {chunk.source}\nHeading: {chunk.heading}\n{text}\n"
            if len(entry) > budget:
                entry = entry[:budget]
            parts.append(entry)
            budget -= len(entry)
            if budget <= 0:
                break
        return "\n---\n".join(parts)

    def _load_chunks(self) -> list[ContextChunk]:
        if self._chunks is not None:
            return self._chunks
        chunks: list[ContextChunk] = []
        for root in self.roots:
            if root.is_dir():
                paths = sorted(root.rglob("*.md"))
            elif root.exists():
                paths = [root]
            else:
                paths = []
            for path in paths:
                chunks.extend(_chunk_markdown(path))
        self._chunks = chunks
        return chunks


def _chunk_markdown(path: Path) -> list[ContextChunk]:
    text = path.read_text(encoding="utf-8")
    chunks: list[ContextChunk] = []
    heading = path.name
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if buffer:
                chunks.append(ContextChunk(path, heading, "\n".join(buffer).strip(), 0))
            heading = line.strip("# ").strip() or path.name
            buffer = [line]
        else:
            buffer.append(line)
    if buffer:
        chunks.append(ContextChunk(path, heading, "\n".join(buffer).strip(), 0))
    return chunks


def _terms(query: str) -> list[str]:
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_\.]{2,}|[\u4e00-\u9fff]{2,}", query.lower())
    stop = {"the", "and", "for", "with", "scene", "object", "animation"}
    return [term for term in raw if term not in stop]

