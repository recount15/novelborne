# -*- coding: utf-8 -*-
"""两步蒸馏的第一步：从章节目录生成低成本剧情大概。

本模块只负责选取少量章节和组织调用输入，不依赖任何模型 SDK。调用方可把
``model`` 设为任意 callable；其返回值可以是字符串或 JSON 可序列化对象。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

Model = Callable[[str], Any]


def _chapter_dir(path: Union[str, Path]) -> Path:
    p = Path(path)
    if p.is_file():
        if p.name == "chapter_index.json":
            return p.parent / "chapters"
        raise ValueError("章节目录参数必须是目录或 chapter_index.json")
    if (p / "chapters").is_dir():
        return p / "chapters"
    return p


def _read_chapters(path: Union[str, Path]) -> List[Dict[str, Any]]:
    directory = _chapter_dir(path)
    files = sorted(directory.glob("*.txt"))
    chapters: List[Dict[str, Any]] = []
    for file in files:
        try:
            idx = int(file.stem)
        except ValueError:
            continue
        chapters.append({"idx": idx, "title": "", "text": file.read_text(encoding="utf-8")})
    if not chapters:
        raise ValueError("未找到章节正文文件")
    index_path = directory.parent / "chapter_index.json"
    if index_path.is_file():
        try:
            indexed = json.loads(index_path.read_text(encoding="utf-8"))
            indexed = indexed.get("chapters", indexed) if isinstance(indexed, dict) else indexed
            by_idx = {item.get("idx"): item for item in indexed if isinstance(item, dict)}
            for chapter in chapters:
                chapter.update({k: by_idx[chapter["idx"]][k] for k in ("title", "chapter_no") if chapter["idx"] in by_idx and k in by_idx[chapter["idx"]]})
        except (OSError, ValueError, TypeError):
            pass
    return chapters


def select_summary_chapters(chapters: Iterable[Dict[str, Any]], max_samples: int = 3) -> List[Dict[str, Any]]:
    """选择首章、末章；书足够长时再均匀抽样。短书只取首尾，避免「抽样」变成全书。"""
    items = list(chapters)
    if not items:
        return []
    wanted = {0, len(items) - 1}
    if max_samples > 0 and len(items) > 6:
        for n in range(1, max_samples + 1):
            wanted.add(round(n * (len(items) - 1) / (max_samples + 1)))
    return [items[i] for i in sorted(wanted) if 0 <= i < len(items)]


def build_summary_prompt(selected: Iterable[Dict[str, Any]], max_chars_per_chapter: int = 4000) -> str:
    parts = [
        "你是剧情摘要器。仅依据给出的章节，输出不超过800字的剧情大概；不要补写原文没有的事实。",
        "建议以 JSON 返回 {\"genre\":\"\",\"premise\":\"\",\"major_threads\":[],\"tone\":\"\"}。",
    ]
    for chapter in selected:
        text = str(chapter.get("text", ""))[:max_chars_per_chapter]
        label = chapter.get("title") or "第%s章" % chapter.get("idx", "?")
        parts.append("\n【%s】\n%s" % (label, text))
    return "".join(parts)


def generate_plot_summary(
    chapters_path: Union[str, Path],
    model: Optional[Model] = None,
    max_samples: int = 3,
    max_chars_per_chapter: int = 4000,
) -> Dict[str, Any]:
    """生成剧情大概数据结构；model 为空时返回 prompt 与章节样本，不发起网络调用。"""
    chapters = _read_chapters(chapters_path)
    selected = select_summary_chapters(chapters, max_samples)
    prompt = build_summary_prompt(selected, max_chars_per_chapter)
    result: Any = None
    if model is not None:
        result = model(prompt)
    return {
        "version": 1,
        "chapter_count": len(chapters),
        "selected_chapters": [{k: c.get(k) for k in ("idx", "chapter_no", "title")} for c in selected],
        "prompt": prompt,
        "summary": result,
    }


# 便于调用方按“蒸馏”语义导入。
distill_plot_summary = generate_plot_summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="生成低成本剧情大概")
    parser.add_argument("chapters_path")
    args = parser.parse_args()
    print(json.dumps(generate_plot_summary(args.chapters_path), ensure_ascii=False, indent=2))
