"""Vue 主前端的最小 FastAPI 桥接。

启动：项目根下 ``python run_app.py``（或 ``uvicorn core.server:app``）。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import uuid
import copy
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
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
from core.services import chat_service  # noqa: E402  (角色闲聊服务，Phase F)
from core.services import copilot_service  # noqa: E402  (Copilot 助手服务)
from core.engine.distill import distill_model  # noqa: E402  (从老版 app._distill_model 提炼)
from core.api.contracts import gradio_state_from_output, public_state, stream_event_from_gradio  # noqa: E402
from core.api import save_contract  # noqa: E402
from core.api.sessions import SessionManager, read_upload  # noqa: E402
from core.api.operations import OperationJournal  # noqa: E402
from core.engine import catalog  # noqa: E402
from core.engine import character_designer  # noqa: E402
from core.engine import gf_designer  # noqa: E402
from core.services.book_prepare_service import prepare_book, verify_preparation  # noqa: E402
from core.services.scene_locator_service import locate_scene, select_scene, project_scene_initial_state  # noqa: E402
from core.services.book_library_service import list_playable_books, mark_book_played  # noqa: E402
from core.services.context_retrieval_service import retrieve_context  # noqa: E402
from core.services import pre_game_service  # noqa: E402
from core.services import golden_finger_service  # noqa: E402
from core.services import structured_question_service  # noqa: E402
from core.services import character_state_service  # noqa: E402
from core.services import opening_service  # noqa: E402  (开局蒸馏进度注册表)


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
sessions = SessionManager(PROJECT_ROOT)
# 操作日志必须跟随 FATE_VAR_DIR，否则多实例并行共用仓库根 var/ 会互相串写。
operation_journal = OperationJournal(Path(fe.WRITABLE_DIR) / "operations.jsonl")
question_service = structured_question_service.StructuredQuestionService()
app = FastAPI(title="书中行 API", version="3.0.1")
_cors_origins = [item.strip() for item in os.getenv("FATE_CORS_ORIGINS", "").split(",") if item.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"]
    )


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
    book_id: str | None = None
    chapter_selection: dict[str, Any] | None = None
    fragment: str = ""
    role: str = ""
    protagonist_gender: str = "unknown"
    timepoint: str = "故事开篇"
    # 原著准备策略：window（普通）| fullbook（强化）；缺省由后端按 mode 推导。
    preparation_mode: str | None = None
    preparation_job_id: str | None = None
    # 目标开局章节：剧情定位确认后的第 N 章（默认第 1 章）。
    target_chapter: int = Field(default=1, ge=1, le=100000)
    # 剧情定位确认结果（select 路由返回，不含大体积投影）：时点/知识截止/
    # 截点前事实随开局提交接入准备窗口与时点初始化（T09）。
    scene_selection: dict[str, Any] | None = None
    difficulty: str = "D4 普通"
    golden_finger: str | None = None
    golden_finger_proposal: dict[str, Any] = Field(default_factory=dict)
    # 推荐金手指的完整规格（含 cost/cooldown/limits）：推荐项的标签只是显示，
    # 开局注入必须使用与用户所见一致的完整规格（D03）。
    golden_finger_spec: dict[str, Any] | None = None
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
    # 宿敌可选身体身份姓名：非空时直接作为宿敌身份（与名册「手动填写优先」一致）。
    nemesis_identity: str = ""
    nemesis_upload_id: str | None = None
    convergence: str = "较高"
    # 故事丰富度：玩家拖动的单回合叙事体量刻度（300–1000）。
    story_richness: int = Field(
        default=engine.participation.RICHNESS_DEFAULT,
        ge=engine.participation.RICHNESS_MIN,
        le=engine.participation.RICHNESS_MAX,
    )
    # 试卷档位（重构 M4）：1–6 双族六档；普通模式限 1–2、第 6 档必须开类 agent。
    # 缺省时服务端按 story_richness 就近映射（旧客户端兼容）。
    paper_tier: int | None = Field(default=None, ge=1, le=6)
    # 类 Agent 生成模式：draft → 自检 → 定向修订循环（仅强化模式生效）。
    story_agent_mode: bool = False
    # 四栏角色卡选择（规格 §7）：[{"slot": "主角|主线|伙伴|宿敌", "card_id": ...}]，可选。
    roster_card_ids: list[dict[str, Any]] = Field(default_factory=list)
    # Pre-game state machine fields (optional, backward compatible)
    use_enhanced_pregame: bool = False
    pre_game_state: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = None


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    thinking_mode: str | None = None
    thinking_param: str | None = None
    client_request_id: str | None = None


class QuestionBatchRequest(BaseModel):
    questions: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    max_concurrency: int = Field(default=10, ge=1, le=10)


class AnswerQuestionRequest(BaseModel):
    question: dict[str, Any]
    answer: Any
    context: dict[str, Any] = Field(default_factory=dict)


class _OptionalBodyCreds(BaseModel):
    """请求体兜底凭据（DEF-E-02）。

    会话密钥只在 /start 提交时驻留内存、不落盘；实例重启后恢复的会话密钥为空。
    回合外模型端点与 copilot 采用同一约定：会话凭据优先，请求体兜底；
    凭据仅在内存中使用，不写入状态或日志。
    """

    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class AskRequest(_OptionalBodyCreds):
    question: str = Field(min_length=1, max_length=4000)


class AutoplayChoiceRequest(_OptionalBodyCreds):
    pass


class CopilotChatRequest(BaseModel):
    """Copilot 对话请求：会话凭据优先，无对局时用请求体凭据。"""
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=40)
    session_id: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class QuestOfferRequest(_OptionalBodyCreds):
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
    persona_markdown: str = Field(default="", max_length=fe.MAX_PERSONA_CHARS)
    filename: str = Field(default="", max_length=80)
    card: dict[str, Any] | None = None


class CharacterLibraryUpsertRequest(BaseModel):
    """角色库新增/更新请求；字段口径与内置角色池 schema 一致。"""
    name: str = Field(min_length=1, max_length=40)
    role: str = "伙伴"
    work: str = ""
    archetype: str = ""
    desire: str = ""
    fear: str = ""
    abilities: list[str] | str = ""
    relationship_vector: dict[str, Any] | list[Any] | str = ""
    knowledge_scope: list[str] | str = ""
    voice: str = ""
    unacceptable_actions: list[str] | str = ""
    background: str = ""
    skill_ids: list[str] | str = ""
    source: str | dict[str, Any] = ""
    gender: str = "unknown"
    original_position: str = ""
    source_medium: str = ""
    source_region: str = ""
    slot_keys: dict[str, Any] = Field(default_factory=dict)
    protagonist_type: str = ""
    mainline_type: str = ""
    partner_type: str = ""
    nemesis_type: str = ""

    id: str | None = None
    character_id: str | None = None
    target_id: str | None = None
    schema_version: int | str | None = None
    revision: int | None = Field(default=None, ge=0)
    base_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = Field(default=None, max_length=128)
    aliases: list[str] = Field(default_factory=list)
    profile: dict[str, Any] = Field(default_factory=dict)
    semantic: dict[str, Any] = Field(default_factory=dict)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    quality: dict[str, Any] = Field(default_factory=dict)
    mind_model: dict[str, Any] = Field(default_factory=dict)
    decision_policy: dict[str, Any] = Field(default_factory=dict)
    voice_transfer: dict[str, Any] = Field(default_factory=dict)
    behavior_boundaries: dict[str, Any] = Field(default_factory=dict)
    distill_level: str = "normal"
    persona_markdown: str = Field(default="", max_length=fe.MAX_PERSONA_CHARS)
    one_line: str = ""
    decision_principle: str | list[str] | dict[str, Any] = ""
    voice_samples: str | list[Any] = ""
    ability_limits: str | list[Any] | dict[str, Any] = ""
    references: str | list[Any] | dict[str, Any] = ""
    model_config = {"extra": "forbid"}


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
    works = list(fe.list_works())
    return {
        "providers": _provider_payload(),
        "works": works,
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
        # 试卷档位（重构 M4）：双族六档由后端统一下发；普通模式前端只渲染
        # basic_ok 档位，agent_required 档位需前端联动类 Agent 开关。
        "paper_tiers": [
            {
                "tier": paper.tier,
                "label": paper.label,
                "family": paper.family,
                "target_chars": paper.target_chars,
                "segments": len(paper.segments),
                "basic_ok": bool(paper.basic_mode),
                "agent_required": bool(paper.agent_required),
                "agent_recommended": bool(paper.agent_recommended),
            }
            for paper in (
                engine.papers.get_paper(tier, "setup") for tier in range(1, 7))
        ],
        "paper_tier_default": 3,
        # 类 Agent 模式的说明文案统一下发，前端不做硬编码。
        "story_agent_mode": {
            "label": "类 Agent 生成",
            "note": "适用于所有模式，与原著准备策略独立。开启后执行多阶段、受并发上限约束的生成与校验；可能增加耗时和模型调用费用。",
        },
        "golden_finger_library": [
            {"id": item["id"], "label": item["label"]}
            for item in gf_designer.list_specs()
        ],
        "counts": {
            "works": len(works),
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
                    q: str | None = None, work: str | None = None) -> dict[str, Any]:
    """四栏角色池候选：两级分组（来源 → 分类）。

    四类角色都从整个角色池选取；先按来源（主角/男主/女主/配角/反派）分组，
    再按该栏位 slot_keys 细分二级分类。

    - ``slot``：栏位（主角栏/伴侣栏/伙伴栏/宿敌栏）。
    - ``gender``：历史兼容参数，已不参与过滤（2026-08-30 起四栏均破除性别栏杆）。
    - ``q``：可选搜索词，按名字/出处/原型模糊匹配。
    - ``work``：可选作品名过滤（v2.0.3 跨书防线）——只保留该书与无出处的卡，
      宁缺毋滥；不传则不过滤（前端默认在客户端按当前书名过滤）。
    """
    slot_name = _normalize_pool_slot(slot)
    gender_value = (gender or "").strip().lower()
    if gender_value and gender_value not in ("male", "female"):
        raise HTTPException(status_code=400, detail="gender 只支持 male 或 female")
    pool, _shadowed = engine.character_library.merged_pool_cached()
    cards = list(pool) or list(catalog.load_character_pool())
    rows = _slot_candidates(cards, slot_name, gender_value)
    work_value = (work or "").strip()
    if work_value:
        rows = [card for card in rows
                if not (card.work or "").strip() or _same_work(card.work, work_value)]
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


def _same_work(card_work: Any, wanted: str) -> bool:
    """书名宽松同源判定：忽略书名号/空白/扩展名后互为包含即视为同书。"""
    def _norm(text: Any) -> str:
        return str(text or "").replace(".txt", "", 1).replace("《", "").replace("》", "").strip()

    left = _norm(card_work)
    right = _norm(wanted)
    return bool(left and right and (left == right or left in right or right in left))


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
    """页面刷新/服务重启后的会话回填：按 session_id 找磁盘上最新存档重建会话。"""
    try:
        saves = engine.persistence.list_saves(root=fe.WRITABLE_DIR)
        candidates = [item for item in saves if item.get("session_id") == session_id]
        if not candidates:
            return None
        latest = max(candidates, key=lambda item: str(item.get("saved_at") or ""))
        restored = engine.persistence.load_state_strict(
            str(latest.get("save_id") or "latest"),
            root=fe.WRITABLE_DIR,
            session_id=session_id,
        )
        if not restored or not restored.get("system"):
            return None
        restored = _normalize_restored_state(restored, session_id)
        if not save_contract.is_usable_state(restored):
            return None
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
    """Only opening and committed states are active; streaming/corrupt states are not."""
    return save_contract.is_usable_state(state)


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


def _apply_frame_state(
    data: dict[str, Any],
    session,
    *,
    stage: str,
    durable_seen: bool,
    operation: str,
    usable_previous: bool,
) -> None:
    """帧内状态收尾：持久化成功后帧使用权威会话状态的公开投影快照。

    帧禁止与 session.state 别名（D01）：投影是深拷贝并完成脱敏，
    帧内补写的 save_stage/game_ready 只作用于快照，不写回会话。
    未持久化的增量帧保持增量字段，不做全量投影（避免用部分状态
    计算聚合值）。
    """
    if not isinstance(data.get("state"), dict):
        return
    if durable_seen and isinstance(session.state, dict):
        data["state"] = public_state(session.state)
    data["state"]["save_stage"] = stage
    data["state"]["game_ready"] = bool(
        durable_seen
        or stage in ("opening", "committed")
        or (operation != "start" and usable_previous)
    )


def _stream_response(
    session,
    generator: Iterator[Any],
    *,
    operation: str,
    api_key_on_commit: str | None = None,
    client_request_id: str | None = None,
    played_book_dir: Path | None = None,
) -> StreamingResponse:
    def body() -> Iterator[bytes]:
        previous_assistant = ""
        previous_state = copy.deepcopy(session.state)
        start_committed = operation != "start"
        durable_seen = False
        operation_id = None
        
        # Create operation journal entry if client provided request ID
        if client_request_id:
            try:
                op = operation_journal.start(
                    client_request_id=client_request_id,
                    data={"operation": operation, "session_id": session.session_id}
                )
                operation_id = op.operation_id
            except Exception:  # noqa: BLE001
                pass
        
        try:
            iterator = iter(generator)
            frame_number = 0
            while True:
                # Legacy app callbacks propose state; only this boundary saves it.
                with engine.persistence.defer_candidate_saves():
                    try:
                        output = next(iterator)
                    except StopIteration:
                        break
                frame_number += 1
                raw_state = gradio_state_from_output(output)
                event = stream_event_from_gradio(output)
                data = event.data
                chat = data.get("chat") if isinstance(data, dict) else None

                progress_frame = (
                    operation == "start"
                    and raw_state is not None
                    and not raw_state.get("system")
                    and isinstance(raw_state.get("opening_distill"), dict)
                    and raw_state["opening_distill"].get("status") == "running"
                )
                if progress_frame:
                    # The opening distillation progress is a transient view. It
                    # may be exposed to the browser, but has no resumable system.
                    raw_state = dict(raw_state)
                    raw_state["save_stage"] = "streaming"
                    raw_state["game_ready"] = False
                    # Keep the last durable session state untouched.
                    raw_state["session_id"] = session.session_id

                if operation == "start" and raw_state is not None and not raw_state.get("system"):
                    if not progress_frame:
                        session.state = previous_state
                        message = str(data.get("status") or "开局失败，请检查设定后重试")
                        if operation_id:
                            operation_journal.error(operation_id, {"message": message})
                        yield (json.dumps({
                            "type": "error",
                            "data": {"operation": operation, "message": message},
                        }, ensure_ascii=False) + "\n").encode("utf-8")
                        return

                if raw_state is not None and not progress_frame:
                    stage = save_contract.classify_state(raw_state)
                    # A confirmation response is durable opening state, while a
                    # normal message starts from a committed state and is only
                    # durable again after its next complete option set.
                    is_confirmation = (
                        operation == "message"
                        and str(raw_state.get("save_stage") or "") == "opening"
                    )
                    if operation == "message" and not is_confirmation:
                        stage = "streaming" if not save_contract.valid_options(raw_state.get("options")) else stage
                    # Every output before the final option commit is a temporary
                    # stream snapshot. It may be rendered to the client but must
                    # never replace the last durable session state.
                    if stage in ("opening", "committed") and raw_state.get("system"):
                        durable_state = dict(raw_state)
                        durable_state["save_stage"] = stage
                        durable_state["game_ready"] = True
                        durable_state["session_id"] = session.session_id
                        try:
                            engine.persistence.save_state(
                                durable_state,
                                root=fe.WRITABLE_DIR,
                                start_params=durable_state.get("start_params"),
                                session_id=session.session_id,
                                request_id=f"{client_request_id}:{frame_number}" if client_request_id else None,
                                expected_revision=int(session.state.get("revision") or 0) if session.state else 0,
                            )
                            session.state = durable_state
                            durable_seen = True
                            start_committed = True
                            if operation == "start" and played_book_dir is not None:
                                # 已玩作品库：开局权威提交后写 played 标记
                                # （投影写入；库列表展示前还会做磁盘复验）。
                                # 书目录取自本次请求已解析的 book_id/上传路径，
                                # 不读持久化 start_params——其中不含 novel_file。
                                # 失败只记日志：标记缺失不应回滚已提交的开局。
                                try:
                                    mark_book_played(played_book_dir, session.session_id)
                                except (OSError, ValueError) as exc:
                                    logging.getLogger("uvicorn.error").warning(
                                        "[playable] played 标记写入失败 %s：%s",
                                        played_book_dir, exc)
                        except (OSError, TypeError, ValueError) as exc:
                            session.state = previous_state
                            durable_seen = False
                            start_committed = operation != "start"
                            raise ValueError(f"正式状态保存失败：{exc}") from exc
                    elif stage == "streaming":
                        pass
                    if operation == "start" and api_key_on_commit is not None and durable_seen:
                        session.api_key = api_key_on_commit
                    _apply_frame_state(
                        data, session,
                        stage=stage,
                        durable_seen=durable_seen,
                        operation=operation,
                        usable_previous=save_contract.is_usable_state(previous_state),
                    )
                if operation != "start" or start_committed:
                    data["session_id"] = session.session_id
                data["operation"] = operation
                if operation_id:
                    data["operation_id"] = operation_id
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
                if operation_id:
                    operation_journal.error(operation_id, {"message": "开局未生成有效状态"})
                yield (json.dumps({
                    "type": "error",
                    "data": {"operation": operation, "message": "开局未生成有效状态，请重试"},
                }, ensure_ascii=False) + "\n").encode("utf-8")
                return
            
            if operation_id:
                operation_journal.done(operation_id, {"session_id": session.session_id})
            
            yield (json.dumps({
                "type": "done",
                "data": {"session_id": session.session_id, "operation": operation},
            }, ensure_ascii=False) + "\n").encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            if operation == "start":
                session.state = previous_state
            if operation_id:
                operation_journal.error(operation_id, {"message": str(exc)})
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
    """本机局域网 IPv4 候选（排除回环/链路本地），按可用性排序。

    旧实现一旦默认路由探测成功就不再枚举其他网卡；VPN/虚拟网卡成为默认
    路由时，二维码会指向手机无法访问的地址。现在同时收集默认路由与主机
    全部 IPv4，并把 RFC1918 私网地址排在最前；默认路由仅作为同等级首选。
    """
    default_ip = ""
    candidates: list[str] = []

    def _add(value: str | None) -> None:
        value = str(value or "").strip()
        try:
            addr = ipaddress.ip_address(value)
        except ValueError:
            return
        if not isinstance(addr, ipaddress.IPv4Address):
            return
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
            return
        if value not in candidates:
            candidates.append(value)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            # connect 不发送数据，只让系统选择当前默认路由出口。
            s.connect(("8.8.8.8", 80))
            default_ip = str(s.getsockname()[0] or "")
            _add(default_ip)
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    except OSError:
        pass

    def _rank(value: str) -> tuple[int, int, tuple[int, int, int, int]]:
        addr = ipaddress.IPv4Address(value)
        # RFC1918 私网优先于运营商 CGNAT/其他非公网；默认路由在同等级优先。
        private = (addr in ipaddress.IPv4Network("10.0.0.0/8")
                   or addr in ipaddress.IPv4Network("172.16.0.0/12")
                   or addr in ipaddress.IPv4Network("192.168.0.0/16"))
        octets = tuple(int(part) for part in value.split("."))
        return (0 if private else 1, 0 if value == default_ip else 1, octets)

    return sorted(candidates, key=_rank)


def _lan_session_id(value: str | None) -> str | None:
    """校验 UUID 会话 ID，**保留服务端实际使用的原始表示**。

    SessionManager 默认创建 ``uuid.uuid4().hex``（32 位、无连字符）；旧代码
    ``return str(uuid.UUID(value))`` 会改成带连字符形式，二维码链接随后按一个
    不存在的新 ID 查会话，造成手机端无法接续。这里只验证，不规范化。
    """
    if not value:
        return None
    text = str(value).strip()
    try:
        uuid.UUID(text)
    except (TypeError, ValueError, AttributeError):
        return None
    return text


def _lan_info(port: int | None, session_id: str | None = None) -> dict[str, Any]:
    addresses = _lan_addresses()
    port = int(port or os.getenv("FATE_API_PORT", "21560"))
    linked_session = _lan_session_id(session_id)

    def _url(address: str) -> str:
        base = f"http://{address}:{port}"
        return f"{base}/?session={linked_session}" if linked_session else base

    url_entries = [{"address": address, "url": _url(address)} for address in addresses]
    primary = addresses[0] if addresses else None
    listening = (os.getenv("FATE_API_HOST", "127.0.0.1") in {"0.0.0.0", "::"}
                 or _LAN_LISTENING)
    if primary and not listening:
        hint = "已检测到局域网地址，但服务只监听本机回环地址；请使用默认 0.0.0.0 监听后重启。"
    elif primary:
        hint = ("手机与电脑连同一 Wi-Fi 后扫码访问。若首个地址不可达，可切换其他网卡地址；"
                "仍无法打开时请在 Windows 防火墙中允许本程序或放行该端口。")
    else:
        hint = "未检测到局域网 IPv4：请确认电脑已连接 Wi-Fi/路由器，并检查 VPN/虚拟网卡设置。"
    return {
        "addresses": addresses,
        "urls": url_entries,
        "port": port,
        "url": url_entries[0]["url"] if url_entries else None,
        "session_id": linked_session,
        "listening_lan": listening,
        "hint": hint,
    }


# uvicorn 实际监听地址是否为全接口（启动时由 run_app 写入；测试与直接
# uvicorn core.server 场景下保持默认推断）。
_LAN_LISTENING = os.getenv("FATE_API_HOST", "127.0.0.1") in {"0.0.0.0", "::"}


@app.get("/api/lan-info")
def lan_info(request: Request, session_id: str | None = None) -> dict[str, Any]:
    """局域网访问信息；指定 session 时二维码可接续电脑当前对局。"""
    return _lan_info(request.url.port, session_id)


@app.get("/api/lan-qrcode.png", include_in_schema=False)
def lan_qrcode(request: Request, session_id: str | None = None,
               address: str | None = None) -> Response:
    """局域网访问二维码，可携带已校验的 session UUID，并可指定网卡地址。"""
    info = _lan_info(request.url.port, session_id)
    url = info.get("url")
    if address:
        # 只允许服务端枚举出的地址，防止二维码端点被当作任意 URL 编码器。
        matched = next((item for item in info.get("urls", [])
                        if item.get("address") == str(address)), None)
        if matched:
            url = matched.get("url")
        else:
            raise HTTPException(status_code=400, detail="指定的局域网地址不在可用网卡列表中")
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

        # 新校验分支：必须构造完整草稿（cost/cooldown 缺玩家输入时按难度规则
        # 取默认值），否则 quality_gate 必然拒绝、分支形同虚设（D02）。
        # 玩家自然语言常缺「可观察动作」标记词：先按原文效果校验，软性
        # 不合格时用「信息」模板包装重试一次；违禁文本包装后仍会被机制
        # 门拦截，回落 legacy 并留痕。
        fallback: dict[str, Any] = {}
        if request.text:
            try:
                prepared = {"script": request.text, "title": "Proposal"}
                difficulty_label = request.difficulty if isinstance(request.difficulty, str) else f"D{_difficulty_int(request.difficulty)}"
                level = _difficulty_int(request.difficulty)
                budget = golden_finger_service.deterministic_budget(prepared, difficulty_label)
                attempt = max(1, int(request.attempt or 1))
                from core.engine.gf_designer import compose_spec

                truncated = request.text[:200]
                drafts = [
                    {"composition": "信息", "difficulty": difficulty_label,
                     "name": "自定义", "effect": truncated,
                     "cost": "精神负荷",
                     "cooldown": "每场景一次" if level <= 3 else "每日一次"},
                    {"composition": "信息", "difficulty": difficulty_label,
                     "name": f"自定义·{request.text[:12]}",
                     "fuels": [{"name": request.text[:18]}],
                     "cost": "精神负荷",
                     "cooldown": "每场景一次" if level <= 3 else "每日一次"},
                ]
                validation: dict[str, Any] = {}
                defaulted = ["composition", "cost", "cooldown"]
                for index, draft in enumerate(drafts):
                    spec = compose_spec(draft)
                    validation = golden_finger_service.validate_spec(
                        spec.to_dict(), difficulty_label, budget
                    )
                    if validation["ok"]:
                        if index == 1:
                            defaulted = defaulted + ["name", "effect"]
                        return {
                            "status": "await_confirmation",
                            "attempt": attempt,
                            "remaining": max(0, engine.MAX_ATTEMPTS - attempt),
                            "spec": validation["spec"],
                            "issues": list(validation["issues"]),
                            "budget": budget,
                            "defaulted_fields": defaulted,
                            "gf": round(engine.gf_scale(nemesis_d), 4),
                            "nemesis_d": round(float(nemesis_d), 2),
                        }
                fallback = {"fallback_reason": "确定性校验未通过："
                            + "；".join(validation.get("issues") or ["未知原因"])[:200]}
            except Exception as exc:  # noqa: BLE001 校验分支异常显式留痕后回落
                fallback = {"fallback_reason": f"校验分支异常：{exc}"}

        # Legacy path
        result = engine.propose_custom(
            request.text,
            world=request.world,
            persona=request.persona,
            difficulty=request.difficulty,
            attempt=request.attempt,
            nemesis_d=nemesis_d,
        )
        if isinstance(result, dict) and fallback:
            result.update(fallback)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/golden-fingers/confirm")
def confirm_golden_finger(request: GoldenFingerConfirmRequest) -> dict[str, Any]:
    try:
        proposal = request.proposal
        if not isinstance(proposal, dict) or proposal.get("status") != "await_confirmation":
            raise ValueError("无可确认的自定义金手指提案")
        spec = proposal.get("spec")
        if not isinstance(spec, dict) or not str(spec.get("name") or "").strip():
            raise ValueError("提案缺少完整规格，无法确认")
        # 服务端对提交内容重新校验：客户端 status 只能表达意图，
        # 成败由服务端 validate_spec 决定（D04）。
        source = str(spec.get("source") or "")
        match = re.search(r":D(\d+)", source)
        difficulty = f"D{match.group(1)}" if match else ""
        validation = golden_finger_service.validate_spec(spec, difficulty)
        if not validation.get("ok"):
            raise ValueError("确认失败：" + "；".join(
                str(item) for item in (validation.get("issues") or [])[:5]))
        result = engine.confirm_custom(proposal, True)
        if isinstance(result, dict):
            result["validated"] = True
        return result
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
        raise _character_library_http_error(exc) from exc
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
    """Compatibility adapter: persona and complete card commit to the same DB."""
    raw = dict(request.card or {})
    raw.setdefault("name", request.filename.strip())
    raw.setdefault("role", "主角")
    if request.persona_markdown:
        raw["persona_markdown"] = request.persona_markdown
    try:
        parsed = CharacterLibraryUpsertRequest.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="角色卡字段无效") from exc
    result = character_library_create(parsed)
    return {**result, "label": result["card"]["name"], "message": "已保存到角色数据库"}


# ---------------------------------------------------------------------------
# 角色库（用户本地扩充 / 替换内置 / 导出导入）
# ---------------------------------------------------------------------------

def _library_card_payload(card, *, shadowed: set[str] | None = None) -> dict[str, Any]:
    """把 CharacterCard 转成带来源标记的响应体。"""
    from core.engine import character_db
    source_type = character_db.get_character_source_type(card.id)
    is_user = source_type == "user"
    is_override = source_type == "override"
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
    record = character_db.get_character_record(card.id) or {}
    return {**payload, **record, "origin": kind}


@app.get("/api/character-library")
def character_library_list() -> dict[str, Any]:
    """全量角色池：内置 + 用户卡 + 替换版合并视图。"""
    pool, shadowed = engine.character_library.merged_pool_cached()
    cards = [_library_card_payload(card, shadowed=shadowed) for card in pool]
    # relationship_vector 若为字符串形态，前端以纯文本展示即可。
    return {"cards": cards, "shadowed_built_in": sorted(shadowed)}


def _character_library_http_error(exc):
    text = str(exc)
    if "冲突" in text:
        return HTTPException(status_code=409, detail={"code": "revision_conflict", "message": text, "retryable": False})
    if "找不到" in text:
        return HTTPException(status_code=404, detail={"code": "not_found", "message": text, "retryable": False})
    from core.engine.character_db import DatabaseError
    if isinstance(exc.__cause__, DatabaseError):
        return HTTPException(status_code=500, detail={"code": "storage_error", "message": "角色数据库保存失败", "retryable": True})
    return HTTPException(status_code=422, detail={"code": "invalid_card", "message": text, "retryable": False})


@app.post("/api/character-library")
def character_library_create(request: CharacterLibraryUpsertRequest,
                             replace_built_in: bool = False) -> dict[str, Any]:
    """新增用户卡；replace_built_in=true 且提供 target_id 时替换内置卡。"""
    try:
        saved = engine.character_library.save_card(
            request.model_dump(exclude_unset=True), replace_built_in=replace_built_in)
    except engine.character_library.LibraryError as exc:
        raise _character_library_http_error(exc) from exc
    record = saved["record"]
    return {"saved": True, "character_id": record["id"], "revision": record["revision"],
            "card": {**record, "origin": saved["origin"], "editable": True, "deletable": True}}


@app.put("/api/character-library/{card_id}")
def character_library_update(card_id: str,
                             request: CharacterLibraryUpsertRequest) -> dict[str, Any]:
    """更新用户卡或内置替换卡；编辑内置卡即自动转为替换语义。"""
    try:
        saved = engine.character_library.update_card(
            card_id, request.model_dump(exclude_unset=True))
    except engine.character_library.LibraryError as exc:
        status = 404 if "找不到" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    record = saved["record"]
    return {"saved": True, "character_id": record["id"], "revision": record["revision"],
            "card": {**record, "origin": saved["origin"], "editable": True, "deletable": True}}


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


@app.get("/api/character-library/{card_id}")
def character_library_detail(card_id: str, revision: int | None = None) -> dict[str, Any]:
    from core.engine.character_db import get_character_record
    record = get_character_record(card_id, revision)
    if record is None:
        raise HTTPException(status_code=404, detail="角色卡或修订不存在")
    return {"character_id": record["id"], "revision": record.get("revision"), "card": record}


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
        if kind == "novel" and suffix == ".txt":
            source_path = sessions.upload_path(session, result["upload_id"])
            split = engine.chapter_tools.split_file(source_path, book_id=Path(source_path).stem,
                                                     output_root=fe.WRITABLE_DIR)
            if not split.get("chapters"):
                raise ValueError("原著 TXT 未识别到可阅读章节")
            result["book_id"] = split["book_id"]
    except (ValueError, OSError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="上传或 TXT 切章失败，请检查文件内容") from exc
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
    if not book_id or Path(book_id).name != book_id or book_id in {".", ".."}:
        raise HTTPException(status_code=400, detail="book_id 只能是单层目录名")
    if not books_root.is_dir():
        raise HTTPException(status_code=404, detail="未找到切章书库，请先上传并成功切章")
    def confined(path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(books_root.resolve()):
            raise HTTPException(status_code=400, detail="书目路径超出书库范围")
        return resolved

    exact = books_root / book_id
    if exact.is_dir():
        return confined(exact)
    matches = [p for p in books_root.iterdir() if p.is_dir() and p.name.startswith(book_id)]
    if len(matches) == 1:
        return confined(matches[0])
    raise HTTPException(status_code=404, detail=f"未找到书目：{book_id}（匹配 {len(matches)} 个）")


def _book_display_name(book_id: str) -> str:
    """把 ``<upload_uuid>_<原文件名>`` 还原为玩家可读书名。"""
    text = str(book_id or "")
    head, sep, tail = text.partition("_")
    return tail if sep and len(head) >= 16 else text


def _book_record(book_dir: Path, *, include_chapters: bool = False) -> dict[str, Any]:
    """读取一本已切章原著的安全公开元数据；损坏目录返回最小记录。"""
    index_path = book_dir / "chapter_index.json"
    data: dict[str, Any] = {}
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = raw
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    chapters = []
    for row in data.get("chapters") or []:
        if not isinstance(row, dict):
            continue
        idx = int(row.get("idx") or row.get("chapter") or len(chapters) + 1)
        chapters.append({
            "index": idx,
            "title": str(row.get("title") or f"第 {idx} 章"),
            "chars": int(row.get("chars") or 0),
        })
    stat = book_dir.stat()
    result = {
        "book_id": book_dir.name,
        "name": _book_display_name(book_dir.name),
        "chapter_count": len(chapters),
        "source_chars": int(data.get("source_chars") or 0),
        "updated_at": stat.st_mtime,
    }
    if include_chapters:
        result["chapters"] = chapters
    return result


@app.get("/api/books")
def list_user_books() -> dict[str, Any]:
    """列出 var/books 下玩家已上传并成功切章的原著（阅读器数据源）。"""
    root = Path(fe.WRITABLE_DIR) / "books"
    if not root.is_dir():
        return {"books": []}
    books = []
    for book_dir in root.iterdir():
        if not book_dir.is_dir() or not (book_dir / "chapter_index.json").is_file():
            continue
        try:
            books.append(_book_record(book_dir))
        except OSError:
            continue
    books.sort(key=lambda row: float(row.get("updated_at") or 0), reverse=True)
    return {"books": books}


@app.get("/api/books/{book_id}")
def user_book_detail(book_id: str) -> dict[str, Any]:
    """返回一本玩家原著的章节目录，不返回整书正文。"""
    return {"book": _book_record(_resolve_book_dir(book_id), include_chapters=True)}


@app.get("/api/books/{book_id}/chapters/{chapter_index}")
def user_book_chapter(book_id: str, chapter_index: int) -> dict[str, Any]:
    """读取指定章节正文；只允许 chapter_index.json 中登记的章节。"""
    if chapter_index < 1:
        raise HTTPException(status_code=400, detail="章节号必须大于 0")
    book_dir = _resolve_book_dir(book_id)
    record = _book_record(book_dir, include_chapters=True)
    entry = next((row for row in record.get("chapters", [])
                  if int(row.get("index") or 0) == chapter_index), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"找不到章节：{chapter_index}")
    path = book_dir / "chapters" / f"{chapter_index:04d}.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"章节文件不可读：{chapter_index}") from exc
    return {"book_id": book_dir.name, "chapter": {**entry, "text": text}}


@app.get("/api/books/{book_id}/chapters/{chapter_index}/anchors")
def user_book_chapter_anchors(book_id: str, chapter_index: int) -> dict[str, Any]:
    """读取指定章节的剧情锚点与活跃人物摘要；锚点缺失/损坏时如实标注 unavailable（F25），不伪造内容。"""
    if chapter_index < 1:
        raise HTTPException(status_code=400, detail="章节号必须大于 0")
    book_dir = _resolve_book_dir(book_id)
    record = _book_record(book_dir, include_chapters=True)
    entry = next((row for row in record.get("chapters", [])
                  if int(row.get("index") or 0) == chapter_index), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"找不到章节：{chapter_index}")
    path = book_dir / "anchors" / f"{chapter_index:04d}.json"
    anchor: dict[str, Any] | None = None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            anchor = raw
    except (OSError, ValueError, json.JSONDecodeError):
        anchor = None
    characters: list[dict[str, str]] = []
    for item in (anchor or {}).get("characters", []):
        if isinstance(item, str):
            name = item.strip()
            if name:
                characters.append({"name": name})
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("角色") or "").strip()
            if name:
                detail = str(item.get("summary") or item.get("detail") or item.get("role") or "").strip()
                characters.append({"name": name, **({"detail": detail} if detail else {})})
    
    # 锚点未生成或不可读时的诊断明细：区分 Window 模式与 Fullbook 蒸馏进度
    detail_msg = "锚点未生成或不可读；章节原文仍可阅读，可稍后重试。"
    if anchor is None:
        opening_ready_path = book_dir / "opening_ready.json"
        if opening_ready_path.is_file():
            try:
                prep_data = json.loads(opening_ready_path.read_text(encoding="utf-8"))
                if prep_data.get("mode") == "window":
                    detail_msg = "当前原著采用窗口快速准备模式，轻量提取无模型消耗；完整剧情锚点与活跃人物将在启动游戏开局管线时实时生成。"
                elif prep_data.get("mode") == "fullbook":
                    detail_msg = f"当前原著为全书蒸馏模式，第 {chapter_index} 章锚点尚未生成或蒸馏未完成；蒸馏覆盖本章后将自动呈现。"
            except Exception:
                pass

    return {"book_id": book_dir.name, "chapter_index": chapter_index,
            "status": "ready" if anchor is not None else "unavailable",
            "anchor": anchor, "characters": characters,
            **({} if anchor is not None else {"detail": detail_msg})}


class _ModelCallRequest(BaseModel):
    """需要临时模型调用的请求公共字段；API Key 只在请求内存中使用，不落盘。"""
    api_key: str = ""
    provider: str = "deepseek"
    base_url: str | None = None
    model: str | None = None


class LocateRequest(_ModelCallRequest):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=100)
    # semantic=True 时用模型做全书语义候选（逐块、证据坐标强校验、需 API Key）。
    semantic: bool = False


class LocateSelectRequest(_ModelCallRequest):
    candidate: dict[str, Any]
    timepoint: str = "during"
    # project_facts=True 时基于截点前证据做时点角色初始状态投影（需 API Key）。
    project_facts: bool = False


def _request_model_callable(provider: str, base_url: str | None, api_key: str,
                            model_name: str | None):
    """按请求内凭据构建临时模型调用闭包；不写任何持久配置。"""
    config = fe.provider_config(provider, base_url)
    name = model_name or (config.get("models") or [fe.DEFAULT_MODEL])[0]
    client = fe.make_client(api_key, provider, base_url or None)
    return lambda prompt: distill_model(client, name, prompt, None, provider)


class PrepareBookRequest(_ModelCallRequest):
    mode: str = Field(default="window", pattern="^(window|fullbook)$")
    opening_chapters: int = Field(default=3, ge=1, le=12)
    target_chapter: int = Field(default=1, ge=1, le=100000)
    model_version: str | None = None


class PreparationJobRequest(PrepareBookRequest):
    idempotency_key: str = Field(min_length=1, max_length=200)


_preparation_jobs = None
_preparation_jobs_lock = threading.RLock()


def _get_preparation_jobs():
    """Configure persistence only on first use, never during module import."""
    global _preparation_jobs
    with _preparation_jobs_lock:
        if _preparation_jobs is None:
            from core.services.preparation_jobs_service import PreparationJobsService
            _preparation_jobs = PreparationJobsService(Path(fe.WRITABLE_DIR) / "preparation_jobs.sqlite3")
        return _preparation_jobs


def _close_preparation_jobs():
    global _preparation_jobs
    with _preparation_jobs_lock:
        if _preparation_jobs is not None:
            _preparation_jobs.close()
            _preparation_jobs = None


# Compose with the existing lifespan rather than replacing its startup/shutdown.
from contextlib import asynccontextmanager
_previous_book_workflows_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _book_workflows_lifespan(application):
    async with _previous_book_workflows_lifespan(application) as state:
        try:
            yield state
        finally:
            _close_preparation_jobs()


app.router.lifespan_context = _book_workflows_lifespan


def _preparation_model(request: _ModelCallRequest, *, required: bool):
    key = request.api_key.strip()
    if not key:
        if required:
            raise HTTPException(status_code=400, detail="模型调用需要 API Key")
        return None
    try:
        return _request_model_callable(request.provider, request.base_url, key, request.model)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="模型配置无效") from exc


def _preparation_model_version(request: PrepareBookRequest) -> str:
    if request.model_version:
        return request.model_version
    if request.model:
        return request.model
    if request.mode == "fullbook":
        config = fe.provider_config(request.provider, request.base_url)
        return (config.get("models") or [fe.DEFAULT_MODEL])[0]
    return "unspecified"


def _workflow_error(exc: Exception) -> HTTPException:
    # Never reflect exceptions/configuration strings containing credentials.
    if isinstance(exc, KeyError) or getattr(exc, "code", None) == "thread_not_found":
        return HTTPException(status_code=404, detail="工作流资源不存在")
    if isinstance(exc, (ValueError, RuntimeError)):
        return HTTPException(status_code=409, detail={"code": getattr(exc, "code", "WORKFLOW_CONFLICT"),
            "message": "工作流状态或参数冲突，请检查输入与当前状态"})
    return HTTPException(status_code=503, detail="工作流暂不可用")


@app.post("/api/books/{book_id}/prepare")
def prepare_user_book(book_id: str, request: PrepareBookRequest) -> dict[str, Any]:
    """Synchronous compatibility endpoint; credentials live in JSON bodies."""
    book_dir = _resolve_book_dir(book_id)
    model = _preparation_model(request, required=True) if request.mode == "fullbook" else None
    try:
        return prepare_book(book_dir, mode=request.mode, model=model,
                            model_version=_preparation_model_version(request),
                            target_chapter=request.target_chapter, opening_chapters=request.opening_chapters)
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.post("/api/books/{book_id}/preparation-jobs", status_code=202)
def create_preparation_job(book_id: str, request: PreparationJobRequest) -> dict[str, Any]:
    book_dir = _resolve_book_dir(book_id)
    model = _preparation_model(request, required=True) if request.mode == "fullbook" else None
    try:
        service = _get_preparation_jobs()
        job = service.create(book_dir, idempotency_key=request.idempotency_key,
                             mode=request.mode, target_chapter=request.target_chapter,
                             opening_chapters=request.opening_chapters,
                             model_version=_preparation_model_version(request))
        if job["status"] == "QUEUED":
            service.start(job["job_id"], model=model)
        return service.get(job["job_id"])
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.get("/api/preparation-jobs/{job_id}")
def get_preparation_job(job_id: str) -> dict[str, Any]:
    try:
        return _get_preparation_jobs().get(job_id)
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.get("/api/preparation-jobs/{job_id}/events")
def preparation_job_events(job_id: str, after: int = 0) -> dict[str, Any]:
    if after < 0:
        raise HTTPException(status_code=422, detail="after 必须为非负序号")
    try:
        return {"events": _get_preparation_jobs().events(job_id, after=after)}
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.post("/api/preparation-jobs/{job_id}/cancel")
def cancel_preparation_job(job_id: str) -> dict[str, Any]:
    try:
        return _get_preparation_jobs().cancel(job_id)
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.post("/api/preparation-jobs/{job_id}/resume", status_code=202)
def resume_preparation_job(job_id: str, request: _ModelCallRequest) -> dict[str, Any]:
    try:
        service = _get_preparation_jobs()
        job = service.get(job_id)
        configuration = service.configuration(job_id)
        version = configuration.get("model_version")
        if request.model and version and request.model != version:
            raise ValueError("resume model differs from original")
        model_request = request.model_copy(update={"model": version or request.model})
        model = _preparation_model(model_request, required=configuration.get("mode") == "fullbook")
        service.resume(job_id, model=model, model_version=version)
        return service.get(job_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _workflow_error(exc) from exc


class ChapterStartRequest(BaseModel):
    chapter_no: int = Field(ge=1, le=100000)
    source_hash: str | None = None


@app.post("/api/books/{book_id}/chapter-start")
def reader_chapter_start(book_id: str, request: ChapterStartRequest) -> dict[str, Any]:
    from core.services.reader_start_service import prepare_chapter_start
    book_dir = _resolve_book_dir(book_id)
    try:
        return prepare_chapter_start(book_dir, book_dir.name, request.chapter_no,
                                     expected_source_hash=request.source_hash)
    except Exception as exc:
        raise _workflow_error(exc) from exc


class ReaderChatThreadRequest(BaseModel):
    character_id: str = Field(min_length=1, max_length=200)
    chapter_no: int = Field(ge=1, le=100000)
    card_revision: int | None = Field(default=None, ge=1)
    source_hash: str | None = None
    # C07：默认 original（原著访谈，无需会话）；game 为本局分支访谈，需 session_id。
    view: str = "original"
    session_id: str | None = None


class ReaderChatMessageRequest(_ModelCallRequest):
    message: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=1, max_length=200)


_reader_chat = None


def _get_reader_chat():
    global _reader_chat
    with _preparation_jobs_lock:
        if _reader_chat is None:
            from core.services.reader_chat_service import ReaderChatService
            _reader_chat = ReaderChatService()
        return _reader_chat


def _reader_thread(thread_id: str) -> dict[str, Any]:
    thread = _get_reader_chat().get_thread(thread_id)
    if thread.get("scope") not in ("reader", "game"):
        raise HTTPException(status_code=409, detail="非阅读域会话")
    # Local single-user server: only threads attached to this instance's book root.
    # Game-view threads may carry no book identity when the source stayed unknown.
    book_id = thread.get("context", {}).get("book_id", "")
    if book_id:
        _resolve_book_dir(book_id)
    return thread


def _game_session_state(book_dir: Path, session_id: str | None) -> dict[str, Any]:
    """C07 本局访谈会话校验：存在、有进行中对局、且锚定当前书目；只取只读深拷贝快照。"""
    session_id = str(session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=422, detail="本局访谈需要 session_id")
    try:
        session = sessions.require(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session 不存在") from exc
    with session.lock:
        state = session.state
        snapshot = copy.deepcopy(state) if isinstance(state, dict) else None
    if not isinstance(snapshot, dict) or not snapshot.get("system"):
        raise HTTPException(status_code=409,
                            detail={"code": "no_active_game", "message": "该会话没有进行中的对局"})
    anchor = str(snapshot.get("distill_key") or "").strip()
    if not anchor:
        raise HTTPException(status_code=409,
                            detail={"code": "no_book_source", "message": "该会话未绑定书目来源，无法进行本局访谈"})
    try:
        matched = Path(anchor).resolve() == book_dir.resolve()
    except OSError:
        matched = False
    if not matched:
        raise HTTPException(status_code=409,
                            detail={"code": "session_book_mismatch", "message": "该会话属于另一本书"})
    # distill_key 已核实指向本书；仅补齐 build_game_context 期望的身份字段，不引入新事实。
    snapshot.setdefault("book_dir", str(book_dir))
    snapshot.setdefault("book_id", book_dir.name)
    return snapshot


@app.get("/api/books/{book_id}/reader-chat/roster")
def reader_chat_roster(book_id: str, chapter_no: int = 1, view: str = "original",
                       session_id: str | None = None) -> dict[str, Any]:
    book_dir = _resolve_book_dir(book_id)
    if chapter_no < 1:
        raise HTTPException(status_code=422, detail="chapter_no 必须为正整数")
    if view not in ("original", "game"):
        raise HTTPException(status_code=422, detail="view 必须是 original 或 game")
    # 原著花名册不触碰会话；本局花名册校验 session/书目后只读快照。
    session_state = _game_session_state(book_dir, session_id) if view == "game" else None
    try:
        return _get_reader_chat().list_roster(book_dir, chapter_no, view=view, session_state=session_state)
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.post("/api/books/{book_id}/reader-chat/threads")
def create_reader_chat_thread(book_id: str, request: ReaderChatThreadRequest) -> dict[str, Any]:
    book_dir = _resolve_book_dir(book_id)
    if request.view not in ("original", "game"):
        raise HTTPException(status_code=422, detail="view 必须是 original 或 game")
    # 原著访谈不触碰会话；本局访谈校验 session/书目并取只读快照（card_revision/source_hash 仅原著视图使用）。
    session_state = _game_session_state(book_dir, request.session_id) if request.view == "game" else None
    try:
        return _get_reader_chat().create_thread(book_dir, request.character_id, request.chapter_no,
            card_revision=request.card_revision, source_hash=request.source_hash,
            view=request.view, session_state=session_state)
    except HTTPException:
        raise
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.get("/api/reader-chat/threads/{thread_id}")
def get_reader_chat_thread(thread_id: str) -> dict[str, Any]:
    try:
        return _reader_thread(thread_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.post("/api/reader-chat/threads/{thread_id}/messages")
def send_reader_chat_message(thread_id: str, request: ReaderChatMessageRequest) -> dict[str, Any]:
    try:
        _reader_thread(thread_id)
        model = _preparation_model(request, required=True)
        return _get_reader_chat().send_message(thread_id, request.message,
            request_id=request.request_id, model_fn=model)
    except HTTPException:
        raise
    except Exception as exc:
        raise _workflow_error(exc) from exc


@app.get("/api/books/{book_id}/preparation")
def book_preparation_status(book_id: str, mode: str = "window",
                            target_chapter: int = 1) -> dict[str, Any]:
    """查看当前准备覆盖与身份校验状态（只读，不触发模型调用）。"""
    book_dir = _resolve_book_dir(book_id)
    try:
        return verify_preparation(book_dir, mode=mode, target_chapter=max(1, int(target_chapter)))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"准备状态读取失败：{exc}") from exc


@app.post("/api/books/{book_id}/locate")
def locate_book_scene(book_id: str, request: LocateRequest) -> dict[str, Any]:
    """原文证据定位开局场景；候选含章节/块坐标、歧义与时点标记。"""
    book_dir = _resolve_book_dir(book_id)
    model = None
    if request.semantic:
        key = request.api_key.strip()
        if not key:
            raise HTTPException(status_code=400, detail="语义定位需要模型调用，请提供 API Key")
        try:
            model = _request_model_callable(request.provider, request.base_url, key, request.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return {"candidates": locate_scene(book_dir, request.query, limit=request.limit, model=model)}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"场景定位失败：{exc}") from exc


@app.post("/api/books/{book_id}/locate/select")
def select_book_scene(book_id: str, request: LocateSelectRequest) -> dict[str, Any]:
    """确认开局位置与时点：返回证据、知识截止与时点限定初始事实。"""
    book_dir = _resolve_book_dir(book_id)
    model = None
    if request.project_facts:
        key = request.api_key.strip()
        if not key:
            raise HTTPException(status_code=400, detail="时点状态投影需要模型调用，请提供 API Key")
        try:
            model = _request_model_callable(request.provider, request.base_url, key, request.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        selected = select_scene(book_dir, request.candidate, request.timepoint)
        if request.project_facts:
            selected["initial_state"] = project_scene_initial_state(book_dir, selected, model=model)
        return selected
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"开局位置确认失败：{exc}") from exc


@app.get("/api/library/playable")
def playable_library() -> dict[str, Any]:
    """只列出已玩过且当前准备校验通过的作品（可复用开局，不删原著）。"""
    root = Path(fe.WRITABLE_DIR) / "books"
    if not root.is_dir():
        return {"books": []}
    try:
        return {"books": list_playable_books(root)}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"作品库读取失败：{exc}") from exc


@app.get("/api/books/{book_id}/search/occurrences")
def search_book_occurrences(book_id: str, q: str, mode: str = "exact",
                            page: int = 1, page_size: int = 20,
                            ignore_punctuation: bool = False,
                            ignore_whitespace: bool = False) -> dict[str, Any]:
    from core.services.book_search_service import search_occurrences, SearchQueryError, BookSourceError
    book_dir = _resolve_book_dir(book_id)
    try:
        return search_occurrences(book_dir, q, book_id=book_id, mode=mode,
                                  page=page, page_size=page_size,
                                  ignore_punctuation=ignore_punctuation,
                                  ignore_whitespace=ignore_whitespace)
    except SearchQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BookSourceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/books/{book_id}/search")
def search_user_book(book_id: str, q: str, limit: int = 20, max_chars: int = 5000) -> dict[str, Any]:
    """按字面检索全书索引并返回受限原文证据块。"""
    if not str(q or "").strip():
        raise HTTPException(status_code=400, detail="搜索词不能为空")
    book_dir = _resolve_book_dir(book_id)
    try:
        return retrieve_context(book_dir, q, limit=max(1, min(50, limit)), max_chars=max(200, min(12000, max_chars)))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail=f"索引不可用：{exc}") from exc


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


def _validated_gf_spec(spec: Any) -> dict[str, Any] | None:
    """开局推荐规格的服务端形状校验（D03）：不完整直接拒绝，不静默丢字段。"""
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise HTTPException(status_code=400, detail="golden_finger_spec 必须是规格对象")
    required = ("name", "cost", "cooldown", "effect", "limits")
    missing = [key for key in required if not str(spec.get(key) or "").strip()]
    if missing:
        raise HTTPException(
            status_code=400,
            detail="金手指规格不完整，缺少字段：" + "、".join(missing))
    return spec


@app.post("/api/sessions/start")
def start(request: StartRequest) -> StreamingResponse:
    book_dir = _resolve_book_dir(request.book_id) if request.book_id else None
    if book_dir is not None and (request.novel_upload_id or request.work):
        raise HTTPException(status_code=422, detail="book_id 不可与上传原著或 work 同时指定")
    try:
        config = fe.provider_config(request.provider or "deepseek", request.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="模型提供方配置无效") from exc
    gf_spec = _validated_gf_spec(request.golden_finger_spec)
    fullbook = request.preparation_mode == "fullbook" or request.mode.startswith("强化")
    if fullbook:
        if book_dir is None or not request.preparation_job_id:
            raise HTTPException(status_code=409, detail="请先完成当前原著的全书准备任务，再提交 book_id 与 preparation_job_id 开局")
        try:
            from core.services.preparation_jobs_service import _source
            service = _get_preparation_jobs()
            identity = service.book_identity(request.preparation_job_id)
            job = service.get(request.preparation_job_id)
            prepared_config = service.configuration(request.preparation_job_id)
            if (Path(identity["book_path"]).resolve() != book_dir.resolve()
                    or identity["source_hash"] != _source(book_dir)
                    or job.get("status") != "READY" or prepared_config.get("mode") != "fullbook"
                    or identity["book_id"] != book_dir.name
                    or not job.get("character_counts", {}).get("verified_cards")
                    or job.get("gap_report", {}).get("card_publication") != "complete"
                    or job.get("gap_report", {}).get("rich_character_extraction") != "complete"
                    or not job.get("published_characters")):
                raise ValueError("fullbook publication not ready")
        except Exception as exc:
            raise HTTPException(status_code=409, detail="当前原著全书准备尚未完成、已过期或未发布角色，请重新准备") from exc
    scene_selection = None
    target_chapter = request.target_chapter
    if request.chapter_selection and request.scene_selection:
        raise HTTPException(status_code=422, detail="仅可指定一种开局位置")
    selection = request.chapter_selection or request.scene_selection
    if selection:
        if book_dir is None:
            raise HTTPException(status_code=422, detail="开局位置必须指定 book_id")
        try:
            if request.chapter_selection or selection.get("kind") == "chapter_start":
                from core.services.reader_start_service import prepare_chapter_start
                evidence = selection.get("evidence", selection)
                if not evidence.get("source_hash"):
                    raise ValueError("chapter selection requires source hash")
                intent = prepare_chapter_start(book_dir, book_dir.name, evidence.get("chapter_no"),
                                               expected_source_hash=evidence["source_hash"])
                scene_selection = intent["scene_selection"]
            else:
                scene_selection = select_scene(book_dir, selection.get("candidate", {}),
                                               selection.get("timepoint", "during"))
            target_chapter = int(scene_selection["knowledge_cutoff"]["chapter_no"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=409, detail="开局场景证据无效或已过期，请重新选择") from exc
    try:
        session = sessions.create(request.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    candidate_api_key = request.api_key.strip()
    provider = request.provider or "deepseek"
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
    # 已玩标记目标目录：book_id 开局直接用解析出的书目录；上传开局用
    # books/<上传文件名主干>（上传 novel TXT 时已同步切章落盘）。
    played_book_dir = (
        book_dir if book_dir is not None
        else (Path(fe.WRITABLE_DIR) / "books" / Path(novel_path).stem if novel_path else None)
    )
    if request.mode.startswith("强化") and book_dir is None:
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
    # 试卷档位门禁（重构 M4，双侧校验第一侧）：显式 paper_tier 才校验拒绝；
    # 缺省（旧客户端）由 on_start 按丰富度就近映射并钳制，不在此拦截。
    if request.paper_tier is not None:
        mode_label = "强化" if request.mode.startswith("强化") else "普通"
        agent_on = bool(request.story_agent_mode) and request.mode.startswith("强化")
        tier_ok, tier_reason = engine.papers.validate_selection(
            int(request.paper_tier), mode_label, agent_on)
        if not tier_ok:
            sessions.release(session)
            raise HTTPException(status_code=400, detail=tier_reason)
    params = dict(
        provider=provider,
        base_url=request.base_url or config["base_url"],
        api_key=candidate_api_key,
        remember=False,
        model=request.model or (config.get("models") or [fe.DEFAULT_MODEL])[0],
        thinking_mode=request.thinking_mode,
        thinking_param=request.thinking_param,
        mode=request.mode,
        work=None if request.mode.startswith("强化") or book_dir is not None else request.work,
        novel_file=novel_path,
        book_dir=str(book_dir) if book_dir is not None else None,
        novel_display_name=novel_display_name,
        fragment=request.fragment,
        role=request.role,
        protagonist_gender=request.protagonist_gender,
        timepoint=request.timepoint,
        preparation_mode=request.preparation_mode,
        target_chapter=target_chapter,
        scene_selection=scene_selection,
        difficulty=request.difficulty,
        gf=request.golden_finger or (fe.GOLDEN_FINGERS[0] if fe.GOLDEN_FINGERS else "无（凡人开局）"),
        gf_custom=request.golden_finger_proposal,
        gf_spec=gf_spec,
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
        nemesis_identity=request.nemesis_identity,
        convergence=request.convergence,
        story_richness=request.story_richness,
        paper_tier=request.paper_tier,
        story_agent_mode=request.story_agent_mode,
        roster_card_ids=request.roster_card_ids,
    )
    try:
        generator = gradio_app.on_start(**params)
    except Exception as exc:  # noqa: BLE001
        sessions.release(session)
        raise HTTPException(status_code=400, detail=f"开局初始化失败：{exc}") from exc
    return _stream_response(
        session, generator, operation="start", api_key_on_commit=candidate_api_key,
        client_request_id=request.client_request_id, played_book_dir=played_book_dir)


@app.post("/api/sessions/{session_id}/messages")
def message(session_id: str, request: MessageRequest) -> StreamingResponse:
    session = _session_or_404(session_id)
    state = _require_game(session)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    provider = request.provider or state.get("provider") or "deepseek"
    config = fe.provider_config(provider, request.base_url or state.get("base_url"))
    try:
        working_state = copy.deepcopy(state)
        working_state["save_stage"] = "streaming"
        generator = gradio_app.on_send(
            provider,
            request.base_url or state.get("base_url") or config["base_url"],
            request.api_key.strip() if request.api_key is not None else session.api_key,
            request.model or state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0],
            request.thinking_mode or state.get("thinking_mode") or "auto",
            request.thinking_param if request.thinking_param is not None else state.get("thinking_param", ""),
            request.message,
            working_state.get("history", []),
            working_state,
        )
    except Exception as exc:  # noqa: BLE001
        sessions.release(session)
        raise HTTPException(status_code=400, detail=f"消息初始化失败：{exc}") from exc
    return _stream_response(session, generator, operation="message",
                           client_request_id=request.client_request_id)


@app.post("/api/sessions/{session_id}/questions/batch")
def question_batch(session_id: str, request: QuestionBatchRequest) -> dict[str, Any]:
    """Batch structured questions: returns answers with confidence and evidence refs."""
    session = _session_or_404(session_id)
    state = session.state or {}
    
    def model_fn(question: dict[str, Any]) -> Any:
        # Simple stub: real implementation would call LLM with question prompt
        # For now, return first choice or empty string
        choices = question.get("choices") or []
        if question.get("answer_type") == "single_choice" and choices:
            return choices[0].get("id", "")
        if question.get("answer_type") == "multi_choice":
            return []
        return ""
    
    try:
        results = question_service.batch(
            request.questions,
            model=model_fn if session.api_key else None,
            context=request.context,
            max_concurrency=request.max_concurrency
        )
        return {"results": results, "count": len(results)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"批量问题处理失败：{exc}") from exc


@app.post("/api/sessions/{session_id}/questions/answer")
def answer_question(session_id: str, request: AnswerQuestionRequest) -> dict[str, Any]:
    """Submit a single structured answer (user or model)."""
    _session_or_404(session_id)
    try:
        result = question_service.answer(
            request.question,
            request.answer,
            context=request.context,
            source="user"
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"回答提交失败：{exc}") from exc


@app.post("/api/setup/questions")
def setup_questions(request: dict[str, Any]) -> dict[str, Any]:
    """Generate setup questions for pre-game (no session required)."""
    purpose = str(request.get("purpose", "pre_game_setup"))
    prepared_script = request.get("prepared_script", {})
    
    if purpose not in structured_question_service.PURPOSES:
        raise HTTPException(status_code=400, detail=f"无效的问题用途：{purpose}")
    
    # Generate questions based on purpose
    questions = []
    if purpose == "pre_game_setup":
        # Example: world, stage, player difficulty questions
        questions = [
            structured_question_service.make_question(
                "pre_game_setup", "world_difficulty",
                "根据开局脚本，世界的整体危险程度如何？",
                answer_type="single_choice",
                choices=[
                    {"id": "1", "label": "1 - 安全祥和"},
                    {"id": "4", "label": "4 - 中等风险"},
                    {"id": "7", "label": "7 - 高度危险"},
                    {"id": "9", "label": "9 - 末日级"},
                ],
                evidence_refs=[{"kind": "script", "excerpt": str(prepared_script.get("script", ""))[:200]}]
            )
        ]
    
    return {"questions": questions, "purpose": purpose}


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
                api_key=session.api_key or (request.api_key or "").strip(),
                session_id=session.session_id)
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
                session.api_key or (request.api_key or "").strip(),
                provider, state.get("base_url") or config["base_url"])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"模型客户端初始化失败：{exc}") from exc
        game_difficulty = _game_difficulty_of(state)
        level = _quest_difficulty_level(request.difficulty, game_difficulty)
        range_lo, range_hi = engine.quest.quest_difficulty_range(game_difficulty)
        prompt = engine.quest.quest_offer_prompt(
            engine.quest.build_quest_context(state, kind, level))
        # 任务生成加 120s 读超时并重试一次：长上下文（整书剧情）下
        # 模型偶发慢响应不应挂死会话锁（实测 ReadTimeout 踩过）。
        # v2.0.4: 初始化独立 usage 累加器（回合外调用，阶段 E）
        from core.engine import token_accounting
        token_accounting.init_turn_usage()
        
        quest_kwargs = dict(state.get("request_kwargs") or {})
        quest_kwargs.setdefault("timeout", 120.0)
        text = None
        last_exc: Exception | None = None
        for _attempt in range(2):
            try:
                text = distill_model(client, model, prompt, quest_kwargs, provider, usage_category="quest")
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
        
        # v2.0.4: 收集本次调用的 usage（回合外端点，阶段 E）
        usage_data = token_accounting.get_turn_usage() or {}
        usage_response = {
            "total": usage_data.get("total_tokens", 0),
            "prompt": usage_data.get("prompt_tokens", 0),
            "completion": usage_data.get("completion_tokens", 0),
        }
        
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
        }, "usage": usage_response}
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
def autoplay_choice(session_id: str,
                    request: AutoplayChoiceRequest | None = None) -> dict[str, Any]:
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
        body_key = (request.api_key or "").strip() if request is not None else ""
        try:
            client = fe.make_client(
                session.api_key or body_key,
                provider, state.get("base_url") or config["base_url"])
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
    # 惰性自愈：重试封顶的失败章原本要等「下一回合」的 enqueue 才复活，
    # 但回合可能被质检/门禁循环长时间占住（实测 agent 模式下蒸馏因此停滞）。
    # 前端会轮询本端点，借轮询触发重入队；60s 冷却避免高频轰炸上游。
    if distiller is not None and failed and not running:
        now = time.monotonic()
        last = getattr(distiller, "_requeue_poll_at", 0.0)
        if now - last >= 60.0:
            try:
                distiller._requeue_poll_at = now
                distiller.enqueue(current, lookahead=6, lookback=-1, total=total or None)
                distiller.start()
            except Exception:  # noqa: BLE001  自愈失败不影响进度汇报
                pass
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
    payload = dict(session.state)
    # 开局蒸馏进行中：session.state 尚未提交 st0，从进程级进度注册表合并
    # 最新阶段（以 distill_key / chapter_index.book_id 为键），供前端轮询显示。
    if not payload.get("opening_distill") or (
            isinstance(payload.get("opening_distill"), dict)
            and payload["opening_distill"].get("status") != "done"):
        book_dir = str(payload.get("distill_key") or "").strip()
        if not book_dir:
            index = payload.get("chapter_index") if isinstance(payload.get("chapter_index"), dict) else {}
            book_id = (index or {}).get("book_id")
            if book_id:
                book_dir = str(Path(fe.WRITABLE_DIR) / "books" / str(book_id))
        if book_dir:
            progress = opening_service.peek_progress(book_dir)
            if progress:
                payload["opening_distill"] = progress
        elif (payload.get("game_ready") is True
              and str(payload.get("mode") or "").startswith("强化")
              and payload.get("opening_confirmed") is not True):
            payload["opening_distill"] = {"status": "running", "stage": "正在准备开局内容", "stage_key": "start"}
    return {"session_id": session.session_id, "state": public_state(payload)}


@app.post("/api/sessions/{session_id}/save")
def save(session_id: str, request: SaveRequest) -> dict[str, Any]:
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        try:
            engine.persistence.save_state(
                state,
                save_id=request.save_id,
                root=fe.WRITABLE_DIR,
                start_params=state.get("start_params"),
                session_id=session.session_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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


def _normalize_restored_state(restored: dict[str, Any], session_id: str) -> dict[str, Any]:
    """统一旧/新存档的开局确认态，不修复缺失的正式选项。"""
    nested = restored.get("opening_state") if isinstance(restored.get("opening_state"), dict) else {}
    for key in ("gf_confirmed", "opening_confirmed"):
        if key not in restored and key in nested:
            restored[key] = bool(nested[key])
    restored.setdefault("gf_confirmed", False)
    restored.setdefault("opening_confirmed", False)
    restored["session_id"] = session_id
    if not isinstance(restored.get("story_ledger"), list):
        restored["story_ledger"] = []
    if not isinstance(restored.get("sequence_feedback"), list):
        restored["sequence_feedback"] = []
    if not isinstance(restored.get("plot_thread_map"), dict):
        restored["plot_thread_map"] = {}
    if not isinstance(restored.get("generation_brief"), dict):
        restored["generation_brief"] = {}
    if not isinstance(restored.get("repair_report"), dict):
        restored["repair_report"] = {}
    restored["degraded"] = bool(restored.get("degraded", False))
    for key, default in (("chapter_arc_plan", {}), ("task_registry", []), ("task_progress_log", []), ("conversation_memory", []), ("conversation_commitments", []), ("mechanism_windows", {})):
        if not isinstance(restored.get(key), type(default)):
            restored[key] = default
    stage = save_contract.classify_state(restored)
    restored["save_stage"] = stage
    restored["game_ready"] = stage in ("opening", "committed")
    if not isinstance(restored.get("options"), list):
        restored["options"] = None
    if not str(restored.get("distill_key") or "").strip():
        index = restored.get("chapter_index") if isinstance(restored.get("chapter_index"), dict) else {}
        book_id = (index or {}).get("book_id")
        if book_id:
            restored["distill_key"] = str(Path(fe.WRITABLE_DIR) / "books" / str(book_id))
    od = restored.get("opening_distill")
    if not isinstance(od, dict) or od.get("status") != "done":
        distill = restored.get("distill") if isinstance(restored.get("distill"), dict) else {}
        anchors_dir = Path(str(restored.get("distill_key") or "")) / "anchors"
        try:
            anchors_count = (len(list(anchors_dir.glob("[0-9]" * 4 + ".json")))
                             if anchors_dir.is_dir() else 0)
        except OSError:
            anchors_count = 0
        plot_ready = bool(distill.get("plot_summary"))
        if plot_ready or anchors_count:
            restored["opening_distill"] = {
                "status": "done", "stage": "开局蒸馏完成（读档恢复）", "ok": True,
                "plot_ready": plot_ready, "restored": True, "anchors_on_disk": anchors_count,
            }
        elif str(restored.get("mode") or "").startswith("强化") and stage == "opening":
            restored["opening_distill"] = {
                "status": "done", "stage": "该存档未含开局蒸馏产物，本局按需自动补锚点",
                "ok": False, "restored": True,
            }
    return restored


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
    restored = _normalize_restored_state(restored, session.session_id)
    if not save_contract.is_usable_state(restored):
        raise HTTPException(status_code=409, detail="存档一致性错误：正式对局缺少完整 A-F 选项，无法继续")
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
        restored = _normalize_restored_state(restored, session.session_id)
        if not save_contract.is_usable_state(restored):
            raise HTTPException(status_code=409, detail="存档一致性错误：正式对局缺少完整 A-F 选项，无法继续")
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

    segments, source_meta, source_check = exporter.prepare_source(state)
    source_kind = source_meta["source_kind"]
    story_ledger = state.get("story_ledger") if isinstance(state.get("story_ledger"), list) else []
    if source_kind == "story_ledger" and not source_check.get("ok"):
        raise HTTPException(status_code=409, detail={"message":"故事账本存在缺口或重复回合，拒绝导出不完整小说", "continuity": source_check})
    if source_kind == "history_fallback" and not source_check.get("complete"):
        raise HTTPException(status_code=409, detail={"message":"旧存档叙事来源无法证明完整，拒绝导出", "continuity": source_check})
    if not segments:
        raise HTTPException(status_code=400, detail="没有可导出的正文")
    if source_kind == "history_fallback" and not source_check.get("complete"):
        raise HTTPException(status_code=409, detail={"message":"旧存档叙事来源无法证明完整，拒绝导出","continuity":source_check})
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
    work_meta = {key: params.get(key) or state.get(key) for key in ("work", "novel", "mode")}
    context = "；".join(
        f"{label}：{value}" for label, value in (("作品", work_meta["work"]), ("篇目", work_meta["novel"]))
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
    from core.engine.export_continuity import report as continuity_report
    continuity = continuity_report(segments)
    chapter_texts: dict[Any, dict[str, Any]] = {}
    for record in records:
        if record.get("output"):
            chapter_texts[record["chapter_idx"]] = {
                "index": int(record["chapter_idx"]),
                "title": str(record.get("chapter_title") or ""),
                "text": str(record["output"]),
            }
    manifest = exporter.build_export_manifest(
        {**source_meta, **work_meta}, chapters, style, model_used=True)
    token_chars = sum(len(r.get("prompt", "")) + len(str(r.get("output") or "")) for r in records)
    return {
        "manifest": manifest,
        "chapters": [chapter_texts[key] for key in sorted(chapter_texts)],
        "full_text": full_text,
        "continuity": continuity,
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
        # DEF-E-04：会话密钥只在 /start 提交时驻留内存；重启恢复后为空时用请求体兜底。
        creds = {
            "provider": state.get("provider"),
            "base_url": state.get("base_url"),
            "api_key": session.api_key or (request.api_key or "").strip(),
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
    restored = _normalize_restored_state(restored, "export")
    if not save_contract.is_usable_state(restored):
        raise HTTPException(status_code=409, detail="存档一致性错误：正式对局缺少完整 A-F 选项，无法导出")
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


# —— Copilot 助手：状态总览 / 文档检索 / 白名单工具对话 ——


@app.get("/api/copilot/overview")
def copilot_overview(session_id: str | None = None) -> dict[str, Any]:
    """面板初始化数据：对局快照 + 功能入口目录 + 工具清单 + 手册目录。"""
    state = None
    if session_id:
        session = sessions.get(session_id)
        if session is not None:
            state = session.state
    return {
        "snapshot": copilot_service.state_snapshot(state),
        "entries": copilot_service.ENTRIES,
        "tools": [{"name": t["name"], "desc": t["desc"]} for t in copilot_service.TOOLS.values()],
        "doc_sections": copilot_service.doc_catalog(),
    }


@app.get("/api/copilot/docs")
def copilot_docs(q: str = "") -> dict[str, Any]:
    """用户手册检索：无关键词时返回前几节节选。"""
    return {"query": q, "results": copilot_service.search_docs(q)}


def _copilot_tool_actions(session, request: CopilotChatRequest) -> dict[str, Any]:
    """构建操作工具闭包：只在 chat 端点持有会话锁期间被调用。

    只包裹已有端点同源逻辑（存/读/准备/托管/任务/导出），不新增权限面；
    无对局时这些闭包不会被注入，服务层会向模型返回「当前不可用」。
    """
    actions: dict[str, Any] = {}
    if session is None or not save_contract.is_usable_state(session.state):
        return actions
    state = session.state

    def _save_game(save_id: str = "latest") -> dict[str, Any]:
        engine.persistence.save_state(
            state, save_id=str(save_id or "latest")[:96], root=fe.WRITABLE_DIR,
            start_params=state.get("start_params"), session_id=session.session_id)
        return {"saved": True, "save_id": str(save_id or "latest")[:96]}

    def _load_save(save_id: str) -> dict[str, Any]:
        restored = engine.persistence.load_state_strict(
            str(save_id or ""), root=fe.WRITABLE_DIR)
        if not restored or not restored.get("system"):
            raise ValueError(f"未找到存档 {save_id}")
        restored = _normalize_restored_state(restored, session.session_id)
        if not save_contract.is_usable_state(restored):
            raise ValueError("存档一致性错误：无法恢复为正式对局")
        session.state = restored
        return {"loaded": True, "save_id": str(save_id),
                "round": restored.get("round"), "mode": restored.get("mode")}

    def _create_preparation_job(book_id: str, mode: str = "window") -> dict[str, Any]:
        book_dir = _resolve_book_dir(str(book_id or ""))
        job_mode = "fullbook" if str(mode) == "fullbook" else "window"
        service = _get_preparation_jobs()
        job = service.create(
            book_dir, idempotency_key=f"copilot-{uuid.uuid4().hex[:16]}",
            mode=job_mode, model_version="copilot")
        if job["status"] == "QUEUED":
            provider = str(request.provider or state.get("provider") or "deepseek")
            config = fe.provider_config(provider, request.base_url or state.get("base_url"))
            model = request.model or state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0]
            service.start(job["job_id"], model=model)
        return {"job_id": job["job_id"], "status": job["status"], "mode": job_mode}

    def _autoplay_choice() -> dict[str, Any]:
        options = state.get("options") or []
        if not options:
            raise ValueError("当前没有可选选项，无法托管")
        provider = state.get("provider") or "deepseek"
        config = fe.provider_config(provider, state.get("base_url"))
        model = state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0]
        client = fe.make_client(session.api_key, provider,
                                state.get("base_url") or config["base_url"])
        prompt = engine.autoplay.build_autoplay_prompt(state, options, state.get("history"))
        text = distill_model(client, model, prompt, state.get("request_kwargs"), provider)
        choice = engine.autoplay.parse_autoplay_choice(text, options)
        return {"choice": choice["choice"], "reason": choice["reason"]}

    def _quest_accept() -> dict[str, Any]:
        engine.quest.accept(state)
        return {"quest": state["quest"]}

    def _quest_decline() -> dict[str, Any]:
        box = state.get("quest") if isinstance(state.get("quest"), dict) else {}
        if box.get("status") != "offered":
            raise ValueError("当前没有可婉拒的任务 offer")
        state["quest"] = {"status": "none"}
        return {"quest": state["quest"]}

    def _export_novel(style: str = "faithful") -> dict[str, Any]:
        creds = {"provider": state.get("provider"), "base_url": state.get("base_url"),
                 "api_key": session.api_key, "model": state.get("model")}
        result = _run_export(state, str(style or "faithful"), creds)
        return {"chapters": len(result.get("chapters") or []),
                "chars": len(result.get("full_text") or ""),
                "manifest": result.get("manifest")}

    actions.update(save_game=_save_game, load_save=_load_save,
                   create_preparation_job=_create_preparation_job,
                   autoplay_choice=_autoplay_choice,
                   quest_accept=_quest_accept, quest_decline=_quest_decline,
                   export_novel=_export_novel)
    return actions


@app.post("/api/copilot/chat")
def copilot_chat(request: CopilotChatRequest) -> dict[str, Any]:
    """Copilot 对话：工具循环在服务端执行（白名单注册表），凭据不落日志。

    会话锁被对局回合或上一次 Copilot 请求占用时不再直接 409，而是降级为
    纯对话模式：不读对局状态（避免与写入方并发）、不注入任何操作工具，
    文档检索等只读工具仍然可用。
    """
    session = None
    degraded = False
    if request.session_id:
        session = _session_or_404(request.session_id)
        degraded = not sessions.acquire(session)
    held = session is not None and not degraded
    try:
        provider = str((session and session.state.get("provider"))
                       or request.provider or "deepseek")
        base_url = (session and session.state.get("base_url")) or request.base_url
        api_key = str((session and session.api_key) or request.api_key or "").strip()
        if not api_key:
            raise HTTPException(
                status_code=400, detail="缺少 API Key：请先在 AI 配置中填写，或使用进行中对局的凭据")
        config = fe.provider_config(provider, base_url)
        model = str(request.model or (session and session.state.get("model"))
                    or (config.get("models") or [fe.DEFAULT_MODEL])[0])
        if held:
            ctx = {
                "state": session.state,
                "writable_root": fe.WRITABLE_DIR,
                "actions": _copilot_tool_actions(session, request),
            }
            busy_note = ""
        else:
            ctx = {"state": None, "writable_root": fe.WRITABLE_DIR, "actions": {}}
            busy_note = ("" if session is None else
                         "【降级模式】当前会话正在处理另一个请求（例如回合生成或上一次提问）。"
                         "本条回复无法读取对局状态，也无法执行存档/准备/导出等操作；"
                         "请基于对话历史、用户手册与只读工具回答，"
                         "并在回答开头简要说明当前处于繁忙降级模式。")
        try:
            return copilot_service.handle_chat(
                request.messages, ctx, provider=provider, base_url=base_url,
                api_key=api_key, model=model, busy_note=busy_note)
        except copilot_service.CopilotClientError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except copilot_service.CopilotUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if held:
            sessions.release(session)


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
        "base": "http://127.0.0.1:" + str(os.getenv("FATE_API_PORT", "21560")),
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


class UiStateRequest(BaseModel):
    session_id: str
    ui_state: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/session/ui-state")
def save_ui_state(request: UiStateRequest) -> dict[str, Any]:
    """保存前端UI状态到会话内存，刷新/换设备后可恢复。"""
    session = sessions.get(request.session_id)
    if session is None:
        session = sessions.create(request.session_id)
    sessions.save_ui_state(session, request.ui_state)
    return {"ok": True, "session_id": request.session_id}


@app.get("/api/session/ui-state")
def get_ui_state(session_id: str) -> dict[str, Any]:
    """获取已保存的UI状态。"""
    session = sessions.get(session_id)
    if session is None:
        return {"ok": True, "session_id": session_id, "ui_state": {}}
    ui_state = sessions.get_ui_state(session)
    return {"ok": True, "session_id": session_id, "ui_state": ui_state}


# ========== 角色闲聊 API（v2.0.4 Agent_refill 优化） ==========

@app.get("/api/chat/roster")
def get_chat_roster(session_id: str) -> dict[str, Any]:
    """获取当前活跃角色列表供闲聊下拉框。"""
    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        roster = chat_service.get_roster(state)
        return {"roster": roster}
    finally:
        sessions.release(session)


class ChatSendRequest(BaseModel):
    character_name: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)


@app.post("/api/chat/send")
def send_chat_message(session_id: str, request: ChatSendRequest) -> dict[str, Any]:
    """与角色闲聊（agent_refill 模式，批改-重填循环提升质量）。

    状态隔离保证：只写 state["side_chats"]，不影响 history/state_memory/quest。
    """
    from core.engine import token_accounting

    session = _session_or_404(session_id)
    if not sessions.acquire(session):
        raise HTTPException(status_code=409, detail="该 session 正在处理另一个请求")
    try:
        state = _require_game(session)
        provider = state.get("provider") or "deepseek"
        config = fe.provider_config(provider, state.get("base_url"))
        try:
            client = fe.make_client(
                session.api_key, provider,
                state.get("base_url") or config["base_url"])
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"聊天初始化失败：{exc}") from exc
        try:
            result = chat_service.generate_reply(
                request.character_name, request.message, state,
                client=client,
                model=state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0],
                request_kwargs=state.get("request_kwargs"),
                provider=provider)

            # 保存聊天记录（状态隔离：只写 side_chats）
            chat_service.save_chat(state, request.character_name, request.message, result["reply"])
            session.state = state

            # v2.0.4: 回合外独立调用立即上报 usage（阶段 E 实时计量）
            usage_data = token_accounting.get_turn_usage() or {}
            return {
                "reply": result["reply"],
                "character": result["character"],
                "meta": result.get("meta", {}),
                "usage": {
                    "total": usage_data.get("total_tokens", 0),
                    "prompt": usage_data.get("prompt_tokens", 0),
                    "completion": usage_data.get("completion_tokens", 0),
                },
            }
        except chat_service.ChatClientError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except chat_service.ChatUpstreamError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
    finally:
        sessions.release(session)


# ========== 前端路由 ==========

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
    uvicorn.run(app, host=os.getenv("FATE_API_HOST", "127.0.0.1"), port=int(os.getenv("FATE_API_PORT", "21560")))
