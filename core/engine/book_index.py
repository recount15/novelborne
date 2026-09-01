# -*- coding: utf-8 -*-
"""Deterministic hierarchical index for local books.

The index is deliberately extractive: it stores metadata and source spans, while
leaf text remains in the chapter files.  This keeps manifests safe to inspect and
makes rebuilding resumable when only a few chapters changed.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

VERSION = 1
DEFAULT_LEAF_CHARS = 1200


def checksum(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def deterministic_id(kind: str, book_id: str, *parts: Any) -> str:
    payload = "|".join([str(kind), str(book_id), *(str(p) for p in parts)])
    return f"{kind}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _terms(text: str) -> set[str]:
    text = text.lower()
    found = set(re.findall(r"[a-z0-9_]+", text))
    chinese = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    found.update(chinese)
    found.update("".join(chinese[i:i + 2]) for i in range(len(chinese) - 1))
    return {x for x in found if x}


def _chapter_data(book_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    index_file = book_dir / "chapter_index.json"
    data = json.loads(index_file.read_text(encoding="utf-8")) if index_file.is_file() else {}
    chapters = data.get("chapters", []) if isinstance(data, Mapping) else []
    if not chapters:
        for path in sorted((book_dir / "chapters").glob("*.txt")):
            chapters.append({"idx": int(path.stem), "title": path.stem})
    return str(data.get("book_id") or book_dir.name), list(chapters)


def _leaf_spans(text: str, size: int) -> list[tuple[int, int]]:
    spans = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind("。", start, end))
            if cut > start + size // 2:
                end = cut + 1
        spans.append((start, end))
        start = end
    return spans


def build_book_index(book_dir: str | Path, *, leaf_chars: int = DEFAULT_LEAF_CHARS,
                     arc_size: int = 10, resume: bool = True) -> dict[str, Any]:
    """Build/update ``book_index.json`` and ``book_manifest.json``.

    IDs depend only on book id and structural coordinates.  Existing unchanged
    chapters are reused, so interrupted/repeated builds are safe and deterministic.
    """
    root = Path(book_dir)
    book_id, chapters = _chapter_data(root)
    old: dict[str, Any] = {}
    old_path = root / "book_index.json"
    if resume and old_path.is_file():
        try: old = json.loads(old_path.read_text(encoding="utf-8"))
        except (OSError, ValueError): old = {}
    old_by_idx = {int(x.get("chapter_no", x.get("idx", 0))): x for x in old.get("chapters", [])}
    old_leaf_chars = int(old.get("config", {}).get("leaf_chars", -1))
    reused = 0
    changed = 0
    leaves: list[dict[str, Any]] = []
    chapter_nodes: list[dict[str, Any]] = []
    arcs: dict[int, list[str]] = defaultdict(list)
    for pos, meta in enumerate(chapters):
        idx = int(meta.get("idx") or pos + 1)
        path = root / "chapters" / f"{idx:04d}.txt"
        text = path.read_text(encoding="utf-8")
        digest = checksum(text)
        prior = old_by_idx.get(idx)
        if prior and prior.get("source", {}).get("checksum") == digest and old_leaf_chars == int(leaf_chars):
            reused += 1
        else:
            changed += 1
        chapter_id = deterministic_id("chapter", book_id, idx)
        chapter = {"id": chapter_id, "kind": "chapter", "chapter_no": idx,
                   "title": str(meta.get("title") or f"第{idx}章"),
                   "source": {"path": str(path.relative_to(root)), "start": int(meta.get("start_char", 0)),
                              "end": int(meta.get("end_char", len(text))), "checksum": digest},
                   "chars": len(text), "children": []}
        for leaf_no, (a, b) in enumerate(_leaf_spans(text, max(1, int(leaf_chars)))):
            leaf_id = deterministic_id("leaf", book_id, idx, leaf_no)
            leaf = {"id": leaf_id, "kind": "leaf", "chapter_no": idx, "leaf_no": leaf_no,
                    "parent_id": chapter_id, "source": {"path": str(path.relative_to(root)),
                    "start": int(meta.get("start_char", 0)) + a, "end": int(meta.get("start_char", 0)) + b,
                    "checksum": checksum(text[a:b])}, "chars": b - a}
            leaves.append(leaf); chapter["children"].append(leaf_id)
        chapter_nodes.append(chapter)
        arcs[pos // max(1, int(arc_size))].append(chapter_id)
    arc_nodes = []
    for arc_no, child_ids in sorted(arcs.items()):
        arc_nodes.append({"id": deterministic_id("arc", book_id, arc_no), "kind": "arc",
                          "arc_no": arc_no, "children": child_ids})
    root_id = deterministic_id("root", book_id)
    index = {"version": VERSION, "book_id": book_id, "root_id": root_id,
             "root": {"id": root_id, "kind": "root", "children": [a["id"] for a in arc_nodes]},
             "arcs": arc_nodes, "chapters": chapter_nodes, "leaves": leaves,
             "stats": {"chapters": len(chapter_nodes), "arcs": len(arc_nodes), "leaves": len(leaves),
                       "chars": sum(x["chars"] for x in chapter_nodes)}}
    index["config"] = {"leaf_chars": int(leaf_chars), "arc_size": int(arc_size), "reused_chapters": reused,
                        "changed_chapters": changed, "resumed": bool(old)}
    postings: dict[str, list[str]] = defaultdict(list)
    for leaf in leaves:
        path = root / leaf["source"]["path"]
        text = path.read_text(encoding="utf-8")[leaf["source"]["start"] - int(next(c for c in chapters if int(c.get("idx", 0)) == leaf["chapter_no"]).get("start_char", 0)):leaf["source"]["end"] - int(next(c for c in chapters if int(c.get("idx", 0)) == leaf["chapter_no"]).get("start_char", 0))]
        for term in _terms(text):
            if leaf["id"] not in postings[term]: postings[term].append(leaf["id"])
    index["postings"] = {k: v for k, v in sorted(postings.items())}
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("book_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"version": VERSION, "book_id": book_id, "index_checksum": checksum(json.dumps(index, ensure_ascii=False, sort_keys=True)),
                "source": [{"chapter_no": c["chapter_no"], "path": c["source"]["path"], "chars": c["chars"], "checksum": c["source"]["checksum"]} for c in chapter_nodes],
                "stats": index["stats"]}
    root.joinpath("book_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return index


def load_index(book_dir: str | Path) -> dict[str, Any]:
    return json.loads((Path(book_dir) / "book_index.json").read_text(encoding="utf-8"))


def search_index(index: Mapping[str, Any], query: str, limit: int = 10) -> list[dict[str, Any]]:
    terms = _terms(query)
    ids: dict[str, int] = defaultdict(int)
    postings = index.get("postings", {})
    for term in terms:
        for node_id in postings.get(term, []): ids[node_id] += 1
    leaves = {x["id"]: x for x in index.get("leaves", [])}
    return [{**leaves[node_id], "score": score} for node_id, score in sorted(ids.items(), key=lambda p: (-p[1], p[0]))[:max(0, limit)] if node_id in leaves]

__all__ = ["VERSION", "checksum", "deterministic_id", "build_book_index", "load_index", "search_index"]
