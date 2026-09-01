# -*- coding: utf-8 -*-
"""开局蒸馏与角色入库流水线（重构 M1，docs/REFACTOR_PLAN.md §4）。

章切分完成即并行发射（开局优先级给满）：

1. **波次一（run_parallel）**：plot 采样卷×3（首/中/末，复用 plot_summary 抽样）
   ∥ 角色抽取卷（多章样本直取，不等 plot）∥ 第 1..chapters_ahead 章锚点卷
   （第 1 章两遍法的第一遍）∥ 长章（>3500 字）~3000 字/块的块级蒸馏卷。
2. **波次二（run_parallel）**：plot 合并卷（→ {genre, premise, major_threads,
   tone}）∥ 长章合并卷（块级 JSON → 九字段锚点）∥ 第 1 章两遍法的验证卷
   （草稿 + 原文 → 修正后的九字段锚点）。两个波次的作业全部是叶子作业，
   绝不嵌套 run_parallel（共享线程池只有 HARD_LIMIT 个 worker，嵌套等待有
   死锁风险）。
3. 作品档案卷（依赖 plot 合并）：13 字段（对齐 work_distiller.normalize_archive
   的 12 键 + premise），anchors 1–6 非空、genre 必填；复用 work_distiller 的
   normalize_archive / render_entry / upsert_work_entry 落 work_library.md
   （library_path 可注入以便测试）。
4. 角色卡入库强化：上限 12 张、排序 主角>反派>女主>其他；relationship_vector
   防编造交叉校验（键必须 ∈ 同批角色名 ∪ 锚点 characters 字段，否则剔除该键）；
   质量门复用 character_designer.quality_assessment（flat 卡带错误清单重填一次，
   仍 flat 丢弃并记录）；入库经注入的 save_characters_fn（默认包装
   character_library.save_card，单卡失败跳过）。
5. 开局零阻塞：第 1 章锚点两遍法后用 anchor_distiller.validate_anchor 严校验
   （quotes 逐字命中）；模型路径全失败时用 anchor_distiller.
   synthesize_anchor_from_text 确定性兜底落盘并标 origin=fallback。

纯机制层：模型全部注入（str->str callable，通常由中台门面用
engine.parallel.budget_model 以 PRIORITY_OPENING 包装后传入），不 import
fate_engine/app；每个子调用失败都有中文降级路径，整体绝不抛错中断
（报告 dict 带 errors 列表）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from core import prompts
from core.engine import anchor_distiller, character_designer, parallel
from core.engine import plot_summary, structured, work_distiller

Model = Callable[[str], Any]

#: 角色入库通道：卡列表 → 入库结果列表（默认包装 character_library.save_card）。
CharacterSaver = Callable[[List[Mapping[str, Any]]], List[Dict[str, Any]]]

# ---------------------------------------------------------------- 限额与常量

#: 超过该长度的章走 map-reduce 切块蒸馏（与 anchor_distiller 单卷 3500 上限对齐）。
LONG_CHAPTER_THRESHOLD = 3500
#: 长章切块的目标块大小（字符）。
BLOCK_TARGET_CHARS = 3000
#: 单章最多蒸馏的块数（防超长章把开局并发打爆；合并卷以这些块为准）。
MAX_BLOCKS_PER_CHAPTER = 8
#: 开局锚点窗口上限（章）。
MAX_CHAPTERS_AHEAD = 6
#: 角色卡入库上限（张）。
MAX_CHARACTER_CARDS = 12
#: 单份 plot 采样卷的正文预算（字符）。
PLOT_SAMPLE_BUDGET = 3500
#: 两遍法验证卷的原文预算（字符）。
VERIFY_TEXT_BUDGET = 8000
#: 单张卡 relationship_vector 的最大条数。
MAX_RELATION_ENTRIES = 8

#: 卡片排序优先级：主角 > 反派 > 女主 > 其他（含男主/配角）。
_POSITION_PRIORITY = {"主角": 0, "反派": 1, "女主": 2}
_OTHER_PRIORITY = 3

# ---------------------------------------------------------------- 字段规格（FieldSpec）

#: plot 采样卷输出（opening_plot_sample.md）。
PLOT_SAMPLE_SPECS: Tuple[structured.FieldSpec, ...] = (
    structured.FieldSpec("main_events", "list", min_items=1, max_items=8, item_max_len=120,
                         hint="本卷实际发生的主要事件，按时间顺序"),
    structured.FieldSpec("characters", "list", min_items=1, max_items=12, item_max_len=40,
                         hint="本卷实际出场的人物名"),
    structured.FieldSpec("tone", "str", min_len=2, max_len=60, hint="叙事基调与题材气质"),
    structured.FieldSpec("threads", "list", min_items=1, max_items=6, item_max_len=60,
                         hint="埋下或推进的主线线索"),
)

#: plot 合并卷输出（opening_plot_merge.md）。
PLOT_MERGE_SPECS: Tuple[structured.FieldSpec, ...] = (
    structured.FieldSpec("genre", "str", min_len=2, max_len=60, hint="题材与子类型"),
    structured.FieldSpec("premise", "str", min_len=10, max_len=300, hint="全书核心前提与冲突"),
    structured.FieldSpec("major_threads", "list", min_items=3, max_items=6, item_max_len=80,
                         hint="贯穿全书的主线，按重要性排序"),
    structured.FieldSpec("tone", "str", min_len=2, max_len=60, hint="全书叙事基调"),
)

#: 长章块级蒸馏输出（代码装配提示词；chapter 字段留给合并卷补）。
BLOCK_SPECS: Tuple[structured.FieldSpec, ...] = (
    structured.FieldSpec("summary", "str", min_len=10, max_len=300, hint="本块剧情概括"),
    structured.FieldSpec("events", "list", min_items=1, max_items=8, item_max_len=120,
                         hint="本块实际发生的事件"),
    structured.FieldSpec("characters", "list", required=False, max_items=12, item_max_len=60,
                         default=[], hint="本块出场人物名"),
    structured.FieldSpec("quotes", "list", required=False, max_items=6, item_max_len=200,
                         default=[], hint="本块原文逐字引文"),
    structured.FieldSpec("world", "str", required=False, max_len=200, default="",
                         hint="本块世界观信息"),
    structured.FieldSpec("foreshadowing", "list", required=False, max_items=6, item_max_len=120,
                         default=[], hint="本块伏笔"),
    structured.FieldSpec("ripple", "str", required=False, max_len=200, default="",
                         hint="本块涟漪/余波"),
)

#: 作品档案卷输出（opening_archive.md）：13 字段 = normalize_archive 的 12 键 + premise。
ARCHIVE_SPECS: Tuple[structured.FieldSpec, ...] = (
    structured.FieldSpec("genre", "str", min_len=2, max_len=60, hint="题材（含子类型）"),
    structured.FieldSpec("premise", "str", required=False, max_len=300, default="",
                         hint="全书前提概括"),
    structured.FieldSpec("tier", "str", required=False, max_len=40, default="",
                         hint="T1-T9 与 WS 系数"),
    structured.FieldSpec("language_style", "str", required=False, max_len=120, default="",
                         hint="语体指纹"),
    structured.FieldSpec("pacing", "str", required=False, max_len=120, default="",
                         hint="节奏档案"),
    structured.FieldSpec("anchors", "list", min_items=1, max_items=6, item_max_len=80,
                         hint="主线大事件（必发生锚点），按时间顺序"),
    structured.FieldSpec("world_will", "str", required=False, max_len=80, default="",
                         hint="叙事钩子/世界意志方向"),
    structured.FieldSpec("golden_finger_fit", "str", required=False, max_len=120, default="",
                         hint="适配金手指类型"),
    structured.FieldSpec("entry_point", "str", required=False, max_len=80, default="",
                         hint="建议开局落点"),
    structured.FieldSpec("power_system", "str", required=False, max_len=80, default="",
                         hint="力量体系"),
    structured.FieldSpec("factions", "str", required=False, max_len=100, default="",
                         hint="势力图谱"),
    structured.FieldSpec("timeline", "str", required=False, max_len=100, default="",
                         hint="时间线锚点"),
    structured.FieldSpec("causal_rules", "str", required=False, max_len=100, default="",
                         hint="因果律/硬设定"),
)

#: 角色抽取卷的顶层规格（characters 为对象数组，逐卡约束在 CHARACTER_CARD_SPECS）。
CHARACTERS_OUTER_SPECS: Tuple[structured.FieldSpec, ...] = (
    structured.FieldSpec("characters", "any", required=True, hint="角色卡数组，最多 12 张"),
)

#: 单张角色卡的逐卡约束（防编造/枚举/白名单口径，也用于重填错误清单）。
CHARACTER_CARD_SPECS: Tuple[structured.FieldSpec, ...] = (
    structured.FieldSpec("name", "str", min_len=1, max_len=40),
    structured.FieldSpec("original_position", "str", enum=("主角", "男主", "女主", "配角", "反派"), required=False, default="配角"),
    structured.FieldSpec("gender", "str", enum=("male", "female", "unknown"), required=False, default="unknown"),
    structured.FieldSpec("slot_keys", "dict", required=False, default={}),
    structured.FieldSpec("desire", "str", min_len=2, max_len=120, required=False, default=""),
    structured.FieldSpec("fear", "str", min_len=2, max_len=90, required=False, default=""),
    structured.FieldSpec("voice", "str", min_len=2, max_len=60, required=False, default=""),
    structured.FieldSpec("background", "str", min_len=2, max_len=200, required=False, default=""),
    structured.FieldSpec("relationship_vector", "dict", required=False, default={}),
    structured.FieldSpec("evidence_chapter", "int", required=False, default=1),
)


# ---------------------------------------------------------------- 小工具


def _zh(exc: BaseException, fallback: str = "调用失败") -> str:
    """把异常转成中文可读错误（剥英文类名前缀；无中文时用统一说明）。"""
    message = re.sub(r"^[A-Za-z_][A-Za-z0-9_.]*\s*(?:Error|Exception)?\s*[:：]\s*",
                     "", str(exc or "")).strip()
    if not message or not re.search(r"[\u4e00-\u9fff]", message):
        return fallback
    return "%s：%s" % (fallback, message[:200])


def _emit_progress(progress: Optional[Callable[[dict], None]], stage: str, **detail: Any) -> None:
    """上报阶段状态；回调自身故障绝不影响流水线。"""
    if progress is None:
        return
    try:
        progress(dict(stage=stage, **detail))
    except Exception:  # noqa: BLE001 上报失败静默忽略
        pass


def _anchor_dir(book_dir: str | Path) -> Path:
    """锚点输出目录（与 anchor_distiller._default_output 同口径）。"""
    path = Path(book_dir)
    return path / "anchors" if (path / "chapters").is_dir() else path.parent / "anchors"


def _write_anchor(anchor_dir: Path, number: int, anchor: Mapping[str, Any]) -> Path:
    """原子写单章锚点（tmp+replace，与 anchor_distiller 落盘方式一致）。"""
    anchor_dir.mkdir(parents=True, exist_ok=True)
    target = anchor_dir / ("%04d.json" % int(number))
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(anchor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


def _split_blocks(text: str, size: int = BLOCK_TARGET_CHARS) -> List[str]:
    """把长章切成 ~size 字符的块：优先在段末/句末断开，避免拦腰斩句。"""
    blocks: List[str] = []
    start, total = 0, len(text)
    while start < total:
        end = min(start + size, total)
        if end < total:
            cut = max(text.rfind("\n", start, end), text.rfind("。", start, end))
            if cut > start + size // 2:
                end = cut + 1
        blocks.append(text[start:end])
        start = end
    return blocks


def _safe_structured(model: Model, prompt: str, specs: Sequence[structured.FieldSpec],
                     attempts: int = 2) -> Tuple[Optional[dict], List[str]]:
    """structured_call 的中文兜底包装：传输层连续失败也只返回 (None, 错误清单)。"""
    try:
        data, meta = structured.structured_call(model, prompt, specs, attempts)
    except Exception as exc:  # noqa: BLE001 传输层全线失败 → 降级为错误清单
        return None, [_zh(exc, "模型调用失败")]
    errors = list((meta or {}).get("errors") or [])
    if data is None and not errors:
        errors = ["结构化输出未通过字段校验"]
    return data, errors


def _fallback_anchor(number: int, full_text: str) -> Dict[str, Any]:
    """确定性兜底锚点：原文摘录式合成 + 严校验（失败抛中文 ValueError）。"""
    try:
        return anchor_distiller.validate_anchor(
            anchor_distiller.synthesize_anchor_from_text(full_text, number), full_text, number)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("原文摘录锚点合成失败：%s" % _zh(exc, "无法合成")) from exc


# ---------------------------------------------------------------- 锚点子卷


def _anchor_prompt(number: int, source: str) -> str:
    """单章锚点蒸馏卷（两遍法第一遍/普通章直通；口径对齐 anchor_distiller）。"""
    return (
        "【任务】单章锚点蒸馏。\n"
        "请仅依据以下单章原文输出严格 JSON，必须包含且只能包含九字段：%s。"
        "quotes 必须是原文逐字连续片段，不能改写；"
        "events/characters/foreshadowing/quotes 四个数组字段必须至少 1 项，不得为空数组，"
        "数组内不要出现空字符串项，单项不超过 200 字；"
        "title/summary/world/ripple 必须为非空字符串。每个字段都要具体、非空。\n"
        "chapter=%d\n原文：\n%s" % (", ".join(anchor_distiller.ANCHOR_FIELDS), number, source)
    )


def _validated_anchor(raw: Any, number: int, full_text: str) -> Dict[str, Any]:
    """把模型输出的锚点修形并严校验（引文先对齐原文再逐字命中）。"""
    parsed = anchor_distiller.sanitize_anchor(raw, number)
    if isinstance(parsed.get("quotes"), list):
        aligned = anchor_distiller._align_quotes(
            [q for q in parsed["quotes"] if isinstance(q, str)], full_text)
        if aligned:
            parsed["quotes"] = aligned
    return anchor_distiller.validate_anchor(parsed, full_text, number)


def _distill_anchor_once(model: Model, number: int, full_text: str,
                         attempts: int = 2) -> Dict[str, Any]:
    """常规章锚点单遍蒸馏（≤3500 字全文入卷）；失败抛最后一个异常。"""
    prompt = _anchor_prompt(number, full_text[:LONG_CHAPTER_THRESHOLD])
    last: Optional[BaseException] = None
    for _ in range(max(1, int(attempts))):
        try:
            return _validated_anchor(
                anchor_distiller._parse_model_output(model(prompt)), number, full_text)
        except Exception as exc:  # noqa: BLE001 单遍失败重试，全部失败才上抛
            last = exc
    raise last or ValueError("单章锚点蒸馏失败")


def _block_prompt(index: int, total: int, block_text: str) -> str:
    """长章块级蒸馏卷：块级中间 JSON（无 chapter 字段，由合并卷融合）。"""
    return "\n".join((
        "【任务】长章切块蒸馏（第 %d/%d 块）。只依据本块原文输出块级中间结果，"
        "不需要 chapter 字段。" % (index, total),
        structured.spec_prompt(BLOCK_SPECS),
        "- quotes 必须是本块原文的逐字连续片段，不能改写。",
        "\n【块原文】\n%s" % block_text,
    ))


def _distill_block(model: Model, index: int, total: int, block_text: str) -> Dict[str, Any]:
    """单块蒸馏：structured_call + 块级字段校验；失败抛中文 ValueError。"""
    data, errors = _safe_structured(model, _block_prompt(index, total, block_text), BLOCK_SPECS)
    if data is None:
        raise ValueError("第 %d/%d 块蒸馏失败：%s" % (index, total, "；".join(errors) or "未知错误"))
    return data


def _merge_anchor(model: Model, number: int,
                  blocks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """长章合并卷：多块 JSON → 九字段锚点（严校验，quotes 逐字命中块内原文）。

    块结果携带 ``_block``（块序）与 ``_source``（块原文）辅助键：前者排序、
    后者用于引文逐字命中校验；两者都不进合并卷提示词，避免提示词膨胀。
    """
    ordered = sorted(blocks, key=lambda b: int(b.get("_block") or 0))
    payload = [{k: v for k, v in dict(block).items() if not k.startswith("_")}
               for block in ordered]
    sources = "\n".join(str(block.get("_source") or "") for block in ordered)
    prompt = prompts.render(
        "opening_anchor_merge.md",
        CHAPTER=int(number),
        BLOCKS=json.dumps(payload, ensure_ascii=False),
    )
    last: Optional[BaseException] = None
    for _ in range(2):
        try:
            return _validated_anchor(
                anchor_distiller._parse_model_output(model(prompt)), number, sources)
        except Exception as exc:  # noqa: BLE001 合并失败重试一次，仍失败上抛走兜底
            last = exc
    raise last or ValueError("长章锚点合并失败")


def _verify_anchor(model: Model, number: int, full_text: str,
                   draft: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """两遍法第二遍：验证卷（草稿 + 原文）→ 修正后的九字段锚点；失败返回 None。"""
    prompt = prompts.render(
        "opening_anchor_verify.md",
        CHAPTER=int(number),
        DRAFT=json.dumps(dict(draft), ensure_ascii=False),
        ORIGINAL=str(full_text)[:VERIFY_TEXT_BUDGET],
    )
    try:
        return _validated_anchor(
            anchor_distiller._parse_model_output(model(prompt)), number, full_text)
    except Exception:  # noqa: BLE001 验证卷失败保留第一遍结果
        return None


# ---------------------------------------------------------------- 波次一作业（叶子作业，绝不嵌套 run_parallel）


def _plot_sample_job(model: Model, work_title: str, idx: int, label: str,
                     text: str) -> Dict[str, Any]:
    """plot 采样卷作业：单卷样本 → {main_events, characters, tone, threads}。"""
    prompt = prompts.render(
        "opening_plot_sample.md",
        WORK_TITLE=work_title,
        CHAPTER_LABEL=label,
        SAMPLE_TEXT=str(text)[:PLOT_SAMPLE_BUDGET],
    )
    data, errors = _safe_structured(model, prompt, PLOT_SAMPLE_SPECS)
    if data is None:
        raise ValueError("剧情采样卷（%s）失败：%s" % (label, "；".join(errors) or "未知错误"))
    return {"kind": "plot_sample", "idx": idx, "label": label, "data": data}


def _characters_extract_job(model: Model, work_title: str,
                            samples_text: str) -> Dict[str, Any]:
    """角色抽取卷作业：多章样本 → 角色卡原始数组（不等 plot）。"""
    prompt = prompts.render(
        "opening_characters.md",
        WORK_TITLE=work_title,
        SAMPLES=samples_text,
        SLOT_VOCAB="、".join(character_designer.SLOT_KEY_VOCAB),
    )
    data, errors = _safe_structured(model, prompt, CHARACTERS_OUTER_SPECS)
    cards = (data or {}).get("characters") if isinstance(data, Mapping) else None
    if not isinstance(cards, list) or not cards:
        raise ValueError("角色抽取卷失败：%s" % ("；".join(errors) or "未返回角色列表"))
    return {"kind": "characters", "cards": cards}


def _anchor_pass_job(model: Model, number: int, full_text: str) -> Dict[str, Any]:
    """第 1 章两遍法第一遍作业：只产草稿不落盘（第二遍在波次二核验后写）。"""
    draft = _distill_anchor_once(model, number, full_text)
    return {"kind": "anchor_pass", "chapter": number, "draft": draft}


def _anchor_finalize_job(anchor_dir: Path, model: Model, number: int,
                         full_text: str) -> Dict[str, Any]:
    """普通章（非第 1 章）锚点作业：蒸馏 → 严校验落盘；失败原文摘录兜底。"""
    try:
        anchor = _distill_anchor_once(model, number, full_text)
        origin = "distilled"
    except Exception as exc:  # noqa: BLE001 保底比空缺好
        try:
            anchor = _fallback_anchor(number, full_text)
            origin = "fallback"
        except Exception:  # noqa: BLE001 兜底也失败才真正失败
            return {"kind": "anchor", "chapter": number, "status": "failed",
                    "origin": "", "error": _zh(exc, "锚点蒸馏失败")}
    _write_anchor(anchor_dir, number, anchor)
    return {"kind": "anchor", "chapter": number, "status": "done", "origin": origin}


def _block_job(model: Model, index: int, total: int, block_text: str,
               chapter: int) -> Dict[str, Any]:
    """长章块级作业：块文本 → 块级 JSON（携 _source 供合并卷引文命中校验）。"""
    data = _distill_block(model, index, total, block_text)
    data["_block"] = index
    data["_source"] = block_text
    return {"kind": "block", "chapter": chapter, "block": data}


# ---------------------------------------------------------------- 波次二作业


def _plot_merge_job(model: Model, work_title: str,
                    samples: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """plot 合并卷作业：多采样 → {genre, premise, major_threads, tone}。"""
    if not samples:
        raise ValueError("剧情采样全部失败，无法合并")
    prompt = prompts.render(
        "opening_plot_merge.md",
        WORK_TITLE=work_title,
        SAMPLES=json.dumps(list(samples), ensure_ascii=False),
    )
    data, errors = _safe_structured(model, prompt, PLOT_MERGE_SPECS)
    if data is None:
        raise ValueError("剧情合并卷失败：%s" % ("；".join(errors) or "未知错误"))
    return {"kind": "plot_merge", "data": data}


def _plot_from_samples(samples: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """降级路径：合并卷失败时从采样结果机械合成剧情大概（纯代码）。"""
    if not samples:
        return None
    events: List[str] = []
    threads: List[str] = []
    tones: List[str] = []
    for sample in samples:
        data = sample.get("data") if isinstance(sample, Mapping) else None
        data = data if isinstance(data, Mapping) else {}
        events.extend(str(item).strip() for item in (data.get("main_events") or [])
                      if str(item).strip())
        threads.extend(str(item).strip() for item in (data.get("threads") or [])
                       if str(item).strip())
        tone = str(data.get("tone") or "").strip()
        if tone:
            tones.append(tone)
    if not events and not threads:
        return None
    tone = (tones[0] if tones else "综合")[:60]
    return {
        "genre": tone,
        "premise": ("；".join(events) or "；".join(threads))[:300],
        "major_threads": (threads or events)[:6],
        "tone": tone,
    }


def _first_chapter_verify_job(anchor_dir: Path, model: Model, number: int,
                              full_text: str,
                              draft: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """第 1 章两遍法第二遍作业：草稿（或兜底）→ 验证卷核验修正 → 落盘。

    开局零阻塞：第一遍失败时先落确定性兜底草稿再尝试验证卷——验证卷成功
    则用修正版覆盖（origin=distilled），失败保留兜底（origin=fallback）。
    """
    origin = "distilled"
    if draft is None:
        try:
            draft = _fallback_anchor(number, full_text)
            origin = "fallback"
        except Exception as exc:  # noqa: BLE001 原文无完整句子，兜底也无法合成
            return {"kind": "anchor", "chapter": number, "status": "failed",
                    "origin": "", "error": _zh(exc, "首章锚点蒸馏与兜底均失败")}
    final = dict(draft)
    status = "done"
    verified = _verify_anchor(model, number, full_text, draft)
    if verified is not None:
        final = verified
        origin = "distilled"
        status = "verified"
    _write_anchor(anchor_dir, number, final)
    entry: Dict[str, Any] = {"kind": "anchor", "chapter": number,
                             "status": status, "origin": origin}
    return entry


def _long_chapter_job(anchor_dir: Path, model: Model, number: int,
                      full_text: str, blocks: Sequence[Mapping[str, Any]],
                      first: bool = False) -> Dict[str, Any]:
    """长章作业（波次二）：块级 JSON → 合并卷融合 → 严校验落盘；失败兜底。

    ``first=True``（第 1 章是长章）时合并后再走两遍法验证卷。
    """
    try:
        if not blocks:
            raise ValueError("分块蒸馏全部失败")
        anchor = _merge_anchor(model, number, blocks)
        origin, status = "distilled", "merged"
    except Exception as exc:  # noqa: BLE001 合并失败 → 原文摘录兜底
        try:
            anchor = _fallback_anchor(number, full_text)
            origin, status = "fallback", "done"
        except Exception:  # noqa: BLE001
            return {"kind": "anchor", "chapter": number, "status": "failed",
                    "origin": "", "error": _zh(exc, "长章锚点合并失败")}
    if first:
        verified = _verify_anchor(model, number, full_text, anchor)
        if verified is not None:
            anchor = verified
            origin, status = "distilled", "verified"
    _write_anchor(anchor_dir, number, anchor)
    return {"kind": "anchor", "chapter": number, "status": status, "origin": origin}


# ---------------------------------------------------------------- 角色卡加工


def _normalize_opening_card(raw: Any, work_title: str) -> Optional[Dict[str, Any]]:
    """单张角色卡归一：复用 work_distiller.normalize_character，并保留质量门
    需要的扩展字段（voice_samples/unacceptable_actions/knowledge_scope 等）。"""
    card = work_distiller.normalize_character(raw, work_title)
    if card is None:
        return None
    if isinstance(raw, Mapping):
        limits = (("voice_samples", 4), ("unacceptable_actions", 3),
                  ("knowledge_scope", 6), ("references", 6))
        for key, limit in limits:
            value = raw.get(key)
            if isinstance(value, list):
                items = [str(item).strip() for item in value if str(item).strip()]
                if items:
                    card[key] = items[:limit]
            elif isinstance(value, str) and value.strip():
                card[key] = [value.strip()[:200]]
        ability = raw.get("ability_limits")
        if isinstance(ability, str) and ability.strip():
            card["ability_limits"] = ability.strip()[:200]
        try:
            card["evidence_chapter"] = max(1, int(raw.get("evidence_chapter") or 1))
        except (TypeError, ValueError):
            card["evidence_chapter"] = 1
    return card


def _assess_card(card: Mapping[str, Any]) -> Dict[str, Any]:
    """质量门评估（复用 character_designer.quality_assessment）。"""
    try:
        result = character_designer.quality_assessment(
            {"name": str(card.get("name") or ""), "work": str(card.get("work") or "")},
            [], {}, dict(card))
        if isinstance(result, Mapping):
            return dict(result)
    except Exception:  # noqa: BLE001 评估故障按 flat 处理
        pass
    return {"level": "flat", "label": "扁平", "score": 0, "missing": ["质量评估失败"]}


def _card_gate(card: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """单卡门禁：FieldSpec 逐卡约束 + 质量分评估。

    返回 (质量结果, 硬约束错误清单, 重填反馈清单)：
    - 硬约束错误 = CHARACTER_CARD_SPECS 校验失败（枚举/白名单/必填），与
      flat 档位一起决定重填与丢弃；
    - 重填反馈 = 硬约束错误 + quality_assessment 缺失字段（含灵魂层提示，
      只作重填卷的错误反馈，不单独构成丢弃理由——playable 卡允许缺判例）。
    """
    quality = _assess_card(card)
    hard_errors = structured.validate(CHARACTER_CARD_SPECS, card)
    feedback = hard_errors + [str(item) for item in (quality.get("missing") or [])]
    return quality, hard_errors, feedback


def _refill_card(model: Model, card: Mapping[str, Any],
                 missing: Sequence[str]) -> Optional[Dict[str, Any]]:
    """flat 卡定向重填一次：错误清单附回提示词，只重输出这一张卡。"""
    prompt = "\n".join((
        "【任务】角色卡重填（开局质量门）。",
        "下面这张角色卡质量不达标（扁平空洞），请依据现有信息补全后重新输出"
        "该角色的完整 JSON 卡。只输出一个 JSON 对象，不要解释与围栏。",
        "【作品】《%s》" % str(card.get("work") or "").strip("《》"),
        "【现有卡内容】\n%s" % json.dumps(dict(card), ensure_ascii=False),
        "【错误清单（必须逐条补齐）】",
        "\n".join("- %s" % item for item in (missing or ["字段空洞"])),
        "",
        "【字段要求】name/gender(male|female|unknown)/"
        "original_position(主角|男主|女主|配角|反派)/archetype/desire/fear/voice/"
        "voice_samples(2-4 条台词)/background/unacceptable_actions(1-3 条)/"
        "relationship_vector(对象→关系)/slot_keys(四栏)/evidence_chapter。",
        "slot_keys 每栏 1-2 个，只能从以下词表选：%s；不适配的栏位填 [\"通用\"]。"
        % "、".join(character_designer.SLOT_KEY_VOCAB),
    ))
    try:
        data = structured.extract_json(model(prompt))
    except Exception:  # noqa: BLE001 重填失败返回 None（调用方丢弃该卡）
        return None
    return data if isinstance(data, Mapping) else None


def _name_known(key: str, batch_names: set, anchor_entries: Sequence[str]) -> bool:
    """防编造判定：键 ∈ 同批角色名，或出现在锚点 characters 字段条目中。"""
    if key in batch_names:
        return True
    return any(key == entry or key in entry for entry in anchor_entries if entry)


def _sanitize_relationships(card: Dict[str, Any], batch_names: set,
                            anchor_entries: Sequence[str]) -> List[str]:
    """relationship_vector 交叉校验：编造的关系对象直接剔除，返回被剔除的键。"""
    removed: List[str] = []
    relation = card.get("relationship_vector")
    if isinstance(relation, Mapping):
        clean: Dict[str, str] = {}
        for raw_key, raw_value in list(relation.items())[:MAX_RELATION_ENTRIES]:
            key = str(raw_key).strip()
            if not key:
                continue
            if _name_known(key, batch_names, anchor_entries):
                clean[key[:40]] = str(raw_value or "").strip()[:120]
            else:
                removed.append(key)
        card["relationship_vector"] = clean
    return removed


def _default_save_characters(cards: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """默认入库：逐张 character_library.save_card 双写；单卡失败跳过。"""
    from core.engine import character_library  # 惰性导入：仅默认入库路径需要

    saved: List[Dict[str, Any]] = []
    for card in cards:
        try:
            result = character_library.save_card(card)
            record = result.get("record") or {}
            saved.append({"id": record.get("id"), "name": record.get("name")})
        except Exception as exc:  # noqa: BLE001 单卡失败不拖垮整批，但返回失败记录
            saved.append({"name": str(card.get("name") or ""), "saved": False,
                          "error": _zh(exc, "角色保存失败")})
    return saved


def _process_characters(model: Model, work_title: str, raw_cards: Sequence[Any],
                        anchor_entries: Sequence[str]
                        ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """角色卡入库强化：归一 → 质量门（flat 重填一次仍不过丢弃）→ 排序截断
    → 防编造剔除。返回 (待入库卡列表, 报告条目列表)。"""
    entries: List[Dict[str, Any]] = []
    kept: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for raw in raw_cards or []:
        name = str((raw or {}).get("name") or "").strip() if isinstance(raw, Mapping) else ""
        card = _normalize_opening_card(raw, work_title)
        if card is None:
            entries.append({"name": name, "quality": None, "saved": False,
                            "removed_relation_keys": [],
                            "dropped_reason": "缺少角色名，无法入库"})
            continue
        quality, hard_errors, feedback = _card_gate(card)
        if quality.get("level") == "flat" or hard_errors:
            refill = _refill_card(model, card, feedback)
            if refill is not None:
                improved = _normalize_opening_card(refill, work_title)
                if improved is not None and improved.get("name"):
                    card = improved
                    quality, hard_errors, feedback = _card_gate(card)
            if quality.get("level") == "flat" or hard_errors:
                entries.append({
                    "name": str(card.get("name") or name),
                    "quality": quality, "saved": False, "removed_relation_keys": [],
                    "dropped_reason": "质量门未过（flat 或字段约束不达标，重填一次仍不过）：%s"
                                      % "；".join(hard_errors or ["字段空洞"])[:200],
                })
                continue
        kept.append((card, quality))
    # 排序：主角 > 反派 > 女主 > 其他；同档按质量分降序；上限 12 张。
    kept.sort(key=lambda pair: (
        _POSITION_PRIORITY.get(str(pair[0].get("original_position") or ""), _OTHER_PRIORITY),
        -int(pair[1].get("score") or 0)))
    kept = kept[:MAX_CHARACTER_CARDS]
    # 防编造交叉校验（在截断后的同批名单上做，锚点人物名兜底）。
    batch_names = {str(card.get("name") or "").strip() for card, _ in kept}
    final_cards: List[Dict[str, Any]] = []
    for card, quality in kept:
        removed = _sanitize_relationships(card, batch_names, anchor_entries)
        final_cards.append(card)
        entries.append({
            "name": str(card.get("name") or ""),
            "original_position": card.get("original_position"),
            "gender": card.get("gender"),
            "slot_keys": card.get("slot_keys"),
            "evidence_chapter": card.get("evidence_chapter"),
            "quality": {"score": quality.get("score"), "level": quality.get("level"),
                        "label": quality.get("label")},
            "removed_relation_keys": removed,
            "saved": False,
        })
    return final_cards, entries


# ---------------------------------------------------------------- 作品档案卷


def _ensure_library(path: Path) -> None:
    """作品库文件缺失时补一个最小骨架（追加式 upsert 需要可读文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text("# 作品库\n\n## 第六章 权限与扩展\n", encoding="utf-8")


def _archive_step(model: Model, work_title: str, plot: Optional[Mapping[str, Any]],
                  samples_text: str, cards: List[Mapping[str, Any]],
                  library_path: Optional[str | Path]
                  ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """作品档案卷 + work_library.md 同名 upsert；返回 (档案摘要, 错误清单)。"""
    errors: List[str] = []
    prompt = prompts.render(
        "opening_archive.md",
        WORK_TITLE=work_title,
        PLOT_SUMMARY=json.dumps(dict(plot), ensure_ascii=False) if plot else "",
        SAMPLES=samples_text,
    )
    data, call_errors = _safe_structured(model, prompt, ARCHIVE_SPECS)
    errors.extend(call_errors)
    origin = "distilled"
    if data is not None:
        archive = work_distiller.normalize_archive(data)
    elif plot:
        # 降级路径：从 plot 合并结果机械合成 13 字段档案（genre 必有、锚点取主线）。
        archive = work_distiller.normalize_archive({
            "genre": plot.get("genre"),
            "premise": plot.get("premise"),
            "anchors": list(plot.get("major_threads") or []),
            "tier": "T4（系数 WS 5.0）",
        })
        origin = "fallback"
        errors.append("作品档案卷未通过校验，已用剧情合并结果降级合成")
    else:
        errors.append("作品档案卷失败且无剧情大概可降级，未写入作品库")
        return None, errors
    if not archive.get("genre") and not archive.get("anchors"):
        errors.append("作品档案为空（缺 genre 与 anchors），未写入作品库")
        return None, errors
    try:
        path = Path(library_path) if library_path else work_distiller._work_library_path()
        _ensure_library(path)
        entry_md = work_distiller.render_entry("W??", work_title, archive, list(cards))
        write = work_distiller.upsert_work_entry(entry_md, work_title, path)
    except Exception as exc:  # noqa: BLE001 写库失败不影响其余产出
        errors.append("作品库写入失败：%s" % _zh(exc, "无法写入作品库"))
        return None, errors
    return {
        "work_id": write.get("work_id"),
        "action": write.get("action"),
        "origin": origin,
        "anchors_count": len(archive.get("anchors") or []),
        "archive": archive,
    }, errors


# ---------------------------------------------------------------- 主流水线


def _plot_volumes(chapters: Sequence[Mapping[str, Any]]) -> List[Tuple[int, str, str]]:
    """plot 采样卷×3：复用 plot_summary 抽样取首/末，中间卷取抽样第二卷或书中位。"""
    selected = plot_summary.select_summary_chapters(list(chapters), max_samples=3)
    if not selected:
        return []
    items = list(chapters)
    first = selected[0]
    last = selected[-1] if len(selected) > 1 else first
    middle = selected[1] if len(selected) >= 3 else (items[len(items) // 2] if items else first)

    def _volume(chapter: Mapping[str, Any]) -> Tuple[int, str, str]:
        idx = int(chapter.get("idx") or 0)
        label = str(chapter.get("title") or "").strip() or ("第%d章" % idx)
        return idx, label, str(chapter.get("text") or "")

    return [_volume(first), _volume(middle), _volume(last)]


def _anchor_name_entries(anchor_reports: Sequence[Mapping[str, Any]],
                         anchor_dir: Path) -> List[str]:
    """从开局窗口锚点（报告条目对应的落盘文件）收集 characters 字段条目。

    防编造交叉校验的名字池来源：同批角色名之外的锚点出场人物。
    """
    names: List[str] = []
    for entry in anchor_reports or []:
        number = int(entry.get("chapter") or 0)
        if not number:
            continue
        try:
            data = json.loads(
                (anchor_dir / ("%04d.json" % number)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in (data.get("characters") or []):
            text = str(item or "").strip()
            if text:
                names.append(text)
    return names


def run_opening_pipeline(book_dir: str | Path,
                         work_title: str,
                         model: Model,
                         *,
                         chapters_ahead: int = 3,
                         progress: Optional[Callable[[dict], None]] = None,
                         library_path: Optional[str | Path] = None,
                         save_characters_fn: Optional[CharacterSaver] = None,
                         ) -> Dict[str, Any]:
    """开局蒸馏与角色入库流水线主入口（详见模块 docstring）。

    ``model`` 为 str->str callable（中台门面用 budget_model 包装开局优先级后
    传入；本函数内部包装幂等，直接调用也安全）。返回可 JSON 化的报告 dict：
    plot / plot_samples / work_entry / characters（含质量分与入库标记）/
    anchors（各章 status 与 origin）/ timings / errors。任何子调用失败都走
    中文降级路径，整体绝不抛错中断。
    """
    started = time.perf_counter()
    work_title = (work_title or "").strip().strip("《》") or "未命名作品"
    report: Dict[str, Any] = {
        "ok": False,
        "work_title": work_title,
        "chapter_count": 0,
        "chapters_ahead": 0,
        "plot": None,
        "plot_degraded": False,
        "plot_samples": [],
        "selected_chapters": [],
        "work_entry": None,
        "characters": [],
        "character_saved_count": 0,
        "anchors": [],
        "timings": {},
        "errors": [],
    }
    timings: Dict[str, Any] = report["timings"]
    errors: List[str] = report["errors"]
    anchors_field: List[Dict[str, Any]] = report["anchors"]
    try:
        model = parallel.budget_model(model, default_priority=parallel.PRIORITY_OPENING)
    except Exception:  # noqa: BLE001 包装失败用原模型继续
        pass

    def _finish() -> Dict[str, Any]:
        timings["total"] = round(time.perf_counter() - started, 3)
        # ok 口径：开局窗口首章锚点可用（两遍法/兜底均可，开局零阻塞红线）。
        report["ok"] = bool(anchors_field) and anchors_field[0].get("status") != "failed"
        _emit_progress(progress, "done", ok=report["ok"], errors=len(errors))
        return report

    # ---- 章节读取 ----
    try:
        chapters = plot_summary._read_chapters(book_dir)
    except Exception as exc:  # noqa: BLE001
        errors.append("章节读取失败：%s" % _zh(exc, "未找到章节正文"))
        return _finish()
    report["chapter_count"] = len(chapters)
    ahead = max(1, min(int(chapters_ahead or 3), MAX_CHAPTERS_AHEAD, len(chapters)))
    report["chapters_ahead"] = ahead
    texts = {int(c.get("idx") or 0): str(c.get("text") or "") for c in chapters}
    window = [int(c.get("idx") or 0) for c in chapters[:ahead]]
    anchor_dir = _anchor_dir(book_dir)
    _emit_progress(progress, "start", chapters=len(chapters), ahead=ahead)

    # ---- 波次一：plot 采样×3 ∥ 角色抽取 ∥ 锚点卷 ∥ 长章块级卷 ----
    wave1_started = time.perf_counter()
    volumes = _plot_volumes(chapters)
    try:
        samples_text = work_distiller.build_samples_text(book_dir)
    except Exception as exc:  # noqa: BLE001
        samples_text = ""
        errors.append("章节样本拼接失败：%s" % _zh(exc, "无法读取章节样本"))

    jobs: List[Callable[[], Dict[str, Any]]] = []
    for idx, label, text in volumes:
        jobs.append(lambda idx=idx, label=label, text=text:
                    _plot_sample_job(model, work_title, idx, label, text))
    if samples_text:
        jobs.append(lambda: _characters_extract_job(model, work_title, samples_text))
    long_blocks: Dict[int, List[str]] = {}
    first_number = window[0] if window else 0
    for position, number in enumerate(window):
        text = texts.get(number, "")
        if not text:
            errors.append("第 %d 章正文缺失，跳过锚点蒸馏" % number)
            anchors_field.append({"chapter": number, "status": "failed",
                                  "origin": "", "error": "章节正文缺失"})
            continue
        if len(text) > LONG_CHAPTER_THRESHOLD:
            blocks = _split_blocks(text)[:MAX_BLOCKS_PER_CHAPTER]
            long_blocks[number] = blocks
            total = len(blocks)
            for index, block in enumerate(blocks, 1):
                jobs.append(lambda number=number, index=index, total=total, block=block:
                            _block_job(model, index, total, block, number))
        elif position == 0:
            jobs.append(lambda number=number, text=text:
                        _anchor_pass_job(model, number, text))
        else:
            jobs.append(lambda number=number, text=text:
                        _anchor_finalize_job(anchor_dir, model, number, text))

    plot_samples: List[Dict[str, Any]] = []
    report["plot_samples"] = plot_samples
    raw_cards: Optional[List[Any]] = None
    first_draft: Dict[int, Dict[str, Any]] = {}
    block_results: Dict[int, List[Dict[str, Any]]] = {}
    if jobs:
        jobs_done = 0
        for result in parallel.run_parallel(jobs, priority=parallel.PRIORITY_OPENING):
            jobs_done += 1
            if not result.ok:
                errors.append(_zh(result.error, "并行作业失败"))
            else:
                value = result.value or {}
                kind = value.get("kind")
                if kind == "plot_sample":
                    plot_samples.append({"label": value.get("label"), "data": value.get("data")})
                    report["selected_chapters"].append({"idx": value.get("idx")})
                elif kind == "characters":
                    raw_cards = list(value.get("cards") or [])
                elif kind == "anchor_pass":
                    first_draft[int(value.get("chapter") or 0)] = dict(value.get("draft") or {})
                elif kind == "anchor":
                    anchors_field.append({k: v for k, v in value.items() if k != "kind"})
                elif kind == "block":
                    block_results.setdefault(int(value.get("chapter") or 0), []).append(
                        dict(value.get("block") or {}))
            if jobs_done % 3 == 0 or jobs_done == len(jobs):
                _emit_progress(progress, "wave1_tick",
                               done=jobs_done, total=len(jobs))
    timings["wave1"] = round(time.perf_counter() - wave1_started, 3)
    _emit_progress(progress, "wave1_done", plot_samples=len(plot_samples),
                   blocks=sum(len(v) for v in block_results.values()),
                   anchors=len(anchors_field))

    # ---- 波次二：plot 合并 ∥ 长章合并 ∥ 第 1 章两遍法验证 ----
    wave2_started = time.perf_counter()
    jobs2: List[Callable[[], Dict[str, Any]]] = [
        lambda: _plot_merge_job(model, work_title, plot_samples)]
    for position, number in enumerate(window):
        text = texts.get(number, "")
        if not text:
            continue
        if number in long_blocks:
            blocks = list(block_results.get(number) or [])
            jobs2.append(lambda number=number, text=text, blocks=blocks:
                         _long_chapter_job(anchor_dir, model, number, text, blocks,
                                           first=(number == first_number)))
        elif position == 0:
            jobs2.append(lambda number=number, text=text:
                         _first_chapter_verify_job(anchor_dir, model, number, text,
                                                   first_draft.get(number)))
    plot_merged: Optional[Dict[str, Any]] = None
    for result in parallel.run_parallel(jobs2, priority=parallel.PRIORITY_OPENING):
        if not result.ok:
            errors.append(_zh(result.error, "并行作业失败"))
            continue
        value = result.value or {}
        kind = value.get("kind")
        if kind == "plot_merge":
            plot_merged = dict(value.get("data") or {})
        elif kind == "anchor":
            anchors_field.append({k: v for k, v in value.items() if k != "kind"})
    timings["wave2"] = round(time.perf_counter() - wave2_started, 3)
    anchors_field.sort(key=lambda entry: int(entry.get("chapter") or 0))
    if plot_merged is None:
        degraded = _plot_from_samples(plot_samples)
        if degraded is not None:
            plot_merged = degraded
            report["plot_degraded"] = True
            errors.append("剧情合并卷失败，已用采样结果降级合成剧情大概")
        else:
            errors.append("剧情采样与合并全部失败，剧情大概缺失")
    report["plot"] = plot_merged
    _emit_progress(progress, "plot_merged", degraded=report["plot_degraded"],
                   anchors=len(anchors_field))

    # ---- 作品档案卷（依赖 plot 合并结果）----
    archive_started = time.perf_counter()
    anchor_entries = _anchor_name_entries(anchors_field, anchor_dir)
    normalized_cards: List[Dict[str, Any]] = []
    if raw_cards:
        for raw in raw_cards:
            card = _normalize_opening_card(raw, work_title)
            if card is not None:
                normalized_cards.append(card)
    work_entry, archive_errors = _archive_step(
        model, work_title, plot_merged, samples_text, normalized_cards, library_path)
    report["work_entry"] = work_entry
    errors.extend(archive_errors)
    timings["archive"] = round(time.perf_counter() - archive_started, 3)
    _emit_progress(progress, "archive_done", work_id=(work_entry or {}).get("work_id"))

    # ---- 角色卡入库强化（质量门 + 防编造 + 入库）----
    characters_started = time.perf_counter()
    if raw_cards is None:
        errors.append("角色抽取卷失败，本局未入库新角色卡")
    else:
        final_cards, entries = _process_characters(
            model, work_title, raw_cards, anchor_entries)
        saver = save_characters_fn or _default_save_characters
        saved: List[Any] = []
        try:
            saved = list(saver(list(final_cards)) or [])
        except Exception as exc:  # noqa: BLE001 入库通道故障只记录
            errors.append("角色入库失败：%s" % _zh(exc, "入库通道故障"))
        for item in saved:
            if isinstance(item, Mapping) and item.get("error"):
                errors.append("角色保存失败（%s）：%s" % (
                    item.get("name") or "未命名角色", item.get("error")))
        saved_names = {str(item.get("name") or "").strip()
                       for item in saved if isinstance(item, Mapping) and item.get("saved", True)}
        saved_by_name = {str(item.get("name") or "").strip(): item for item in saved
                         if isinstance(item, Mapping) and item.get("saved", True)}
        for index, card in enumerate(final_cards):
            name = str(card.get("name") or "").strip()
            saved_flag = name in saved_names
            for entry in entries:
                if entry.get("name") == name and not entry.get("dropped_reason"):
                    entry["saved"] = saved_flag
                    if saved_flag and 0 <= index < len(saved) and isinstance(saved[index], Mapping):
                        entry["card_id"] = saved[index].get("id")
        report["characters"] = entries
        report["character_saved_count"] = sum(1 for entry in entries if entry.get("saved"))
    timings["characters"] = round(time.perf_counter() - characters_started, 3)
    _emit_progress(progress, "characters_done", saved=report["character_saved_count"])
    return _finish()


__all__ = [
    "run_opening_pipeline",
    "LONG_CHAPTER_THRESHOLD", "BLOCK_TARGET_CHARS", "MAX_BLOCKS_PER_CHAPTER",
    "MAX_CHAPTERS_AHEAD", "MAX_CHARACTER_CARDS",
    "PLOT_SAMPLE_SPECS", "PLOT_MERGE_SPECS", "BLOCK_SPECS", "ARCHIVE_SPECS",
    "CHARACTERS_OUTER_SPECS", "CHARACTER_CARD_SPECS",
]
