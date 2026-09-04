"""用户本地角色库：可扩充、可替换、可导出的角色卡 CRUD 存储层。

设计要点：
- 内置卡存于 ``data/character_pools.json``（只读，升级安全）。
- 用户卡每张一个 JSON 文件，存于 ``data/characters/user/``。
- 用户卡 id 统一加 ``user-`` 前缀；``overrides/`` 子目录存放“替换内置”
  的用户版卡，文件名与被遮蔽的内置卡 id 相同（可整体删除以还原）。
- 所有写操作线程安全；读取失败的单张坏卡只跳过不炸全局。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.engine.catalog import ROLES, CharacterCard

PROJECT_DATA_DIR = Path(__file__).resolve().parents[2] / "assets" / "data"
USER_LIBRARY_DIR = PROJECT_DATA_DIR / "characters" / "user"
OVERRIDES_DIR = USER_LIBRARY_DIR / "overrides"

_USER_PREFIX = "user-"
_LOCK = threading.Lock()

# 字段长度上限，防止把整个语料库塞进一张卡。
MAX_FIELD_TEXT = 2000
MAX_LIST_ITEMS = 12
MAX_NAME = 40
MAX_ID = 64


class LibraryError(ValueError):
    """角色库操作错误；信息面向最终用户展示。"""


def _slug(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", str(text)).strip("-")
    return slug or "card"


def user_card_id(name: str) -> str:
    """按名称生成稳定的用户卡 id（含前缀）。"""
    return f"{_USER_PREFIX}{_slug(name)}"


def ensure_dirs() -> None:
    USER_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)


def _validate_role(role: str) -> str:
    if role not in ROLES:
        raise LibraryError(f"role 必须是 {', '.join(ROLES)}")
    return role


def _clip_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_FIELD_TEXT:
        raise LibraryError(f"单字段文本超过 {MAX_FIELD_TEXT} 字上限")
    return text


def _clip_list(value: Any) -> tuple[str, ...]:
    items = catalog_values(value)
    if len(items) > MAX_LIST_ITEMS:
        raise LibraryError(f"列表字段最多 {MAX_LIST_ITEMS} 项")
    for item in items:
        if len(item) > MAX_FIELD_TEXT:
            raise LibraryError(f"列表项文本超过 {MAX_FIELD_TEXT} 字上限")
    return items


def catalog_values(value: Any) -> tuple[str, ...]:
    """复用 CharacterCard 的宽松解析口径（逗号/数组均可），供列表字段使用。"""
    from core.engine.catalog import _values

    return _values(value)


def build_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    """从任意输入构造合法角色卡记录；非法字段抛 LibraryError。"""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise LibraryError("角色卡缺少 name")
    if len(name) > MAX_NAME:
        raise LibraryError(f"name 超过 {MAX_NAME} 字")
    # 新建角色名回避党和国家领导人（仅新建拦截；既有库卡与虚构角色名不受影响）
    from core.engine.name_collision import is_political_figure
    if is_political_figure(name):
        raise LibraryError("角色名涉及党和国家领导人，不可使用，请更换")
    record: dict[str, Any] = {
        "id": "",
        "role": _validate_role(str(payload.get("role") or "伙伴")),
        "name": name,
        "work": _clip_text(payload.get("work")),
        "archetype": _clip_text(payload.get("archetype")),
        "desire": _clip_text(payload.get("desire")),
        "fear": _clip_text(payload.get("fear")),
        "abilities": list(_clip_list(payload.get("abilities"))),
        "relationship_vector": (
            payload.get("relationship_vector")
            if isinstance(payload.get("relationship_vector"), (dict, Mapping))
            else _clip_text(payload.get("relationship_vector"))
        ),
        "knowledge_scope": list(_clip_list(payload.get("knowledge_scope"))),
        "voice": _clip_text(payload.get("voice")),
        "unacceptable_actions": list(_clip_list(payload.get("unacceptable_actions"))),
        "background": _clip_text(payload.get("background")),
        "skill_ids": list(_clip_list(payload.get("skill_ids"))),
        "source": _clip_text(payload.get("source") or "用户自定义"),
        "gender": _clip_text(payload.get("gender") or "unknown").lower(),
        "original_position": _clip_text(payload.get("original_position")),
        "source_medium": _clip_text(payload.get("source_medium")),
        "source_region": _clip_text(payload.get("source_region")).lower(),
        "slot_keys": {
            "主角栏": list(_clip_list(payload.get("protagonist_type"))),
            "伴侣栏": list(_clip_list(payload.get("companion_type") or payload.get("mainline_type"))),
            "伙伴栏": list(_clip_list(payload.get("partner_type"))),
            "宿敌栏": list(_clip_list(payload.get("nemesis_type"))),
        },
    }
    # 四维语义字段（可选，向后兼容）
    from core.engine import character_semantic_distiller
    semantic_fields = character_semantic_distiller.normalize_semantic_fields(payload)
    for field_name in character_semantic_distiller.SEMANTIC_FIELDS:
        record[field_name] = semantic_fields[field_name]
    # 四维兜底：任何维度缺失或为空都填"通用"，确保每张卡四栏均可选。
    for _slot in ("主角栏", "伴侣栏", "伙伴栏", "宿敌栏"):
        if not record["slot_keys"].get(_slot):
            record["slot_keys"][_slot] = ["通用"]
    provided_slots = payload.get("slot_keys")
    if isinstance(provided_slots, Mapping):
        record["slot_keys"].update({str(k): list(_clip_list(v)) for k, v in provided_slots.items()})
    relation = record["relationship_vector"]
    if isinstance(relation, Mapping):
        if len(relation) > MAX_LIST_ITEMS:
            raise LibraryError(f"关系向量最多 {MAX_LIST_ITEMS} 条")
        record["relationship_vector"] = {
            str(k)[:MAX_NAME]: _clip_text(v) for k, v in relation.items()
        }
    return record


def _card_from_record(record: Mapping[str, Any], fallback_id: str = "") -> CharacterCard:
    """用 catalog 的校验口径把记录转成 CharacterCard；失败抛 ValueError。"""
    raw = dict(record)
    raw.setdefault("id", fallback_id)
    if not raw.get("id"):
        raw["id"] = fallback_id
    card = CharacterCard.from_record(raw)
    if not card.name:
        raise ValueError("角色卡缺少 name")
    return card


def _scan_cards(directory: Path, prefix_ids: bool = False) -> list[CharacterCard]:
    """扫描目录内单卡 JSON 文件；坏卡跳过。prefix_ids 强制 id 带 user- 前缀。"""
    cards: list[CharacterCard] = []
    if not directory.is_dir():
        return cards
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stem):
        try:
            with path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, Mapping):
                continue
            wrapper = raw.get("characters") if isinstance(raw.get("characters"), list) else None
            rows: list[Mapping[str, Any]] = []
            if wrapper is not None:
                rows = [item for item in wrapper if isinstance(item, Mapping)]
            elif raw.get("role"):
                rows = [raw]
            for row in rows:
                with_id = dict(row)
                if prefix_ids and not str(with_id.get("id", "")).startswith(_USER_PREFIX):
                    with_id["id"] = user_card_id(str(row.get("name") or path.stem))
                cards.append(_card_from_record(with_id, fallback_id=user_card_id(str(row.get("name") or path.stem))))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return cards


def built_in_cards() -> list[CharacterCard]:
    """加载内置池原始卡（未经遮蔽）。"""
    from core.engine import catalog as catalog_module

    return list(catalog_module.load_character_pool())


def load_user_cards() -> tuple[list[CharacterCard], list[CharacterCard]]:
    """返回 (新增用户卡, 替换内置的用户版卡)。"""
    added = _scan_cards(USER_LIBRARY_DIR, prefix_ids=True)
    overrides = _scan_cards(OVERRIDES_DIR, prefix_ids=False)
    return added, overrides


def merged_pool() -> tuple[tuple[CharacterCard, ...], set[str]]:
    """返回合并后的完整角色池与被遮蔽的内置 id 集合。

    统一数据库优先：首先尝试从SQLite数据库加载，如果数据库不可用则回退到JSON文件加载。
    合并顺序：未被遮蔽的内置卡 + overrides 中与内置同名 role 的卡 + 用户新增。
    """
    # 统一数据库优先
    try:
        from core.engine.character_db import get_all_characters, get_character_by_id
        
        # 从数据库获取所有内置角色
        builtin = get_all_characters()
        
        # 获取用户卡和替换卡（从JSON文件）
        added, overrides = load_user_cards()
        
        shadowed: set[str] = set()
        result: list[CharacterCard] = []
        seen: set[str] = set()
        
        # 处理替换卡
        override_index = {(c.id, c.role): c for c in overrides}
        remaining_overrides = {key: value for key, value in override_index.items()}
        
        for card in builtin:
            override = override_index.get((card.id, card.role))
            if override is not None:
                result.append(override)
                shadowed.add(card.id)
                remaining_overrides.pop((card.id, card.role), None)
                continue
            if card.id in seen:
                continue
            seen.add(card.id)
            result.append(card)
        
        # 添加剩余的替换卡
        for card in remaining_overrides.values():
            result.append(card)
        
        # 添加用户新增卡
        for card in added:
            if card.id in seen:
                continue
            seen.add(card.id)
            result.append(card)
        
        result.sort(key=lambda item: (item.role, item.name))
        return tuple(result), shadowed
        
    except Exception:
        # 数据库加载失败，记录错误并回退到JSON文件加载
        import warnings
        warnings.warn("数据库加载失败，回退到JSON文件加载")
        
        builtin = built_in_cards()
        added, overrides = load_user_cards()
        shadowed: set[str] = set()
        result: list[CharacterCard] = []
        seen: set[str] = set()
        
        override_index = {(c.id, c.role): c for c in overrides}
        remaining_overrides = {key: value for key, value in override_index.items()}
        
        for card in builtin:
            override = override_index.get((card.id, card.role))
            if override is not None:
                result.append(override)
                shadowed.add(card.id)
                remaining_overrides.pop((card.id, card.role), None)
                continue
            if card.id in seen:
                continue
            seen.add(card.id)
            result.append(card)
        
        for card in remaining_overrides.values():
            result.append(card)
        
        for card in added:
            if card.id in seen:
                continue
            seen.add(card.id)
            result.append(card)
        
        result.sort(key=lambda item: (item.role, item.name))
        return tuple(result), shadowed

    result.sort(key=lambda item: (item.role, item.name))
    return tuple(result), shadowed


def merged_pool_cached() -> tuple[tuple[CharacterCard, ...], set[str]]:
    """``merged_pool`` 的读缓存版（HTTP 端点用）。

    未缓存时算一次并存入 registries 缓存，命中则直接返回。写路径
    （save_card/delete_card/导入/换库）全部经 ``refresh_game_cache`` 失效，
    所以新增卡片刷新页面即可见——与直算版语义一致。

    为什么需要：``merged_pool`` 内部对每张卡调 ``get_character_by_id``，
    而后者每次新开 SQLite 连接执行数组查询；数百张卡时单个 HTTP 请求会产生
    数百次建连。前端角色四栏下拉与悬停详情都打这些端点。

    返回的 shadowed 是缓存集合的拷贝，调用方就地修改不会污染缓存。
    """
    try:
        from core.services import registries

        cached = registries.get_character_pool_cache()
        if isinstance(cached, tuple) and len(cached) == 2:
            cards, shadowed = cached
            return cards, set(shadowed)
        cards, shadowed = merged_pool()
        registries.set_character_pool_cache((cards, frozenset(shadowed)))
        return cards, shadowed
    except Exception:  # noqa: BLE001  缓存层故障不得影响功能
        return merged_pool()


def refresh_game_cache() -> None:
    """让内置池缓存失效，下一次开局立即看到新卡。

    缓存本体在 core.services.registries（中立层）；engine 不再反向写
    app 模块全局，app↔engine 循环依赖断根。延迟 import 保留（中立模块
    无循环风险），仅防御早期初始化阶段。
    """
    try:
        from core.services import registries

        registries.invalidate_character_pool_cache()
    except Exception:  # noqa: BLE001  缓存失效是尽力而为
        pass


def save_card(payload: Mapping[str, Any], *, replace_built_in: bool = False,
              allow_name_change: bool = True) -> dict[str, Any]:
    """新增或更新一张用户卡。

    - ``replace_built_in=False``：存到 user/ 根目录（普通用户卡）。
    - ``replace_built_in=True``：payload 必须带要遮蔽的内置 ``target_id``，
      卡片写入 overrides/ 目录并以 target_id 作为卡 id。
    """
    record = build_record(payload)
    role = record["role"]
    name = record["name"]
    
    # 确定来源类型
    source_type = "override" if replace_built_in else "user"
    
    with _LOCK:
        ensure_dirs()
        if replace_built_in:
            target_id = str(payload.get("target_id") or "").strip()
            builtin_map = {c.id: c for c in built_in_cards()}
            base = builtin_map.get(target_id)
            if base is None:
                raise LibraryError(f"找不到要替换的内置角色：{target_id}")
            # 替换版保留内置卡的 id 与 role 归属，保证游戏链路选择一致。
            record["id"] = target_id
            record["role"] = base.role
            merged_name = name or base.name
            record["name"] = merged_name
            path = OVERRIDES_DIR / f"{target_id}.json"
        else:
            current_id = str(payload.get("id") or "").strip()
            if current_id.startswith(_USER_PREFIX):
                new_id = user_card_id(name) if allow_name_change and name != _lookup_user_name(current_id) else current_id
            else:
                existing = _find_by_name(role, name)
                if existing is not None:
                    new_id = existing.id
                else:
                    new_id = user_card_id(name)
                    suffix = 2
                    while (USER_LIBRARY_DIR / f"{new_id}.json").exists():
                        new_id = f"{new_id}-{suffix}"
                        suffix += 1
            record["id"] = new_id
            old_path = USER_LIBRARY_DIR / f"{current_id}.json" if current_id.startswith(_USER_PREFIX) else None
            if old_path is not None and old_path.exists() and record["id"] != current_id:
                old_path.unlink(missing_ok=True)
            path = USER_LIBRARY_DIR / f"{record['id']}.json"
        
        # 保存到JSON文件
        payload_out = {
            "schema_version": "3.0",
            "asset": "member_character_cards",
            "scope": "user_customized",
            "origin": "replace_built_in" if replace_built_in else "user_created",
            "character": record,
        }
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise LibraryError(f"角色卡写入失败：{exc}") from exc
        
        # 同时保存到数据库
        try:
            from core.engine.character_db import insert_character, update_character
            card = _card_from_record(record, fallback_id=record["id"])
            
            # 检查是否已存在
            existing_card = get_character_by_id_from_db(record["id"])
            if existing_card:
                update_character(record["id"], record)
            else:
                insert_character(card, source_type=source_type)
        except Exception as e:
            # 数据库保存失败不影响JSON文件保存
            import warnings
            warnings.warn(f"数据库保存失败: {e}")
    
    refresh_game_cache()
    return {"record": record, "path": path, "origin": payload_out["origin"]}


def _lookup_user_name(card_id: str) -> str | None:
    path = USER_LIBRARY_DIR / f"{card_id}.json"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        return str(raw.get("name") or "")
    except (OSError, json.JSONDecodeError):
        return None


def _find_by_name(role: str, name: str) -> CharacterCard | None:
    added, _ = load_user_cards()
    return next((c for c in added if c.role == role and c.name == name), None)


def get_character_by_id_from_db(card_id: str) -> CharacterCard | None:
    """从数据库获取角色卡"""
    try:
        from core.engine.character_db import get_character_by_id
        return get_character_by_id(card_id)
    except Exception:
        return None


def update_card(card_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """更新既有用户卡（user- 前缀）或内置替换卡。"""
    card_id = str(card_id or "").strip()
    record_in = dict(payload)
    if card_id.startswith(_USER_PREFIX):
        record_in.setdefault("id", card_id)
        if _lookup_user_name(card_id) is None:
            raise LibraryError(f"找不到用户角色卡：{card_id}")
        return save_card(record_in, replace_built_in=False)
    # 非 user- 前缀视为替换内置的编辑（保持继续遮蔽同一张内置卡）。
    builtin_map = {c.id: c for c in built_in_cards()}
    if card_id not in builtin_map:
        raise LibraryError(f"不是用户卡也不是内置卡：{card_id}")
    record_in.setdefault("target_id", card_id)
    if not record_in.get("name"):
        record_in["name"] = builtin_map[card_id].name
    return save_card(record_in, replace_built_in=True)


def delete_card(card_id: str) -> dict[str, Any]:
    """删除用户卡；删除 override 即还原内置卡。"""
    card_id = str(card_id or "").strip()
    removed = ""
    with _LOCK:
        user_path = USER_LIBRARY_DIR / f"{card_id}.json"
        ovr_path = OVERRIDES_DIR / f"{card_id}.json"
        if card_id.startswith(_USER_PREFIX) and user_path.is_file():
            user_path.unlink(missing_ok=True)
            removed = "user"
        elif ovr_path.is_file():
            ovr_path.unlink(missing_ok=True)
            removed = "override"
        else:
            raise LibraryError(f"找不到可删除的用户角色卡：{card_id}")

        # 同步数据库：
        # - user 卡：硬删除数据库行；
        # - override 卡：数据库行来自 save_card 时的覆盖写入，删除 override 后
        #   必须从 JSON 源把原始内置卡重新插回，否则数据库优先加载会永远丢失该内置卡。
        try:
            from core.engine import character_db
            if removed == "user":
                character_db.delete_character(card_id, soft_delete=False)
            else:
                builtin_map = _json_builtin_map()
                base = builtin_map.get(card_id)
                if base is not None:
                    character_db.insert_character(base, source_type="builtin")
                else:
                    # JSON 源也没有该卡（极端情况）：删行兜底，避免残留 override 数据
                    character_db.delete_character(card_id, soft_delete=False)
        except Exception as e:
            # 数据库同步失败不影响JSON文件删除
            import warnings
            warnings.warn(f"数据库删除失败: {e}")

    refresh_game_cache()
    return {"removed": removed, "id": card_id}


def _json_builtin_map() -> dict[str, CharacterCard]:
    """仅从 JSON 文件（不含数据库、不含用户覆盖）加载内置卡索引。"""
    from core.engine import catalog as catalog_module

    rows = catalog_module._record_list(
        catalog_module._load_json(catalog_module.CHARACTERS_PATH),
        ("characters", "items", "entries"), "角色目录")
    rows = list(rows) + catalog_module._builtin_character_rows()
    cards = (CharacterCard.from_record(row, index) for index, row in enumerate(rows))
    return {card.id: card for card in cards if card.id}


def export_payload(card_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """导出为可与内置池互转的单体 JSON；默认导出全部用户侧卡。"""
    pool, _ = merged_pool()
    wanted = {str(i).strip() for i in (card_ids or []) if str(i).strip()}
    selected: list[dict[str, Any]] = []
    for card in pool:
        is_user = card.id.startswith(_USER_PREFIX) or (OVERRIDES_DIR / f"{card.id}.json").exists()
        if wanted and card.id not in wanted:
            continue
        if not wanted and not is_user:
            continue
        record = _record_of_card(card)
        if record is not None:
            selected.append(record)
    if wanted:
        found = {rec.get("id") for rec in selected}
        missing = sorted(wanted - {str(v) for v in found})
        if missing:
            raise LibraryError(f"以下角色不存在或不可导出：{'、'.join(missing)}")
    return {
        "schema_version": "3.0",
        "asset": "member_character_cards",
        "scope": "user_export",
        "exported_at_source": "书中行 角色库",
        "characters": selected,
    }


def _record_of_card(card: CharacterCard) -> dict[str, Any] | None:
    override_path = OVERRIDES_DIR / f"{card.id}.json"
    user_path = USER_LIBRARY_DIR / f"{card.id}.json"
    source_path = override_path if override_path.is_file() else (
        user_path if user_path.is_file() else None)
    if source_path is not None:
        try:
            with source_path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
            record = raw.get("character") if isinstance(raw.get("character"), Mapping) else raw
            if isinstance(record, Mapping):
                out = dict(record)
                out.pop("schema_version", None)
                out.pop("asset", None)
                out.pop("scope", None)
                return out
        except (OSError, json.JSONDecodeError):
            return None
    # 兜底：由 CharacterCard 反推记录。
    return {
        "id": card.id, "role": card.role, "name": card.name,
        "archetype": card.archetype, "desire": card.desire, "fear": card.fear,
        "abilities": list(card.abilities),
        "relationship_vector": dict(card.relationship_vector),
        "knowledge_scope": list(card.knowledge_scope), "voice": card.voice,
        "unacceptable_actions": list(card.unacceptable_actions),
        "background": card.background, "skill_ids": list(card.skill_ids),
        "source": card.source, "work": card.work, "gender": card.gender,
        "original_position": card.original_position,
        "source_medium": card.source_medium, "source_region": card.source_region,
        "slot_keys": {key: list(values) for key, values in card.slot_keys.items()},
        "protagonist_type": list(card.protagonist_type),
        "mainline_type": list(card.mainline_type),
        "partner_type": list(card.partner_type),
        "nemesis_type": list(card.nemesis_type),
    }


def import_records(rows: Iterable[Mapping[str, Any]], *,
                   overwrite: bool = False) -> dict[str, Any]:
    """批量导入角色记录；返回 成功/覆盖/失败 明细。

    记录携带与内置卡相同的内置 id 时视为“替换内置”导入（需 overwrite 确认）；
    否则一律作为新用户卡入库（同名报错除非 overwrite）。
    """
    results: dict[str, Any] = {"imported": [], "replaced": [], "failed": []}
    for index, row in enumerate(rows or [], start=1):
        label = str(row.get("name") or f"第{index}条")
        try:
            row_id = str(row.get("id") or "").strip()
            builtin_map = {c.id for c in built_in_cards()}
            if row_id and row_id in builtin_map:
                saved = save_card({**row, "target_id": row_id}, replace_built_in=True)
                results["replaced"].append(saved["record"]["name"])
            else:
                clean = {**row}
                clean.pop("id", None)
                exists = _find_by_name(_validate_role(str(clean.get("role") or "伙伴")), str(clean.get("name") or ""))
                if exists is not None and not overwrite:
                    raise LibraryError("已存在同名同类型用户卡；如需覆盖请勾选“允许覆盖”")
                saved = save_card(clean, replace_built_in=False)
                results["imported"].append(saved["record"]["name"])
        except LibraryError as exc:
            results["failed"].append({"row": index, "name": label, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001  单条失败不影响整批
            results["failed"].append({"row": index, "name": label, "error": f"无效记录：{exc}"})
    return results
