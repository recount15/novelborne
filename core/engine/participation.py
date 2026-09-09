"""强化模式的人物参与度、场景字数与交互/锚点约束机制。

本模块只负责确定性规则计算和提示块装配，不调用模型，也不依赖 UI 状态。
同一组输入始终得到同一结果，便于回放、存档和离线校验。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping, Sequence

PARTICIPATION_MIN = 1
PARTICIPATION_MAX = 9
SCENE_TARGET = 1000
SCENE_MIN = 850
SCENE_MAX = 1150

# —— 故事丰富度 ——
# 玩家可拖动的单回合叙事体量刻度；对外只叫「故事丰富度」，不暴露字数语义。
# 门禁区间由刻度按固定容差派生，因此 1000 刻度恰好还原历史的 850–1150。
RICHNESS_MIN = 300
RICHNESS_MAX = 1000
RICHNESS_DEFAULT = 700
RICHNESS_STEP = 50
SCENE_TOLERANCE = 0.15

# 每档给出玩家可读的说明与模型要求；门槛越高越依赖强模型与思考模式。
RICHNESS_TIERS = (
    (450, "轻盈", "轻量模型也能稳定达成，适合快节奏推进"),
    (650, "适中", "主流模型可稳定达成，叙事与节奏平衡"),
    (820, "厚重", "建议使用带思考模式的模型，场景更完整"),
    (RICHNESS_MAX, "沉浸", "需要强模型并开启思考模式，否则容易触发门禁回滚"),
)

_RELATION_SCORES = {
    "陌生": 1,
    "疏远": 2,
    "敌对": 2,
    "普通": 4,
    "认识": 4,
    "合作": 6,
    "信任": 7,
    "亲密": 8,
    "羁绊": 9,
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _scale_relevance(value: Any) -> float:
    """将行动相关性统一为 0--1；支持 0--1、1--9 和常用文本标签。"""
    if isinstance(value, str):
        labels = {"无关": 0.0, "低": 0.25, "中": 0.5, "高": 0.75, "关键": 1.0}
        if value.strip() in labels:
            return labels[value.strip()]
    number = _number(value, 0.0)
    if number <= 1:
        return max(0.0, min(1.0, number))
    return max(0.0, min(1.0, number / 9.0))


def _relation_score(value: Any) -> float:
    if isinstance(value, str):
        text = value.strip()
        if text in _RELATION_SCORES:
            return float(_RELATION_SCORES[text])
    return max(1.0, min(9.0, _number(value, 4.0)))


def _last_round(value: Any, current_round: int) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        value = value.get("round", value.get("last_appeared_round"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not value:
            return None
        value = max(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compute_participation(
    pool_size: int = 1,
    chapter: int = 1,
    round_no: int = 1,
    last_appeared_round: Any = None,
    action_relevance: Any = 0.0,
    relationship_state: Any = "普通",
    role: str = "伙伴",
    cooldown_span: int | None = None,
) -> dict[str, Any]:
    """计算伙伴/女主本回合参与度（1--9）及确定性出场决策。

    ``probability`` 是规则概率而非随机抽样值；``appear`` 在满足冷却且概率不低于
    0.5 时为真。池越大，单个角色的基础参与度越低；行动越相关、关系越紧密，参与度越高。
    """
    pool = max(0, int(_number(pool_size, 1)))
    chapter_no = max(1, int(_number(chapter, 1)))
    current = max(1, int(_number(round_no, 1)))
    relevance = _scale_relevance(action_relevance)
    relation = _relation_score(relationship_state)
    role_name = str(role or "伙伴")

    if pool == 0:
        return {
            "level": 1,
            "probability": 0.0,
            "appear": False,
            "cooldown": 0,
            "cooldown_remaining": 0,
            "next_cooldown": 0,
            "pool_size": 0,
            "role": role_name,
        }

    # 章节/回合是轻微的节拍扰动，使用取模避免伪随机和回放漂移。
    beat = ((chapter_no * 3 + current * 5) % 7 - 3) / 6.0
    pool_penalty = min(3.0, (pool - 1) * 0.8)
    role_bonus = 0.25 if role_name == "女主" else 0.0
    raw = 4.5 + beat + relevance * 2.8 + (relation - 4.0) * 0.35 + role_bonus - pool_penalty
    level = max(PARTICIPATION_MIN, min(PARTICIPATION_MAX, int(round(raw))))
    probability = round(level / PARTICIPATION_MAX, 4)

    last = _last_round(last_appeared_round, current)
    span = cooldown_span if cooldown_span is not None else max(1, 4 - (level // 3))
    span = max(0, int(_number(span, 1)))
    elapsed = None if last is None else max(0, current - last)
    cooldown_remaining = 0 if elapsed is None else max(0, span - elapsed)
    appear = cooldown_remaining == 0 and probability >= 0.5
    next_cooldown = span if appear else cooldown_remaining
    return {
        "level": level,
        "probability": probability,
        "appear": appear,
        "cooldown": next_cooldown,
        "cooldown_remaining": cooldown_remaining,
        "next_cooldown": next_cooldown,
        "pool_size": pool,
        "chapter": chapter_no,
        "round": current,
        "role": role_name,
        "relevance": round(relevance, 4),
        "relationship": round(relation, 2),
    }


# 语义更直观的兼容入口。
calculate_participation = compute_participation
participation_decision = compute_participation


def normalize_richness(value: Any = RICHNESS_DEFAULT) -> int:
    """故事丰富度归一化：钳制到 300–1000 并对齐步进，保证回放与存档确定。"""
    number = _number(value, RICHNESS_DEFAULT)
    if number <= 0:
        number = RICHNESS_DEFAULT
    snapped = int(round(number / RICHNESS_STEP)) * RICHNESS_STEP
    return max(RICHNESS_MIN, min(RICHNESS_MAX, snapped))


def richness_tier(value: Any = RICHNESS_DEFAULT) -> dict[str, Any]:
    """返回故事丰富度档位说明；``thinking_recommended`` 供 UI 提示强模型需求。"""
    richness = normalize_richness(value)
    label, note = RICHNESS_TIERS[-1][1], RICHNESS_TIERS[-1][2]
    for bound, tier_label, tier_note in RICHNESS_TIERS:
        if richness <= bound:
            label, note = tier_label, tier_note
            break
    return {
        "richness": richness,
        "label": label,
        "note": note,
        "thinking_recommended": richness >= RICHNESS_TIERS[1][0] + 1,
    }


def scene_budget(target: int = SCENE_TARGET, minimum: int | None = None, maximum: int | None = None,
                 richness: Any = None) -> dict[str, int]:
    """返回强化模式单回合场景体量约束，并规范边界。

    传入 ``richness``（故事丰富度 300–1000）时，目标与上下界按固定容差派生，
    因此丰富度 1000 与历史固定区间 850–1150 完全一致；未传则沿用显式参数。
    """
    if richness is not None:
        target = normalize_richness(richness)
        minimum = maximum = None
    target = max(1, int(target))
    if minimum is None:
        minimum = int(round(target * (1 - SCENE_TOLERANCE)))
    if maximum is None:
        maximum = int(round(target * (1 + SCENE_TOLERANCE)))
    minimum = max(1, int(minimum))
    maximum = max(minimum, int(maximum))
    target = max(minimum, min(maximum, target))
    return {"target": target, "minimum": minimum, "maximum": maximum}


def build_scene_budget_prompt(chapter: int = 1, round_no: int = 1, target: int = SCENE_TARGET,
                              richness: Any = None) -> str:
    """生成强化模式单回合体量提示块（对模型仍以明确字数下达要求）。"""
    budget = scene_budget(target, richness=richness)
    return (
        f"【强化模式场景预算】当前第{max(1, int(chapter))}章、第{max(1, int(round_no))}回合。"
        f"本回合正文目标约{budget['target']}字，允许{budget['minimum']}–{budget['maximum']}字。"
        "只统计剧情正文，不统计选项、回合日志、提示块和代码标记；不得用重复句或元叙事凑字数。"
    )


def validate_scene_length(text: str, minimum: int | None = None, maximum: int | None = None,
                          richness: Any = None) -> dict[str, Any]:
    """校验生成正文长度，返回可持久化的机械校验结果。

    未显式给出边界时按 ``richness`` 派生；两者都缺省则回退历史固定区间。
    """
    content = str(text or "")
    count = len(content)
    if richness is not None:
        budget = scene_budget(richness=richness)
        low, high = budget["minimum"], budget["maximum"]
    else:
        low = SCENE_MIN if minimum is None else int(minimum)
        high = SCENE_MAX if maximum is None else int(maximum)
    return {
        "valid": low <= count <= high,
        "chars": count,
        "minimum": low,
        "maximum": high,
        "shortfall": max(0, low - count),
        "excess": max(0, count - high),
    }


check_scene_length = validate_scene_length
validate_scene_budget = validate_scene_length


def build_interaction_constraint_block(
    characters: Sequence[Mapping[str, Any]] | None = None,
    action: str = "",
    relationship_state: Any = "普通",
) -> str:
    """生成角色交互的机械提示块，要求交互产生可观测后果并更新关系。"""
    names = [str(item.get("name", "未命名角色")) for item in (characters or ())]
    names_text = "、".join(names) if names else "当前场景角色"
    return (
        "【角色交互约束】\n"
        f"本回合可交互角色：{names_text}。\n"
        f"关系状态基线：{relationship_state}；玩家行动：{action or '待输入'}。\n"
        "每次交互必须由角色既有目标、性格和当前关系驱动；至少给出一句回应、一个可观测动作或代价，"
        "并记录关系变化（升温、稳定、恶化或无变化）。不得替角色替玩家做关键决定，不得凭空新增未登记角色。"
    )


build_character_interaction_block = build_interaction_constraint_block


_INTERACTION_MARKERS = re.compile(
    r"回应|答道|说道|问道|点头|摇头|看向|递出|接过|拦住|跟随|协助|配合|守住|检查|救治|出手|行动|示意"
)


def validate_character_interaction(text: str, characters: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """校验被规则选中的角色是否真实参与本回合正文。

    没有活跃角色时不强制角色出场；存在活跃角色时，正文必须至少点名一人，
    且包含一个可观察的回应或动作词。结果只包含 JSON 基础类型，适合落盘。
    """
    content = str(text or "")
    names = [str(item.get("name") or "").strip() for item in (characters or ())]
    names = [name for name in names if name]
    mentioned = [name for name in names if name in content]
    marker = _INTERACTION_MARKERS.search(content)
    required = bool(names)
    valid = not required or bool(mentioned and marker)
    return {
        "valid": valid,
        "required": required,
        "active_names": names,
        "mentioned_names": mentioned,
        "interaction_marker": marker.group(0) if marker else "",
    }


CONVERGENCE_LEVELS = ("一般", "较高", "极高")
CONVERGENCE_DEFAULT = "较高"


def normalize_convergence(value: Any) -> str:
    """收束力档位归一化：只认「一般」「较高」「极高」，其余一律按默认「较高」。"""
    text = str(value or "").strip()
    return text if text in CONVERGENCE_LEVELS else CONVERGENCE_DEFAULT


_CONVERGENCE_RULES = {
    "一般": (
        "锚点应尽量发生；当积势足够时，允许玩家行动完全扭转锚点的发生形式，"
        "甚至让锚点以截然不同的结果落地，只要因果可信、代价清晰。"
    ),
    "较高": (
        "锚点必须以原著因果兼容的某种形式发生，只允许改变路径、代价、见证者或局部关系；"
        "不得直接取消锚点；积势足够（相容性 K≥60）时，允许锚点以偏移形式发生。"
    ),
    "极高": (
        "锚点必须原封不动地发生，事件、结果与关键见证缺一不可；"
        "积势仅用于促成或铺垫锚点，绝不允许扭转、推迟或取消锚点。"
    ),
}


def build_anchor_constraint_block(anchor: str = "", action: str = "", compatibility: int | None = None,
                                  convergence: str = CONVERGENCE_DEFAULT,
                                  reference_only: bool = False) -> str:
    """生成锚点收束提示块，保留玩家行动因果；收束力度由 convergence 档位决定。

    ``reference_only=True``（碎锚后）：锚点照常注入但语义降级为"仅供参考"——
    不再要求收束、不因偏离改写；剧情由过往回合、角色性格与世界观自主推进，
    游戏机制约束（回合预算/选项/账目）不变。
    """
    anchor_text = str(anchor or "未提供锚点").strip()
    action_text = str(action or "未提供行动").strip()
    suffix = ""
    if compatibility is not None:
        suffix = f"当前相容性 K={max(0, min(100, int(compatibility)))}。"
    if reference_only:
        return (
            "【主线锚点·仅供参考（锚点已全部失效，不再收束）】\n"
            f"参考锚点：{anchor_text}\n玩家行动：{action_text}\n{suffix}"
            "上述锚点仅供叙事连续性参考：不得强制履约、不得因偏离锚点而要求改写；"
            "剧情由过往回合、角色性格与全局世界观自主生成推进。"
            "原创事件须保留并说明其影响；游戏机制约束（回合预算/选项/账目）照常。"
        )
    level = normalize_convergence(convergence)
    return (
        f"【锚点收束约束·收束力{level}】\n"
        f"既定锚点：{anchor_text}\n玩家行动：{action_text}\n{suffix}"
        + _CONVERGENCE_RULES[level]
        + "不得宣称规则失效或用跳跃叙事绕过因果。原创事件须保留并说明其对锚点的影响。"
    )


build_anchor_prompt = build_anchor_constraint_block


def _anchor_evidence_terms(anchor: str) -> list[str]:
    """将锚点 JSON/JSONL 转为可在正文中验证的标题、摘要、事件或原文引句。"""
    anchor_text = str(anchor or "").strip()
    if not anchor_text:
        return []
    items = []
    try:
        parsed = json.loads(anchor_text)
        items = parsed if isinstance(parsed, list) else [parsed]
    except (TypeError, ValueError):
        for line in anchor_text.splitlines():
            try:
                items.append(json.loads(line))
            except (TypeError, ValueError):
                continue
    terms = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for key in ("title", "summary"):
            value = str(item.get(key) or "").strip()
            if len(value) >= 4:
                terms.append(value)
        for key in ("events", "quotes"):
            for value in item.get(key) or []:
                value = str(value or "").strip()
                if len(value) >= 2:
                    terms.append(value)
    return list(dict.fromkeys(terms)) or [anchor_text]


_ANCHOR_ACTION_MARKERS = re.compile(
    r"打开|关闭|撞开|攻破|冲入|涌入|击退|杀死|救下|夺取|交出|递出|查证|揭露|"
    r"撤换|退守|追赶|阻止|破坏|离开|抵达|签署|宣布|抓住|释放|点燃|摧毁|完成"
)
_ANCHOR_RESULT_MARKERS = re.compile(
    r"失守|断联|死亡|受伤|被捕|撤退|退守|落定|曝光|获救|失去|获得|改变|"
    r"瓦解|关闭|开启|中断|恢复|失败|成功|留下|消失|生效"
)
_ANCHOR_CAUSAL_MARKERS = re.compile(
    r"因此|因而|于是|导致|致使|迫使|从而|由此|之后|随后|结果|代价"
)
_ANCHOR_NEGATION_MARKERS = re.compile(r"没有|并未|未曾|避免|取消|阻止|不再|没有发生")


# 各档位判定为「通过」所需的锚点状态集合。
_CONVERGENCE_PASS_STATUS = {
    "一般": frozenset({"fulfilled", "partial"}),
    "较高": frozenset({"fulfilled"}),
    "极高": frozenset({"fulfilled"}),
}


def validate_anchor_convergence(text: str, anchor: str, convergence: str = CONVERGENCE_DEFAULT) -> dict[str, Any]:
    """校验锚点是否由行动落成并产生可观察结果，而非只被复述。

    ``convergence`` 决定通过门槛：「一般」允许 partial 通过；「较高」（默认）
    与「极高」都要求 fulfilled。结果中附带归一化后的档位，便于落盘回放。
    """
    level = normalize_convergence(convergence)
    content = str(text or "")
    evidence_terms = _anchor_evidence_terms(anchor)
    matched = [term for term in evidence_terms if term in content]
    matched.sort(key=content.find)
    evidence_masked = content
    for term in sorted(matched, key=len, reverse=True):
        evidence_masked = evidence_masked.replace(term, " " * len(term))
    action_match = _ANCHOR_ACTION_MARKERS.search(evidence_masked)
    result_match = _ANCHOR_RESULT_MARKERS.search(evidence_masked)
    causal_match = _ANCHOR_CAUSAL_MARKERS.search(evidence_masked)
    conflicted_terms = []
    for term in matched:
        start = max(0, content.find(term) - 8)
        end = min(len(content), content.find(term) + len(term) + 8)
        if _ANCHOR_NEGATION_MARKERS.search(content[start:end]):
            conflicted_terms.append(term)

    mentioned = bool(matched)
    observable_action = bool(action_match)
    observable_result = bool(result_match)
    causal = bool(causal_match)
    if conflicted_terms:
        status = "conflicted"
    elif mentioned and observable_action and observable_result and causal:
        status = "fulfilled"
    elif mentioned and (observable_action or observable_result):
        status = "partial"
    elif mentioned:
        status = "mentioned"
    else:
        status = "pending"
    return {
        "valid": status in _CONVERGENCE_PASS_STATUS[level],
        "status": status,
        "convergence": level,
        "anchor_mentioned": mentioned,
        "matched_terms": matched,
        "observable_action": observable_action,
        "action_marker": action_match.group(0) if action_match else "",
        "observable_result": observable_result,
        "result_marker": result_match.group(0) if result_match else "",
        "causal_marker": causal,
        "causal_term": causal_match.group(0) if causal_match else "",
        "conflicted_terms": conflicted_terms,
    }


__all__ = [
    "PARTICIPATION_MIN", "PARTICIPATION_MAX", "SCENE_TARGET", "SCENE_MIN", "SCENE_MAX",
    "RICHNESS_MIN", "RICHNESS_MAX", "RICHNESS_DEFAULT", "RICHNESS_STEP", "RICHNESS_TIERS",
    "normalize_richness", "richness_tier",
    "compute_participation", "calculate_participation", "participation_decision",
    "scene_budget", "build_scene_budget_prompt", "validate_scene_length",
    "validate_scene_budget", "check_scene_length", "build_interaction_constraint_block",
    "build_character_interaction_block", "validate_character_interaction", "build_anchor_constraint_block", "build_anchor_prompt",
    "validate_anchor_convergence",
    "CONVERGENCE_LEVELS", "CONVERGENCE_DEFAULT", "normalize_convergence",
]
