"""伙伴与女主预设目录的 schema、去重和加载接口。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

CATALOG_DIR = Path(__file__).resolve().parents[2] / "assets" / "data"
SKILLS_PATH = CATALOG_DIR / "skills_catalog.json"
CHARACTERS_PATH = CATALOG_DIR / "character_pools.json"
CORPUS_PATH = CATALOG_DIR / "layered_corpus.json"
# 新蒸馏内置卡的落位目录（一卡一文件，目录可不存在）；不动 character_pools.json。
BUILTIN_CHARACTERS_DIR = CATALOG_DIR / "characters" / "builtin"
# 四栏展示与选择层的栏位名（规格 §2）。
SLOT_NAMES = ("主角栏", "伴侣栏", "伙伴栏", "宿敌栏")
# 卡 role 枚举：前三项为既有引擎链路角色；"主角"/"反派" 为 2026-08-28 新增
# （放尾部不改既有顺序），供主角栏/宿敌栏蒸馏卡直接落库。
ROLES = ("伙伴", "single_heroine", "multi_heroine", "主角", "反派", "女主")


def _values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.replace("，", ",").split(",") if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _parse_relationship_vector(relation: Any) -> tuple[tuple[str, str], ...]:
    """宽松解析 relationship_vector，兼容 dict / str / list 三种历史形态。

    - Mapping: {"云绾": "亦师亦友", ...} → (target, rel_type) 对的有序 tuple。
    - str/list: "对云绾：亦师亦友；对林霜华：青梅竹马" 或
      ["对云绾：亦师亦友", ...] / [{"target": ..., "type": ...}, ...]
      → 按分隔符切段后提取目标与关系类型；解析不出的段静默丢弃。
    """
    if isinstance(relation, Mapping):
        return tuple(sorted((str(key), str(value)) for key, value in relation.items()))
    import re as _re

    pairs: list[tuple[str, str]] = []

    def pairs_append(target: str, rel: str) -> None:
        target, rel = target.strip(), rel.strip()
        if target and rel:
            pairs.append((target, rel.rstrip("。，,；;")))

    segments: list[str] = []
    if isinstance(relation, str):
        segments = [seg for seg in _re.split(r"[；;]\s*", relation) if seg.strip()]
    elif isinstance(relation, (list, tuple, set)):
        for item in relation:
            if isinstance(item, Mapping):
                target = str(item.get("target") or item.get("entity") or "").strip()
                rel = str(item.get("type") or item.get("relationship") or item.get("relation") or "").strip()
                if target and rel:
                    segments.append(f"对{target}：{rel}")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                # (target, rel_type) 元组形态（get_character_by_id 从关系表组装）
                target, rel = str(item[0]).strip(), str(item[1]).strip()
                if target and rel:
                    pairs_append(target, rel)
            elif isinstance(item, str):
                segments.extend(seg for seg in _re.split(r"[；;]\s*", item) if seg.strip())
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # 形态1："对云绾：亦师亦友" / "对云绾 重视"
        m = _re.match(r"^对(.{1,14}?)(?:的|重视|保持|怀有|有|怀|带|是|：|:|\s)+(.{1,20})$", seg)
        if m:
            pairs_append(m.group(1), m.group(2))
            continue
        # 兜底："A：B" / "A-B" 拆两半
        m = _re.match(r"^(.{1,14}?)[：:\-—](.{1,20})$", seg)
        if m:
            pairs_append(m.group(1), m.group(2))
    # 去重保序
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen and pair[0] and pair[1]:
            seen.add(pair)
            ordered.append(pair)
    return tuple(ordered)


def _record_list(raw: Any, keys: tuple[str, ...], label: str) -> list[Mapping[str, Any]]:
    if isinstance(raw, Mapping):
        for key in keys:
            if key in raw:
                raw = raw[key]
                break
        else:
            raw = []
    if not isinstance(raw, list):
        raise ValueError(f"{label} 文件必须是数组或对象列表字段")
    return [item for item in raw if isinstance(item, Mapping)]


def _load_json(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True)
class SkillPreset:
    """可注入伙伴或女主角色卡的技能预设。"""

    id: str
    role: str
    name: str = ""
    summary: str = ""
    capabilities: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    source: str = ""

    @classmethod
    def from_record(cls, raw: Mapping[str, Any], index: int = 0) -> "SkillPreset":
        role = str(raw.get("role") or "伙伴")
        if role not in ROLES:
            raise ValueError(f"技能预设 role 无效: {role}")
        return cls(
            id=str(raw.get("id") or f"skill-{index}"),
            role=role,
            name=str(raw.get("name") or ""),
            summary=str(raw.get("summary") or ""),
            capabilities=_values(raw.get("capabilities")),
            limits=_values(raw.get("limits")),
            tags=_values(raw.get("tags")),
            source=str(raw.get("source") or ""),
        )


@dataclass(frozen=True)
class CharacterCard:
    """伙伴或女主角色卡；字段对应 rules/runtime.md 的角色约束。"""

    id: str
    role: str
    name: str = ""
    work: str = ""
    archetype: str = ""
    desire: str = ""
    fear: str = ""
    abilities: tuple[str, ...] = ()
    relationship_vector: tuple[tuple[str, str], ...] = ()
    knowledge_scope: tuple[str, ...] = ()
    voice: str = ""
    unacceptable_actions: tuple[str, ...] = ()
    background: str = ""
    skill_ids: tuple[str, ...] = ()
    source: str = ""
    # —— 扩池新增可选字段（规格 §1）：全部向后兼容，缺失给默认值 ——
    gender: str = "unknown"
    original_position: str = ""
    source_medium: str = ""
    source_region: str = ""
    slot_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    distill_level: str = "normal"

    @property
    def protagonist_type(self) -> tuple[str, ...]:
        return self.slot_keys.get("主角栏", ())

    @property
    def mainline_type(self) -> tuple[str, ...]:
        return self.slot_keys.get("伴侣栏", ())

    @property
    def partner_type(self) -> tuple[str, ...]:
        return self.slot_keys.get("伙伴栏", ())

    @property
    def nemesis_type(self) -> tuple[str, ...]:
        return self.slot_keys.get("宿敌栏", ())

    def __hash__(self) -> int:  # slot_keys 为 dict，保持卡对象可哈希
        return hash((self.id, self.role, self.name))

    @classmethod
    def from_record(cls, raw: Mapping[str, Any], index: int = 0) -> "CharacterCard":
        role = str(raw.get("role") or "伙伴")
        if role not in ROLES:
            raise ValueError(f"角色卡 role 无效: {role}")
        relation = raw.get("relationship_vector", {})
        relationship_vector = _parse_relationship_vector(relation)
        # 宽松解析新字段：非法/缺失一律回落默认，不让坏数据炸加载。
        gender = str(raw.get("gender") or "").strip().lower()
        if gender not in ("male", "female"):
            gender = "unknown"
        distill_level = str(raw.get("distill_level") or "normal").strip().lower() or "normal"
        if distill_level not in ("normal", "enhanced"):
            distill_level = "normal"
        slot_keys: dict[str, tuple[str, ...]] = {}
        slot_raw = raw.get("slot_keys")
        if isinstance(slot_raw, Mapping):
            for key, value in slot_raw.items():
                # 历史键名"主线栏"统一读作"伴侣栏"
                normalized_key = "伴侣栏" if str(key).strip() == "主线栏" else str(key).strip()
                parsed = _values(value)
                if parsed:
                    slot_keys[normalized_key] = parsed
        for field_name, slot_name in (("protagonist_type", "主角栏"), ("mainline_type", "伴侣栏"),
                                      ("partner_type", "伙伴栏"), ("nemesis_type", "宿敌栏")):
            if slot_name not in slot_keys:
                parsed = _values(raw.get(field_name))
                slot_keys[slot_name] = parsed
        # 四维兜底：任何维度缺失或为空都填"通用"，确保每张卡四栏均可选。
        for slot_name in SLOT_NAMES:
            if not slot_keys.get(slot_name):
                slot_keys[slot_name] = ("通用",)
        return cls(
            id=str(raw.get("id") or f"character-{index}"),
            role=role,
            name=str(raw.get("name") or ""),
            work=str(raw.get("work") or ""),
            archetype=str(raw.get("archetype") or ""),
            desire=str(raw.get("desire") or ""),
            fear=str(raw.get("fear") or ""),
            abilities=_values(raw.get("abilities")),
            relationship_vector=relationship_vector,
            knowledge_scope=_values(raw.get("knowledge_scope")),
            voice=str(raw.get("voice") or ""),
            unacceptable_actions=_values(raw.get("unacceptable_actions")),
            background=str(raw.get("background") or ""),
            skill_ids=_values(raw.get("skill_ids")),
            source=str(raw.get("source") or ""),
            gender=gender,
            original_position=str(raw.get("original_position") or "").strip(),
            source_medium=str(raw.get("source_medium") or "").strip(),
            source_region=str(raw.get("source_region") or "").strip().lower(),
            slot_keys=slot_keys,
            distill_level=distill_level,
        )


def _dedupe_sorted(items: Iterable[Any]) -> tuple[Any, ...]:
    seen: set[str] = set()
    unique: list[Any] = []
    for item in items:
        item_id = str(getattr(item, "id", ""))
        if item_id in seen:
            continue
        seen.add(item_id)
        unique.append(item)
    unique.sort(key=lambda item: (str(getattr(item, "id", "")), str(getattr(item, "role", "")), str(getattr(item, "name", ""))))
    return tuple(unique)


def load_skill_catalog(path: str | Path = SKILLS_PATH) -> tuple[SkillPreset, ...]:
    """加载技能目录，丢弃非对象记录并按 id 去重、稳定排序。"""
    rows = _record_list(_load_json(path), ("skills", "items", "entries"), "技能目录")
    return _dedupe_sorted(SkillPreset.from_record(row, index) for index, row in enumerate(rows))


def _builtin_character_rows(directory: Path | None = None) -> list[Mapping[str, Any]]:
    """扫描 builtin 目录（含子目录）内的单卡 JSON；坏文件跳过，目录不存在返回空。

    directory 缺省时取模块常量 BUILTIN_CHARACTERS_DIR（调用时解析，便于测试替换）。
    """
    directory = Path(directory) if directory is not None else BUILTIN_CHARACTERS_DIR
    rows: list[Mapping[str, Any]] = []
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("**/*.json"), key=lambda p: (str(p.parent), p.stem)):
        try:
            with path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(raw, Mapping):
            continue
        wrapper = raw.get("characters") if isinstance(raw.get("characters"), list) else None
        if wrapper is not None:
            rows.extend(item for item in wrapper if isinstance(item, Mapping))
        elif raw.get("role"):
            rows.append(raw)
    return rows


def load_character_pool(path: str | Path = CHARACTERS_PATH) -> tuple[CharacterCard, ...]:
    """加载角色卡目录，丢弃非对象记录并按 id 去重、稳定排序。

    统一数据库优先：首先尝试从SQLite数据库加载，如果数据库不可用则回退到JSON文件加载。
    这是所有角色数据访问的统一入口。
    """
    # 统一数据库优先
    try:
        from core.engine.character_db import get_all_characters
        characters = get_all_characters()
        if characters:
            return tuple(characters)
    except Exception:
        # 数据库加载失败，记录错误并回退
        import warnings
        warnings.warn("数据库加载失败，回退到JSON文件加载")
    
    # 回退到JSON文件加载（兼容模式）；索引文件缺失（清库分发态）按空池处理
    try:
        rows = _record_list(_load_json(path), ("characters", "items", "entries"), "角色目录")
    except OSError:
        rows = []
    rows = list(rows) + _builtin_character_rows()
    return _dedupe_sorted(CharacterCard.from_record(row, index) for index, row in enumerate(rows))


# 与 load_entries 同类的短名称，方便调用方按资源类型选择。
load_skills = load_skill_catalog
load_characters = load_character_pool


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, tuple[Any, ...]]:
    """加载默认伙伴/女主目录；返回 skills 和 characters 两个稳定元组。"""
    return {"skills": load_skill_catalog(), "characters": load_character_pool()}


@lru_cache(maxsize=1)
def load_layered_corpus(path: str | Path = CORPUS_PATH) -> dict[str, Any]:
    """加载三层语料索引；模板按去重后的稳定 ID 保存。"""
    raw = _load_json(path)
    templates = raw.get("templates", []) if isinstance(raw, Mapping) else []
    if not isinstance(templates, list):
        templates = []
    seen = set()
    unique = []
    for item in templates:
        if not isinstance(item, Mapping):
            continue
        ident = str(item.get("id") or "")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        unique.append(dict(item))
    index = raw.get("index", {}) if isinstance(raw, Mapping) else {}
    return {"templates": tuple(unique), "index": {str(k): tuple(v) for k, v in index.items() if isinstance(v, list)}, "dedupe": dict(raw.get("dedupe", {})) if isinstance(raw, Mapping) else {}}


def sample_layered_corpus(theme: str = "", mechanism: str = "", style: str = "", seed: int | None = None) -> Mapping[str, Any] | None:
    """按大题材、叙事机制、风格三层过滤并稳定随机抽取一条模板。"""
    import random
    rows = list(load_layered_corpus()["templates"])
    rows = [row for row in rows if (not theme or row.get("theme") == theme) and (not mechanism or row.get("mechanism") == mechanism) and (not style or row.get("style") == style)]
    if not rows:
        return None
    return random.Random(seed).choice(rows)


def clear_catalog_cache() -> None:
    """清理默认目录缓存，便于测试或热更新目录文件。"""
    load_catalog.cache_clear()


__all__ = [
    "CATALOG_DIR",
    "SKILLS_PATH",
    "CHARACTERS_PATH", "CORPUS_PATH",
    "BUILTIN_CHARACTERS_DIR", "SLOT_NAMES",
    "ROLES",
    "SkillPreset",
    "CharacterCard",
    "load_skill_catalog",
    "load_character_pool",
    "load_skills",
    "load_characters",
    "load_catalog",
    "load_layered_corpus", "sample_layered_corpus", "clear_catalog_cache",
]
