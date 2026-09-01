# -*- coding: utf-8 -*-
"""把会话叙事导出为小说文稿的三遍流水线。

第一遍 plot：把跑团记录改写为连贯小说情节，剔除回合/选项等游戏痕迹；
第二遍 style：按 ``STYLES`` 中的目标风格整体改写；
第三遍 final_polish：校对统一称谓、时态与用语，去除 AI 腔。

本模块不依赖任何模型 SDK。``model`` 为任意 ``callable(prompt) -> 文本``；
``model=None`` 时各阶段只返回 prompt，不发起网络调用（与 plot_summary 一致）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Union

Model = Callable[[str], Any]

SCHEMA = "fate-engine.novel_export"
VERSION = 1

# 目标风格预设。description 供 UI 展示；instruction 直接进入 style 阶段提示词。
STYLES: Dict[str, Dict[str, str]] = {
    "webnovel": {
        "name": "网文热血",
        "description": "节奏快、爽点密集、短段落、章末留钩子",
        "instruction": "节奏明快，短句短段；强化冲突与爽点；每章末尾留悬念钩子；避免大段抒情与景物铺陈。",
    },
    "literary": {
        "name": "出版文学",
        "description": "文气沉稳、注重心理描写与意象",
        "instruction": "语言克制有质感，允许细腻的心理与环境描写；删去口号式表达；保持人称与视角统一。",
    },
    "light": {
        "name": "轻松白话",
        "description": "口语化、幽默、易读",
        "instruction": "口语化叙述，适度幽默吐槽；句子简短直白；避免生僻词与书面腔。",
    },
    "faithful": {
        "name": "忠实整理",
        "description": "尽量保留原文，只做连贯性修整",
        "instruction": "不得增删情节与对白，只修复叙述连贯性、去除游戏痕迹、统一称谓；改动最小化。",
    },
}
DEFAULT_STYLE = "webnovel"

# 游戏痕迹：可选行动块、系统/引擎行、OOC 提示等。宁保守勿误伤正文。
_OPTION_HEADER_RE = re.compile(r"^\s*(?:【?可选行动】?|可选行动[:：]|行动选项[:：])\s*$")
_OPTION_ITEM_RE = re.compile(r"^\s*(?:\d{1,2}\s*[、.．]|[①②③④⑤⑥⑦⑧⑨⑩]|[-*•]\s).{0,80}$")
_META_LINE_RE = re.compile(
    r"^\s*(?:【(?:引擎日志|系统|OOC|旁白说明)[^】]*】.*|\((?:OOC|系统)[:：].*\)|(?:OOC|系统)[:：].*)$"
)


def _is_narrative_text(text: str) -> bool:
    return bool(text and text.strip())


def _strip_game_artifacts(text: str) -> str:
    """剔除可选行动块与元信息行；其余内容逐字保留。"""
    lines = text.split("\n")
    kept: List[str] = []
    in_options = False
    for line in lines:
        if _OPTION_HEADER_RE.match(line):
            in_options = True
            continue
        if in_options:
            if line.strip() == "":
                continue
            if _OPTION_ITEM_RE.match(line):
                continue
            # 遇到非选项行即结束选项块。
            in_options = False
        if _META_LINE_RE.match(line):
            continue
        kept.append(line)
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip("\n")


def _load_history(source: Union[str, Path, Dict, List]) -> List[Dict[str, Any]]:
    """接受存档 dict、存档 JSON 路径或 history 列表，统一返回 history。"""
    if isinstance(source, (str, Path)):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        data = source
    if isinstance(data, list):
        history = data
    elif isinstance(data, dict):
        history = data.get("history")
    else:
        raise TypeError("source 必须是存档 dict、存档路径或 history 列表")
    if not isinstance(history, list):
        raise ValueError("存档中缺少 history 列表")
    return history


def extract_narrative(source: Union[str, Path, Dict, List],
                      strip_artifacts: bool = True) -> List[Dict[str, Any]]:
    """从会话存档抽取 assistant 叙事片段。

    返回 [{idx, role, text}]，idx 为原始 history 下标，便于回溯。
    """
    history = _load_history(source)
    segments: List[Dict[str, Any]] = []
    for idx, entry in enumerate(history):
        if not isinstance(entry, dict) or entry.get("role") != "assistant":
            continue
        text = str(entry.get("content", ""))
        if strip_artifacts:
            text = _strip_game_artifacts(text)
        if _is_narrative_text(text):
            segments.append({"idx": idx, "role": "assistant", "text": text})
    return segments


def merge_narrative(segments: Iterable[Dict[str, Any]]) -> str:
    """把叙事片段合并为连续文本，片段间以空行分隔。"""
    return "\n\n".join(seg["text"].strip() for seg in segments if seg.get("text", "").strip())


def plan_chapters(text: str, target_chars: int = 2500,
                  max_chapters: Optional[int] = None) -> List[Dict[str, Any]]:
    """按段落边界确定性切章；单段超长时按句号硬切，保证不产生巨型章节。"""
    if target_chars <= 0:
        raise ValueError("target_chars 必须为正数")
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: List[str] = []
    for para in paragraphs:
        if len(para) <= target_chars:
            units.append(para.strip())
            continue
        sentences = re.split(r"(?<=[。！？!?])", para)
        buf = ""
        for sentence in sentences:
            if buf and len(buf) + len(sentence) > target_chars:
                units.append(buf.strip())
                buf = sentence
            else:
                buf += sentence
        if buf.strip():
            units.append(buf.strip())

    chapters: List[Dict[str, Any]] = []
    buf: List[str] = []
    buf_chars = 0
    cursor = 0
    start = 0

    def flush() -> None:
        nonlocal buf, buf_chars, start
        if not buf:
            return
        body = "\n\n".join(buf)
        idx = len(chapters) + 1
        chapters.append({
            "idx": idx,
            "title": "第%d章" % idx,
            "start_char": start,
            "end_char": cursor,
            "chars": len(body),
            "text": body,
        })
        buf = []
        buf_chars = 0
        start = cursor

    for unit in units:
        if buf and buf_chars + len(unit) > target_chars:
            flush()
        buf.append(unit)
        buf_chars += len(unit)
        cursor += len(unit)
    flush()

    if max_chapters and len(chapters) > max_chapters:
        raise ValueError("切分得到 %d 章，超过 max_chapters=%d；请调大 target_chars" % (len(chapters), max_chapters))
    return chapters


def build_plot_prompt(chapter_text: str, context: str = "") -> str:
    """第一遍：情节化改写，去除游戏痕迹。"""
    parts = [
        "你是小说改写者。把下面的跑团叙事记录改写为连贯的小说章节：",
        "1) 去除回合、选项、系统等游戏痕迹；2) 保留全部关键情节与对白，不新增原文没有的重大事实；",
        "3) 用第三人称连贯叙述，修复视角跳跃；4) 直接输出正文，不要解释。",
    ]
    if context:
        parts.append("\n【作品背景】\n" + context.strip())
    parts.append("\n【跑团记录】\n" + chapter_text)
    return "".join(parts)


def build_style_prompt(draft: str, style: str = DEFAULT_STYLE) -> str:
    """第二遍：按目标风格整体改写。"""
    spec = STYLES.get(style)
    if spec is None:
        raise ValueError("未知风格 %r；可选：%s" % (style, "/".join(sorted(STYLES))))
    return (
        "你是文字风格编辑。把下面章节改写为「%s」风格：%s\n"
        "要求：不改变情节走向与人物关系；直接输出正文。\n\n【待改写章节】\n%s"
        % (spec["name"], spec["instruction"], draft)
    )


def build_final_polish_prompt(draft: str) -> str:
    """第三遍：终稿润色。"""
    return (
        "你是终稿校对。对下面章节做最后一遍润色：统一人物称谓与时态，修正错别字与标点，"
        "删去重复的套话与 AI 腔（如“总的来说”“值得注意的是”）；不得改动情节。直接输出正文。\n\n"
        "【待润色章节】\n%s" % draft
    )


def iter_pipeline(chapters: Iterable[Dict[str, Any]],
                  model: Optional[Model] = None,
                  style: str = DEFAULT_STYLE,
                  context: str = "") -> Iterator[Dict[str, Any]]:
    """逐章执行 plot→style→final_polish 三遍，yield 每阶段记录。

    记录形如 {chapter_idx, chapter_title, stage, prompt, output}；
    model=None 时 output 为 None，便于调用方先审查 prompt。
    """
    if style not in STYLES:
        raise ValueError("未知风格 %r；可选：%s" % (style, "/".join(sorted(STYLES))))
    for chapter in chapters:
        text = str(chapter.get("text", ""))
        if not text.strip():
            continue
        draft = text
        builders = {
            "plot": lambda d: build_plot_prompt(d, context),
            "style": lambda d: build_style_prompt(d, style),
            "final_polish": build_final_polish_prompt,
        }
        for stage in ("plot", "style", "final_polish"):
            prompt = builders[stage](draft)
            output = model(prompt) if model is not None else None
            yield {
                "chapter_idx": chapter.get("idx"),
                "chapter_title": chapter.get("title"),
                "stage": stage,
                "prompt": prompt,
                "output": output,
            }
            if output is not None:
                draft = str(output)


def build_export_manifest(source_meta: Optional[Dict[str, Any]] = None,
                          chapters: Optional[List[Dict[str, Any]]] = None,
                          style: str = DEFAULT_STYLE,
                          model_used: bool = False) -> Dict[str, Any]:
    """生成导出清单，供 API/前端展示与存档追溯。"""
    if style not in STYLES:
        raise ValueError("未知风格 %r；可选：%s" % (style, "/".join(sorted(STYLES))))
    chapter_list = chapters or []
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": dict(source_meta or {}),
        "style": style,
        "style_name": STYLES[style]["name"],
        "passes": ["plot", "style", "final_polish"],
        "model_used": model_used,
        "chapter_count": len(chapter_list),
        "total_chars": sum(int(c.get("chars", len(str(c.get("text", ""))))) for c in chapter_list),
        "chapters": [
            {k: c.get(k) for k in ("idx", "title", "chars") if c.get(k) is not None}
            for c in chapter_list
        ],
    }


def assemble(pipeline_records: Iterable[Dict[str, Any]],
             include_titles: bool = True) -> str:
    """把流水线记录拼成终稿全文。

    每章取 stage 顺序上最后一条非空 output；无 output 时回退到该章 plot 的输入文本。
    """
    stages_order = {"plot": 0, "style": 1, "final_polish": 2}
    by_chapter: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []
    for record in pipeline_records:
        idx = record.get("chapter_idx")
        if idx not in by_chapter:
            by_chapter[idx] = {"title": record.get("chapter_title"), "best": None, "best_stage": -1}
            order.append(idx)
        output = record.get("output")
        if output is None:
            continue
        stage_rank = stages_order.get(str(record.get("stage")), -1)
        if stage_rank >= by_chapter[idx]["best_stage"]:
            by_chapter[idx]["best"] = str(output)
            by_chapter[idx]["best_stage"] = stage_rank
    parts: List[str] = []
    for idx in order:
        entry = by_chapter[idx]
        body = entry["best"]
        if body is None:
            # model=None 的纯 prompt 流水线下没有正文可拼。
            raise ValueError("章节 %s 没有任何模型输出，无法 assemble" % idx)
        if include_titles and entry.get("title"):
            parts.append(str(entry["title"]).strip() + "\n\n" + body.strip())
        else:
            parts.append(body.strip())
    return "\n\n\n".join(parts) + "\n"


def export_novel(source: Union[str, Path, Dict, List],
                 model: Optional[Model] = None,
                 style: str = DEFAULT_STYLE,
                 target_chars: int = 2500,
                 context: str = "",
                 source_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """一站式便捷入口：抽取→切章→三遍→成稿。model=None 时 text 为 None。"""
    segments = extract_narrative(source)
    if not segments:
        raise ValueError("未从存档中抽取到任何叙事片段")
    chapters = plan_chapters(merge_narrative(segments), target_chars=target_chars)
    records = list(iter_pipeline(chapters, model=model, style=style, context=context))
    text = assemble(records) if model is not None else None
    manifest = build_export_manifest(source_meta, chapters, style, model_used=model is not None)
    return {"manifest": manifest, "records": records, "text": text}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="会话叙事导出为小说文稿")
    parser.add_argument("save", help="存档 JSON 路径")
    parser.add_argument("--style", default=DEFAULT_STYLE, choices=sorted(STYLES))
    parser.add_argument("--target-chars", type=int, default=2500)
    parser.add_argument("--output", help="终稿输出路径；不提供时只打印清单")
    args = parser.parse_args()
    result = export_novel(args.save, model=None, style=args.style, target_chars=args.target_chars)
    if args.output:
        Path(args.output).write_text(
            json.dumps(result["manifest"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
