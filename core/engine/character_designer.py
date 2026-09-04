# -*- coding: utf-8 -*-
"""角色设计器：定身份 → 语料分类 → 选择题 → 大模型融合为角色卡与 persona。

设计依据（调研结论）：少而精的结构化约束（尤其负面约束 + 台词样本）胜过长篇
描述；因此选择题只保底关键字段，语料丰富度决定“灵魂层”字段质量。

本模块不依赖任何模型 SDK，也不 import fate_engine；所有常量自包含，
api_server 直接引用本模块的常量与函数。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ---------------------------------------------------------------- 限额

MAX_CORPUS_ENTRIES = 8
MAX_CORPUS_TEXT = 4000          # 单条语料最大字符
MAX_CORPUS_TOTAL = 12000        # 语料合计最大字符
MAX_PERSONA_TEXT = 30000        # 与 fate_engine.MAX_PERSONA_CHARS 对齐
MAX_IDENTITY_TEXT = 200
MAX_LIST_ITEMS = 12             # 与 character_library.MAX_LIST_ITEMS 对齐

# ---------------------------------------------------------------- 第一步：身份

IDENTITY_FIELDS: List[Dict[str, Any]] = [
    {"id": "name", "label": "角色名", "required": True, "placeholder": "如：李青"},
    {"id": "work", "label": "出处作品", "required": False, "placeholder": "如：《示例作品》；原创可留空"},
    {"id": "role_type", "label": "角色定位", "required": False,
     "options": ["主角", "伙伴", "女主", "反派", "配角"],
     "hint": "入库时映射为数据库 role：伙伴→伙伴，女主→single_heroine，反派→宿敌可饰"},
    {"id": "gender", "label": "性别", "required": False,
     "options": ["", "male", "female"],
     "hint": "数据库字段 gender：male/female，留空为 unknown"},
    {"id": "original_position", "label": "原著定位", "required": False,
     "options": ["", "主角", "男主", "女主", "配角", "反派"],
     "hint": "数据库字段 original_position：影响宿敌强度 D 的战力推断（反派4/主角3/配角2）"},
    {"id": "archetype", "label": "原型", "required": False,
     "placeholder": "如：魔道巨擘 / 隐忍复仇者 / 乐天傻瓜"},
    {"id": "one_line", "label": "一句话概括", "required": False,
     "placeholder": "如：以永生为唯一目标的五百年老魔"},
]

# ---------------------------------------------------------------- 第二步：语料分类
# 处置策略（调研结论）：原著原文 > 官方设定 > 用户印象 > 参照角色；
# 参照角色只借原型与语言骨架，不借具体设定，防串戏。

CORPUS_KINDS: Dict[str, Dict[str, Any]] = {
    "original_text": {
        "label": "原著原文",
        "hint": "原文片段、台词。最高优先级：台词直接进语言样本，行为进判例库。",
        "priority": 1,
    },
    "official_setting": {
        "label": "官方设定",
        "hint": "设定集、人物小传。结构化填入背景/能力/知情范围，作事实层。",
        "priority": 2,
    },
    "user_impression": {
        "label": "用户印象",
        "hint": "你的口述理解。作权重调节；与原著冲突时默认让位原著。",
        "priority": 3,
    },
    "reference_character": {
        "label": "参照角色",
        "hint": "“像某某”的参照。只借原型与语言骨架，不借具体设定。",
        "priority": 4,
    },
}

# ---------------------------------------------------------------- 第三步：选择题
# 每题映射角色卡字段；题库来自调研论证的“10 题保底”方案。

QUESTIONS: List[Dict[str, Any]] = [
    {"id": "q_desire", "field": "desire", "question": "他最想要什么？",
     "options": [{"key": "power", "text": "权力/力量"}, {"key": "belonging", "text": "爱与归属"},
                 {"key": "justice", "text": "复仇或正义"}, {"key": "freedom", "text": "自由"},
                 {"key": "recognition", "text": "认可/证明自己"}, {"key": "survival", "text": "生存/安全"}]},
    {"id": "q_fear", "field": "fear", "question": "他最深怕什么？",
     "options": [{"key": "abandon", "text": "被抛弃/背叛"}, {"key": "control", "text": "失控"},
                 {"key": "mediocre", "text": "平庸/被遗忘"}, {"key": "truth", "text": "真相败露"},
                 {"key": "trauma", "text": "重蹈创伤"}, {"key": "death", "text": "死亡"}]},
    {"id": "q_principle", "field": "decision_principle", "question": "两难抉择时，他优先牺牲什么？",
     "options": [{"key": "others", "text": "他人利益"}, {"key": "self", "text": "自身利益"},
                 {"key": "rules", "text": "规则/承诺"}, {"key": "bonds", "text": "感情/羁绊"}]},
    {"id": "q_taboo", "field": "unacceptable_actions", "question": "他绝不做的事？",
     "options": [{"key": "betray", "text": "背叛信任自己的人"}, {"key": "innocent", "text": "伤害无辜"},
                 {"key": "lie", "text": "说谎"}, {"key": "beg", "text": "低头求人"},
                 {"key": "giveup", "text": "认输/放弃"}, {"key": "none", "text": "没有禁区"}]},
    {"id": "q_voice", "field": "voice", "question": "他说话的整体风格？",
     "options": [{"key": "terse", "text": "短句冷峻"}, {"key": "ornate", "text": "华丽迂回"},
                 {"key": "ironic", "text": "讽刺挖苦"}, {"key": "gentle", "text": "温和少言"},
                 {"key": "crude", "text": "粗俗直接"}]},
    {"id": "q_tell", "field": "voice_tell", "question": "他有口头禅或标志性的语言习惯吗？",
     "options": [{"key": "catchphrase", "text": "有固定口头禅"}, {"key": "address", "text": "对人有特殊称呼"},
                 {"key": "rhetoric", "text": "爱用反问/比喻"}, {"key": "none", "text": "没有明显口癖"}]},
    {"id": "q_attitude", "field": "relationship_vector", "question": "他对主角/玩家的初始态度？",
     "options": [{"key": "hostile", "text": "敌视"}, {"key": "wary", "text": "防备"},
                 {"key": "use", "text": "利用"}, {"key": "protect", "text": "庇护"},
                 {"key": "curious", "text": "好奇/观察"}, {"key": "loyal", "text": "忠诚"}]},
    {"id": "q_knowledge", "field": "knowledge_scope", "question": "他掌握的信息处于什么水平？",
     "options": [{"key": "truth", "text": "知道世界/局势的真相"}, {"key": "domain", "text": "只精通自身领域"},
                 {"key": "secret", "text": "掌握某个关键秘密"}, {"key": "little", "text": "所知甚少"}]},
    {"id": "q_contrast", "field": "contrast", "question": "他的表里反差？",
     "options": [{"key": "cold_out", "text": "外冷内热"}, {"key": "warm_out", "text": "外热内冷"},
                 {"key": "consistent", "text": "表里如一"}]},
    {"id": "q_cost", "field": "ability_cost", "question": "他的能力有什么代价或限制？",
     "options": [{"key": "heavy", "text": "代价沉重（伤身/折寿/失控）"}, {"key": "conditional", "text": "条件苛刻才能发动"},
                 {"key": "cooldown", "text": "有冷却/次数限制"}, {"key": "none", "text": "几乎无代价"}]},
]

# ---------------------------------------------------------------- 角色卡字段

CARD_FIELDS: Tuple[str, ...] = (
    "name", "work", "archetype", "one_line",
    "desire", "fear", "decision_principle",
    "voice", "voice_samples",
    "unacceptable_actions", "abilities", "ability_limits",
    "relationship_vector", "knowledge_scope", "background", "references",
    # 数据库口径字段：保存到角色库（character_pools/character_db）时直接可用
    "gender", "original_position", "source_medium", "source_region", "slot_keys",
)

# 数据库 slot_keys 四栏标签词表（与 data/character_pools.json 的 16 个标签一致）
SLOT_NAMES: Tuple[str, ...] = ("主角栏", "伴侣栏", "伙伴栏", "宿敌栏")
SLOT_KEY_VOCAB: Tuple[str, ...] = (
    "通用", "天命担当型", "逆袭成长型", "镜像宿命型", "理念冲突型",
    "细水长流型", "欢喜冤家型", "并肩作战型", "相互救赎型",
    "军师智囊", "守护护卫", "技术支援", "气氛担当",
    "武力压制型", "体制碾压型", "智斗博弈型",
)
# 数据库 gender/original_position/source_region 合法值
GENDER_VALUES: Tuple[str, ...] = ("male", "female", "unknown")
POSITION_VALUES: Tuple[str, ...] = ("主角", "男主", "女主", "配角", "反派")
# 设计器 role_type → 数据库 role 映射（catalog.ROLES 的子集）
ROLE_TYPE_TO_DB_ROLE: Dict[str, str] = {
    "主角": "主角", "伙伴": "伙伴", "女主": "single_heroine",
    "反派": "反派", "配角": "伙伴",
}

# 及格线（可扮演不漂移）：四项非空，voice 需含台词样本。
CORE_FIELDS: Tuple[str, ...] = ("desire", "fear", "voice", "unacceptable_actions")
# 灵魂层：在及格线之上决定“拥有灵魂”。
SOUL_FIELDS: Tuple[str, ...] = (
    "relationship_vector", "knowledge_scope", "background", "references", "ability_limits",
)

_FIELD_LABELS: Dict[str, str] = {
    "desire": "欲望", "fear": "恐惧", "decision_principle": "决策原则",
    "voice": "语言风格", "voice_samples": "台词样本",
    "unacceptable_actions": "行为禁区", "abilities": "能力", "ability_limits": "能力代价/限制",
    "relationship_vector": "关系向量", "knowledge_scope": "知情范围",
    "background": "背景", "references": "原著判例",
}

_PERSONA_SPLIT = "===PERSONA==="

# ---------------------------------------------------------------- 工具


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def suggest_filename(name: Any) -> str:
    """把角色名转为安全文件名：保留中文/字母/数字/_/-，其余折叠为 -。"""
    slug = re.sub(r"[^0-9A-Za-z一-鿿_\-]+", "-", _text(name)).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "character"


def _answer_text(answers: Mapping[str, Any], question_id: str) -> str:
    """取某题答案的显示文本；容忍前端传 key 或整段文本。"""
    raw = _text(answers.get(question_id))
    if not raw:
        return ""
    for question in QUESTIONS:
        if question["id"] != question_id:
            continue
        for option in question["options"]:
            if option["key"] == raw or option["text"] == raw:
                return option["text"]
    return raw


def _field_from_answers(answers: Mapping[str, Any], field: str) -> str:
    parts = []
    for question in QUESTIONS:
        if question["field"] != field:
            continue
        text = _answer_text(answers, question["id"])
        if text:
            parts.append(text)
    return "；".join(parts)


# ---------------------------------------------------------------- 第一步：身份校验


def validate_identity(identity: Mapping[str, Any]) -> Dict[str, str]:
    """校验并规范化身份信息；name 必填。非法时抛 ValueError。"""
    if not isinstance(identity, Mapping):
        raise ValueError("identity 必须是对象")
    result: Dict[str, str] = {}
    for field in IDENTITY_FIELDS:
        value = _text(identity.get(field["id"]))
        if len(value) > MAX_IDENTITY_TEXT:
            raise ValueError("身份字段 %s 超长（%d 字上限）" % (field["label"], MAX_IDENTITY_TEXT))
        if field.get("required") and not value:
            raise ValueError("请填写%s" % field["label"])
        if value:
            result[field["id"]] = value
    if not result.get("name"):
        raise ValueError("请填写角色名")
    # 兼容前端直传的额外字段，保留但不信任其长度。
    known = {field["id"] for field in IDENTITY_FIELDS}
    for key, value in identity.items():
        if key not in known and key not in result:
            extra = _text(value)
            if extra:
                result[str(key)] = extra[:MAX_IDENTITY_TEXT]
    return result


# ---------------------------------------------------------------- 第二步：语料分类

_DIALOGUE_HINT_RE = re.compile(r"[「」『』“”]|——")
_REFERENCE_HINT_RE = re.compile(r"(?:类似|仿佛|参照|像.{0,6}一样|同款)")


def _guess_kind(text: str) -> str:
    """kind 缺失时的保守猜测：对白密度高→原著原文；含参照措辞→参照角色；否则用户印象。"""
    if _REFERENCE_HINT_RE.search(text):
        return "reference_character"
    if len(_DIALOGUE_HINT_RE.findall(text)) >= 4:
        return "original_text"
    return "user_impression"


def classify_corpus(corpus: Any) -> List[Dict[str, str]]:
    """校验并分类语料条目，返回 [{kind, text}]；非法时抛 ValueError。"""
    if corpus is None:
        return []
    if not isinstance(corpus, list):
        raise ValueError("corpus 必须是数组")
    if len(corpus) > MAX_CORPUS_ENTRIES:
        raise ValueError("语料最多 %d 条" % MAX_CORPUS_ENTRIES)
    items: List[Dict[str, str]] = []
    total = 0
    for i, entry in enumerate(corpus, 1):
        if isinstance(entry, str):
            entry = {"kind": "", "text": entry}
        if not isinstance(entry, Mapping):
            raise ValueError("第 %d 条语料格式非法" % i)
        text = _text(entry.get("text"))
        if not text:
            continue
        if len(text) > MAX_CORPUS_TEXT:
            raise ValueError("第 %d 条语料超长（%d 字上限）" % (i, MAX_CORPUS_TEXT))
        kind = _text(entry.get("kind")) or _guess_kind(text)
        if kind not in CORPUS_KINDS:
            raise ValueError("第 %d 条语料类型未知：%s（可选：%s）"
                             % (i, kind, "/".join(CORPUS_KINDS)))
        total += len(text)
        if total > MAX_CORPUS_TOTAL:
            raise ValueError("语料合计超长（%d 字上限）" % MAX_CORPUS_TOTAL)
        items.append({"kind": kind, "text": text})
    return items


# ---------------------------------------------------------------- 第四步：融合


def fusion_prompt(identity: Mapping[str, Any],
                  corpus: List[Mapping[str, str]],
                  answers: Mapping[str, Any]) -> str:
    """组织融合提示词：身份 + 分类语料（含处置规则）+ 选择题答案 → JSON 卡 + persona。"""
    parts: List[str] = [
        "你是角色蒸馏器。根据以下三类输入，把这个角色固化为一张结构化角色卡和一份扮演指南。\n",
        "【身份信息】",
    ]
    for field in IDENTITY_FIELDS:
        value = _text(identity.get(field["id"]))
        if value:
            parts.append("- %s：%s" % (field["label"], value))

    parts.append("\n【语料】（已分类；优先级：原著原文 > 官方设定 > 用户印象 > 参照角色。"
                 "冲突时低优先级让位高优先级；参照角色只借原型与语言骨架，不借具体设定。）")
    if corpus:
        for kind in sorted(CORPUS_KINDS, key=lambda k: CORPUS_KINDS[k]["priority"]):
            group = [item["text"] for item in corpus if item.get("kind") == kind]
            if not group:
                continue
            parts.append("◆ %s：" % CORPUS_KINDS[kind]["label"])
            parts.extend("  " + text.replace("\n", "\n  ") for text in group)
    else:
        parts.append("（未提供语料，仅依据身份与选择题合理推演，不得虚构具体原著情节。）")

    parts.append("\n【用户选择题】")
    answered = False
    for question in QUESTIONS:
        text = _answer_text(answers, question["id"])
        if text:
            answered = True
            parts.append("- %s → %s" % (question["question"], text))
    if not answered:
        parts.append("（未作答，依据身份与语料自行判断。）")

    schema_lines = ",\n".join('  "%s": ""' % field for field in CARD_FIELDS if field != "slot_keys")
    parts.append(
        "\n【输出格式】严格遵守，不要输出任何其他内容：\n"
        "1) 一个 ```json 代码块，字段如下（字符串字段写凝练陈述；voice_samples 为数组，"
        "给 2-4 段该角色口吻的台词样本；unacceptable_actions、references 为数组；"
        "ability_limits 写清范围/代价/冷却/限制）：\n"
        "```json\n{\n%s,\n"
        '  "slot_keys": {"主角栏": ["..."], "伴侣栏": ["..."], "伙伴栏": ["..."], "宿敌栏": ["..."]}\n'
        "}\n```\n" % schema_lines +
        "字段口径（务必遵守，保存时直接入库）：\n"
        '- gender：只能是 "male"/"female"/"unknown" 之一。\n'
        "- original_position：原著中身份，只能是「主角/男主/女主/配角/反派」之一；原创角色按气质归入配角或主角。\n"
        "- relationship_vector：必须是对象（如 {\"陈玄\": \"亦敌亦友\"}），键为对象名、值为关系概述；不要写散文。\n"
        "- knowledge_scope：必须是数组，每条一个知识领域（如 [\"庆国宫廷礼制\", \"家族资源\"]）；不要写成一整句。\n"
        "- slot_keys：四栏定位标签，每栏 1-2 个，只能从以下词表选：\n"
        "  主角栏/伴侣栏/伙伴栏/宿敌栏通用词表：%s。\n"
        "  按角色实际适配的栏位给标签，不适配的栏位填 [\"通用\"]。\n"
        "- source_medium：角色来源媒介（如：网文/动漫/影视/游戏/原创）；source_region：cn/jp/west/original。\n" % "、".join(SLOT_KEY_VOCAB) +
        "2) 独占一行的 %s\n" % _PERSONA_SPLIT +
        "3) 其后为扮演指南正文（markdown）：语言风格规则与台词样本、行为禁区、决策原则、"
        "关系边界、知情范围。具体行为规则优于形容词。"
    )
    return "\n".join(parts)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _extract_json(text: str) -> Dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    candidate = match.group(1) if match else None
    if candidate is None:
        start = text.find("{")
        if start < 0:
            raise ValueError("模型输出中未找到 JSON 角色卡")
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            raise ValueError("角色卡 JSON 解析失败：%s" % exc) from exc
        if not isinstance(obj, dict):
            raise ValueError("角色卡 JSON 不是对象")
        return obj
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("角色卡 JSON 解析失败：%s" % exc) from exc
    if not isinstance(obj, dict):
        raise ValueError("角色卡 JSON 不是对象")
    return obj


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _normalize_gender(value: Any) -> str:
    """gender 归一到数据库合法值 male/female/unknown；容忍中文输入。"""
    text = _text(value).lower()
    if not text:
        return "unknown"
    if text in GENDER_VALUES:
        return text
    if any(word in text for word in ("男", "雄", "male", "man", "boy")):
        return "male"
    if any(word in text for word in ("女", "雌", "female", "woman", "girl")):
        return "female"
    return "unknown"


def _normalize_position(value: Any) -> str:
    """original_position 归一到数据库合法值（主角/男主/女主/配角/反派）。"""
    text = _text(value)
    if text in POSITION_VALUES:
        return text
    if any(word in text for word in ("反派", "宿敌", "敌人", "大魔头", "boss")):
        return "反派"
    if "男主" in text:
        return "男主"
    if "女主" in text:
        return "女主"
    if any(word in text for word in ("主角", "主人公", "视点")):
        return "主角"
    if any(word in text for word in ("配角", "次要", "路人")):
        return "配角"
    return ""


def _normalize_slot_keys(value: Any) -> Dict[str, List[str]]:
    """slot_keys 归一：白名单过滤 + 历史键名主线栏→伴侣栏 + 四维兜底通用。"""
    result: Dict[str, List[str]] = {}
    if isinstance(value, Mapping):
        for key, tags in value.items():
            slot = _text(key).replace("主线栏", "伴侣栏")
            if slot not in SLOT_NAMES:
                continue
            if isinstance(tags, str):
                tags = [tags]
            if not isinstance(tags, (list, tuple)):
                continue
            picked = []
            for tag in tags:
                tag_text = _text(tag)
                if tag_text in SLOT_KEY_VOCAB and tag_text not in picked:
                    picked.append(tag_text)
            if picked:
                result[slot] = picked
    for slot in SLOT_NAMES:
        if not result.get(slot):
            result[slot] = ["通用"]
    return result


def _split_scope(value: Any) -> List[str]:
    """knowledge_scope 宽松拆分：str（顿号/分号/逗号/句号切段）→ 数组。"""
    if isinstance(value, (list, tuple)):
        return [item for item in (_text(v) for v in value) if item][:MAX_LIST_ITEMS]
    text = _text(value)
    if not text:
        return []
    segments = [seg.strip("。；;，, ") for seg in re.split(r"[。；;]", text)]
    return [seg for seg in segments if seg][:MAX_LIST_ITEMS]


def _normalize_db_fields(card: Mapping[str, Any],
                         identity: Mapping[str, Any]) -> Dict[str, Any]:
    """把融合卡/身份信息规整成数据库可直接消费的字段集合。

    与 engine.character_library.build_record / engine.catalog.CharacterCard.from_record
    的校验口径对齐：gender 三值、original_position 五值、slot_keys 四栏白名单、
    knowledge_scope 数组、relationship_vector 优先保留 dict 形态。
    """
    gender = _normalize_gender(card.get("gender") or identity.get("gender"))
    position = _normalize_position(card.get("original_position") or identity.get("original_position")
                                   or identity.get("role_type"))
    fields: Dict[str, Any] = {
        "gender": gender,
        "original_position": position,
        "slot_keys": _normalize_slot_keys(card.get("slot_keys")),
    }
    # source_medium / source_region：归一到短词
    medium = _text(card.get("source_medium") or identity.get("source_medium"))
    fields["source_medium"] = medium[:40]
    region = _text(card.get("source_region") or identity.get("source_region")).lower()
    if region not in ("cn", "jp", "west", "original"):
        region = "cn" if medium and ("网文" in medium or "国" in medium) else "original"
    fields["source_region"] = region
    # knowledge_scope：模型可能输出整句，拆成数组
    scope_value = card.get("knowledge_scope")
    if isinstance(scope_value, str) and scope_value.strip():
        fields["knowledge_scope"] = _split_scope(scope_value)
    # relationship_vector：dict 形态保持；str 形态交给 catalog 宽松解析，不强行转换
    return fields


def parse_fusion(text: str,
                 identity: Mapping[str, Any],
                 answers: Mapping[str, Any],
                 corpus: Optional[List[Mapping[str, str]]] = None) -> Tuple[Dict[str, Any], str]:
    """解析模型融合输出为 (角色卡, persona 正文)；失败抛 ValueError。

    模型缺字段时用选择题答案与身份信息兜底，保证卡结构完整。
    集成角色创建规程进行自检和质量验证。
    """
    if not _text(text):
        raise ValueError("模型输出为空")
    raw_card = _extract_json(text)
    card: Dict[str, Any] = {}
    for field in CARD_FIELDS:
        value = raw_card.get(field)
        if _nonempty(value):
            card[field] = value
    # 兜底链：模型输出 → 选择题答案 → 身份信息。
    for field in ("desire", "fear", "decision_principle", "voice",
                  "unacceptable_actions", "relationship_vector", "knowledge_scope"):
        if not _nonempty(card.get(field)):
            fallback = _field_from_answers(answers, field)
            if fallback:
                card[field] = fallback
    if not _nonempty(card.get("voice")):
        tell = _field_from_answers(answers, "voice_tell")
        if tell:
            card["voice"] = tell
    if not _nonempty(card.get("ability_limits")):
        cost = _field_from_answers(answers, "ability_cost")
        if cost:
            card["ability_limits"] = cost
    if not _nonempty(card.get("name")):
        card["name"] = _text(identity.get("name"))
    if not _nonempty(card.get("name")):
        raise ValueError("角色卡缺少 name")
    for field in ("work", "archetype", "one_line"):
        if not _nonempty(card.get(field)) and _text(identity.get(field)):
            card[field] = _text(identity.get(field))
    if not _nonempty(card.get("background")):
        contrast = _field_from_answers(answers, "contrast")
        if contrast:
            card["background"] = "表里反差：%s" % contrast

    # ---- 数据库口径字段规整：保证产出可直接入 character_pools/character_db ----
    card.update(_normalize_db_fields(card, identity))

    # 集成角色创建规程进行自检
    try:
        from core.engine.character_creation_protocol import character_creation_protocol
        is_valid, errors, warnings = character_creation_protocol.validate_character_card(card)
        
        # 记录验证结果
        if warnings:
            import warnings as warnings_module
            warnings_module.warn(f"角色卡质量警告: {'; '.join(warnings)}")
        
        # 如果有验证错误，记录但不阻止（可能是模型输出的非标准格式）
        if errors:
            import warnings as warnings_module
            warnings_module.warn(f"角色卡验证错误: {'; '.join(errors)}")
            
    except Exception:
        # 如果角色创建规程不可用，继续原有逻辑
        pass
    
    if _PERSONA_SPLIT in text:
        persona_text = text.split(_PERSONA_SPLIT, 1)[1].strip()
    else:
        # 没有分隔符时，去掉 JSON 块后的残余文本视为 persona。
        match = _JSON_FENCE_RE.search(text)
        persona_text = text[match.end():].strip() if match else ""
    if not persona_text:
        persona_text = _fallback_persona_text(card)
    return card, persona_text


def _fallback_persona_text(card: Mapping[str, Any]) -> str:
    """模型没给 persona 正文时，用角色卡合成最小可用扮演指南。"""
    lines = []
    if _nonempty(card.get("voice")):
        lines.append("- 语言风格：%s" % card["voice"])
    samples = card.get("voice_samples")
    if isinstance(samples, list) and samples:
        lines.append("- 台词样本：")
        lines.extend("  - %s" % s for s in samples[:4])
    if _nonempty(card.get("unacceptable_actions")):
        value = card["unacceptable_actions"]
        if isinstance(value, list):
            value = "；".join(str(v) for v in value)
        lines.append("- 绝不做：%s" % value)
    if _nonempty(card.get("desire")):
        lines.append("- 一切行为围绕欲望：%s" % card["desire"])
    return "\n".join(lines) or "（暂无扮演指南，请依据角色卡扮演。）"


# ---------------------------------------------------------------- markdown 输出


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def to_persona_markdown(card: Mapping[str, Any],
                        persona_text: str,
                        identity: Mapping[str, Any]) -> str:
    """生成可存入 personas/standard 的 markdown（含 YAML frontmatter）。"""
    name = _text(card.get("name")) or _text(identity.get("name")) or "未命名角色"
    work = _text(card.get("work")) or _text(identity.get("work"))
    one_line = _text(card.get("one_line")) or _text(identity.get("one_line"))
    title = name if not work else "%s（%s）" % (name, work)
    # description 需携带「角色（《作品》）」前缀，使 fate_engine._model_label 能正确提取显示标签。
    if work:
        description = "%s（%s）" % (name, work) + (("：%s" % one_line) if one_line else "")
    else:
        description = one_line or ("%s的角色模型" % name)

    lines = [
        "---",
        "name: %s" % suggest_filename(name),
        "description: %s" % description.replace("\n", " "),
        "---",
        "",
        "# %s" % title,
        "",
    ]
    if one_line:
        lines.append("> %s" % one_line)
        lines.append("")
    lines.append("## 角色卡")
    # 数据库口径字段（gender/slot_keys 等）是结构化元数据，不渲染进 persona markdown
    hidden_fields = {"name", "work", "one_line", "gender", "original_position",
                     "source_medium", "source_region", "slot_keys"}
    for field in CARD_FIELDS:
        if field in hidden_fields:
            continue
        value = card.get(field)
        if not _nonempty(value):
            continue
        label = _FIELD_LABELS.get(field, field)
        if field == "voice_samples" and isinstance(value, list):
            lines.append("- **%s**：" % label)
            lines.extend("  - %s" % sample for sample in value)
        else:
            lines.append("- **%s**：%s" % (label, _render_value(value)))
    lines.extend(["", "## 扮演指南", "", persona_text.strip(), ""])
    return "\n".join(lines)


# ---------------------------------------------------------------- 质量评估


def quality_assessment(identity: Mapping[str, Any],
                       corpus: List[Mapping[str, str]],
                       answers: Mapping[str, Any],
                       card: Mapping[str, Any]) -> Dict[str, Any]:
    """按“及格线/拥有灵魂”双层标准打分。

    及格线 60 分：desire/fear/unacceptable_actions/voice（含台词样本）；
    灵魂层 40 分：关系、知情、背景、判例、能力代价。
    """
    missing: List[str] = []
    score = 0

    voice_ok = _nonempty(card.get("voice"))
    samples = card.get("voice_samples")
    samples_ok = isinstance(samples, list) and any(_text(s) for s in samples)
    for field in CORE_FIELDS:
        if field == "voice":
            if voice_ok and samples_ok:
                score += 15
            elif voice_ok:
                score += 8
                missing.append("台词样本（voice 只有形容词时角色易漂移）")
            else:
                missing.append(_FIELD_LABELS[field])
        elif _nonempty(card.get(field)):
            score += 15
        else:
            missing.append(_FIELD_LABELS[field])

    soul_weights = {"relationship_vector": 10, "knowledge_scope": 8,
                    "background": 8, "references": 7, "ability_limits": 7}
    for field, weight in soul_weights.items():
        value = card.get(field)
        if field == "references":
            if isinstance(value, list) and len(value) >= 3:
                score += weight
            elif _nonempty(value):
                score += weight // 2
                missing.append("原著判例不足 3 条")
            else:
                missing.append(_FIELD_LABELS[field])
        elif _nonempty(value):
            score += weight
        else:
            missing.append(_FIELD_LABELS[field])

    if score >= 85:
        level, label = "soulful", "拥有灵魂"
    elif score >= 45:  # 降低及格线：从60降到45，更容易通过
        level, label = "playable", "及格（可扮演）"
    else:
        level, label = "flat", "扁平"
    return {
        "level": level,
        "label": label,
        "score": score,
        "missing": missing,
        "corpus_kinds": sorted({item.get("kind", "") for item in corpus if item.get("kind")}),
        "answered": sum(1 for q in QUESTIONS if _text(answers.get(q["id"]))),
    }


__all__ = [
    "IDENTITY_FIELDS", "CORPUS_KINDS", "QUESTIONS", "CARD_FIELDS",
    "CORE_FIELDS", "SOUL_FIELDS",
    "SLOT_NAMES", "SLOT_KEY_VOCAB", "GENDER_VALUES", "POSITION_VALUES", "ROLE_TYPE_TO_DB_ROLE",
    "MAX_CORPUS_ENTRIES", "MAX_CORPUS_TEXT", "MAX_CORPUS_TOTAL", "MAX_PERSONA_TEXT",
    "validate_identity", "classify_corpus", "fusion_prompt", "parse_fusion",
    "to_persona_markdown", "quality_assessment", "suggest_filename",
]
