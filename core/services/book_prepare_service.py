# -*- coding: utf-8 -*-
"""Prepare a book for bounded retrieval and opening-time consumption."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from core.engine.book_index import build_book_index, checksum


def _extractive(text: str, limit: int = 600) -> str:
    text = " ".join(text.split())
    if len(text) <= limit: return text
    parts = [p for p in text.replace("！", "。\n").replace("？", "。\n").split("。") if p.strip()]
    result = "。".join(parts[:3]).strip()
    return (result + "。" if result else text[:limit])[:limit]


def prepare_book(book_dir: str | Path, *, leaf_chars: int = 1200, arc_size: int = 10,
                 opening_chapters: int = 3, model: Any = None, resume: bool = True) -> dict[str, Any]:
    """Build indexes and write a small ``opening_ready.json`` package.

    ``model`` is accepted for compatibility but never receives the full book;
    preparation has a deterministic extractive path and does not require a model.
    """
    root = Path(book_dir)
    index = build_book_index(root, leaf_chars=leaf_chars, arc_size=arc_size, resume=resume)
    chapters = index.get("chapters", [])[:max(0, int(opening_chapters))]
    opening = []
    for chapter in chapters:
        path = root / chapter["source"]["path"]
        text = path.read_text(encoding="utf-8")
        opening.append({"chapter_no": chapter["chapter_no"], "title": chapter["title"],
                        "summary": _extractive(text), "chars": len(text),
                        "checksum": chapter["source"]["checksum"]})
    package = {"version": 1, "book_id": index["book_id"], "index_id": index["root_id"],
               "chapters": opening, "stats": index["stats"], "extractive": True}
    (root / "opening_ready.json").write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return package


class BookPrepareService:
    def prepare(self, book_dir: str | Path, **kwargs: Any) -> dict[str, Any]:
        return prepare_book(book_dir, **kwargs)


__all__ = ["prepare_book", "BookPrepareService"]
