# -*- coding: utf-8 -*-
"""上传作品快速蒸馏：作品档案入基础模式作品库 + 角色性格卡（含四维）入角色库。

链路：章节样本（复用 plot_summary 抽样）→ 单次模型蒸馏 → 解析 JSON →
作品档案块 upsert 进 assets/rules/work_library.md 第五章（W 编号自增、同名覆盖）；
角色卡逐张经 character_library.save_card 落 user/ 目录 + SQLite（source_type=user）。

纯函数为主、模型调用经传入的 model callable 完成，不依赖任何 SDK；
作品库路径与存卡函数可注入，测试全程离线。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from core import prompts
from core.engine import character_designer, plot_summary

Model = Callable[[str], Any]

_ENTRY_RE = re.compile(r"^### (W\d+) · 《([^》]+)》", re.M)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
_CHAPTER_FIVE_RE = re.compile(r"^## 第六章", re.M)

MAX_SAMPLES = 4                 # 蒸馏抽样章节数（首/末+均匀抽样）
MAX_CHARS_PER_CHAPTER = 2500    # 单章样本字符上限
MAX_DISTILL_CHARACTERS = 8      # 单次入库角色卡上限
MAX_ANCHORS = 6
SOURCE_TAG = "用户上传蒸馏"      # 档案块来源标注（溯源用）


def _work_library_path() -> Path:
    """作品库路径（调用时解析，便于测试替换项目根）。"""
    return Path(__file__).resolve().parents[2] / "assets" / "rules" / "work_library.md"


# ---------------------------------------------------------------- 抽样与提示词


def build_samples_text(book_dir: str | Path,
                       max_samples: int = MAX_SAMPLES,
                       max_chars: int = MAX_CHARS_PER_CHAPTER) -> str:
    """按 plot_summary 的口径抽样首/末+均匀章节，拼成样本正文。"""
    chapters = plot_summary._read_chapters(book_dir)
    selected = plot_summary.select_summary_chapters(chapters, max_samples)
    parts: List[str] = []
    for chapter in selected:
        label = chapter.get("title") or "第%s章" % chapter.get("idx", "?")
        text = str(chapter.get("text", ""))[:max_chars]
        parts.append("【%s】\n%s" % (label, text))
    return "\n\n".join(parts)


def build_distill_prompt(work_title: str,
                         samples_text: str,
                         plot: Optional[Mapping[str, Any]] = None) -> str:
    """装配快速蒸馏提示词（文案在 assets/prompts/work_archive_distill.md）。"""
    summary_text = json.dumps(plot, ensure_ascii=False) if plot else ""
    return prompts.render(
        "work_archive_distill.md",
        WORK_TITLE=work_title or "未命名作品",
        PLOT_SUMMARY=summary_text,
        SAMPLES=samples_text,
        SLOT_VOCAB="、".join(character_designer.SLOT_KEY_VOCAB),
    )


# ---------------------------------------------------------------- 输出解析


def _extract_json(text: str) -> Dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    candidate = match.group(1) if match else None
    if candidate is None:
        start = str(text).find("{")
        end = str(text).rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型输出中未找到 JSON")
        candidate = str(text)[start:end + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("蒸馏输出 JSON 解析失败：%s" % exc) from exc
    if not isinstance(obj, dict):
        raise ValueError("蒸馏输出 JSON 不是对象")
    return obj


def _pick(data: Mapping[str, Any], *keys: str) -> Any:
    """多键名宽容取值：模型偶发改写顶层键名（archive→work_archive 等）。"""
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def normalize_archive(raw: Any) -> Dict[str, Any]:
    """作品档案字段归一：缺失给空串/空数组，anchors 限 6 条。"""
    raw = raw if isinstance(raw, Mapping) else {}
    anchors = raw.get("anchors")
    if isinstance(anchors, str):
        anchors = [anchors]
    if not isinstance(anchors, (list, tuple)):
        anchors = []
    return {
        "genre": _clip(raw.get("genre"), 60),
        "tier": _clip(raw.get("tier"), 40),
        "language_style": _clip(raw.get("language_style"), 120),
        "pacing": _clip(raw.get("pacing"), 120),
        "anchors": [_clip(item, 80) for item in anchors if _clip(item, 80)][:MAX_ANCHORS],
        "world_will": _clip(raw.get("world_will"), 80),
        "golden_finger_fit": _clip(raw.get("golden_finger_fit"), 120),
        "entry_point": _clip(raw.get("entry_point"), 80),
        "power_system": _clip(raw.get("power_system"), 80),
        "factions": _clip(raw.get("factions"), 100),
        "timeline": _clip(raw.get("timeline"), 100),
        "causal_rules": _clip(raw.get("causal_rules"), 100),
    }


_POSITION_TO_ROLE = {"主角": "主角", "男主": "主角", "女主": "single_heroine", "反派": "反派"}


def normalize_character(raw: Any, work_title: str) -> Optional[Dict[str, Any]]:
    """单张角色卡归一：缺名丢弃；四维 slot_keys 走设计器白名单+兜底。"""
    if not isinstance(raw, Mapping):
        return None
    name = _clip(raw.get("name"), 40)
    if not name:
        return None
    position = character_designer._normalize_position(raw.get("original_position"))
    return {
        "name": name,
        "role": _POSITION_TO_ROLE.get(position, "伙伴"),
        "work": "《%s》" % work_title if work_title else "",
        "archetype": _clip(raw.get("archetype"), 40),
        "desire": _clip(raw.get("desire"), 120),
        "fear": _clip(raw.get("fear"), 90),
        "voice": _clip(raw.get("voice"), 60),
        "background": _clip(raw.get("background"), 200),
        "relationship_vector": raw.get("relationship_vector")
        if isinstance(raw.get("relationship_vector"), Mapping) else {},
        "gender": character_designer._normalize_gender(raw.get("gender")),
        "original_position": position,
        "source_medium": _clip(raw.get("source_medium"), 40) or "上传作品",
        "source_region": _clip(raw.get("source_region"), 20).lower(),
        "slot_keys": character_designer._normalize_slot_keys(raw.get("slot_keys")),
        "source": SOURCE_TAG,
    }


# ---------------------------------------------------------------- 档案块渲染与入库


def render_entry(work_id: str, work_title: str, archive: Mapping[str, Any],
                 characters: List[Mapping[str, Any]]) -> str:
    """渲染与作品库既有条目同构的档案块（### Wxx · 《title》）。"""
    anchor_text = "；".join("%d.%s" % (i + 1, item) for i, item in enumerate(archive["anchors"]))
    role_bits: List[str] = []
    for card in characters[:MAX_DISTILL_CHARACTERS]:
        relation = card.get("relationship_vector")
        if isinstance(relation, Mapping):
            relation_text = "、".join("%s(%s)" % (k, v) for k, v in list(relation.items())[:4])
        else:
            relation_text = ""
        clm = "；".join(bit for bit in (card.get("voice"), card.get("fear")) if bit)
        bit = "《%s》核心欲望：%s" % (card["name"], card.get("desire") or "未明")
        if relation_text:
            bit += "；关系向量：%s" % relation_text
        if clm:
            bit += "；CLM 要点：%s" % clm
        role_bits.append(bit)
    summary_bits = [bit for bit in (
        "力量体系%s" % archive["power_system"] if archive["power_system"] else "",
        "势力图谱%s" % archive["factions"] if archive["factions"] else "",
        "时间线锚点%s" % archive["timeline"] if archive["timeline"] else "",
        "因果律%s" % archive["causal_rules"] if archive["causal_rules"] else "",
    ) if bit]
    lines = [
        "### %s · 《%s》" % (work_id, work_title),
        "",
        "- **题材**：%s｜ **Tier**：%s｜ **模式适配**：基础/强化均支持｜ **来源**：%s"
        % (archive["genre"] or "综合", archive["tier"] or "T4（系数 WS 5.0）", SOURCE_TAG),
        "- **语体指纹**：%s" % (archive["language_style"] or "（蒸馏未提供）"),
        "- **节奏档案**：%s" % (archive["pacing"] or "（蒸馏未提供）"),
        "- **主要锚点**（必发生，仅可换形式）：%s" % (anchor_text or "（蒸馏未提供）"),
        "- **核心角色**：%s" % ("。".join(role_bits) or "（蒸馏未提供）"),
        "- **叙事钩子/世界意志方向**（M16）：%s" % (archive["world_will"] or "（蒸馏未提供）"),
        "- **适配金手指**（按 Tier 缩放）：%s" % (archive["golden_finger_fit"] or "（蒸馏未提供）"),
        "- **开局落点**：%s" % (archive["entry_point"] or "故事开篇"),
        "- **强化模式结构化摘要（M30）**：%s" % ("；".join(summary_bits) or "（蒸馏未提供）"),
        "",
    ]
    return "\n".join(lines)


def _next_work_id(text: str) -> str:
    numbers = [int(m.group(1)[1:]) for m in _ENTRY_RE.finditer(text)]
    return "W%02d" % ((max(numbers) if numbers else 0) + 1)


def find_work_id(text: str, work_title: str) -> Optional[str]:
    """按作品名查既有条目编号（同名判重）。"""
    for match in _ENTRY_RE.finditer(text):
        if match.group(2) == work_title:
            return match.group(1)
    return None


def upsert_work_entry(entry_md: str, work_title: str,
                      library_path: str | Path | None = None) -> Dict[str, str]:
    """把档案块写入作品库：同名条目整块替换（编号不变），否则追加到第五章末尾。

    返回 {"work_id", "action"}（action ∈ added/updated）。原子写（tmp+replace）。
    """
    path = Path(library_path) if library_path else _work_library_path()
    text = path.read_text(encoding="utf-8")
    existing = find_work_id(text, work_title)
    work_id = existing or _next_work_id(text)
    # 统一档案头编号：渲染时的占位编号（W??）在这里落定为真实编号。
    entry_md = re.sub(r"^### W[\d?]+ ·", "### %s ·" % work_id, entry_md, count=1, flags=re.M)
    if existing:
        pattern = re.compile(r"^### %s · 《%s》.*?(?=^### |^## |\Z)" % (re.escape(work_id), re.escape(work_title)),
                             re.M | re.S)
        new_text, count = pattern.subn(entry_md.rstrip() + "\n\n", text, count=1)
        if count == 0:
            raise ValueError("同名条目定位失败：%s（%s）" % (work_title, work_id))
        action = "updated"
    else:
        chapter_five_end = _CHAPTER_FIVE_RE.search(text)
        block = entry_md.rstrip() + "\n\n"
        if chapter_five_end:
            new_text = text[:chapter_five_end.start()] + block + text[chapter_five_end.start():]
        else:
            new_text = text.rstrip() + "\n\n" + block
        action = "added"
    tmp = path.with_suffix(".md.tmp")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ValueError("作品库写入失败：%s" % exc) from exc
    return {"work_id": work_id, "action": action}


# ---------------------------------------------------------------- 角色卡入库


def _save_distilled_characters(cards: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """逐张经 character_library.save_card 落 user/ + SQLite；单卡失败不拖垮整批。"""
    from core.engine import character_library

    saved: List[Dict[str, Any]] = []
    for card in cards:
        try:
            result = character_library.save_card(card)
            record = result.get("record") or {}
            saved.append({"id": record.get("id"), "name": record.get("name")})
        except Exception:  # noqa: BLE001 单卡失败跳过（日志由调用方记）
            continue
    return saved


# ---------------------------------------------------------------- 一站式入口


def quick_distill(book_dir: str | Path,
                  work_title: str,
                  model: Model,
                  plot: Optional[Mapping[str, Any]] = None,
                  *,
                  library_path: str | Path | None = None,
                  save_characters_fn: Optional[Callable[[List[Mapping[str, Any]]], List[Dict[str, Any]]]] = None
                  ) -> Dict[str, Any]:
    """快速蒸馏一本书：作品档案入作品库 + 角色性格卡（含四维）入角色库。

    model 为单次文本调用（内部子调用通道，如 engine.distill.distill_model 的包装）；
    save_characters_fn 可注入（测试隔离）；返回摘要 dict（不含大块正文，可入 state）。
    """
    work_title = (work_title or "").strip().strip("《》") or "未命名作品"
    samples = build_samples_text(book_dir)
    prompt = build_distill_prompt(work_title, samples, plot)
    output = model(prompt)
    data = _extract_json(output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))

    archive = normalize_archive(_pick(data, "archive", "work_archive", "作品档案"))
    characters: List[Mapping[str, Any]] = []
    for raw in (_pick(data, "characters", "roles", "角色") or [])[:MAX_DISTILL_CHARACTERS]:
        card = normalize_character(raw, work_title)
        if card:
            characters.append(card)

    # 空结果拒收：档案与角色全空说明模型输出结构不符——写库只会留下占位垃圾条目。
    if not any((archive["genre"], archive["anchors"], archive["power_system"],
                archive["language_style"], archive["pacing"])) and not characters:
        raise ValueError("蒸馏输出为空或结构不符（未获得档案与角色），未写入作品库")

    entry_md = render_entry("W??", work_title, archive, characters)
    write = upsert_work_entry(entry_md, work_title, library_path)

    saver = save_characters_fn or _save_distilled_characters
    saved = saver(characters)
    return {
        "version": 1,
        "work_id": write["work_id"],
        "work_title": work_title,
        "action": write["action"],
        "character_count": len(saved),
        "characters": saved,
        "anchors": len(archive["anchors"]),
        "source": SOURCE_TAG,
    }


__all__ = [
    "MAX_SAMPLES", "MAX_CHARS_PER_CHAPTER", "MAX_DISTILL_CHARACTERS", "SOURCE_TAG",
    "build_samples_text", "build_distill_prompt", "normalize_archive", "normalize_character",
    "render_entry", "find_work_id", "upsert_work_entry", "quick_distill",
]
