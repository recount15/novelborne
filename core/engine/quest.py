"""任务机制：任务生成上下文、子智能体提示词、offer 解析、奖励公式与任务状态机。

纯计算、无 IO、不调用模型：子智能体只负责产出任务文案（title/requirements/goal/
plot_hook），时限、奖励、状态转移全部由本模块确定性计算，同样输入必然得到同样
结果，便于回放、存档和离线校验。

时限方向论证（难度越高时限越宽，而非越紧）：
本引擎的难度语义是「世界阻力」——ripple 机制中难度越高，改世所需积势阈值越高、
行动越容易被阻挡。同一目标在高难度世界里需要更多回合做铺垫、积势与试错，因此
时限必须放宽才公平；反之低难度世界行动畅通，任务应当快速了结，时限收紧。若反
向设计（难度越高时限越紧），会与积势门槛叠加形成双重惩罚，高难度玩家几乎必然
超时失败，违背「难度只改变代价、不锁死路径」的设计原则。
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .ripple import difficulty_number

# 任务档位：档位名 -> (最短时限, 最长时限)，单位均为回合。
QUEST_KINDS: dict[str, tuple[int, int]] = {
    "short": (1, 5),
    "medium": (5, 20),
    "long": (20, 100),
}

# 任务难度档位标签（1–9，与游戏难度 DIFFICULTIES 对齐）。
LEVEL_LABELS: dict[int, str] = {
    1: "极易", 2: "很易", 3: "较易", 4: "普通", 5: "较难",
    6: "困难", 7: "很困难", 8: "极难", 9: "炼狱",
}

# 各类型任务的「体量」约束：要求条数（requirements 数量）按短/中/长分档。
KIND_REQUIREMENT_COUNTS: dict[str, tuple[int, int]] = {
    "short": (2, 3),
    "medium": (3, 5),
    "long": (5, 8),
}


def quest_difficulty_range(game_difficulty: int | str) -> tuple[int, int]:
    """任务难度档位区间：受游戏总体难度制约，真人实测中永不可能任务。

    区间公式 lo = max(1, D-2)、hi = min(9, D+2)：
      D1 极易   -> [1, 3] 极易到较易
      D4 普通   -> [2, 6] 较易到困难
      D5 较难   -> [3, 7] 较易到很困难（默认体验）
      D9 炼狱   -> [7, 9] 很困难到炼狱
    难度系数（拖动条 0–1）在区间内线性取值，分布均匀、有上下界。
    """
    d = difficulty_number(game_difficulty)
    return max(1, d - 2), min(9, d + 2)


def compute_quest_level(coefficient: float, game_difficulty: int | str) -> int:
    """难度系数 0–1 在游戏难度决定的区间内线性映射为任务档位 1–9。"""
    lo, hi = quest_difficulty_range(game_difficulty)
    # 注意 0 是合法系数：只把 None 回退默认 0.5，不能用 or 短路。
    coef = 0.5 if coefficient is None else float(coefficient)
    coef = max(0.0, min(1.0, coef))
    return max(lo, min(hi, lo + int(round((hi - lo) * coef))))


def level_label(level: int) -> str:
    return LEVEL_LABELS.get(max(1, min(9, int(level))), "普通")

# 奖励类型池与数量上下界。数量永不跳出本表，是奖励公式的硬边界。
REWARD_TYPES = ("积势", "物资", "技能碎片", "关系进展", "关键情报")
REWARD_BOUNDS: dict[str, tuple[int, int]] = {
    "积势": (1, 3),
    "物资": (1, 2),
    "技能碎片": (1, 2),
    "关系进展": (1, 1),
    "关键情报": (1, 1),
}
REWARD_UNITS: dict[str, str] = {
    "积势": "点",
    "物资": "件",
    "技能碎片": "片",
    "关系进展": "阶",
    "关键情报": "条",
}

# 各档位的候选奖励池：短线任务给即时资源，中线给成长，长线给改世资本与情报。
_KIND_REWARD_POOL: dict[str, tuple[str, ...]] = {
    "short": ("积势", "物资"),
    "medium": ("物资", "技能碎片", "关系进展"),
    "long": ("积势", "技能碎片", "关键情报"),
}

# 档位基础量与难度附加量：raw = base + (difficulty-1)/8 * span。
_KIND_REWARD_SCALE: dict[str, tuple[float, float]] = {
    "short": (1.0, 0.5),
    "medium": (1.5, 1.0),
    "long": (2.0, 1.0),
}

# 任务完成后的收束松弛幅度（减弱动态收束系数的 position 偏移量）。
# 档位越高，主角获得越多的「偏离原著」自由。
_CONVERGENCE_RELIEF: dict[str, float] = {
    "short": 0.02,
    "medium": 0.03,
    "long": 0.05,
}

# 任务档位决定窗口长度：短任务只看临近锚点，长任务参考更远的后续锚点。
# 值为「当前锚点之外、再向后看多少个后续锚点」。
_WINDOW_LOOKAHEAD: dict[str, int] = {
    "short": 1,
    "medium": 3,
    "long": 6,
}

# 任务档位占蒸馏窗口（后六章总回合数）的比例：短 1/6、中 3/6、长 6/6。
_KIND_WINDOW_SHARE: dict[str, float] = {
    "short": 1 / 6,
    "medium": 3 / 6,
    "long": 6 / 6,
}

_FENCE_PATTERN = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _normalize_kind(kind: Any) -> str:
    text = str(kind or "").strip().lower()
    if text not in QUEST_KINDS:
        raise ValueError(f"未知任务档位: {kind!r}（可选 {sorted(QUEST_KINDS)}）")
    return text


def _requirement_bounds(kind: Any) -> tuple[int, int]:
    """按任务类型返回 requirements 条数上下限（与生成提示词同一张表）。

    kind 为 None/空（缺省调用）时按最宽容的全局区间 1–8 校验——长任务
    档位上限即 8，避免「提示词要求 5–8 条、解析上限写死 5」的自相矛盾
    （实测 long offer 502 事故根因）。
    """
    text = str(kind or "").strip().lower()
    counts = KIND_REQUIREMENT_COUNTS.get(text) if text else None
    if counts:
        return counts
    return 1, 8


def window_round_budget(state: Mapping[str, Any] | None) -> int:
    """后六章（当前章之后 6 章）的回合预算总和，用于动态决定任务时限上限。

    每章回合预算已在切章时算好（chapter_index[chapters][*].turn_budget，3–9 回合
    按字数）；本函数把蒸馏窗口内后六章的预算加总。缺数据时返回 0（调用方回退
    到固定档位值）。
    """
    state = state if isinstance(state, Mapping) else {}
    chapter_index = state.get("chapter_index") if isinstance(state.get("chapter_index"), Mapping) else {}
    chapters = chapter_index.get("chapters") or []
    current = int(state.get("current_chapter", 1) or 1)
    total = 0
    count = 0
    for row in chapters:
        if not isinstance(row, Mapping):
            continue
        try:
            idx = int(row.get("idx", 0) or 0)
        except (TypeError, ValueError):
            continue
        if idx <= current:
            continue
        if count >= 6:
            break
        try:
            budget = int(row.get("turn_budget", 0) or 0)
        except (TypeError, ValueError):
            budget = 0
        total += budget if budget > 0 else 3
        count += 1
    return total


def compute_deadline_span(kind: str, difficulty: int | str,
                          window_rounds: int | None = None) -> int:
    """按档位区间与难度插值计算任务时限（回合数），确定性、无随机。

    ``window_rounds`` 传入蒸馏窗口（后六章）总回合数时，动态决定上限：
    短/中/长分别占窗口的 1/6、3/6、6/6，长任务最长不超过整个窗口；最短时限取
    上限的 1/4。难度 1 取区间下界、难度 9 取区间上界，中间线性插值取整——
    难度越高时限越宽（世界阻力越大，同一目标需要更多回合铺垫）。
    """
    kind = _normalize_kind(kind)
    level = difficulty_number(difficulty)
    if window_rounds and window_rounds > 0:
        hi = max(1, int(round(window_rounds * _KIND_WINDOW_SHARE[kind])))
        lo = max(1, hi // 4)
    else:
        lo, hi = QUEST_KINDS[kind]
    span = lo + round((hi - lo) * (level - 1) / 8)
    return max(lo, min(hi, int(span)))


def build_quest_context(state: Mapping[str, Any] | None, kind: str,
                        difficulty: int | str) -> dict[str, Any]:
    """从运行态 state 提取任务生成所需局势，供集成层组装子智能体提示词。

    只读取、不修改 state；所有字段都做了缺省容错，缺字段时返回空串/空列表。
    """
    state = state if isinstance(state, Mapping) else {}
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), Mapping) else {}
    location = memory.get("location") if isinstance(memory.get("location"), Mapping) else {}
    goals = memory.get("goals") if isinstance(memory.get("goals"), Mapping) else {}
    timeline = state.get("anchor_timeline") if isinstance(state.get("anchor_timeline"), Mapping) else {}
    current_anchor = timeline.get("current") if isinstance(timeline.get("current"), Mapping) else {}
    params = state.get("start_params") if isinstance(state.get("start_params"), Mapping) else {}
    last_ripple = state.get("last_ripple") if isinstance(state.get("last_ripple"), Mapping) else {}
    distill = state.get("distill") if isinstance(state.get("distill"), Mapping) else {}

    def _names(key: str) -> list[str]:
        pool = state.get(key) or []
        names = []
        for item in pool:
            if isinstance(item, Mapping) and str(item.get("name") or "").strip():
                names.append(str(item["name"]).strip())
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
        return names

    # 重点剧情事件：当前锚点 + 蒸馏窗口内的后续锚点（标题 + 摘要）。
    # 窗口长度由任务档位决定：短任务看临近、长任务看更远。
    lookahead = _WINDOW_LOOKAHEAD.get(_normalize_kind(kind), 3)
    anchor_events: list[dict[str, str]] = []
    entries = [timeline.get("current")] + list(timeline.get("upcoming") or [])
    for entry in entries[: 1 + lookahead]:
        if not isinstance(entry, Mapping):
            continue
        title = str(entry.get("title") or "").strip()
        summary = str(entry.get("summary") or "").strip()
        if title:
            anchor_events.append({"title": title, "summary": summary})

    goal_items = goals.get("current") or []
    if isinstance(goal_items, (str, bytes)):
        goal_items = [goal_items]
    return {
        "kind": _normalize_kind(kind),
        "difficulty": difficulty_number(difficulty),
        "chapter": int(state.get("current_chapter", 1) or 1),
        "round": int(state.get("round", 0) or 0),
        "anchor_title": str(current_anchor.get("title") or ""),
        "anchor_status": str(current_anchor.get("status") or ""),
        "location": str(location.get("name") or ""),
        "goals": [str(item) for item in goal_items][:5],
        "companions": _names("companions"),
        "heroines": _names("heroines"),
        "strongly_relevant": _strongly_relevant_names(state),
        "golden_finger": str(params.get("golden_finger") or ""),
        "player_difficulty": str(params.get("difficulty") or ""),
        "last_ripple": {
            "level": str(last_ripple.get("level") or ""),
            "allowed": bool(last_ripple.get("allowed")),
            "note": str(last_ripple.get("note") or ""),
        },
        "plot_summary": distill.get("plot_summary"),
        "anchor_events": anchor_events,
        "window_rounds": window_round_budget(state),
    }


def _strongly_relevant_names(state: Mapping[str, Any]) -> list[str]:
    """选角相关度报告中的强相关成员名（任务设计优先围绕他们）。"""
    report = state.get("roster_relevance") if isinstance(state, Mapping) else None
    report = report if isinstance(report, Mapping) else {}
    return [name for name, info in (report.get("members") or {}).items()
            if isinstance(info, Mapping) and info.get("tier") == "强"
            and not info.get("scaled")]


def _summary_to_text(summary: Any) -> str:
    """把初步蒸馏剧情大概（dict/str）转成给模型看的紧凑文本。"""
    if summary is None:
        return ""
    if isinstance(summary, str):
        return summary.strip()
    if isinstance(summary, dict):
        parts: list[str] = []
        for key in ("genre", "premise", "major_threads", "tone"):
            value = summary.get(key)
            if isinstance(value, list):
                parts.extend(str(v).strip() for v in value if str(v).strip())
            elif value:
                parts.append(str(value).strip())
        return "；".join(parts) if parts else json.dumps(summary, ensure_ascii=False)
    return str(summary)


def quest_offer_prompt(context: Mapping[str, Any]) -> str:
    """生成任务派发子智能体的提示词，要求其输出严格 JSON。

    子智能体只产出文案；时限与奖励已由代码确定，提示词中明确告知，防止模型
    自行编造数值。任务内容依据初步蒸馏剧情与窗口内锚点事件确定，目标是促成
    某个锚点事件发生（收束），但不强制与原著完全一致。
    """
    ctx = dict(context or {})
    span = compute_deadline_span(ctx.get("kind", "short"), ctx.get("difficulty", 4),
                                  ctx.get("window_rounds"))
    kind_key = _normalize_kind(ctx.get("kind", "short"))
    req_lo, req_hi = KIND_REQUIREMENT_COUNTS.get(kind_key, (2, 3))
    level = difficulty_number(ctx.get("difficulty", 4))
    label = level_label(level)
    companions = "、".join(ctx.get("companions") or []) or "无"
    heroines = "、".join(ctx.get("heroines") or []) or "无"
    strong_names = "、".join(ctx.get("strongly_relevant") or []) or "无"
    goals = "；".join(ctx.get("goals") or []) or "暂无明确目标"
    ripple = ctx.get("last_ripple") or {}
    plot_summary = _summary_to_text(ctx.get("plot_summary")) or "（暂无）"
    anchor_events = ctx.get("anchor_events") or []
    if anchor_events:
        events_text = "；".join(
            f"{e.get('title')}：{e.get('summary') or '（摘要未蒸馏）'}" for e in anchor_events
            if isinstance(e, Mapping) and e.get("title"))
        events_text = events_text or "（暂无）"
    else:
        events_text = "（暂无）"
    return (
        "【任务派发】你是任务设计子智能体。请依据初步蒸馏剧情与窗口内锚点事件，"
        f"设计一个 {ctx.get('kind')} 档任务，并以严格 JSON 输出，不要输出任何其他文字。\n"
        f"当前局势：第 {ctx.get('chapter')} 章 / 第 {ctx.get('round')} 回合；"
        f"当前锚点：{ctx.get('anchor_title') or '未知'}（{ctx.get('anchor_status') or 'pending'}）；"
        f"所在地点：{ctx.get('location') or '未知'}；"
        f"主角目标：{goals}；在场伙伴：{companions}；在场女主：{heroines}；"
        f"强剧情相关角色（任务设计优先围绕）：{strong_names}；"
        f"金手指：{ctx.get('golden_finger') or '无'}；"
        f"世界难度：D{ctx.get('difficulty')}（{label}）；任务档位：{level}（{label}）；"
        f"最近涟漪：{ripple.get('level') or '无'}（{'通过' if ripple.get('allowed') else '阻挡/无'}）。\n"
        f"初步剧情重点：{plot_summary}\n"
        f"窗口内重点剧情事件（锚点）：{events_text}\n"
        f"输出 JSON 形状：{{\"title\": \"任务标题（不超过30字）\", "
        f"\"requirements\": [\"完成条件1\", \"...\"], \"goal\": \"任务目标一句话\", "
        f"\"plot_hook\": \"可选的剧情钩子，可留空字符串\"}}\n"
        f"硬性要求：requirements 恰好 {req_lo}–{req_hi} 条且每条非空（{kind_key} 档体量）；"
        f"任务必须在 {span} 回合内可完成；"
        "任务应围绕促成某个锚点事件（上述重点剧情事件）来设计，目标要具体描述成"
        "「事件」（谁在何时何地促成什么发生），促成即视为收束该锚点，但不必与原著"
        "完全一致，可改变路径、代价或见证；不得引入未登记角色或超出当前局势的能力。"
    )


def parse_quest_offer(text: str, kind: str | None = None) -> dict[str, Any]:
    """解析子智能体的任务 offer 文本，剥离 ```json fence 与多余文本后校验。

    校验规则：title 非空且不超过 30 字；requirements 条数按任务类型分档
    （短 2–3 / 中 3–5 / 长 5–8，见 KIND_REQUIREMENT_COUNTS；未传 kind 时
    按全局 1–8 宽容）；goal 非空；plot_hook 可空（缺省为空串）。任何一步
    失败都抛 ValueError，供集成层捕获后刷新重试。
    """
    content = str(text or "").strip()
    if not content:
        raise ValueError("任务 offer 为空")
    fenced = _FENCE_PATTERN.search(content)
    if fenced:
        content = fenced.group(1).strip()
    else:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("任务 offer 中找不到 JSON 对象")
        content = content[start:end + 1]
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ValueError(f"任务 offer 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("任务 offer 必须是 JSON 对象")

    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("任务 title 不能为空")
    if len(title) > 30:
        raise ValueError(f"任务 title 超过 30 字（{len(title)} 字）")

    req_lo, req_hi = _requirement_bounds(kind)
    requirements = data.get("requirements")
    if not isinstance(requirements, list) or not (req_lo <= len(requirements) <= req_hi):
        raise ValueError(f"requirements 必须是 {req_lo}–{req_hi} 条的列表")
    requirements = [str(item or "").strip() for item in requirements]
    if any(not item for item in requirements):
        raise ValueError("requirements 中不允许空条目")

    goal = str(data.get("goal") or "").strip()
    if not goal:
        raise ValueError("任务 goal 不能为空")

    plot_hook = str(data.get("plot_hook") or "").strip()
    return {"title": title, "requirements": requirements, "goal": goal, "plot_hook": plot_hook}


def compute_reward(kind: str, difficulty: int | str,
                   player_difficulty: int | str = 4) -> dict[str, Any]:
    """纯代码任务奖励公式，返回 {"items": [{"type", "amount", "unit"}, ...]}。

    公式与论证：
    - 档位基础量 base 与难度附加 span 见 _KIND_REWARD_SCALE；任务档位越高、任务
      难度越高，raw = base + (difficulty-1)/8 * span 越大，体现「多劳多得」。
    - 主角难度反向约束：f = max(0.45, 1 - (D-1)*0.07)。高档主角（D7–D9）本身
      强度已碾压世界，若奖励仍按满额发放会迅速通胀、积势门槛形同虚设；因此按
      主角难度压缩边际收益，D9 时仅保留 45% 下限，保证高档主角「仍有得拿、但
      拿不快」。D1 主角 f=1.0 不受压缩。
    - 最终 amount = clamp(round(raw * f), 类型下界, 类型上界)，上下界见
      REWARD_BOUNDS（如积势恒为 1–3 点、关键情报恒为 1 条），任何输入组合都
      不会跳出设定。
    - 奖励类型由 kind+difficulty 确定性选择：候选池按档位固定，起始下标随难度
      轮转，难度 5 及以上取 2 项、否则取 1 项，无随机。
    """
    kind = _normalize_kind(kind)
    level = difficulty_number(difficulty)
    player = difficulty_number(player_difficulty)

    pool = _KIND_REWARD_POOL[kind]
    count = 2 if level >= 5 else 1
    count = min(count, len(pool))
    start = (level - 1) % len(pool)
    selected = [pool[(start + i) % len(pool)] for i in range(count)]

    base, span = _KIND_REWARD_SCALE[kind]
    raw = base + (level - 1) / 8 * span
    factor = max(0.45, 1.0 - (player - 1) * 0.07)
    amount_raw = raw * factor

    items = []
    for reward_type in selected:
        lo, hi = REWARD_BOUNDS[reward_type]
        amount = max(lo, min(hi, int(round(amount_raw))))
        items.append({"type": reward_type, "amount": amount, "unit": REWARD_UNITS[reward_type]})
    return {
        "kind": kind,
        "difficulty": level,
        "player_difficulty": player,
        "items": items,
        # 收束松弛：任务完成后轻微减弱动态收束系数，让主角更自由。档位越高松弛越大。
        "convergence_relief": _CONVERGENCE_RELIEF.get(kind, 0.03),
    }


def _quest_box(state: dict[str, Any]) -> dict[str, Any]:
    box = state.get("quest")
    return box if isinstance(box, dict) else {}


def can_request_offer(state: Mapping[str, Any] | None) -> bool:
    """仅当没有 active 任务时，才允许生成/刷新任务 offer。"""
    box = _quest_box(state if isinstance(state, dict) else {})
    return box.get("status") != "active"


def new_offer(state: dict[str, Any], offer: Mapping[str, Any], kind: str,
              difficulty: int | str, reward: Mapping[str, Any],
              current_round: int) -> dict[str, Any]:
    """写入新任务 offer（status=offered）。已有 active 任务时抛 ValueError。

    offer 需已通过 parse_quest_offer 校验；reward 需为 compute_reward 的结果。
    decline/refresh 不需要专门接口——集成层直接再次调用本函数覆盖即可。
    """
    if not isinstance(state, dict):
        raise ValueError("state 必须是字典")
    box = _quest_box(state)
    if box.get("status") == "active":
        raise ValueError("已有进行中的任务，不能生成新 offer")
    offer = dict(offer or {})
    title = str(offer.get("title") or "").strip()
    requirements = [str(item or "").strip() for item in (offer.get("requirements") or [])]
    goal = str(offer.get("goal") or "").strip()
    kind = _normalize_kind(kind)
    # 二次校验与 parse_quest_offer 同一张分档表：短 2–3 / 中 3–5 / 长 5–8。
    req_lo, req_hi = _requirement_bounds(kind)
    if not title or not (req_lo <= len(requirements) <= req_hi) or any(not r for r in requirements) or not goal:
        raise ValueError(f"offer 形状不合法（requirements 需 {req_lo}–{req_hi} 条），请先通过 parse_quest_offer 校验")
    level = difficulty_number(difficulty)
    quest = {
        "status": "offered",
        "kind": kind,
        "difficulty": level,
        "title": title,
        "requirements": requirements,
        "goal": goal,
        "plot_hook": str(offer.get("plot_hook") or "").strip(),
        "reward": dict(reward or {}),
        "deadline_span": compute_deadline_span(kind, level, window_round_budget(state)),
        "offered_round": int(current_round or 0),
        "accepted_round": None,
        "deadline_round": None,
        "progress": [],
    }
    state["quest"] = quest
    return quest


def accept(state: dict[str, Any]) -> dict[str, Any]:
    """接受当前 offer：offered -> active，记录 accepted_round 与 deadline_round。

    回合数取 state["round"]；deadline_round = accepted_round + deadline_span。
    """
    box = _quest_box(state)
    if box.get("status") != "offered":
        raise ValueError(f"当前任务状态为 {box.get('status')!r}，不能接受")
    current = int(state.get("round", 0) or 0)
    box["status"] = "active"
    box["accepted_round"] = current
    box["deadline_round"] = current + int(box.get("deadline_span") or 0)
    state["quest"] = box
    return box


def _validate_verdict(verdict: Any) -> dict[str, Any]:
    if not isinstance(verdict, Mapping):
        raise ValueError("verdict 必须是 {completed: bool, evidence: str} 字典")
    completed = verdict.get("completed")
    evidence = verdict.get("evidence")
    if not isinstance(completed, bool):
        raise ValueError("verdict.completed 必须是布尔值")
    if not isinstance(evidence, str):
        raise ValueError("verdict.evidence 必须是字符串")
    return {"completed": completed, "evidence": evidence.strip()}


def settle_round(state: dict[str, Any], current_round: int,
                 verdict: Mapping[str, Any]) -> dict[str, Any]:
    """按子智能体判定结算任务回合，返回 {"status", "changed", "reward"?}。

    verdict 形状不合格抛 ValueError。仅对 active 任务结算：
    - completed=True  -> status=completed，发放奖励（返回值带 reward）；
    - 未完成且 current_round > deadline_round -> status=failed（超时）；
    - 否则保持 active，并向 progress 追加 {round, evidence} 记录。
    非 active 状态（无任务 / offered / 已终结）返回 changed=False，不做任何修改。
    """
    verdict = _validate_verdict(verdict)
    box = _quest_box(state)
    if box.get("status") != "active":
        return {"status": box.get("status") or "none", "changed": False}

    current = int(current_round or 0)
    if verdict["completed"]:
        box["status"] = "completed"
        reward = box.get("reward") or {}
        box["granted_reward"] = reward
        box.setdefault("progress", []).append(
            {"round": current, "evidence": verdict["evidence"], "result": "completed"})
        state["quest"] = box
        return {"status": "completed", "changed": True, "reward": reward}

    deadline = box.get("deadline_round")
    if deadline is not None and current > int(deadline):
        box["status"] = "failed"
        box.setdefault("progress", []).append(
            {"round": current, "evidence": verdict["evidence"], "result": "failed_timeout"})
        state["quest"] = box
        return {"status": "failed", "changed": True}

    box.setdefault("progress", []).append(
        {"round": current, "evidence": verdict["evidence"], "result": "ongoing"})
    state["quest"] = box
    return {"status": "active", "changed": False}


__all__ = [
    "QUEST_KINDS", "REWARD_TYPES", "REWARD_BOUNDS", "REWARD_UNITS",
    "LEVEL_LABELS", "KIND_REQUIREMENT_COUNTS",
    "quest_difficulty_range", "compute_quest_level", "level_label",
    "window_round_budget", "compute_deadline_span", "build_quest_context", "quest_offer_prompt",
    "parse_quest_offer", "compute_reward", "can_request_offer",
    "new_offer", "accept", "settle_round",
]
