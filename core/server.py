"""Vue 主前端的最小 FastAPI 桥接。

启动：项目根下 ``python run_app.py``（或 ``uvicorn core.server:app``）。
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# 项目结构（2026-08-28 统一整理）：全部引擎代码集中于 core/ 包，
# 本服务通过 core.* 绝对导入，不再依赖 sys.path 修补。
CORE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CORE_DIR.parent

import core.engine.chapter_tools  # noqa: E402
import core.engine.cheat_code  # noqa: E402
import core.engine.character_library  # noqa: E402
import core.engine.novel_exporter  # noqa: E402
import core.engine.quest  # noqa: E402
import core.engine.skill_drift  # noqa: E402
import core.engine.break_anchor  # noqa: E402
import core.engine.persistence  # noqa: E402
import core.engine.work_distiller  # noqa: E402
from core import app as gradio_app  # noqa: E402  (老版对局流程)
from core import fate_engine as fe  # noqa: E402  (模型接入层)
from core import engine  # noqa: E402
from core.services import registries  # noqa: E402  (跨层共享注册表，中立层)
from core.services import ask_service  # noqa: E402  (ask 端点业务逻辑，Phase 3b)
from core.engine.distill import distill_model  # noqa: E402  (从老版 app._distill_model 提炼)
from core.api.contracts import gradio_state_from_output, public_state, stream_event_from_gradio  # noqa: E402
from core.api.sessions import SessionManager, read_upload  # noqa: E402
from core.engine import catalog  # noqa: E402
from core.engine import character_designer  # noqa: E402
from core.engine import gf_designer  # noqa: E402


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
sessions = SessionManager(PROJECT_ROOT)
app = FastAPI(title="书中行 API", version="2.0.0")


class StartRequest(BaseModel):
    session_id: str | None = None
    provider: str = "deepseek"
    base_url: str | None = None
    api_key: str = ""
    model: str | None = None
    thinking_mode: str = "auto"
    thinking_param: str = ""
    mode: str = "基础模式"
    work: str | None = None
    novel_upload_id: str | None = None
    fragment: str = ""
    role: str = ""
    protagonist_gender: str = "unknown"
    timepoint: str = "故事开篇"
    difficulty: str = "D4 普通"
    golden_finger: str | None = None
    golden_finger_proposal: dict[str, Any] = Field(default_factory=dict)
    persona_preset: str = "自定义（在下方文本框描述）"
    persona_custom: str = ""
    persona_upload_id: str | None = None
    distill_enabled: bool = True
    companion_roster: list[dict[str, Any]] = Field(default_factory=list)
    heroine_roster: list[dict[str, Any]] = Field(default_factory=list)
    companion_count: int = 0
    heroine_count: int = 0
    heroine_mode: str = "单女主"
    enable_nemesis: bool = False
    nemesis_select: str = ""
    nemesis_upload_id: str | None = None
    convergence: str = "较高"
    # 故事丰富度：玩家拖动的单回合叙事体量刻度（300–1000）。
    story_richness: int = Field(
        default=engine.participation.RICHNESS_DEFAULT,
        ge=engine.participation.RICHNESS_MIN,
        le=engine.participation.RICHNESS_MAX,
    )
    # 类 Agent 生成模式：draft → 自检 → 定向修订循环（仅强化模式生效）。
    story_agent_mode: bool = False
    # 四栏角色卡选择（规格 §7）：[{"slot": "主角|主线|伙伴|宿敌", "card_id": ...}]，可选。
    roster_card_ids: list[dict[str, Any]] = Field(default_factory=list)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    thinking_mode: str | None = None
    thinking_param: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class QuestOfferRequest(BaseModel):
    kind: str = "short"
    difficulty: float = 0.5


class SaveRequest(BaseModel):
    save_id: str = Field(default="latest", min_length=1, max_length=96)


class LoadRequest(BaseModel):
    save_id: str = Field(default="latest", min_length=1, max_length=96)


class ExportNovelRequest(BaseModel):
    style: str = ""
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class GoldenFingerContext(BaseModel):
    world: str = ""
    persona: str = ""
    difficulty: str = "D4 普通"
    # 宿敌强度 D（浮点 0.01–9.99，越小越强）；驱动 GF(D)=D^1.15 缩放。
    # None 表示尚未确定宿敌（前端应禁止此时生成金手指）。
    nemesis_d: float | None = Field(default=None, ge=0.01, le=9.99)


class GoldenFingerProposalRequest(GoldenFingerContext):
    text: str = Field(min_length=1, max_length=1000)
    attempt: int = Field(default=1, ge=1, le=3)


class GoldenFingerConfirmRequest(BaseModel):
    proposal: dict[str, Any]


class ModelFetchRequest(BaseModel):
    provider: str = "deepseek"
    base_url: str | None = None
    api_key: str = ""


class ModelTestRequest(ModelFetchRequest):
    model: str | None = None


class DesignerGenerateRequest(BaseModel):
    identity: dict[str, Any] = Field(default_factory=dict)
    corpus: list[dict[str, Any]] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    session_id: str | None = None


class DesignerSaveRequest(BaseModel):
    persona_markdown: str = Field(min_length=1, max_length=fe.MAX_PERSONA_CHARS)
    filename: str = Field(min_length=1, max_length=80)


class CharacterLibraryUpsertRequest(BaseModel):
    """角色库新增/更新请求；字段口径与内置角色池 schema 一致。"""
    name: str = Field(min_length=1, max_length=40)
    role: str = "伙伴"
    work: str = ""
    archetype: str = ""
    desire: str = ""
    fear: str = ""
    abilities: list[str] | str = ""
    relationship_vector: dict[str, Any] | str = ""
    knowledge_scope: list[str] | str = ""
    voice: str = ""
    unacceptable_actions: list[str] | str = ""
    background: str = ""
    skill_ids: list[str] | str = ""
    source: str = ""
    gender: str = "unknown"
    original_position: str = ""
    source_medium: str = ""
    source_region: str = ""
    slot_keys: dict[str, Any] = Field(default_factory=dict)
    protagonist_type: str = ""
    mainline_type: str = ""
    partner_type: str = ""
    nemesis_type: str = ""


class CharacterLibraryImportRequest(BaseModel):
    characters: list[dict[str, Any]] = Field(default_factory=list)
    overwrite: bool = False


class GfComposeRequest(BaseModel):
    composition: str = ""
    fuels: list[Any] = Field(default_factory=list)
    cost: str = ""
    cooldown: str = ""
    difficulty: str = ""
    name: str = ""
    effect: str = ""
    scope: str = ""
    fit: str = ""
    world: str = ""
    draft: dict[str, Any] = Field(default_factory=dict)


class GfPolishRequest(GfComposeRequest):
    spec: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    session_id: str | None = None


class GfSaveRequest(BaseModel):
    spec: dict[str, Any] = Field(default_factory=dict)
    draft: dict[str, Any] = Field(default_factory=dict)

ALLOWED_UPLOAD_SUFFIXES = {".txt", ".md", ".markdown"}
UPLOAD_KINDS = {"novel", "persona", "roster-skill", "nemesis"}

# 角色设计器保存 persona 文件时的写盘互斥锁（防并发同名竞态）。
_DESIGNER_SAVE_LOCK = threading.Lock()


def _provider_payload() -> list[dict[str, Any]]:
    return [
        {"id": ident, "label": cfg["label"], "base_url": cfg["base_url"], "models": list(cfg.get("models", []))}
        for ident, cfg in fe.PROVIDERS.items()
    ]


def _skills_payload() -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "role": item.role,
            "name": item.name,
            "summary": item.summary,
            "capabilities": list(item.capabilities),
            "limits": list(item.limits),
            "tags": list(item.tags),
            "source": item.source,
        }
        for item in catalog.load_skill_catalog()
    ]


def _characters_payload(pool=None) -> list[dict[str, Any]]:
    cards = pool if pool is not None else catalog.load_character_pool()
    return [
        {
            "id": item.id,
            "role": item.role,
            "name": item.name,
            "work": item.work,
            "gender": item.gender,
            "original_position": item.original_position,
            "source_medium": item.source_medium,
            "source_region": item.source_region,
            "slot_keys": {key: list(values) for key, values in item.slot_keys.items()},
            "protagonist_type": ",".join(item.slot_keys.get("主角栏", ())),
            "companion_type": ",".join(item.slot_keys.get("伴侣栏", ())),
            "mainline_type": ",".join(item.slot_keys.get("伴侣栏", ())),
            "partner_type": ",".join(item.slot_keys.get("伙伴栏", ())),
            "nemesis_type": ",".join(item.slot_keys.get("宿敌栏", ())),
            "archetype": item.archetype,
            "desire": item.desire,
            "fear": item.fear,
            "abilities": list(item.abilities),
            "relationship_vector": dict(item.relationship_vector),
            "knowledge_scope": list(item.knowledge_scope),
            "voice": item.voice,
            "unacceptable_actions": list(item.unacceptable_actions),
            "background": item.background,
            "skill_ids": list(item.skill_ids),
            "source": item.source,
        }
        for item in cards
    ]


def _difficulty_int(difficulty: Any) -> int:
    """从 "D4 普通" 等文本中取整数难度（1–9，缺省 4）。"""
    for chunk in re.findall(r"\d+", str(difficulty or "")):
        return max(1, min(9, int(chunk)))
    return 4


def bootstrap_payload() -> dict[str, Any]:
    """只返回初始化目录和选项；绝不返回 API key 或 secret。"""
    models = [
        {"label": label, "path": path}
        for label, path in fe.list_character_models()
    ]
    pool, shadowed = engine.character_library.merged_pool_cached()
    return {
        "providers": _provider_payload(),
        "works": list(fe.list_works()),
        "skills": _skills_payload(),
        "character_pools": _characters_payload(pool),
        "custom_character_ids": sorted(
            card.id for card in pool
            if card.id.startswith("user-")
            or (engine.character_library.OVERRIDES_DIR / f"{card.id}.json").is_file()),
        "character_models": [{"label": item["label"]} for item in models],
        "personas": list(fe.PERSONAS),
        "modes": ["基础模式", "强化模式"],
        "difficulties": list(fe.DIFFICULTIES),
        "golden_fingers": list(fe.GOLDEN_FINGERS),
        "heroine_modes": ["单女主", "多女主"],
        # 故事丰富度刻度与档位说明由后端统一定义，前端只负责渲染与拖动。
        "story_richness": {
            "min": engine.participation.RICHNESS_MIN,
            "max": engine.participation.RICHNESS_MAX,
            "step": engine.participation.RICHNESS_STEP,
            "default": engine.participation.RICHNESS_DEFAULT,
            "tiers": [
                {"upper": bound, "label": label, "note": note}
                for bound, label, note in engine.participation.RICHNESS_TIERS
            ],
        },
        # 类 Agent 模式的说明文案统一下发，前端不做硬编码。
        "story_agent_mode": {
            "label": "类 Agent 生成",
            "note": "开启后每回合先起草，再经质检自检与定向修订后才提交；质量更稳但耗时与 token 约为两倍。仅强化模式生效。",
        },
        "golden_finger_library": [
            {"id": item["id"], "label": item["label"]}
            for item in gf_designer.list_specs()
        ],
        "counts": {
            "works": len(list(fe.list_works())),
            "character_pools": len(pool),
            "personas": len(list(fe.PERSONAS)),
            "character_models": len(models),
        },
    }


_SLOT_ALIASES = {"主角": "主角栏", "伙伴": "伙伴栏", "女主": "伴侣栏", "男主": "伴侣栏",
                 "主线": "伴侣栏", "主线栏": "伴侣栏", "伴侣": "伴侣栏", "宿敌": "宿敌栏"}


def _normalize_pool_slot(slot: str) -> str:
    """把栏名别名归一为规格 §2 的四个栏位名；未知值回落宿敌栏（全库）。

    四类角色皆从整个角色池选取；"主线栏"是历史叫法，现统一为"伴侣栏"。
    """
    slot = str(slot or "").strip()
    if slot in ("主角栏", "伴侣栏", "伙伴栏", "宿敌栏"):
        return slot
    return _SLOT_ALIASES.get(slot, "宿敌栏")


def _pool_card_entry(card) -> dict[str, Any]:
    return {
        "id": card.id,
        "name": card.name,
        "gender": card.gender,
        "work": card.work,
        "source_medium": card.source_medium,
        "source_region": card.source_region,
        "archetype": card.archetype,
        "original_position": card.original_position,
        "slot_keys": {key: list(values) for key, values in card.slot_keys.items()},
        "protagonist_type": list(card.protagonist_type),
        "mainline_type": list(card.mainline_type),
        "partner_type": list(card.partner_type),
        "nemesis_type": list(card.nemesis_type),
        # 悬停/选中简介：全部可选、向后兼容，缺失给空值由前端兜底
        "one_line": getattr(card, "one_line", "") or "",
        "background": getattr(card, "background", "") or "",
        "desire": getattr(card, "desire", "") or "",
        "abilities": list(getattr(card, "abilities", ()) or ()),
    }


def _slot_candidates(cards: list[Any], slot: str, gender: str | None) -> list[Any]:
    """四栏统一两级选取的第一层：全池 + 栏位排序。

    四类角色（主角/伴侣/伙伴/宿敌）都从整个角色池选取。
    2026-08-30 起破除性别栏杆：任何栏位均不按性别过滤——卡和性格都只是
    「魂」，叙事以附身角色（书中身体）的生理性别为准。gender 参数保留
    仅为兼容旧调用签名，不再参与过滤。
    """
    if slot == "主角栏":
        rows = list(cards)
        rows.sort(key=lambda card: (card.original_position != "主角", card.name))
        return rows
    if slot == "伴侣栏":
        rows = list(cards)
        rows.sort(key=lambda card: (card.original_position != "女主" and card.original_position != "男主", card.name))
        return rows
    return list(cards)


# 来源一级分组固定顺序（original_position 取值）
_SOURCE_ORDER = ("主角", "男主", "女主", "配角", "反派")


def _source_key(card) -> str:
    """一级分组键：卡的原作定位；未标注进"未标注"且排最后。"""
    value = str(getattr(card, "original_position", "") or "").strip()
    return value or "未标注"


def _group_pool(rows: list[Any], slot: str) -> list[dict[str, Any]]:
    """两级分组：第一级按来源（original_position），第二级按栏位 slot_keys 分类。

    返回结构：
    ``[{"key": 来源, "sub_groups": [{"key": 分类, "cards": [...]}, ...]}, ...]``
    """
    by_source: dict[str, list[Any]] = {}
    for card in rows:
        by_source.setdefault(_source_key(card), []).append(card)

    def _source_sort(key: str) -> tuple[int, str]:
        try:
            return (_SOURCE_ORDER.index(key), "")
        except ValueError:
            return (len(_SOURCE_ORDER), key)

    grouped: list[dict[str, Any]] = []
    for source in sorted(by_source, key=_source_sort):
        cards_in_source = by_source[source]
        by_type: dict[str, list[Any]] = {}
        for card in cards_in_source:
            raw_keys = card.slot_keys.get(slot) or ("通用",)
            if isinstance(raw_keys, str):
                raw_keys = (raw_keys,)
            type_keys = [str(k).strip() for k in raw_keys if str(k).strip()] or ["通用"]
            for type_key in type_keys:
                by_type.setdefault(type_key, []).append(card)
        sub_groups = [
            {"key": type_key, "cards": [_pool_card_entry(card) for card in cards]}
            for type_key, cards in sorted(by_type.items())
        ]
        grouped.append({"key": source, "sub_groups": sub_groups})
    return grouped


@app.get("/api/characters/pool")
def characters_pool(slot: str = "宿敌栏", gender: str | None = None,
                    q: str | None = None) -> dict[str, Any]:
    """四栏角色池候选：两级分组（来源 → 分类）。

    四类角色都从整个角色池选取；先按来源（主角/男主/女主/配角/反派）分组，
    再按该栏位 slot_keys 细分二级分类。

    - ``slot``：栏位（主角栏/伴侣栏/伙伴栏/宿敌栏）。
    - ``gender``：历史兼容参数，已不参与过滤（2026-08-30 起四栏均破除性别栏杆）。
    - ``q``：可选搜索词，按名字/出处/原型模糊匹配。
    """
    slot_name = _normalize_pool_slot(slot)
    gender_value = (gender or "").strip().lower()
    if gender_value and gender_value not in ("male", "female"):
        raise HTTPException(status_code=400, detail="gender 只支持 male 或 female")
    pool, _shadowed = engine.character_library.merged_pool_cached()
    cards = list(pool) or list(catalog.load_character_pool())
    rows = _slot_candidates(cards, slot_name, gender_value)
    keyword = (q or "").strip().lower()
    if keyword:
        rows = [card for card in rows if keyword in f"{card.name}{card.work}{card.archetype}".lower()]
    groups = _group_pool(rows, slot_name)
    total = sum(len(sub["cards"]) for group in groups for sub in group["sub_groups"])
    payload: dict[str, Any] = {
        "slot": slot_name,
        "total": total,
        "keys": groups,
    }
    return payload


@app.get("/api/characters/pool/{card_id}/detail")
def characters_pool_detail(card_id: str, slot: str = "宿敌栏") -> dict[str, Any]:
    """单卡完整简介：悬停/选中时渲染角色简介卡。

    返回原型、出处、定位、适配类型与简介；卡不存在返回 404。
    """
    slot_name = _normalize_pool_slot(slot)
    pool, _shadowed = engine.character_library.merged_pool_cached()
    cards = list(pool) or list(catalog.load_character_pool())
    card = next((item for item in cards if item.id == card_id), None)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色卡不存在：{card_id}")
    entry = _pool_card_entry(card)
    entry["slot_types"] = list(card.slot_keys.get(slot_name) or ("通用",))
    entry["original_position"] = card.original_position or "未标注"
    return entry


def _restore_session_from_disk(session_id: str):
    """页面刷新/服务重启后的会话回填：按 session_id 找磁盘上最新存档重建会话。

    强化/基础模式在开局与每回合都会落盘存档（latest 等），state 完整可恢复；
    凭据不落盘，恢复后由玩家在连接区重填（或请求体携带）。任何失败都返回
    None 交由调用方按 404 处理，绝不抛错阻断。
    """
    try:
        saves = engine.persistence.list_saves(root=fe.WRITABLE_DIR)
        candidates = [item for item in saves if item.get("session_id") == session_id]
        if not candidates:
            return None
        latest = max(candidates, key=lambda item: str(item.get("saved_at") or ""))
        restored = engine.persistence.load_state(str(latest.get("save_id") or "latest"),
                                                 root=fe.WRITABLE_DIR)
        if not restored or not restored.get("system"):
            return None
        # 存档里不带 game_ready（它是流式提交时才写入内存的），
        # 回填时与 load 端点一致置位，否则前端会把恢复后的会话误判为未开局。
        restored["game_ready"] = True
        session = sessions.create(session_id)
        session.state = restored
        return session
    except Exception:  # noqa: BLE001  恢复失败按不存在处理，不阻断请求
        return None


def _session_or_404(session_id: str):
    try:
        return sessions.require(session_id)
    except KeyError as exc:
        restored = _restore_session_from_disk(session_id)
        if restored is not None:
            return restored
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _game_ready(state: Any) -> bool:
    """只有含系统规则和至少一条助手消息的状态才算完成开局。"""
    if not isinstance(state, dict) or not state.get("system"):
        return False
    if state.get("game_ready") is True:
        return True
    history = state.get("history")
    return isinstance(history, list) and any(
        isinstance(item, dict)
        and item.get("role") == "assistant"
        and str(item.get("content") or "").strip()
        for item in history
    )


def _require_game(session) -> dict[str, Any]:
    if not _game_ready(session.state):
        raise HTTPException(status_code=400, detail="当前 session 尚未开始对局")
    return session.state


def _upload_or_404(session, upload_id: str | None) -> str | None:
    if not upload_id:
        return None
    try:
        return str(sessions.upload_path(session, upload_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _stream_response(
    session,
    generator: Iterator[Any],
    *,
    operation: str,
    api_key_on_commit: str | None = None,
) -> StreamingResponse:
    def body() -> Iterator[bytes]:
        previous_assistant = ""
        previous_state = dict(session.state)
        start_committed = operation != "start"
        try:
            for output in generator:
                raw_state = gradio_state_from_output(output)
                event = stream_event_from_gradio(output)
                data = event.data
                chat = data.get("chat") if isinstance(data, dict) else None

                if operation == "start" and raw_state is not None and not raw_state.get("system"):
                    session.state = previous_state
                    message = str(data.get("status") or "开局失败，请检查设定后重试")
                    yield (json.dumps({
                        "type": "error",
                        "data": {"operation": operation, "message": message},
                    }, ensure_ascii=False) + "\n").encode("utf-8")
                    return

                if raw_state is not None:
                    ready = _game_ready(raw_state)
                    if operation != "start" or ready:
                        session.state = dict(raw_state)
                        session.state["game_ready"] = True
                        # 会话标识注入 state：自动存档按会话隔离归属。
                        session.state.setdefault("session_id", session.session_id)
                        if operation == "start" and api_key_on_commit is not None:
                            session.api_key = api_key_on_commit
                        start_committed = True
                    if isinstance(data.get("state"), dict):
                        data["state"]["game_ready"] = ready
                if operation != "start" or start_committed:
                    data["session_id"] = session.session_id
                data["operation"] = operation
                assistant = ""
                if isinstance(chat, list) and chat:
                    last = chat[-1]
                    if isinstance(last, dict) and last.get("role") == "assistant":
                        assistant = str(last.get("content") or "")
                if assistant and assistant.startswith(previous_assistant):
                    data["delta"] = assistant[len(previous_assistant):]
                elif assistant:
                    data["delta"] = assistant
                previous_assistant = assistant
                yield (json.dumps({"type": event.type, "data": data}, ensure_ascii=False) + "\n").encode("utf-8")
            if operation == "start" and not start_committed:
                session.state = previous_state
                yield (json.dumps({
                    "type": "error",
                    "data": {"operation": operation, "message": "开局未生成有效状态，请重试"},
                }, ensure_ascii=False) + "\n").encode("utf-8")
                return
            yield (json.dumps({
                "type": "done",
                "data": {"session_id": session.session_id, "operation": operation},
            }, ensure_ascii=False) + "\n").encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            if operation == "start":
                session.state = previous_state
            yield (json.dumps({
                "type": "error",
                "data": {"operation": operation, "message": str(exc)},
            }, ensure_ascii=False) + "\n").encode("utf-8")
        finally:
            close = getattr(generator, "close", None)
            if callable(close):
                close()
            sessions.release(session)

    return StreamingResponse(body(), media_type="application/x-ndjson")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "fate-engine-api", "version": app.version}


def _lan_addresses() -> list[str]:
    """本机局域网 IPv4 候选（排除回环）。

    用 UDP connect 技巧取默认路由出口 IP（不实际发包）；拿不到时退化为
    解析主机名。多网卡时全部返回，二维码取第一个（通常是主网卡）。
    """
    addrs: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                addrs.append(ip)
    except OSError:
        pass
    if not addrs:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip and not ip.startswith("127.") and ip not in addrs:
                    addrs.append(ip)
        except OSError:
            pass
    return addrs


def _lan_info(port: int | None) -> dict[str, Any]:
    addresses = _lan_addresses()
    port = int(port or 8000)
    primary = addresses[0] if addresses else None
    return {
        "addresses": addresses,
        "port": port,
        "url": f"http://{primary}:{port}" if primary else None,
        "listening_lan": os.getenv("FATE_API_HOST", "127.0.0.1") not in {"127.0.0.1", "localhost"}
        or _LAN_LISTENING,
        "hint": "手机与电脑连同一 Wi-Fi 后扫码访问；若无法打开，请检查系统防火墙是否放行该端口。"
                if primary else "未检测到局域网地址：请确认电脑已连接 Wi-Fi/路由器。",
    }


# uvicorn 实际监听地址是否为全接口（启动时由 run_app 写入；测试与直接
# uvicorn core.server 场景下保持默认推断）。
_LAN_LISTENING = os.getenv("FATE_API_HOST", "127.0.0.1") in {"0.0.0.0", "::"}


@app.get("/api/lan-info")
def lan_info(request: Request) -> dict[str, Any]:
    """局域网访问信息：地址、端口、二维码链接（手机扫码远程使用）。"""
    return _lan_info(request.url.port)


@app.get("/api/lan-qrcode.png", include_in_schema=False)
def lan_qrcode(request: Request) -> Response:
    """局域网访问二维码（内容 http://<局域网IP>:<端口>）。"""
    info = _lan_info(request.url.port)
    url = info.get("url")
    if not url:
        raise HTTPException(status_code=503, detail="未检测到局域网地址，无法生成二维码")
    try:
        import io

        import qrcode
    except ImportError as exc:  # noqa: BLE001  qrcode 为可选依赖
        raise HTTPException(status_code=501, detail="服务端未安装 qrcode 库：pip install qrcode") from exc
    img = qrcode.make(url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, Any]:
    return bootstrap_payload()


@app.post("/api/golden-fingers/recommend")
def recommend_golden_fingers(request: GoldenFingerContext) -> dict[str, Any]:
    nemesis_d = request.nemesis_d if request.nemesis_d is not None else 10.0 - _difficulty_int(request.difficulty)
    recommendations = engine.recommend(request.world, request.persona, request.difficulty,
                                       nemesis_d=nemesis_d)
    specs = [item.to_dict() for item in recommendations]
    return {
        "choices": [item.label() for item in recommendations]
        + [engine.NONE_LABEL, engine.CUSTOM_LABEL],
        "specs": specs,
        "nemesis_d": round(nemesis_d, 2),
        "gf": round(engine.gf_scale(nemesis_d), 4),
        "none_label": engine.NONE_LABEL,
        "custom_label": engine.CUSTOM_LABEL,
        "max_attempts": engine.MAX_ATTEMPTS,
    }


@app.post("/api/golden-fingers/propose")
def propose_golden_finger(request: GoldenFingerProposalRequest) -> dict[str, Any]:
    try:
        nemesis_d = request.nemesis_d if request.nemesis_d is not None else 10.0 - _difficulty_int(request.difficulty)
        return engine.propose_custom(
            request.text,
            world=request.world,
            persona=request.persona,
            difficulty=request.difficulty,
            attempt=request.attempt,
            nemesis_d=nemesis_d,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/golden-fingers/confirm")
def confirm_golden_finger(request: GoldenFingerConfirmRequest) -> dict[str, Any]:
    try:
        return engine.confirm_custom(request.proposal, True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _gf_draft_from(request: GfComposeRequest) -> dict[str, Any]:
    """把请求体摊平成 compose_spec 可吃的草稿；显式字段覆盖 draft。"""
    draft = dict(request.draft or {})
    for key in ("composition", "fuels", "cost", "cooldown", "difficulty",
                "name", "effect", "scope", "fit"):
        value = getattr(request, key, None)
        if value not in (None, "", [], {}):
            draft[key] = value
    if request.world and not draft.get("world"):
        draft["world"] = request.world
    return draft


def _gf_compose_payload(spec, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    gate = gf_designer.quality_gate(spec)
    payload = {
        "spec": spec.to_dict() if hasattr(spec, "to_dict") else dict(spec),
        "label": gf_designer.spec_label(spec),
        "quality": gate,
        "ok": gate["ok"],
        "issues": list(gate["issues"]),
    }
    if extra:
        payload.update(extra)
    return payload


@app.get("/api/gf-designer/options")
def gf_designer_options() -> dict[str, Any]:
    return gf_designer.wizard_options()


@app.post("/api/gf-designer/compose")
def gf_designer_compose(request: GfComposeRequest) -> dict[str, Any]:
    try:
        spec = gf_designer.compose_spec(_gf_draft_from(request))
    except gf_designer.DesignerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _gf_compose_payload(spec, extra={"source": "compose"})


@app.post("/api/gf-designer/polish")
def gf_designer_polish(request: GfPolishRequest) -> dict[str, Any]:
    """无 key 时直接返回 compose 结果；有 key 才调模型润色，失败回退 compose。"""
    try:
        draft = _gf_draft_from(request)
        if draft.get("composition"):
            spec = gf_designer.compose_spec({**draft, **(request.spec or {})})
        elif request.spec:
            spec = gf_designer.as_spec(request.spec)
        else:
            raise gf_designer.DesignerError("缺少构成或规格")
    except (gf_designer.DesignerError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    composed = _gf_compose_payload(spec, extra={"source": "compose"})
    api_key = (request.api_key or "").strip()
    session = None
    if request.session_id and not api_key:
        try:
            session = sessions.require(request.session_id)
            api_key = (session.api_key or "").strip()
        except KeyError:
            session = None
    if not api_key:
        return composed

    prompt = gf_designer.polish_prompt(spec, world=request.world)
    try:
        state = session.state if session is not None and isinstance(session.state, dict) else {}
        provider = request.provider or state.get("provider") or "deepseek"
        config = fe.provider_config(provider, request.base_url or (state.get("base_url") if state else None))
        base_url = request.base_url or (state.get("base_url") if state else None) or config["base_url"]
        model = request.model or (state.get("model") if state else None) or (config.get("models") or [fe.DEFAULT_MODEL])[0]
        client = fe.make_client(api_key, provider, base_url)
        extra_kwargs = dict(fe.thinking_kwargs(provider))
        extra_kwargs["max_tokens"] = 1200
        text = distill_model(client, model, prompt, extra_kwargs, provider)
        polished = gf_designer.apply_polish(spec, text)
        payload = _gf_compose_payload(polished, extra={"source": "polish" if polished != spec else "compose"})
        if api_key in repr(payload):
            return composed
        return payload
    except Exception:  # noqa: BLE001  润色失败必须回退 compose，不得 502
        return composed


@app.get("/api/gf-designer/specs")
def gf_designer_list_specs() -> dict[str, Any]:
    items = gf_designer.list_specs()
    return {"specs": items, "library": [{"id": item["id"], "label": item["label"]} for item in items]}


@app.post("/api/gf-designer/specs")
def gf_designer_save_spec(request: GfSaveRequest) -> dict[str, Any]:
    try:
        if request.spec:
            raw = dict(request.spec)
            if request.draft:
                composed = gf_designer.compose_spec({**request.draft, **raw})
                raw = composed.to_dict()
            saved = gf_designer.save_spec(raw)
        elif request.draft:
            saved = gf_designer.save_spec(gf_designer.compose_spec(request.draft))
        else:
            raise gf_designer.DesignerError("缺少 spec 或 draft")
    except gf_designer.DesignerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"spec": saved, "id": saved["id"], "label": saved.get("label") or gf_designer.spec_label(saved)}


@app.get("/api/gf-designer/specs/{spec_id}")
def gf_designer_load_spec(spec_id: str) -> dict[str, Any]:
    try:
        record = gf_designer.load_spec(spec_id)
    except gf_designer.DesignerError as exc:
        status = 404 if "找不到" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"spec": record, "id": record["id"], "label": record.get("label") or gf_designer.spec_label(record)}


@app.post("/api/models/fetch")
def fetch_models(request: ModelFetchRequest) -> dict[str, Any]:
    """拉取可用模型列表；凭据只在本次请求内存中使用，不写入会话或磁盘。

    base_url 为空时按 provider 预设兜底（与 models/test 同规则）。
    """
    provider = request.provider or "deepseek"
    config = fe.provider_config(provider, request.base_url or None)
    try:
        models = fe.fetch_models(
            request.api_key.strip(), provider, config["base_url"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"拉取模型列表失败：{exc}") from exc
    return {"models": list(models)}


@app.post("/api/models/test")
def test_model_connection(request: ModelTestRequest) -> dict[str, Any]:
    """测试模型服务连通性；失败只回错误消息，不回显凭据。

    base_url 为空时按 provider 预设兜底（与开局/gf-designer 同规则），
    已知提供商无需手填即可测通。
    """
    provider = request.provider or "deepseek"
    config = fe.provider_config(provider, request.base_url or None)
    try:
        ok, message = fe.test_connection(
            request.api_key.strip(), provider,
            config["base_url"], request.model)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}
    return {"ok": bool(ok), "message": str(message)}


@app.get("/api/character-designer/schema")
def character_designer_schema() -> dict[str, Any]:
    """角色设计器表单 schema：前端据此渲染，题目改后端即可热更新。"""
    return {
        "identity_fields": character_designer.IDENTITY_FIELDS,
        "corpus_kinds": [
            {"id": kind, **meta}
            for kind, meta in character_designer.CORPUS_KINDS.items()
        ],
        "questions": character_designer.QUESTIONS,
        "limits": {
            "max_corpus_entries": character_designer.MAX_CORPUS_ENTRIES,
            "max_corpus_text": character_designer.MAX_CORPUS_TEXT,
            "max_persona_text": character_designer.MAX_PERSONA_TEXT,
        },
    }


@app.post("/api/character-designer/generate")
def character_designer_generate(request: DesignerGenerateRequest) -> dict[str, Any]:
    """融合身份+语料+选择题生成角色卡与 persona；凭据只在内存中使用。"""
    try:
        identity = character_designer.validate_identity(request.identity)
        corpus = character_designer.classify_corpus(request.corpus)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 凭据优先级：已存在的 session > body 内 api_key；session_id 不存在时静默回落。
    session = None
    if request.session_id:
        try:
            session = sessions.require(request.session_id)
        except KeyError:
            session = None
    locked = False
    if session is not None:
        if not sessions.acquire(session):
            raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
        locked = True
    try:
        state = session.state if session is not None and isinstance(session.state, dict) else {}
        provider = request.provider or state.get("provider") or "deepseek"
        config = fe.provider_config(provider, request.base_url or state.get("base_url"))
        base_url = request.base_url or state.get("base_url") or config["base_url"]
        api_key = (session.api_key if session is not None and session.api_key
                   else (request.api_key or "").strip())
        if not api_key:
            raise HTTPException(status_code=400, detail="缺少 API Key：请提供 session_id 或 api_key")
        model = request.model or state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0]

        prompt = character_designer.fusion_prompt(identity, corpus, request.answers)
        try:
            client = fe.make_client(api_key, provider, base_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"模型客户端初始化失败：{exc}") from exc
        # 融合输出含 JSON 卡 + persona 全文，需要比蒸馏默认更大的 token 预算。
        extra_kwargs = dict(fe.thinking_kwargs(provider))
        extra_kwargs["max_tokens"] = 6000
        try:
            text = distill_model(client, model, prompt, extra_kwargs, provider)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"角色生成失败，请重试：{exc}") from exc
        try:
            card, persona_text = character_designer.parse_fusion(
                text, identity, request.answers, corpus)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"角色卡解析失败，请重试：{exc}") from exc
        persona_markdown = character_designer.to_persona_markdown(card, persona_text, identity)
        quality = character_designer.quality_assessment(identity, corpus, request.answers, card)
        # 防泄露：任何输出都不得回显 API Key。
        if api_key in persona_markdown:
            persona_markdown = persona_markdown.replace(api_key, "***")
        card_json = json.dumps(card, ensure_ascii=False)
        if api_key in card_json:
            card = json.loads(card_json.replace(api_key, "***"))
        return {
            "card": card,
            "persona_markdown": persona_markdown,
            "quality": quality,
            "suggested_filename": character_designer.suggest_filename(card.get("name", "")),
        }
    finally:
        if locked:
            sessions.release(session)


@app.post("/api/character-designer/save")
def character_designer_save(request: DesignerSaveRequest) -> dict[str, Any]:
    """保存 persona 到 personas/standard；同名自动加 -2/-3 后缀。

    fe._scan_models 不带缓存，每次 /api/bootstrap 实时扫描目录，
    因此保存后下一次 bootstrap 的 character_models 即可见，无需失效处理。
    """
    if not re.search(r"[0-9A-Za-z一-鿿_\-]", request.filename):
        raise HTTPException(status_code=400, detail="文件名不含任何合法字符（中文/字母/数字/_/-）")
    filename = character_designer.suggest_filename(request.filename)
    directory = Path(fe.STANDARD_MODEL_DIR)
    with _DESIGNER_SAVE_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        candidate = directory / f"{filename}.md"
        suffix = 2
        while candidate.exists():
            candidate = directory / f"{filename}-{suffix}.md"
            suffix += 1
        try:
            candidate.write_text(request.persona_markdown, encoding="utf-8")
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"persona 写入失败：{exc}") from exc
    return {"label": candidate.stem, "path": candidate.name}


# ---------------------------------------------------------------------------
# 角色库（用户本地扩充 / 替换内置 / 导出导入）
# ---------------------------------------------------------------------------

def _library_card_payload(card, *, shadowed: set[str] | None = None) -> dict[str, Any]:
    """把 CharacterCard 转成带来源标记的响应体。"""
    is_user = card.id.startswith("user-")
    is_override = (not is_user) and (engine.character_library.OVERRIDES_DIR / f"{card.id}.json").is_file()
    kind = "user" if is_user else ("override" if is_override else "built_in")
    payload = {
        "id": card.id,
        "role": card.role,
        "name": card.name,
        "work": getattr(card, "work", ""),
        "archetype": card.archetype,
        "desire": card.desire,
        "fear": card.fear,
        "abilities": list(card.abilities),
        "relationship_vector": dict(card.relationship_vector)
        if isinstance(card.relationship_vector, tuple) and card.relationship_vector
        and isinstance(card.relationship_vector[0], tuple) else str(getattr(card, "_raw_relation", "") or ""),
        "knowledge_scope": list(card.knowledge_scope),
        "voice": card.voice,
        "unacceptable_actions": list(card.unacceptable_actions),
        "background": card.background,
        "skill_ids": list(card.skill_ids),
        "source": card.source,
        "gender": card.gender,
        "original_position": card.original_position,
        "source_medium": card.source_medium,
        "source_region": card.source_region,
        "slot_keys": {key: list(values) for key, values in card.slot_keys.items()},
        "protagonist_type": list(card.protagonist_type),
        "mainline_type": list(card.mainline_type),
        "partner_type": list(card.partner_type),
        "nemesis_type": list(card.nemesis_type),
        "origin": kind,
        "editable": is_user or is_override,
        "deletable": kind != "built_in",
        "replaces_built_in": card.id in (shadowed or set()),
    }
    return payload


@app.get("/api/character-library")
def character_library_list() -> dict[str, Any]:
    """全量角色池：内置 + 用户卡 + 替换版合并视图。"""
    pool, shadowed = engine.character_library.merged_pool_cached()
    cards = [_library_card_payload(card, shadowed=shadowed) for card in pool]
    # relationship_vector 若为字符串形态，前端以纯文本展示即可。
    return {"cards": cards, "shadowed_built_in": sorted(shadowed)}


@app.post("/api/character-library")
def character_library_create(request: CharacterLibraryUpsertRequest,
                             replace_built_in: bool = False) -> dict[str, Any]:
    """新增用户卡；replace_built_in=true 且提供 target_id 时替换内置卡。"""
    try:
        saved = engine.character_library.save_card(
            request.model_dump(), replace_built_in=replace_built_in)
    except engine.character_library.LibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = saved["record"]
    return {"card": {**record, "origin": saved["origin"],
                     "editable": True, "deletable": True}}


@app.put("/api/character-library/{card_id}")
def character_library_update(card_id: str,
                             request: CharacterLibraryUpsertRequest) -> dict[str, Any]:
    """更新用户卡或内置替换卡；编辑内置卡即自动转为替换语义。"""
    try:
        saved = engine.character_library.update_card(
            card_id, request.model_dump())
    except engine.character_library.LibraryError as exc:
        status = 404 if "找不到" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    record = saved["record"]
    return {"card": {**record, "origin": saved["origin"],
                     "editable": True, "deletable": True}}


@app.delete("/api/character-library/{card_id}")
def character_library_delete(card_id: str) -> dict[str, Any]:
    """删除用户卡；删除替换卡即还原内置原版。"""
    try:
        result = engine.character_library.delete_card(card_id)
    except engine.character_library.LibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@app.get("/api/character-library/export")
def character_library_export(id: str | None = None) -> FileResponse:
    """导出用户侧角色卡为 JSON 文件下载；id 支持逗号分隔多选。"""
    ids = [item.strip() for item in (id or "").split(",") if item.strip()]
    try:
        payload = engine.character_library.export_payload(ids or None)
    except engine.character_library.LibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    count = len(payload["characters"])
    target_dir = Path(fe.WRITABLE_DIR) / "outputs"
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{len(ids)}张" if ids else "-全部"
    path = target_dir / f"character-library-export{suffix}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return FileResponse(path, filename=path.name, media_type="application/json",
                        headers={"X-Export-Count": str(count)})


@app.post("/api/character-library/import")
async def character_library_import(file: UploadFile = File(...),
                                   overwrite: bool = Form(default=False)) -> dict[str, Any]:
    """上传 JSON 批量导入角色卡；兼容单体导出文件与内置池整池格式。"""
    suffix = Path(file.filename or "").suffix.lower()
    if file.filename and suffix not in {".json"}:
        raise HTTPException(status_code=400, detail="只允许上传 .json 角色库文件")
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件超过 10MB 上限")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败：{exc}") from exc
    rows = _extract_character_rows(data)
    if not rows:
        raise HTTPException(status_code=400, detail="文件中未找到任何角色记录（characters 字段）")
    results = engine.character_library.import_records(rows, overwrite=overwrite)
    total = len(results["imported"]) + len(results["replaced"]) + len(results["failed"])
    return {**results, "total": total}


def _extract_character_rows(data: Any) -> list[dict[str, Any]]:
    """从导入数据中提取角色记录列表，兼容三种形状。"""
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        wrapper = data.get("characters")
        if isinstance(wrapper, list):
            return [row for row in wrapper if isinstance(row, dict)]
        if data.get("role"):  # 单卡文件
            return [data]
    return []


@app.post("/api/uploads")
async def upload(
    file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    kind: str = Form(default="novel"),
) -> dict[str, Any]:
    suffix = Path(file.filename or "").suffix.lower()
    if not file.filename or suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="只允许上传 TXT/MD 文件")
    if kind not in UPLOAD_KINDS:
        raise HTTPException(status_code=400, detail="kind 必须是 novel/persona/roster-skill/nemesis")
    try:
        session = sessions.create(session_id)
        content = await read_upload(file)
        result = sessions.put_upload(session, file.filename, content, kind=kind, extension=suffix)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_id": session.session_id, "upload": result}


class QuickDistillRequest(BaseModel):
    """手动触发快速蒸馏：把已切章的书目收录进基础模式作品库 + 角色性格卡入库。"""

    book_id: str
    work_title: str = ""
    api_key: str = ""
    provider: str = "deepseek"
    base_url: str = ""
    model: str = ""


def _resolve_book_dir(book_id: str) -> Path:
    """按 book_id 定位 var/books 下的切章目录：精确匹配优先，其次唯一前缀匹配。"""
    book_id = str(book_id or "").strip()
    books_root = Path(fe.WRITABLE_DIR) / "books"
    if not book_id or not books_root.is_dir():
        raise HTTPException(status_code=404, detail="未找到切章书库，请先上传并成功切章")
    exact = books_root / book_id
    if exact.is_dir():
        return exact
    matches = [p for p in books_root.iterdir() if p.is_dir() and p.name.startswith(book_id)]
    if len(matches) == 1:
        return matches[0]
    raise HTTPException(status_code=404, detail=f"未找到书目：{book_id}（匹配 {len(matches)} 个）")


@app.post("/api/books/quick-distill")
def quick_distill_book(request: QuickDistillRequest) -> dict[str, Any]:
    """对已切章书目执行快速蒸馏（自动路径外的手动/复蒸入口）。

    强化模式开局成功后会自动蒸馏一次；本端点用于复蒸或独立收录。
    API Key 只在请求内存中使用，不落盘。
    """
    book_dir = _resolve_book_dir(request.book_id)
    if not (book_dir / "chapters").is_dir():
        raise HTTPException(status_code=400, detail="书目缺少 chapters/，切章不完整")
    api_key = request.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="快速蒸馏需要模型调用，请提供 API Key")
    config = fe.provider_config(request.provider, request.base_url)
    model_name = request.model or (config.get("models") or [fe.DEFAULT_MODEL])[0]
    try:
        client = fe.make_client(api_key, request.provider, request.base_url or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = engine.work_distiller.quick_distill(
            book_dir,
            work_title=request.work_title or book_dir.name,
            model=lambda prompt: distill_model(client, model_name, prompt, None, request.provider),
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"快速蒸馏失败：{exc}") from exc
    fe.invalidate_rules_cache()
    return result


def _resolve_roster_uploads(session, rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """把名册每行的 skill_upload_id 解析为本 session 的上传路径。"""
    resolved: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row) if isinstance(row, dict) else {}
        upload_id = item.pop("skill_upload_id", None)
        if upload_id:
            item["skill_upload"] = _upload_or_404(session, str(upload_id))
        resolved.append(item)
    return resolved


@app.post("/api/sessions/start")
def start(request: StartRequest) -> StreamingResponse:
    try:
        session = sessions.create(request.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    candidate_api_key = request.api_key.strip()
    provider = request.provider or "deepseek"
    config = fe.provider_config(provider, request.base_url)
    # 单女主/单宿敌由后端强制：宿敌本身是单选+单上传，这里只校验女主名册。
    if not str(request.heroine_mode or "").startswith("多") and len(request.heroine_roster) > 1:
        sessions.release(session)
        raise HTTPException(status_code=400, detail="单女主模式最多配置 1 位女主")
    try:
        novel_path = _upload_or_404(session, request.novel_upload_id)
        persona_path = _upload_or_404(session, request.persona_upload_id)
        nemesis_path = _upload_or_404(session, request.nemesis_upload_id)
        novel_display_name = sessions.upload_display_name(session, request.novel_upload_id)
        nemesis_display_name = sessions.upload_display_name(session, request.nemesis_upload_id)
        companion_roster = _resolve_roster_uploads(session, request.companion_roster)
        heroine_roster = _resolve_roster_uploads(session, request.heroine_roster)
    except HTTPException:
        sessions.release(session)
        raise
    if request.mode.startswith("强化"):
        if not novel_path or Path(novel_path).suffix.lower() != ".txt":
            sessions.release(session)
            raise HTTPException(status_code=400, detail="强化模式必须先上传完整 TXT 原著")
        try:
            checked = engine.chapter_tools.split_file(novel_path, book_id=Path(novel_path).stem, output_root=fe.WRITABLE_DIR)
        except (OSError, ValueError, UnicodeError) as exc:
            sessions.release(session)
            raise HTTPException(status_code=400, detail=f"TXT 切章失败：{exc}") from exc
        if not checked.get("chapters"):
            sessions.release(session)
            raise HTTPException(status_code=400, detail="强化模式要求可成功识别章节的完整 TXT")
    params = dict(
        provider=provider,
        base_url=request.base_url or config["base_url"],
        api_key=candidate_api_key,
        remember=False,
        model=request.model or (config.get("models") or [fe.DEFAULT_MODEL])[0],
        thinking_mode=request.thinking_mode,
        thinking_param=request.thinking_param,
        mode=request.mode,
        work=None if request.mode.startswith("强化") else (request.work or (fe.list_works() or [None])[0]),
        novel_file=novel_path,
        novel_display_name=novel_display_name,
        fragment=request.fragment,
        role=request.role,
        protagonist_gender=request.protagonist_gender,
        timepoint=request.timepoint,
        difficulty=request.difficulty,
        gf=request.golden_finger or (fe.GOLDEN_FINGERS[0] if fe.GOLDEN_FINGERS else "无（凡人开局）"),
        gf_custom=request.golden_finger_proposal,
        persona_preset=request.persona_preset,
        persona_custom=request.persona_custom,
        persona_file=persona_path,
        # 强化模式的开工确认与每回合门禁都依赖锚点蒸馏产物（首章锚点）；
        # 用户关闭蒸馏会导致永远无法确认开局（实测卡死路径），一律强制开启。
        # 基础模式不蒸馏（on_start 内部按 mode 归一为 False），此值无关。
        distill_enabled=bool(request.distill_enabled) or request.mode.startswith("强化"),
        companion_roster=companion_roster,
        heroine_roster=heroine_roster,
        companion_count=request.companion_count,
        heroine_count=request.heroine_count,
        heroine_mode=request.heroine_mode,
        enable_nemesis=request.enable_nemesis,
        nemesis_select=request.nemesis_select,
        nemesis_file=nemesis_path,
        nemesis_display_name=nemesis_display_name,
        convergence=request.convergence,
        story_richness=request.story_richness,
        story_agent_mode=request.story_agent_mode,
        roster_card_ids=request.roster_card_ids,
    )
    try:
        generator = gradio_app.on_start(**params)
    except Exception as exc:  # noqa: BLE001
        sessions.release(session)
        raise HTTPException(status_code=400, detail=f"开局初始化失败：{exc}") from exc
    return _stream_response(
        session, generator, operation="start", api_key_on_commit=candidate_api_key)


@app.post("/api/sessions/{session_id}/messages")
def message(session_id: str, request: MessageRequest) -> StreamingResponse:
    session = _session_or_404(session_id)
    state = _require_game(session)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    provider = request.provider or state.get("provider") or "deepseek"
    config = fe.provider_config(provider, request.base_url or state.get("base_url"))
    try:
        generator = gradio_app.on_send(
            provider,
            request.base_url or state.get("base_url") or config["base_url"],
            request.api_key.strip() if request.api_key is not None else session.api_key,
            request.model or state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0],
            request.thinking_mode or state.get("thinking_mode") or "auto",
            request.thinking_param if request.thinking_param is not None else state.get("thinking_param", ""),
            request.message,
            state.get("history", []),
            state,
        )
    except Exception as exc:  # noqa: BLE001
        sessions.release(session)
        raise HTTPException(status_code=400, detail=f"消息初始化失败：{exc}") from exc
    return _stream_response(session, generator, operation="message")


@app.post("/api/sessions/{session_id}/ask")
def ask(session_id: str, request: AskRequest) -> dict[str, Any]:
    """非流式规则问答：复用对局模型配置，只依据规则文档与公开状态回答。

    业务逻辑（作弊码状态机/协议文案/persist 策略/prompt 装配）在
    core/services/ask_service.py；本端点只做会话锁与异常→状态码映射。
    """
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        try:
            return ask_service.handle_ask(
                state, request.question,
                api_key=session.api_key, session_id=session.session_id)
        except ask_service.AskClientError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ask_service.AskUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        sessions.release(session)


def _quest_difficulty_level(value: float, game_difficulty: int | str = 4) -> int:
    """把 offer 请求的 0–1 难度系数映射为任务档位，受游戏总体难度制约。

    区间由游戏难度决定（D → [D-2, D+2] 夹在 1–9），系数在区间内线性取值：
    真人实测中永不出现「D1 世界里的炼狱任务」，多数任务随系数线性变化。
    """
    return engine.quest.compute_quest_level(value, game_difficulty)


def _game_difficulty_of(state: dict[str, Any]) -> int | str:
    params = state.get("start_params") if isinstance(state.get("start_params"), dict) else {}
    return params.get("difficulty", 4)


@app.post("/api/sessions/{session_id}/quests/offer")
def quest_offer(session_id: str, request: QuestOfferRequest) -> dict[str, Any]:
    """生成任务 offer：模型只产文案，时限与奖励由 engine.quest 确定性计算。"""
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        kind = str(request.kind or "").strip().lower()
        if kind not in engine.quest.QUEST_KINDS:
            raise HTTPException(
                status_code=400, detail=f"kind 必须是 {sorted(engine.quest.QUEST_KINDS)} 之一")
        if not (0.0 <= float(request.difficulty) <= 1.0):
            raise HTTPException(status_code=400, detail="difficulty 必须在 0–1 之间")
        if not engine.quest.can_request_offer(state):
            raise HTTPException(status_code=400, detail="当前有未完成任务")
        provider = state.get("provider") or "deepseek"
        config = fe.provider_config(provider, state.get("base_url"))
        model = state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0]
        try:
            client = fe.make_client(
                session.api_key, provider, state.get("base_url") or config["base_url"])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"模型客户端初始化失败：{exc}") from exc
        game_difficulty = _game_difficulty_of(state)
        level = _quest_difficulty_level(request.difficulty, game_difficulty)
        range_lo, range_hi = engine.quest.quest_difficulty_range(game_difficulty)
        prompt = engine.quest.quest_offer_prompt(
            engine.quest.build_quest_context(state, kind, level))
        # 任务生成加 120s 读超时并重试一次：长上下文（整书剧情）下
        # 模型偶发慢响应不应挂死会话锁（实测 ReadTimeout 踩过）。
        quest_kwargs = dict(state.get("request_kwargs") or {})
        quest_kwargs.setdefault("timeout", 120.0)
        text = None
        last_exc: Exception | None = None
        for _attempt in range(2):
            try:
                text = distill_model(client, model, prompt, quest_kwargs, provider)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if text is None:
            raise HTTPException(
                status_code=502, detail=f"任务生成失败，请刷新重试：{last_exc}") from last_exc
        offer = None
        try:
            offer = engine.quest.parse_quest_offer(text, kind)
        except ValueError as exc:
            last_exc = exc
        if offer is None:
            raise HTTPException(
                status_code=502, detail=f"任务 offer 解析失败，请刷新重试：{last_exc}") from last_exc
        # 先解析成功再写入 state：模型/解析失败不得污染对局状态。
        start_params = state.get("start_params") if isinstance(state.get("start_params"), dict) else {}
        reward = engine.quest.compute_reward(kind, level, start_params.get("difficulty", 4))
        engine.quest.new_offer(state, offer, kind, level, reward, state.get("round", 0))
        session.state = state
        # 难度预估进度条：区间受游戏难度制约，系数线性映射档位。
        return {"quest": state["quest"], "reward": reward, "estimated": {
            "coefficient": round(max(0.0, min(1.0, float(request.difficulty))), 2),
            "level": level,
            "label": engine.quest.level_label(level),
            "range_lo": range_lo, "range_hi": range_hi,
            "range_label": f"{engine.quest.level_label(range_lo)}–{engine.quest.level_label(range_hi)}",
            "kind": kind,
            "deadline_span": engine.quest.compute_deadline_span(
                kind, level, engine.quest.window_round_budget(state)),
        }}
    finally:
        sessions.release(session)


@app.post("/api/sessions/{session_id}/quests/accept")
def quest_accept(session_id: str) -> dict[str, Any]:
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        try:
            engine.quest.accept(state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"quest": state["quest"]}
    finally:
        sessions.release(session)


@app.post("/api/sessions/{session_id}/quests/decline")
def quest_decline(session_id: str) -> dict[str, Any]:
    """婉拒当前 offer：offered -> none，之后可重新请求新 offer。"""
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        box = state.get("quest") if isinstance(state.get("quest"), dict) else {}
        if box.get("status") != "offered":
            raise HTTPException(status_code=400, detail="当前没有可婉拒的任务 offer")
        state["quest"] = {"status": "none"}
        return {"quest": state["quest"]}
    finally:
        sessions.release(session)


@app.get("/api/sessions/{session_id}/quests")
def quest_state(session_id: str) -> dict[str, Any]:
    session = _session_or_404(session_id)
    box = public_state(_require_game(session)).get("quest")
    return {"quest": box if isinstance(box, dict) and box else {"status": "none"}}


def _break_anchor_offer_context(state: dict[str, Any]) -> dict[str, Any]:
    """组装碎锚 offer_prompt 所需上下文；模型失败由调用方回退模板。"""
    timeline = state.get("anchor_timeline") if isinstance(state.get("anchor_timeline"), dict) else {}
    current = timeline.get("current") if isinstance(timeline.get("current"), dict) else {}
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), dict) else {}
    location = ""
    if isinstance(memory.get("location"), dict):
        location = str(memory["location"].get("name") or "")
    companions = state.get("companions") if isinstance(state.get("companions"), list) else []
    names = []
    for row in companions:
        if isinstance(row, dict):
            name = str(row.get("name") or "").strip()
            if name:
                names.append(name)
    conv = state.get("convergence_state") if isinstance(state.get("convergence_state"), dict) else {}
    tier = str(conv.get("effective") or state.get("convergence") or "较高")
    return {
        "anchor": current,
        "current_anchor": current,
        "chapter": current.get("chapter") or state.get("current_chapter", 1),
        "anchor_title": current.get("title") or "",
        "anchor_summary": current.get("summary") or "",
        "persona_hint": engine.skill_drift.prompt_block(state) or "",
        "location": location,
        "companions": names,
        "registered_names": names,
        "tier": tier,
        "momentum_bar": engine.break_anchor.momentum_bar(state),
    }


def _public_break_anchor(state: dict[str, Any]) -> dict[str, Any]:
    snap = engine.break_anchor.public_snapshot(state)
    return {"break_anchor": snap, "momentum_bar": snap.get("momentum_bar"),
            "broken_anchors": snap.get("broken_anchors", [])}


@app.post("/api/sessions/{session_id}/break-anchor/offer")
def break_anchor_offer(session_id: str) -> dict[str, Any]:
    """生成碎锚 offer：模型优先，parse 失败回退模板；不修改 quest。"""
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        if not isinstance(state.get("break_anchor"), dict):
            state["break_anchor"] = engine.break_anchor.idle_box()
        if not isinstance(state.get("broken_anchors"), list):
            state["broken_anchors"] = []
        if not engine.break_anchor.can_offer(state):
            raise HTTPException(status_code=400, detail="当前不能发起碎锚（积势不足、已有进行中碎锚、或仍在冷却）")
        context = _break_anchor_offer_context(state)
        prompt = engine.break_anchor.offer_prompt(context)
        provider = state.get("provider") or "deepseek"
        config = fe.provider_config(provider, state.get("base_url"))
        model = state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0]
        offer = None
        try:
            client = fe.make_client(
                session.api_key, provider, state.get("base_url") or config["base_url"])
            # 模型调用在持锁状态下进行：给本次请求加 30s 读超时，避免
            # 上游慢响应长期占住 session 锁、令其他端点持续 409。
            # 锁不释放重取：context 读取与 new_offer 写入必须原子，
            # 中途放锁会让并发请求改写 state 造成 offer 落在过期快照上。
            offer_kwargs = dict(state.get("request_kwargs") or {})
            offer_kwargs.setdefault("timeout", 30.0)
            text = distill_model(
                client, model, prompt, offer_kwargs, provider)
            offer = engine.break_anchor.parse_offer(text, context)
        except Exception:  # noqa: BLE001  模型/解析失败回退模板，不把坏 JSON 写入状态
            offer = None
        if offer is None:
            offer = engine.break_anchor.template_stages(
                context.get("anchor"), persona_hint=context.get("persona_hint"),
                location=context.get("location"), companions=context.get("companions"),
                difficulty=context.get("tier"))
        try:
            engine.break_anchor.new_offer(state, offer, state.get("round", 0))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _public_break_anchor(state)
    finally:
        sessions.release(session)


@app.post("/api/sessions/{session_id}/break-anchor/accept")
def break_anchor_accept(session_id: str) -> dict[str, Any]:
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        try:
            engine.break_anchor.accept(state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _public_break_anchor(state)
    finally:
        sessions.release(session)


@app.post("/api/sessions/{session_id}/break-anchor/decline")
def break_anchor_decline(session_id: str) -> dict[str, Any]:
    """婉拒当前碎锚 offer：offered -> idle，quest 不受影响。"""
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        box = state.get("break_anchor") if isinstance(state.get("break_anchor"), dict) else {}
        if box.get("status") != "offered":
            raise HTTPException(status_code=400, detail="当前没有可婉拒的碎锚 offer")
        cooldown_until = box.get("cooldown_until") or 0
        idle = engine.break_anchor.idle_box()
        idle["cooldown_until"] = cooldown_until
        idle["tier"] = box.get("tier") or idle["tier"]
        state["break_anchor"] = idle
        return _public_break_anchor(state)
    finally:
        sessions.release(session)


@app.post("/api/sessions/{session_id}/autoplay-choice")
def autoplay_choice(session_id: str) -> dict[str, Any]:
    """主角性格子智能体从当前选项自动选一项（单回合托管，不连续推进）。"""
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        options = state.get("options") or []
        if not options:
            raise HTTPException(status_code=400, detail="当前没有可选选项，无法托管")
        provider = state.get("provider") or "deepseek"
        config = fe.provider_config(provider, state.get("base_url"))
        model = state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0]
        try:
            client = fe.make_client(
                session.api_key, provider, state.get("base_url") or config["base_url"])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"模型客户端初始化失败：{exc}") from exc
        prompt = engine.autoplay.build_autoplay_prompt(state, options, state.get("history"))
        try:
            text = distill_model(
                client, model, prompt, state.get("request_kwargs"), provider)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"托管选择失败，请重试：{exc}") from exc
        try:
            choice = engine.autoplay.parse_autoplay_choice(text, options)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"托管选择解析失败，请重试：{exc}") from exc
        return {"choice": choice["choice"], "reason": choice["reason"]}
    finally:
        sessions.release(session)


@app.get("/api/sessions/{session_id}/distill/progress")
def distill_progress(session_id: str) -> dict[str, Any]:
    """锚点蒸馏进度汇报（右侧小窗口数据源，纯中文，不暴露原始 JSON/英文）。

    汇报当前章前后窗口内各章的蒸馏状态与全书已完成章数，供前端轮询。
    """
    session = _session_or_404(session_id)
    state = session.state or {}
    mode = str(state.get("mode") or "")
    if not mode.startswith("强化"):
        return {"enabled": False, "summary": "基础模式无需锚点蒸馏", "chapters": []}
    if state.get("distill_enabled") is False:
        return {"enabled": False, "summary": "锚点蒸馏已关闭", "chapters": []}
    chapter_index = state.get("chapter_index") or {}
    total = int(state.get("total_chapters") or len((chapter_index or {}).get("chapters") or []) or 0)
    current = int(state.get("current_chapter") or 1)
    distiller = registries.distillers.get(state.get("distill_key"))
    per_chapter = {}
    if distiller is not None:
        try:
            per_chapter = {int(k): v for k, v in (distiller.status() or {}).items()}
        except (TypeError, ValueError):
            per_chapter = {}
    # 磁盘上已落盘的锚点数（后台队列重启后也能汇报真实进度）。
    done_on_disk = 0
    book_dir = Path(str(state.get("distill_key") or "") or "")
    anchors_dir = book_dir / "anchors" if book_dir.name != "anchors" else book_dir
    if anchors_dir.is_dir():
        done_on_disk = len(list(anchors_dir.glob("[0-9]" * 4 + ".json")))
    _STATUS_ZH = {"pending": "待蒸馏", "in_progress": "蒸馏中", "done": "已完成", "failed": "失败待重试"}
    window = range(max(1, current - 1), min(total, current + 6) + 1) if total else []
    chapters = []
    for number in window:
        row = per_chapter.get(number) or {}
        status = str(row.get("status") or "pending")
        item = {"chapter": number, "status_zh": _STATUS_ZH.get(status, "待蒸馏"),
                "status": status, "current": number == current}
        chapters.append(item)
    done_count = sum(1 for v in per_chapter.values() if v.get("status") == "done")
    done_count = max(done_count, done_on_disk)
    running = any(v.get("status") == "in_progress" for v in per_chapter.values())
    failed = [int(k) for k, v in per_chapter.items() if v.get("status") == "failed"]
    if running:
        summary = f"后台蒸馏中 · 已完成 {done_count} 章"
    elif failed and not chapters:
        summary = f"锚点蒸馏失败 {len(failed)} 章，稍后自动重试"
    elif chapters and all(c["status"] == "done" for c in chapters):
        summary = f"当前章节锚点已就绪 · 已完成 {done_count} 章"
    else:
        summary = f"已完成 {done_count} 章" if total else "等待开局后启动"
    if total:
        summary += f" / 共 {total} 章"
    return {"enabled": True, "summary": summary,
            "done": done_count, "total": total, "chapters": chapters}


@app.get("/api/sessions/{session_id}/state")
def state(session_id: str) -> dict[str, Any]:
    session = _session_or_404(session_id)
    return {"session_id": session.session_id, "state": public_state(session.state)}


@app.post("/api/sessions/{session_id}/save")
def save(session_id: str, request: SaveRequest) -> dict[str, Any]:
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        engine.persistence.save_state(
            state,
            save_id=request.save_id,
            root=fe.WRITABLE_DIR,
            start_params=state.get("start_params"),
            session_id=session.session_id,
        )
        metadata = engine.persistence.save_metadata(
            request.save_id, root=fe.WRITABLE_DIR, session_id=session.session_id)
        return {
            "session_id": session.session_id,
            "save_id": request.save_id,
            "saved": True,
            "metadata": metadata,
        }
    finally:
        sessions.release(session)


@app.get("/api/saves")
def list_all_saves() -> dict[str, Any]:
    """自由存档列表：日期、小说、角色、难度、模式等描述信息，新的在前。"""
    return {"saves": engine.persistence.list_saves(root=fe.WRITABLE_DIR)}


@app.post("/api/saves/load")
def load_any_save(request: LoadRequest) -> dict[str, Any]:
    """设定模式自由读档：无需既有对局，可读取任意存档点。"""
    restored = engine.persistence.load_state_strict(request.save_id, root=fe.WRITABLE_DIR)
    if not restored or not restored.get("system"):
        raise HTTPException(status_code=404, detail="未找到可恢复的存档")
    try:
        session = sessions.create(None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    restored["game_ready"] = True
    session.state = restored
    metadata = engine.persistence.save_metadata(request.save_id, root=fe.WRITABLE_DIR)
    return {
        "session_id": session.session_id,
        "save_id": request.save_id,
        "metadata": metadata,
        "state": public_state(restored),
    }


@app.post("/api/sessions/{session_id}/load")
def load(session_id: str, request: LoadRequest) -> dict[str, Any]:
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        # 自由读档：同一存档库中的任意存档点都可读入当前会话。
        restored = engine.persistence.load_state_strict(request.save_id, root=fe.WRITABLE_DIR)
        if not restored or not restored.get("system"):
            raise HTTPException(status_code=404, detail="未找到可恢复的存档")
        restored["game_ready"] = True
        session.state = restored
        metadata = engine.persistence.save_metadata(request.save_id, root=fe.WRITABLE_DIR)
        return {
            "session_id": session.session_id,
            "save_id": request.save_id,
            "metadata": metadata,
            "state": public_state(restored),
        }
    finally:
        sessions.release(session)


_EXPORT_STAGES = ("plot", "style", "final_polish")
_EXPORT_STAGE_LABELS = {"plot": "情节还原", "style": "风格化", "final_polish": "终稿润色"}


class _ExportStageError(Exception):
    """携带失败章节与阶段信息，供 _run_export 转成 502。"""

    def __init__(self, chapter_pos: int, total: int, stage: str, cause: Exception):
        super().__init__(str(cause))
        self.chapter_pos = chapter_pos
        self.total = total
        self.stage = stage
        self.cause = cause


def _run_export(state: dict[str, Any], style: str, creds: dict[str, Any]) -> dict[str, Any]:
    """会话端点与读档端点共享的导出流程：逐章 plot→style→final_polish 三遍。"""
    exporter = engine.novel_exporter
    style = str(style or "").strip() or exporter.DEFAULT_STYLE
    if style not in exporter.STYLES:
        raise HTTPException(
            status_code=400, detail=f"style 必须是 {sorted(exporter.STYLES)} 之一")

    segments = exporter.extract_narrative(state)
    if not segments:
        raise HTTPException(status_code=400, detail="没有可导出的正文")
    chapters = exporter.plan_chapters(exporter.merge_narrative(segments))
    total = len(chapters)

    provider = creds.get("provider") or "deepseek"
    config = fe.provider_config(provider, creds.get("base_url"))
    model_name = creds.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0]
    api_key = str(creds.get("api_key") or "").strip()
    try:
        client = fe.make_client(api_key, provider, config["base_url"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"模型客户端初始化失败：{exc}") from exc
    extra_kwargs = state.get("request_kwargs")

    def _scrub(text: Any) -> str:
        content = str(text or "")
        return content.replace(api_key, "***") if api_key and api_key in content else content

    counter = {"n": 0}

    def _model(prompt: str) -> str:
        # iter_pipeline 对每章固定按 plot→style→final_polish 顺序调用，
        # 调用序号可确定性地映射回「第几章第几遍」，用于失败段报错。
        seq = counter["n"]
        counter["n"] += 1
        chapter_pos, stage = seq // len(_EXPORT_STAGES), _EXPORT_STAGES[seq % len(_EXPORT_STAGES)]
        try:
            output = distill_model(client, model_name, prompt, extra_kwargs, provider)
        except Exception as exc:  # noqa: BLE001
            raise _ExportStageError(chapter_pos, total, stage, exc) from exc
        return _scrub(output)

    params = state.get("start_params") if isinstance(state.get("start_params"), dict) else {}
    source_meta = {key: params.get(key) or state.get(key) for key in ("work", "novel", "mode")}
    context = "；".join(
        f"{label}：{value}" for label, value in (("作品", source_meta["work"]), ("篇目", source_meta["novel"]))
        if value)
    try:
        records = list(exporter.iter_pipeline(chapters, model=_model, style=style, context=context))
    except _ExportStageError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"导出失败：第 {exc.chapter_pos + 1}/{exc.total} 段"
                   f"{_EXPORT_STAGE_LABELS.get(exc.stage, exc.stage)}阶段出错：{exc.cause}",
        ) from exc

    full_text = _scrub(exporter.assemble(records))
    chapter_texts: dict[Any, dict[str, Any]] = {}
    for record in records:
        if record.get("output"):
            chapter_texts[record["chapter_idx"]] = {
                "index": int(record["chapter_idx"]),
                "title": str(record.get("chapter_title") or ""),
                "text": str(record["output"]),
            }
    manifest = exporter.build_export_manifest(
        source_meta, chapters, style, model_used=True)
    token_chars = sum(len(r.get("prompt", "")) + len(str(r.get("output") or "")) for r in records)
    return {
        "manifest": manifest,
        "chapters": [chapter_texts[key] for key in sorted(chapter_texts)],
        "full_text": full_text,
        "tokens_est": (len(full_text) + token_chars) // 2,
    }


@app.post("/api/sessions/{session_id}/export-novel")
def export_session_novel(session_id: str, request: ExportNovelRequest) -> dict[str, Any]:
    """导出当前对局为小说：锁内同步执行，全程不落盘、不写入凭据。"""
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        creds = {
            "provider": state.get("provider"),
            "base_url": state.get("base_url"),
            "api_key": session.api_key,
            "model": state.get("model"),
        }
        return _run_export(state, request.style, creds)
    finally:
        sessions.release(session)


@app.post("/api/saves/{save_id}/export-novel")
def export_save_novel(save_id: str, request: ExportNovelRequest) -> dict[str, Any]:
    """自由读档导出：任意存档点无需活动 session 即可导出小说。

    模型凭据可在请求体可选传入；缺省回退到对应提供商的环境变量 Key，
    凭据只在本次请求内存中使用，不写入会话或磁盘。
    """
    restored = engine.persistence.load_state_strict(save_id, root=fe.WRITABLE_DIR)
    if not restored:
        raise HTTPException(status_code=404, detail="未找到可导出的存档")
    provider = request.provider or "deepseek"
    config = fe.provider_config(provider, request.base_url)
    api_key = (request.api_key or "").strip() or os.environ.get(config.get("env_key", ""), "")
    creds = {
        "provider": provider,
        "base_url": request.base_url,
        "api_key": api_key,
        "model": request.model,
    }
    return _run_export(restored, request.style, creds)


if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


# —— 真实检验监控管线：浏览器页面实时观看（工具包 tools/playtest_kit） ——

class PlaytestStartRequest(BaseModel):
    api_key: str = Field(min_length=4, max_length=300)
    provider: str = "deepseek"
    base_url: str | None = None
    model: str = Field(min_length=1, max_length=120)
    thinking_mode: str = "auto"
    rounds: int = Field(default=30, ge=1, le=100)
    story_richness: int = Field(default=700, ge=300, le=1000)
    force: bool = False


@app.post("/api/playtest/start")
def playtest_start(request: PlaytestStartRequest) -> dict[str, Any]:
    """启动一场真实模型检验；同一时刻只允许一场。供应商/模型随主程序预设。"""
    from tools.playtest_kit import pipeline as playtest_pipeline
    from tools.playtest_kit import runner as playtest_runner
    cfg = fe.provider_config(request.provider, request.base_url)
    if not (request.base_url or "").strip() and not cfg.get("base_url"):
        raise HTTPException(status_code=400, detail="该供应商需要填写 Base URL")
    config = {
        "base": "http://127.0.0.1:" + str(os.getenv("FATE_API_PORT", "8000")),
        "provider": request.provider,
        "base_url": request.base_url or "",
        "model": request.model.strip(),
        "thinking_mode": request.thinking_mode,
        "rounds": request.rounds,
        "story_richness": request.story_richness,
        # 双写：config 快照只带掩码；_api_key 只在 runner 内存中取用。
        "_api_key": request.api_key.strip(),
    }
    ok, message = playtest_pipeline.start_run(config, playtest_runner.run, force_restart=request.force)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"started": True}


@app.post("/api/playtest/stop")
def playtest_stop() -> dict[str, Any]:
    from tools.playtest_kit import pipeline as playtest_pipeline
    return {"stopped": playtest_pipeline.stop_run()}


@app.get("/api/playtest/status")
def playtest_status() -> dict[str, Any]:
    from tools.playtest_kit import pipeline as playtest_pipeline
    run = playtest_pipeline.current_run()
    if run is None:
        return {"status": "idle"}
    return run["reporter"].snapshot()


@app.get("/api/playtest/stream")
def playtest_stream() -> StreamingResponse:
    """SSE：实时推送检验事件（phase/check/round/note/end）。"""
    from tools.playtest_kit import pipeline as playtest_pipeline

    def generator() -> Iterator[bytes]:
        run = playtest_pipeline.current_run()
        rep = run.get("reporter") if isinstance(run, dict) else None
        if rep is None:
            yield b"event: snapshot\ndata: {\"status\":\"idle\"}\n\n"
            return
        q = rep.subscribe()
        try:
            import queue as _queue
            idle_deadline = time.time()
            while True:
                try:
                    event = q.get(timeout=2.0)
                    kind = event.get("kind")
                    payload = event.get("data") if kind == "snapshot" else {
                        k: v for k, v in event.items() if k != "kind"}
                    body = json.dumps(payload, ensure_ascii=False, default=str)
                    yield f"event: {kind}\ndata: {body}\n\n".encode("utf-8")
                    if kind == "end":
                        break
                except _queue.Empty:
                    yield b": keep-alive\n\n"
                    if time.time() - idle_deadline > 7200:
                        break
        finally:
            rep.unsubscribe(q)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


PLAYTEST_MONITOR = PROJECT_ROOT / "tools" / "playtest_kit" / "monitor.html"


@app.get("/playtest-monitor", include_in_schema=False)
def playtest_monitor_page() -> FileResponse:
    if not PLAYTEST_MONITOR.is_file():
        raise HTTPException(status_code=404, detail="监控页尚未生成：tools/playtest_kit/monitor.html")
    return FileResponse(PLAYTEST_MONITOR)


@app.get("/", include_in_schema=False)
def frontend_index() -> FileResponse:
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="Vue 前端尚未构建，请先在 frontend 目录运行 npm run build")
    return FileResponse(index)


@app.get("/{path:path}", include_in_schema=False)
def frontend_route(path: str) -> FileResponse:
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API 路由不存在")
    # dist 内真实存在的静态文件（如 lomsting.html）直接返回；其余路径 SPA fallback
    candidate = (FRONTEND_DIST / path).resolve()
    if candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST.resolve()):
        return FileResponse(candidate)
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="前端资源不存在")
    return FileResponse(index)


if __name__ == "__main__":
    import uvicorn

    # 直接以 app 对象启动（老写法 "api_server:app" 引用不存在的模块名，直跑必失败）
    uvicorn.run(app, host=os.getenv("FATE_API_HOST", "127.0.0.1"), port=int(os.getenv("FATE_API_PORT", "8000")))
