# -*- coding: utf-8 -*-
"""Bounded lexical retrieval over a prepared book."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from core.engine.book_index import load_index, search_index


def retrieve_context(book_dir: str | Path, query: str, *, limit: int = 8,
                      max_chars: int = 5000, index: Mapping[str, Any] | None = None) -> dict[str, Any]:
    root = Path(book_dir)
    idx = index or load_index(root)
    hits = search_index(idx, query, limit=max(1, int(limit)))
    leaves = {x["id"]: x for x in idx.get("leaves", [])}
    chunks = []
    used = 0
    for hit in hits:
        node = leaves.get(hit["id"], hit)
        path = root / node["source"]["path"]
        text = path.read_text(encoding="utf-8")
        chapter = next((c for c in idx.get("chapters", []) if c["chapter_no"] == node["chapter_no"]), {})
        base = chapter.get("source", {}).get("start", 0)
        start = node["source"]["start"] - base
        end = node["source"]["end"] - base
        part = text[start:end]
        if used + len(part) > max_chars: break
        chunks.append({"id": node["id"], "chapter_no": node["chapter_no"],
                       "leaf_no": node["leaf_no"], "score": hit.get("score", 0), "text": part})
        used += len(part)
    return {"query": query, "book_id": idx.get("book_id"), "chunks": chunks,
            "chars": used, "truncated": bool(hits and len(chunks) < len(hits))}


class ContextRetrievalService:
    def retrieve(self, book_dir: str | Path, query: str, **kwargs: Any) -> dict[str, Any]:
        return retrieve_context(book_dir, query, **kwargs)


__all__ = ["retrieve_context", "ContextRetrievalService"]
