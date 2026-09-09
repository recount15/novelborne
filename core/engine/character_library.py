"""Database-authoritative character library; JSON is explicit import/export only."""
from __future__ import annotations

import json
import re
import threading
from uuid import uuid4
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
    """Generate an opaque identity, independent of mutable display names."""
    return f"{_USER_PREFIX}{uuid4().hex}"


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
            if isinstance(payload.get("relationship_vector"), (dict, Mapping, list, tuple))
            else _clip_text(payload.get("relationship_vector"))
        ),
        "knowledge_scope": list(_clip_list(payload.get("knowledge_scope"))),
        "voice": _clip_text(payload.get("voice")),
        "unacceptable_actions": list(_clip_list(payload.get("unacceptable_actions"))),
        "background": _clip_text(payload.get("background")),
        "skill_ids": list(_clip_list(payload.get("skill_ids"))),
        "source": (dict(payload["source"]) if isinstance(payload.get("source"), Mapping)
                   else _clip_text(payload.get("source") or "用户自定义")),
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
    # Preserve extended structured DTO fields without truncating them into legacy text.
    for key, value in payload.items():
        if key not in record and key not in {"target_id", "id", "character_id"}:
            record[key] = value
    for key in ("source", "profile", "semantic", "facts", "aliases", "relationships", "evidence", "quality",
                "mind_model", "decision_policy", "voice_transfer", "behavior_boundaries"):
        if key in payload:
            record[key] = payload[key]
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
    from core.engine import character_db as db
    return [c for c in db.get_all_characters() if db.get_character_source_type(c.id) in {"builtin", "override"}]


def load_user_cards() -> tuple[list[CharacterCard], list[CharacterCard]]:
    from core.engine import character_db as db
    added, overrides = [], []
    for card in db.get_all_characters():
        kind = db.get_character_source_type(card.id)
        if kind == "user":
            added.append(card)
        elif kind == "override":
            overrides.append(card)
    return added, overrides


def merged_pool() -> tuple[tuple[CharacterCard, ...], set[str]]:
    """Database-only runtime pool; no file merge and no fallback on errors."""
    from core.engine import character_db as db
    cards = tuple(db.get_all_characters())
    return cards, {c.id for c in cards if db.get_character_source_type(c.id) == "override"}


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
    from core.engine.catalog import clear_catalog_cache
    clear_catalog_cache()
    try:
        from core.services import registries

        registries.invalidate_character_pool_cache()
    except Exception:  # noqa: BLE001  缓存失效是尽力而为
        pass


def validate_card_record(payload: Mapping[str, Any]) -> None:
    """Validate structured card claims against authoritative chapter sources."""
    def secrets(value):
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in {"api_key", "apikey", "token", "access_token", "secret", "password", "authorization", "credentials"}:
                    raise LibraryError("角色卡不能包含凭据")
                secrets(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                secrets(item)
    secrets(payload)
    if payload.get("schema_version") in (2, "2"):
        for key in ("source", "profile", "semantic", "quality"):
            if not isinstance(payload.get(key), Mapping):
                raise LibraryError(f"Card v2 缺少结构字段 {key}")
        if payload["source"].get("origin") not in {"source_extracted", "user_created", "model_created", "imported"}:
            raise LibraryError("source.origin 无效")
        if payload["quality"].get("state") not in {"draft", "ready", "review_required", "failed"}:
            raise LibraryError("quality.state 无效")
        for key in ("aliases", "facts", "relationships", "evidence"):
            if not isinstance(payload.get(key), list):
                raise LibraryError(f"Card v2 缺少数组字段 {key}")
    evidence = payload.get("evidence", [])
    ids = set()
    for item in evidence:
        if not isinstance(item, Mapping) or not item.get("evidence_id") or item["evidence_id"] in ids:
            raise LibraryError("evidence_id 缺失或重复")
        ids.add(item["evidence_id"])
        book_id = str(item.get("book_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", book_id):
            raise LibraryError("证据 book_id 无效")
        start, end, chapter = item.get("start"), item.get("end"), item.get("chapter_no")
        if any(type(v) is not int for v in (start, end, chapter)) or start < 0 or end <= start or chapter < 1:
            raise LibraryError("证据范围无效")
        from core.services.book_search_service import _load_sources
        from core.services.book_prepare_service import _hash
        from core.engine.book_index import checksum
        from core.engine.character_db import DATABASE_PATH
        root = DATABASE_PATH.parent.parent / "books"
        directory = (root / book_id).resolve()
        if not directory.is_relative_to(root.resolve()):
            raise LibraryError("证据目录无效")
        try:
            _, sources = _load_sources(directory, book_id)
            text = dict(sources)[chapter]
            source_hash = _hash([{"chapter_no": no, "checksum": checksum(content), "chars": len(content)} for no, content in sources])
        except Exception as exc:
            raise LibraryError("无法核验角色证据原文") from exc
        if item.get("source_hash") != source_hash or end > len(text) or text[start:end] != item.get("quote"):
            raise LibraryError("角色证据与原文不一致")
        if not item.get("block_id"):
            raise LibraryError("证据缺少 block_id")
        try:
            from core.services.book_prepare_service import _sources
            from core.engine.book_index import load_index
            index_path = (directory / "book_index.json").resolve()
            if not index_path.is_relative_to(directory):
                raise ValueError("unsafe index")
            inventory_sources, blocks = _sources(directory, load_index(directory))
            block = next((b for b in blocks if b["block_id"] == item["block_id"]), None)
            if (_hash(inventory_sources) != source_hash or block is None or
                    block["chapter_no"] != chapter or start < block["start"] or end > block["end"]):
                raise ValueError("block mismatch")
        except Exception as exc:
            raise LibraryError("角色证据 block_id 与原文索引不一致") from exc

    if payload.get("schema_version") in (2, "2"):
        allowed_profile = {"identity", "appearance", "habits", "personality", "values", "boundaries", "motivations", "goals", "background"}
        if set(payload["profile"]) - allowed_profile:
            raise LibraryError("profile 包含未知领域")
        for field, conclusions in payload["semantic"].items():
            if field not in {"mind_model", "decision_policy", "voice_transfer", "behavior_boundaries"}:
                raise LibraryError("semantic 包含未知维度")
            if not isinstance(conclusions, list):
                raise LibraryError("semantic 结论必须为结构数组")
            for conclusion in conclusions:
                if not isinstance(conclusion, Mapping) or conclusion.get("epistemic_kind") not in {"observation", "inference", "authored"}:
                    raise LibraryError("semantic 缺少知识类型")
                refs = conclusion.get("evidence_ids")
                if not isinstance(refs, list) or not set(refs).issubset(ids):
                    raise LibraryError("semantic 证据引用无效")
                if conclusion["epistemic_kind"] != "authored" and not refs:
                    raise LibraryError("非创作结论必须引用证据")
        fact_ids = set()
        for fact in payload["facts"]:
            if not isinstance(fact, Mapping) or not all(fact.get(k) for k in ("fact_id", "domain", "predicate", "subject_id", "status")):
                raise LibraryError("fact 缺少必需结构")
            if fact["fact_id"] in fact_ids or "value" not in fact:
                raise LibraryError("fact_id 重复或缺少 value")
            fact_ids.add(fact["fact_id"])
        relation_ids = set()
        for relation in payload["relationships"]:
            if not isinstance(relation, Mapping) or not all(relation.get(k) for k in ("relation_id", "source_character_id", "type")):
                raise LibraryError("relationship 缺少必需结构")
            if relation["relation_id"] in relation_ids or not (relation.get("target_character_id") or relation.get("unresolved_target")):
                raise LibraryError("relationship 身份缺失或重复")
            relation_ids.add(relation["relation_id"])
        if payload["quality"]["state"] == "ready" and (not payload["profile"].get("identity") or not payload["quality"].get("checks")):
            raise LibraryError("ready 卡必须包含身份与校验记录")
    def boundary(value, label):
        if value is None:
            return None
        if not isinstance(value, Mapping) or type(value.get("chapter_no")) is not int or type(value.get("offset")) is not int:
            raise LibraryError(f"{label} 必须为章节/码点边界")
        if value["chapter_no"] < 1 or value["offset"] < 0:
            raise LibraryError(f"{label} 边界超出范围")
        if "source_hash" in value and not isinstance(value["source_hash"], str):
            raise LibraryError(f"{label} source_hash 无效")
        return value["chapter_no"], value["offset"]
    for fact in payload.get("facts", []):
        start = boundary(fact.get("valid_from"), "valid_from")
        end = boundary(fact.get("valid_until"), "valid_until")
        if start is not None and end is not None and end < start:
            raise LibraryError("事实时间范围倒置")
        if "certainty" in fact and fact["certainty"] is not None:
            certainty = fact["certainty"]
            if type(certainty) not in (int, float) or not 0 <= certainty <= 1:
                raise LibraryError("certainty 必须为 0..1")
        if "status" in fact and fact["status"] not in {"confirmed", "unknown", "uncertain", "disputed", "inferred", "not_applicable"}:
            raise LibraryError("fact.status 无效")
        if "knowledge_holder_id" in fact and fact["knowledge_holder_id"] is not None and not isinstance(fact["knowledge_holder_id"], str):
            raise LibraryError("knowledge_holder_id 必须为身份字符串")
        predicate = fact.get("predicate")
        if predicate in {"is_alive", "is_dead", "has_ability", "owns"} and fact.get("status") == "confirmed" and type(fact.get("value")) is not bool:
            raise LibraryError("布尔事实必须使用布尔值")
    for relation in payload.get("relationships", []):
        boundary(relation.get("effective_boundary"), "effective_boundary")
        for field in ("source_character_id", "target_character_id", "unresolved_target", "type", "stance"):
            if field in relation and relation[field] is not None and not isinstance(relation[field], str):
                raise LibraryError(f"relationship.{field} 必须为字符串")
    for key in ("facts", "relationships"):
        for item in payload.get(key, []):
            if not isinstance(item, Mapping) or not isinstance(item.get("evidence_ids", []), list):
                raise LibraryError(f"{key} 结构无效")
            if not set(item.get("evidence_ids", [])).issubset(ids):
                raise LibraryError(f"{key} 引用了不存在的证据")


def save_card(payload: Mapping[str, Any], *, replace_built_in: bool = False,
              allow_name_change: bool = True) -> dict[str, Any]:
    """Commit one authoritative DB record; names never determine identity."""
    from core.engine import character_db as db
    with _LOCK:
        current_id = str(payload.get("target_id") if replace_built_in else payload.get("id") or payload.get("character_id") or "").strip()
        previous = db.get_character_record(current_id) if current_id else None
        if replace_built_in and previous is None:
            raise LibraryError(f"找不到要替换的内置角色：{current_id}")
        incoming = {**(previous or {}), **payload}
        if "idempotency_key" not in payload:
            incoming.pop("idempotency_key", None)
        if previous and not allow_name_change:
            incoming["name"] = previous["name"]
        validate_card_record(incoming)
        record = build_record(incoming)
        record["id"] = current_id or ("" if record.get("idempotency_key") else user_card_id(record["name"]))
        if previous and replace_built_in:
            record["role"] = previous["role"]
        if previous and "base_revision" not in record:
            record["base_revision"] = previous.get("revision")
        source_type = "override" if replace_built_in else (db.get_character_source_type(current_id) or "user")
        try:
            record = db.save_character_record(record, source_type)
        except db.DatabaseError as exc:
            raise LibraryError(str(exc)) from exc
    refresh_game_cache()
    return {"record": record, "path": db.DATABASE_PATH,
            "origin": "replace_built_in" if replace_built_in else "user_created",
            "saved": True, "character_id": record["id"], "revision": record["revision"], "card": record}


def _lookup_user_name(card_id: str) -> str | None:
    card = get_character_by_id_from_db(card_id)
    return card.name if card else None


def _find_by_name(role: str, name: str) -> CharacterCard | None:
    added, _ = load_user_cards()
    return next((c for c in added if c.role == role and c.name == name), None)


def get_character_by_id_from_db(card_id: str) -> CharacterCard | None:
    from core.engine.character_db import get_character_by_id
    return get_character_by_id(card_id)


def update_card(card_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if get_character_by_id_from_db(card_id) is None:
        raise LibraryError(f"找不到角色卡：{card_id}")
    return save_card({**payload, "id": card_id})


def delete_card(card_id: str) -> dict[str, Any]:
    """Retire the DB card; never restore a deleted override from JSON."""
    from core.engine import character_db as db
    with _LOCK:
        kind = db.get_character_source_type(card_id)
        if kind is None:
            raise LibraryError(f"找不到角色卡：{card_id}")
        try:
            db.delete_character(card_id, soft_delete=True)
        except db.DatabaseError as exc:
            raise LibraryError(str(exc)) from exc
    refresh_game_cache()
    return {"removed": kind, "id": card_id}


def export_payload(card_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """导出为可与内置池互转的单体 JSON；默认导出全部用户侧卡。"""
    pool, _ = merged_pool()
    wanted = {str(i).strip() for i in (card_ids or []) if str(i).strip()}
    selected: list[dict[str, Any]] = []
    for card in pool:
        from core.engine.character_db import get_character_source_type
        is_user = get_character_source_type(card.id) in {"user", "override"}
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
    from core.engine.character_db import get_character_record
    return get_character_record(card.id)


def import_records(rows: Iterable[Mapping[str, Any]], *, overwrite: bool = False) -> dict[str, Any]:
    """Explicit JSON records import: IDs, never names, identify existing cards."""
    from core.engine import character_db as db
    results: dict[str, Any] = {"imported": [], "replaced": [], "failed": []}
    for index, row in enumerate(rows or [], start=1):
        label = str(row.get("name") or f"第{index}条")
        try:
            row_id = str(row.get("id") or row.get("character_id") or "").strip()
            existing = db.get_character_record(row_id) if row_id else None
            if existing is not None and not overwrite:
                raise LibraryError("覆盖现有卡需要 overwrite")
            clean = dict(row)
            if existing is not None:
                clean["id"] = row_id
                clean["base_revision"] = existing.get("revision")
            else:
                # Imported foreign identity is not trusted as a new server identity.
                clean.pop("id", None)
                clean.pop("character_id", None)
                clean.pop("base_revision", None)
            saved = save_card(clean)
            results["replaced" if existing else "imported"].append(saved["record"]["name"])
        except Exception as exc:
            results["failed"].append({"row": index, "name": label, "error": str(exc)})
    return results
