# -*- coding: utf-8 -*-
"""穿越保障（traverse guard，原 gender guard）。

现行规则（2026-08-30 起）：**破除性别栏杆**——穿越者的性别（玩家选择/
卡面性别）不构成任何附身限制；卡和性格都只是「魂」，名字（穿成谁）由
模型在开局确定；叙事一律以附身角色（书中身体）的生理性别为准。

保留的硬约束：
1. 禁止悬空穿越——每位穿越者必须附身书中一个具体角色；
2. 开局核对必须包含「穿越对照表」（谁穿成了谁 + 身体性别 + 初始处境），
   缺表视为核对未完成；
3. 穿越身份落定（traverse map）：开局时由模型为每位穿越者指定附身角色，
   代码解析校验后写入 state 并注入硬约束，开局回执向玩家反馈实际名字
   与身份。空位默认分配：主角→原著主角本人；其余→性格最类似/最贴合的
   原著角色。

历史沿革：模块原名「穿越性别保障」，曾强制同性别穿越并配套文本/网络
双重性别查证（probe_gender_from_text / gender_probe_prompt 等函数仍
保留在文件内，但已从开局链路下线，仅供兼容调用）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List

# —— 性别值与归一 ——————————————————————————————————————————————

GENDER_VALUES = ("male", "female", "unknown")

_ZH_MALE = {"男", "男性", "雄", "公", "m", "male"}
_ZH_FEMALE = {"女", "女性", "雌", "母", "f", "female"}

_SLOT_ZH = {"主角": "主角", "主线": "伴侣", "伙伴": "伙伴", "宿敌": "宿敌",
            "女主": "伴侣", "男主": "主角"}


def normalize_gender(value: Any) -> str:
    """把任意输入归一为 male/female/unknown（容忍中文与大小写）。"""
    text = str(value or "").strip().lower()
    if text in _ZH_MALE:
        return "male"
    if text in _ZH_FEMALE:
        return "female"
    return "unknown"


_GENDER_ZH = {"male": "男性", "female": "女性", "unknown": "性别未知"}

# —— 文本查证：中文代词与称谓线索 ————————————————————————————

_PRONOUN_MALE = re.compile(r"他")
_PRONOUN_FEMALE = re.compile(r"她")

_TITLE_MALE = ("公子", "少爷", "先生", "夫君", "汉子", "师兄", "师弟",
               "兄台", "郎君", "男子", "男童", "老爷", "大爷", "叔父", "父亲",
               "爷爷", "爸爸", "儿子", "师尊", "掌门", "宗主", "长老")
_TITLE_FEMALE = ("姑娘", "小姐", "夫人", "娘子", "女子", "女童", "丫鬟", "侍女",
                 "师姐", "师妹", "姨母", "母亲", "奶奶", "妈妈",
                 "女儿", "宫女", "舞姬", "美妇", "少妇", "道姑", "仙子", "圣女")


_SENTENCE_SPLIT = re.compile(r"[。！？；\n]+")


def _score_window(ctx: str) -> tuple[float, float]:
    """统计一段文本的男女信号分（代词权重 2，称谓权重 0.5）。"""
    pron_m = len(_PRONOUN_MALE.findall(ctx))
    pron_f = len(_PRONOUN_FEMALE.findall(ctx))
    title_m = sum(ctx.count(word) for word in _TITLE_MALE)
    title_f = sum(ctx.count(word) for word in _TITLE_FEMALE)
    return pron_m * 2.0 + title_m * 0.5, pron_f * 2.0 + title_f * 0.5


def probe_gender_from_text(name: str, text: str, window: int = 120,
                           min_score: float = 1.0) -> Dict[str, Any]:
    """在 text 中查证 name 的性别（纯本地文本推断）。

    两级策略：
    1. 句级统计（优先）：中文代词「他/她」与指称对象通常同句，只统计
       名字所在句的信号，避免同一窗口内其他角色的代词污染；
    2. 窗口法兜底：名字所在句全无信号时，退回「出现位置前后 window
       字符」统计（适合无句读的设定档案文本）。

    一方须占总信号 65% 以上且达到 min_score 才判定，否则 unknown
    （避免男女信号打架或纯称谓噪音误判）。
    """
    name = str(name or "").strip()
    if not name or not text:
        return {"gender": "unknown", "confidence": 0.0, "evidence": "", "hits": 0}
    positions: List[int] = []
    start = 0
    while True:
        idx = text.find(name, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + len(name)
    if not positions:
        return {"gender": "unknown", "confidence": 0.0, "evidence": "", "hits": 0}

    evidences: List[str] = []

    def _judge(male_score: float, female_score: float) -> Dict[str, Any]:
        total = male_score + female_score
        if total <= 0:
            return {"gender": "unknown", "confidence": 0.0,
                    "evidence": "；".join(evidences), "hits": len(positions)}
        if male_score / total >= 0.65 and male_score >= min_score:
            return {"gender": "male", "confidence": round(male_score / total, 2),
                    "evidence": "；".join(evidences), "hits": len(positions)}
        if female_score / total >= 0.65 and female_score >= min_score:
            return {"gender": "female", "confidence": round(female_score / total, 2),
                    "evidence": "；".join(evidences), "hits": len(positions)}
        return {"gender": "unknown", "confidence": round(max(male_score, female_score) / total, 2),
                "evidence": "；".join(evidences), "hits": len(positions)}

    # —— 第一级：句级统计 ——
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s]
    hit_sentences = [s for s in sentences if name in s]
    if hit_sentences:
        male_score = female_score = 0.0
        for sent in hit_sentences[:64]:
            m, f = _score_window(sent)
            male_score += m
            female_score += f
            if len(evidences) < 3 and (m or f):
                evidences.append(sent.strip()[:40])
        result = _judge(male_score, female_score)
        if result["gender"] != "unknown":
            return result

    # —— 第二级：窗口法兜底（句级无信号或信号打架时） ——
    male_score = female_score = 0.0
    evidences = []
    for idx in positions[:64]:
        lo = max(0, idx - window)
        hi = min(len(text), idx + len(name) + window)
        m, f = _score_window(text[lo:hi])
        male_score += m
        female_score += f
        if len(evidences) < 3 and (m or f):
            evidences.append(text[lo:hi].strip()[:40])
    return _judge(male_score, female_score)


# —— 网络查证：模型提问与解析 ——————————————————————————————————

def gender_probe_prompt(entries: List[Dict[str, Any]], work_title: str) -> str:
    """构造向模型查证角色性别的提问（严格 JSON 回复）。"""
    names = [str(item.get("name") or "").strip() for item in entries if item.get("name")]
    listed = "\n".join("- %s" % name for name in names)
    return (
        "你是原著角色资料核对员。请判断下列角色在《%s》中的生理性别。\n"
        "只依据原著事实；查无此角色或原著未明示时回答 未知，不要猜测。\n"
        "严格以 JSON 回复：{\"结果\":[{\"名字\":\"…\",\"性别\":\"男|女|未知\"}]}，"
        "不要输出任何其他文字。\n\n角色名单：\n%s" % (work_title, listed)
    )


def parse_gender_probe(reply: Any) -> Dict[str, str]:
    """解析网络查证回复：{名字: male|female|unknown}。

    容错：剥 markdown 围栏；剥前后说明文字后提取首个 { 到末个 }；性别值
    兼容中文（男/女/未知）与英文（male/female/unknown）；坏行跳过。
    """
    if isinstance(reply, dict):
        return _rows_to_map(reply.get("结果") or reply.get("results") or [])
    text = str(reply or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            return {}
    rows = []
    if isinstance(parsed, dict):
        rows = parsed.get("结果") or parsed.get("results") or []
    elif isinstance(parsed, list):
        rows = parsed
    return _rows_to_map(rows)


def _rows_to_map(rows: Any) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("名字") or row.get("name") or "").strip()
        raw = row.get("性别") if row.get("性别") is not None else row.get("gender")
        if not name:
            continue
        result[name] = normalize_gender(raw)
    return result


# —— 约束生成与总装配 ————————————————————————————————————————

def build_gender_constraint(entries: List[Dict[str, Any]]) -> str:
    """生成注入 system prompt 的穿越铁律（纯中文）。

    现行规则（2026-08-30 起）：破除性别栏杆——穿越者的性别（玩家选择/卡面）
    不构成任何限制；叙事一律以附身角色（书中身体）的生理性别为准。
    仅保留两条硬约束：禁止悬空穿越 + 身体性别优先。
    """
    lines = [
        "# 穿越铁律（代码级保障，违反即判定输出无效）",
        "- 每位穿越者都必须附身/成为书中一个具体角色，禁止悬空穿越（只以本体出现在书中世界）。",
        "- 附身不受性别限制：穿越者自身的性别（玩家选择或卡面性别）与附身对象无关，"
        "原著主角是男就穿男、是女就穿女，任何槽位均不设性别门槛。",
        "- 叙事一律以附身角色的生理性别为准：代词、称谓、外貌描写与社会互动都跟随书中身体；"
        "穿越者的心理自我可保留在内心活动，但外在表现以身体为准。",
    ]
    named = [item for item in entries or [] if item.get("name")]
    if named:
        roster = "、".join("%s「%s」" % (_SLOT_ZH.get(str(item.get("slot") or ""), "成员"),
                                        item.get("name")) for item in named)
        lines.append("- 本局穿越者名单：%s——无人可以悬空。" % roster)
    lines.append(
        "- 开局核对必须包含「穿越对照表」：以列表逐一交代每位穿越者（主角、伴侣、伙伴、宿敌）"
        "穿越成了书中哪个角色、该角色的生理性别、穿越时间点与初始处境；缺对照表视为开局核对未完成，"
        "须补齐后才能进入第一幕。")
    return "\n".join(lines)


def guard_entries(entries: List[Dict[str, Any]], book_text: str = "",
                  protagonist_gender: str = "unknown") -> Dict[str, Any]:
    """装配主入口：归一 entry 并产出穿越铁律约束文本。

    2026-08-30 起破除性别栏杆：不再做性别查证（文本代词统计与网络查证
    均已下线），entry 的性别字段仅作信息保留、不参与任何约束；
    book_text / protagonist_gender 参数保留仅为兼容旧调用签名。
    返回 {"entries": [...], "constraint_text": str, "pending": []}。
    """
    resolved: List[Dict[str, Any]] = []
    for raw in entries or []:
        item = dict(raw) if isinstance(raw, dict) else {"name": str(raw)}
        item["slot"] = _SLOT_ZH.get(str(item.get("slot") or ""), str(item.get("slot") or "成员"))
        item["name"] = str(item.get("name") or "").strip()
        item["gender"] = normalize_gender(item.get("gender"))
        resolved.append(item)
    return {
        "entries": resolved,
        "constraint_text": build_gender_constraint(resolved),
        "pending": [],
    }


# —— 穿越身份落定：开局即锁定附身角色（实际名字 + 具体身份） ————————————————
#
# 机制定位：性别铁律只约束「同性别、不悬空」，具体穿成谁原本交给模型在
# 开场白里自由安排——玩家要到第一幕才知道结果，且无法校验、无法锁定。
# 本机制把这一步前置：开局时（第一幕生成前）由模型依据原著为每位穿越者
# 指定附身角色，代码解析校验后写入 state、注入硬约束、并在开局回执中
# 向玩家反馈实际名字与身份。失败静默降级为原「开场白交代」路径。

_TRAVERSE_GENDER_ZH = {"male": "男", "female": "女", "unknown": "未知"}


def traverse_map_prompt(entries: List[Dict[str, Any]], work_title: str,
                        book_hint: str = "") -> str:
    """构造穿越身份落定提问：为每位穿越者指定原著中的附身角色（严格 JSON）。

    book_hint：原著开篇节选或作品档案文本。上传的小众作品模型未必知晓，
    提供文本后附身角色可从原文具名角色中选取，而非凭印象编造。
    """
    lines = []
    for item in entries or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        slot = str(item.get("slot") or "成员").strip()
        # 分配指令：卡和性格都只是「魂」，名字（穿成谁）由模型定。
        # 空位默认规则——主角→原著主角本人；其余→性格最类似/最贴合的原著角色。
        # 性别栏杆已破除：不为模型提供魂的性别，附身对象性别不限。
        directive = ""
        if str(item.get("assign") or "") == "original_protagonist":
            directive = "；★附身对象必须是原著主角本人"
        else:
            soul = str(item.get("soul") or "").strip()
            if soul:
                directive = "；★请分配原著中性格最类似「%s」的具名角色" % soul[:40]
            elif item.get("name_pending"):
                directive = "；★未指定灵魂：请分配原著中最适合担任「%s」的具名角色（性格与该定位最贴合）" % slot
        lines.append("- %s「%s」%s" % (slot, name, directive))
    hint_block = ""
    hint = str(book_hint or "").strip()
    if hint:
        hint_block = (
            "\n原著文本/档案节选（附身角色应优先从其中出现的具名角色里选择）：\n"
            + hint[:6000] + "\n")
    return (
        "你是原著角色安排师。下列穿越者即将进入《%s》的世界，"
        "请为每位穿越者从原著中选定一个具体的、有名字的角色作为其附身对象。\n"
        "硬性要求：\n"
        "1. 附身角色性别不限：穿越者的性别与附身对象无关，按原著与剧情需要分配即可；\n"
        "2. 附身角色必须是原著中真实存在的具名角色，禁止原创路人；"
        "若节选范围内确无合适的同性别具名角色，选定位最接近的次要具名角色，"
        "并在身份中注明「次要角色」；\n"
        "3. 附身角色在故事开篇时应处于可接入状态（未死亡、可行动）；\n"
        "4. 身份用一句话说明该角色在原著中的身份与开篇处境；\n"
        "5. 名单中带 ★ 指令的条目，优先严格按照各自指令分配；\n"
        "6. 同一具身体只能承载一个灵魂：每位原著角色最多分配给一位穿越者，不得重复分配。\n"
        "严格以 JSON 回复：{\"结果\":[{\"穿越者\":\"…\",\"栏位\":\"主角|伴侣|伙伴|宿敌\","
        "\"附身角色\":\"…\",\"身份\":\"…\",\"性别\":\"男|女\"}]}，"
        "不要输出任何其他文字。\n%s\n穿越者名单：\n%s"
        % (work_title, hint_block, "\n".join(lines))
    )


def parse_traverse_map(reply: Any, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """解析穿越身份落定回复，产出校验后的对照表。

    校验规则：附身角色非空；穿越者能匹配回 entries（按名字）。
    性别栏杆已破除：不做任何性别一致性检查，gender 字段即模型报告的
    附身角色（书中身体）生理性别，叙事以其为准。
    返回 [{"slot","traverser","book_name","identity","gender"}]，按 entries 顺序。
    """
    text = reply if isinstance(reply, str) else json.dumps(reply or {}, ensure_ascii=False)
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            return []
    rows = []
    if isinstance(parsed, dict):
        rows = parsed.get("结果") or parsed.get("results") or []
    elif isinstance(parsed, list):
        rows = parsed
    if not isinstance(rows, list):
        return []

    by_name: Dict[str, Dict[str, Any]] = {}
    for item in entries or []:
        name = str(item.get("name") or "").strip()
        if name:
            by_name[name] = item

    mapped: Dict[str, Dict[str, Any]] = {}
    used_bodies: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        traverser = str(row.get("穿越者") or row.get("traverser") or "").strip().strip("「」")
        book_name = str(row.get("附身角色") or row.get("book_name")
                       or row.get("角色") or "").strip().strip("「」")
        identity = str(row.get("身份") or row.get("identity") or "").strip()
        gender = normalize_gender(row.get("性别") if row.get("性别") is not None
                                  else row.get("gender"))
        entry = by_name.get(traverser)
        if not entry or not book_name:
            continue
        if book_name in used_bodies:
            continue  # 一具身体一个灵魂：重复分配丢弃后者
        used_bodies.add(book_name)
        mapped[traverser] = {
            "slot": str(entry.get("slot") or "成员"),
            "traverser": traverser,
            "book_name": book_name,
            "identity": identity,
            "gender": gender,
        }
    # 按 entries 原顺序输出，保证回执与约束稳定
    ordered = []
    for item in entries or []:
        name = str(item.get("name") or "").strip()
        if name in mapped:
            ordered.append(mapped[name])
    return ordered


def build_traverse_constraint(mappings: List[Dict[str, Any]]) -> str:
    """把已锁定的穿越对照注入 system prompt（后续叙事不得更改附身对象）。"""
    lines = [
        "# 穿越身份落定（代码级锁定，违反即判定输出无效）",
        "以下穿越安排已在开局锁定：第一幕及此后所有叙事必须严格遵循，"
        "不得更换附身对象、不得让任何穿越者悬空、不得擅自改写附身角色的名字。",
    ]
    for item in mappings or []:
        lines.append("- %s「%s」→ 附身角色：%s（身体性别：%s）%s" % (
            item.get("slot"), item.get("traverser"), item.get("book_name"),
            _TRAVERSE_GENDER_ZH.get(item.get("gender"), "未知"),
            ("，身份：" + item["identity"]) if item.get("identity") else ""))
    lines.append(
        "开局核对必须原样列出本对照表（穿越者 → 附身角色 + 身份 + 身体性别）；"
        "此后叙事中这些书中角色即由对应穿越者扮演，其言行受穿越者人格驱动，"
        "外在表现（代词/称谓/社会互动）一律以附身角色的生理性别为准。")
    return "\n".join(lines)


def format_traverse_receipt(mappings: List[Dict[str, Any]]) -> str:
    """玩家可见的开局回执文本：穿越对照表（实际名字 + 具体身份）。"""
    lines = ["🧭 穿越对照表（开局落定）"]
    for item in mappings or []:
        identity = (" —— " + item["identity"]) if item.get("identity") else ""
        lines.append("· %s「%s」→ 附身角色：%s%s（%s）" % (
            item.get("slot"), item.get("traverser"), item.get("book_name"),
            identity, _TRAVERSE_GENDER_ZH.get(item.get("gender"), "未知")))
    lines.append("以上安排已锁定，本局叙事不得更改附身对象。")
    return "\n".join(lines)


def apply_model_probe(report: Dict[str, Any], reply: Any) -> Dict[str, Any]:
    """把网络查证结果合并进 guard_entries 报告并重建约束（就地更新副本）。

    查证仍 unknown 的成员保持 unknown——由约束中的「依原著选定性别明确
    角色」条款兜底，绝不悬空。
    """
    merged = dict(report or {})
    entries = [dict(item) for item in (merged.get("entries") or [])]
    facts = parse_gender_probe(reply)
    changed = False
    for item in entries:
        if item.get("gender") in ("male", "female") or not item.get("name"):
            continue
        fact = facts.get(item["name"])
        if fact in ("male", "female"):
            item["gender"] = fact
            item["source"] = "网络查证"
            changed = True
    merged["entries"] = entries
    merged["constraint_text"] = build_gender_constraint(entries)
    merged["pending"] = [dict(item) for item in entries
                         if item.get("gender") not in ("male", "female") and item.get("name")]
    merged["model_probed"] = changed or bool(facts)
    return merged
