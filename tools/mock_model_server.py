# -*- coding: utf-8 -*-
"""本机 OpenAI 兼容 mock 模型服务：生成矩阵 + 全模块覆盖实测用。

目的：在不需要真实凭据的前提下，让 Novelborne 的完整真实管线
（HTTP / 门禁 / 校验 / 并发 / 持久化）跑起来，观察四种配置的生成路径，
以及金手指/设计器/角色库/搜索/reader-chat/ask/chat/quests/playtest 等
模块的模型调用契约：

    基础+正常    → LEGACY 单卷流式（模型自己在回复尾部列 A–F 选项）
    基础+类Agent → 试卷管线（导演卷→段卷∥选项卷→批改→润色→质量门）
    强化+正常    → 全书准备 + 试卷管线
    强化+类Agent → 全书准备 + 试卷管线 + agent 自检/任务判定等子智能体

路由原则（与 tests/ 夹具同源，见 test_opening_distill.py / test_turn_pipeline.py）：
- 按提示词特征标记分派，**顺序敏感**：合并卷排在采样卷之前、
  导演卷(option_seeds)排在选项卷(options)之前。
- 一切"逐字引文"证据都从提示词内嵌的原文动态摘取，保证过
  difflib/逐字校验门禁——绝不伪造通过，只提供合法输入。
- 未知提示词走 generic 散文体回复，并记为 unmatched 路由，
  供测试报告暴露遗漏。

全程只监听 127.0.0.1；不读取任何用户数据；JSONL 调用日志即测试证据。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- 工具


def _sentence(text: str, min_len: int = 12) -> str:
    """从原文取第一个足够长的完整句（引文证据必须逐字属于原文）。"""
    for piece in re.split(r"(?<=[。！？；])", text):
        piece = piece.strip()
        if len(piece) >= min_len:
            return piece
    return text[:24]


def _last_json_blob(prompt: str):
    """取提示词末尾内嵌的 JSON 对象（部分派发卷把结构化上下文放尾部）。

    说明文字里也会出现 {...} 形状的花括号（如「返回{left:提及ID,...}」），
    因此从最右 '{' 逐个向前尝试后缀解析，命中末尾真 JSON 为止。
    """
    text = prompt.rstrip()
    idx = text.rfind("{")
    while idx >= 0:
        try:
            data = json.loads(text[idx:])
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        idx = text.rfind("{", 0, idx)
    return None


def _numbers(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", text)]


# ---------------------------------------------------------------- 预置合成内容
# 世界观与 tests 夹具同源（李青/白芷/北墙/旧册/铜扣暗记/茶棚），
# 驱动脚本上传的合成小说也写同一批实体，保证"情景扎根"类校验成立。

_RICH_CARD = {
    "name": "李青", "gender": "male", "original_position": "主角",
    "archetype": "隐忍复仇者", "desire": "查清灭门真相并复仇",
    "fear": "再次失去珍视之人", "voice": "短句冷峻，少言",
    "voice_samples": ["风雪要来了。", "刀在人在。"],
    "background": "沈家灭门后隐居边城的旧刀客",
    "unacceptable_actions": ["伤害无辜", "背叛同伴"],
    "relationship_vector": {"白芷": "互相信任的盟友", "沈嬛": "宿敌"},
    "slot_keys": {"主角栏": ["逆袭成长型"], "伴侣栏": ["通用"],
                  "伙伴栏": ["并肩作战型"], "宿敌栏": ["通用"]},
    "evidence_chapter": 1,
}

_OPTION_ITEMS = [
    "推演北墙裂痕下一步走势（后果：提前看清暗哨位置）",
    "回溯旧册水渍前的数目（后果：锁定军械批次）",
    "比对铜扣暗记的刻痕（后果：确认经手人身份）",
    "透支预知换撤离窗口（后果：金手指进入冷却）",
    "先撤回茶棚再图后计（后果：暂避锋芒）",
    "只抄录暗记不惊动守军（后果：证据留存）",
]

_NARRATIVE_TAIL = (
    "风从北墙的裂口里灌进来，把火把压成一豆昏黄。李青按着刀柄没有动，"
    "砖缝里的旧痕在光下显出深浅不一的刻度，像有人反复描过。远处换岗的梆子"
    "敲了两声，守军的甲叶声由密转疏。他把袖口那半页旧册又摸了一遍，纸角的"
    "水渍早干了，数目却还认得出来。白芷站在茶棚檐下，目光越过他的肩落在"
    "城门方向，那里有马蹄声，不急，却一直没有停。"
)


# ---------------------------------------------------------------- 各卷应答


def _reply_legacy_turn(prompt: str) -> str:
    """LEGACY 回合：叙事正文 + A–F 选项行 + 自由输入提示（rounds_rule 契约）。"""
    lines = [_NARRATIVE_TAIL, ""]
    for letter, item in zip("ABCDEF", _OPTION_ITEMS):
        lines.append(f"{letter}. {item}")
    lines.append("（也可以自由输入其它行动。）")
    return "\n".join(lines)


def _reply_option_repair(_: str) -> str:
    lines = [f"{letter}. {item}" for letter, item in zip("ABCDEF", _OPTION_ITEMS)]
    lines.append("（也可以自由输入其它行动。）")
    return "\n".join(lines)


def _reply_generic(_: str) -> str:
    return _NARRATIVE_TAIL


def _reply_opening_check(prompt: str) -> str:
    """开局首条流式调用：基础模式在 start 流内就要 A–F（app.py 开局门禁
    要求回复可解析出完整选项），核对清单与选项并存以满足两侧契约。"""
    return "\n".join([
        "【开局设定核对】作品《边城旧册》；穿越角色：李青（边城旧刀客）；"
        "时间点：第一章 边城风雪，入城当夜；性格要点：冷静谨慎，重证据。",
        "",
        _NARRATIVE_TAIL,
        "",
    ] + [f"{letter}. {item}" for letter, item in zip("ABCDEF", _OPTION_ITEMS)]
      + ["（也可以自由输入其它行动。）"])


def _reply_plot_sample(_: str) -> str:
    return json.dumps({
        "main_events": ["李青风雪夜入城", "城主递信召见", "北境战事将起"],
        "characters": ["李青", "白芷"],
        "tone": "冷峻苍茫的东方玄幻",
        "threads": ["灭门旧案", "城主之谜", "北境战事"],
    }, ensure_ascii=False)


def _reply_plot_merge(_: str) -> str:
    return json.dumps({
        "genre": "东方玄幻（复仇流）",
        "premise": "废刀之刃李青重回边城，在城主召见与北境战事之间周旋，誓要查清当年灭门真相。",
        "major_threads": ["灭门旧案", "城主之谜", "北境战事"],
        "tone": "冷峻克制",
    }, ensure_ascii=False)


def _reply_characters(prompt: str) -> str:
    card = json.loads(json.dumps(_RICH_CARD, ensure_ascii=False))
    # 引文证据从提示词内嵌原文摘取（若有）。
    source = prompt.split("原文：", 1)[1] if "原文：" in prompt else ""
    if source:
        quote = _sentence(source)
        card["evidence_quote"] = quote
    return json.dumps({"characters": [card, {"name": "路人乙", "gender": "unknown",
                                             "original_position": "配角"}]},
                      ensure_ascii=False)


def _reply_refill(_: str) -> str:
    return json.dumps(_RICH_CARD, ensure_ascii=False)


def _reply_archive(_: str) -> str:
    return json.dumps({
        "genre": "东方玄幻（复仇流）", "premise": "边城风雪中的复仇与守护。",
        "tier": "T5（系数 WS 9.5）", "language_style": "短句冷峻，意象密集。",
        "pacing": "事件密集，三章一大战。", "anchors": ["城主召见", "北境战起", "真相揭露"],
        "world_will": "旧案必须清算。", "golden_finger_fit": "刀意觉醒系。",
        "entry_point": "风雪夜入城。", "power_system": "刀意九品。",
        "factions": "城主府对北境军。", "timeline": "入城三日内。",
        "causal_rules": "杀戮必留涟漪。",
    }, ensure_ascii=False)


def _reply_anchor_single(prompt: str) -> str:
    """单章锚点蒸馏：chapter=N + 原文： 动态产九字段，引文逐字取自原文。"""
    match = re.search(r"chapter=(\d+)", prompt)
    number = int(match.group(1)) if match else 1
    original = prompt.split("原文：", 1)[1] if "原文：" in prompt else prompt
    return json.dumps({
        "chapter": number, "title": "第%d章锚点" % number,
        "summary": _sentence(original), "events": ["主角入城", "城楼点灯"],
        "characters": ["李青", "白芷"], "world": "北境边城风雪连年。",
        "foreshadowing": ["城主的信"], "quotes": [_sentence(original)],
        "ripple": "复仇之局初开。",
    }, ensure_ascii=False)


def _reply_block(prompt: str) -> str:
    block_text = prompt.split("【块原文】", 1)[1] if "【块原文】" in prompt else prompt
    return json.dumps({
        "summary": "本块：北境战线推进与扎营。",
        "events": ["敌军过冰河", "李青下令扎营"], "characters": ["李青", "白芷"],
        "quotes": [_sentence(block_text)], "world": "北境战线绵延。",
        "foreshadowing": ["三处隘口"], "ripple": "战事升级。",
    }, ensure_ascii=False)


def _reply_anchor_merge(prompt: str) -> str:
    section = prompt.split("【分块蒸馏结果】", 1)[1] if "【分块蒸馏结果】" in prompt else prompt
    picked: list[str] = []
    for match in re.finditer(r'"quotes"\s*:\s*\[(.*?)\]', section, re.S):
        for quote in re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1)):
            if quote:
                picked.append(quote)
    return json.dumps({
        "chapter": 3, "title": "北境扎营", "summary": "战线推进，李青下令扎营备战。",
        "events": ["敌军过冰河", "李青扎营", "圈定隘口"], "characters": ["李青", "白芷"],
        "world": "北境战线绵延千里。", "foreshadowing": ["三处隘口"],
        "quotes": picked[:3] or [_sentence(section)], "ripple": "战事全面升级。",
    }, ensure_ascii=False)


def _reply_verify(prompt: str) -> str:
    original = prompt.split("【章节原文】", 1)[1] if "【章节原文】" in prompt else prompt
    return json.dumps({
        "chapter": 1, "title": "城门风雪（核验）",
        "summary": _sentence(original), "events": ["李青入城", "白芷点灯"],
        "characters": ["李青", "白芷"], "world": "北境边城风雪连年。",
        "foreshadowing": ["城主的信"], "quotes": [_sentence(original)],
        "ripple": "复仇之局经核验。",
    }, ensure_ascii=False)


def _reply_director(prompt: str) -> str:
    """导演卷：段数/种子数从渲染后的提示词解析（paper_director.md 契约）。"""
    count_match = re.search(r"恰\s*(\d+)\s*段", prompt)
    count = int(count_match.group(1)) if count_match else 3
    gf_match = re.search(r"(\d+)\s*颗[^。]*金手指", prompt)
    persona_match = re.search(r"(\d+)\s*颗[^。]*性格", prompt)
    gf_n = int(gf_match.group(1)) if gf_match else 4
    persona_n = int(persona_match.group(1)) if persona_match else 2
    seeds = [{"factor": "金手指", "direction": f"金手指路线{i}", "preview": "冷却代价"}
             for i in range(gf_n)]
    seeds += [{"factor": "性格", "direction": f"性格路线{i}", "preview": "关系变化"}
              for i in range(persona_n)]
    return json.dumps({
        "beat": "查验北墙", "goal": "拿到旧册", "conflict": "守军阻拦",
        "segments": [
            {"id": f"seg{i + 1}", "role": f"第{i + 1}段", "window": [180, 420],
             "events": [f"独立事件{i + 1}：北墙与旧册线索推进"],
             "must_include": [], "must_mention": ["李青"] if i == 0 else []}
            for i in range(count)
        ],
        "anchor_plan": {"stage": "setup", "action_terms": ["北墙", "旧册"],
                        "result_terms": ["暗记"], "causal_phrase": "因此落定"},
        "ripple_resolution": "以代价收束",
        "world_beats": ["传闻扩散"],
        "cliffhanger": "马蹄声未散",
        "log_draft": {"player": "查验北墙", "golden_finger": "预知示警",
                      "world": "北墙裂痕", "beat": "推进"},
        "option_seeds": seeds,
    }, ensure_ascii=False)


def _reply_options(_: str) -> str:
    return json.dumps({"options": list(_OPTION_ITEMS)}, ensure_ascii=False)


def _parse_segment_window(prompt: str) -> tuple[int, int]:
    for line in prompt.splitlines():
        if "字数窗口" in line:
            nums = _numbers(line)
            if len(nums) >= 2:
                low, high = nums[0], nums[1]
                return max(40, low), max(low + 40, high)
    return 200, 320


def _filler(prompt: str) -> str:
    """段卷/重填卷：窗口内成段，含必含词与点名角色。"""
    low, high = _parse_segment_window(prompt)
    terms: list[str] = []
    for line in prompt.splitlines():
        if "必含词" in line and "：" in line:
            terms += [t for t in re.split(r"[、，,]", line.split("：", 1)[1]) if t.strip()]
        if "点名角色" in line and "：" in line:
            terms += [t for t in re.split(r"[、，,]", line.split("：", 1)[1]) if t.strip()]
    text = "李青沿着北墙查验裂痕，指尖在砖缝的旧刻痕上停了停。"
    for term in terms[:6]:
        text += term if term in text else f" {term}"
    text += "因此守军的换岗节奏被彻底打乱。"
    pad = "风声压过火把的噼啪，旧册的水渍在袖口里发潮，随行的人不敢多问。"
    while len(text) < low:
        text += pad
    return text[:high - 1] if len(text) >= high else text


def _reply_polish(prompt: str) -> str:
    """润色/后处理整合：逐字回显草稿——必含词/因果句/人名全部天然保留。"""
    for marker in ("## 待润色草稿", "【待整合正文】", "【草稿】"):
        if marker in prompt:
            return prompt.split(marker, 1)[1].strip() or _NARRATIVE_TAIL
    return _NARRATIVE_TAIL


def _reply_quality_judge(prompt: str) -> str:
    """质量裁判：每维 88 分 + 从候选稿逐字摘引（证据制硬性要求）。"""
    dims = ["沉浸感", "人物一致性", "锚点因果", "世界观一致", "文笔"]
    for line in prompt.splitlines():
        if line.startswith("- 维度：") or "维度：" in line[:24]:
            dims = [d.strip() for d in line.split("：", 1)[1].rstrip("；;。").split("、") if d.strip()]
            break
    candidate = prompt.split("## 候选稿", 1)[1] if "## 候选稿" in prompt else prompt
    quote = _sentence(candidate, min_len=8)
    return json.dumps({
        "scores": {dim: 88 for dim in dims},
        "evidence": {dim: [quote] for dim in dims},
    }, ensure_ascii=False)


def _reply_verdict(_: str) -> str:
    """任务判定/碎锚阶段判定：保守未完成（合法形状，不伪造完成）。"""
    return json.dumps({"completed": False,
                       "evidence": "本回合正文尚未写到完成条件实际达成。"},
                      ensure_ascii=False)


def _reply_quest_offer(prompt: str) -> str:
    span = re.search(r"恰好\s*(\d+)\s*[–—-]\s*(\d+)\s*条", prompt)
    n = int(span.group(1)) if span else 2
    requirements = ["查清北墙裂痕下暗记的来历", "取回旧册缺失的一页",
                    "确认茶棚掌柜看到的马牌"][:max(1, n)]
    while len(requirements) < n:
        requirements.append(f"核实城门守军换岗记录第{len(requirements)}条")
    return json.dumps({
        "title": "北墙旧册的线索", "requirements": requirements,
        "goal": "在守军换防前查明军械批次流向", "plot_hook": "夜里有快马出城。",
    }, ensure_ascii=False)


def _reply_break_anchor_offer(prompt: str) -> str:
    count = 2
    m = re.search(r"stages\s*必须恰好\s*(\d+)\s*条", prompt) or re.search(
        r"设计一个\s*(\d+)\s*阶段碎锚任务", prompt)
    if m:
        count = int(m.group(1))
    anchor = re.search(r"当前锚点：第\s*(\d+)\s*章「(.*?)」——(.*?)；", prompt)
    chapter = int(anchor.group(1)) if anchor else 1
    title = anchor.group(2) if anchor else "城门风雪"
    summary = anchor.group(3) if anchor else "李青入城，风雪未歇。"
    stages = [{"id": i + 1, "title": f"第{i + 1}步",
               "requirement": req} for i, req in enumerate(
        ["查明北墙裂痕的成因", "证实旧册数目被改动", "挡下守军的搜查", "谈成茶棚的线人"][:count])]
    return json.dumps({
        "target_anchor": {"chapter": chapter, "title": title, "summary": summary},
        "stages": stages,
    }, ensure_ascii=False)


def _reply_self_check(_: str) -> str:
    return json.dumps({"issues": []}, ensure_ascii=False)


def _reply_compressor(_: str) -> str:
    return ("接手摘要：李青入城后以旧册与北墙刻痕为线索追查灭门旧案，"
            "白芷自茶棚传递消息，城主召见将至，北境战事传闻四起。")


def _reply_chat(_: str) -> str:
    return ("白芷把茶碗往前推了推，声音压得很低：「北墙今夜换岗会晚半刻，你若要查那半页旧册，"
            "就得赶在梆子响之前动身。」她说完便低头继续擦桌角，不再多看这边一眼。")


def _reply_semantic_dims(prompt: str) -> str:
    """角色四维语义蒸馏：rules + 逐字 evidence（quote 必须属于证据原文）。"""
    source = prompt.split("【证据原文】", 1)[1] if "【证据原文】" in prompt else prompt
    chap_match = re.search(r"source_chapter_no=(\d+)", prompt)
    chapter = int(chap_match.group(1)) if chap_match else 1
    quote = _sentence(source)
    evidence = [{"chapter": chapter, "quote": quote, "interpretation": "原文行为直接支持该规则"}]
    dims = {
        "mind_model": ["把线索当债，先核证据再行动"],
        "decision_policy": ["冲突时先保活口与证据，牺牲速度"],
        "voice_transfer": ["短句、少修饰，动词前置"],
        "behavior_boundaries": ["绝不伤害无辜；越界需先有实证"],
    }
    return json.dumps({field: {"rules": rules, "evidence": evidence}
                       for field, rules in dims.items()}, ensure_ascii=False)


def _reply_identity_pair(prompt: str) -> str:
    blob = _last_json_blob(prompt) or {}
    left, right = blob.get("left") or {}, blob.get("right") or {}
    left_id = str(left.get("mention_id") or "L")
    right_id = str(right.get("mention_id") or "R")
    left_name = str(left.get("name") or "")
    right_name = str(right.get("name") or "")
    relation = "same" if left_name and left_name == right_name else "different"
    evidence = {}
    for mid, mention in ((left_id, left), (right_id, right)):
        excerpt = str(mention.get("excerpt") or "")
        evidence[mid] = excerpt[:30] if excerpt else ""
    return json.dumps({
        "left": left_id, "right": right_id, "relation": relation,
        "reason": "同名且同段共指。" if relation == "same" else "名字不同，无共指证据。",
        "evidence": evidence,
    }, ensure_ascii=False)


def _reply_scoped_facts(_: str) -> str:
    return json.dumps({"facts": []}, ensure_ascii=False)


def _reply_semantic_scene(prompt: str) -> str:
    """语义定位卷（SEMANTIC_SCENE_V1 契约）：整块作为一个事件候选。

    坐标取自提示词尾部的 {"query","text"}：start=0、end=len(text)、
    during=len//2，excerpt 逐字等于 text[start:end]——通过块内偏移与
    逐字证据校验；不匹配时返回空数组（合法形状）。
    """
    blob = _last_json_blob(prompt) or {}
    text = str(blob.get("text") or "")
    if len(text) >= 8:
        during = max(1, len(text) // 2)
        return json.dumps({"scenes": [{
            "start": 0, "end": len(text), "during_offset": during,
            "excerpt": text, "reason": "该块围绕北墙换岗与旧册线索，与查询语义相关。",
        }]}, ensure_ascii=False)
    return json.dumps({"scenes": []}, ensure_ascii=False)


def _reply_fullbook_rich(prompt: str) -> str:
    blob = _last_json_blob(prompt) or {}
    identity = blob.get("identity") or {}
    names = identity.get("names") or ["李青"]
    evidence_list = blob.get("evidence") or []
    card = json.loads(json.dumps(_RICH_CARD, ensure_ascii=False))
    card["name"] = str(names[0])
    facts = []
    for ev in evidence_list[:2]:
        quote = str(ev.get("quote") or "")
        if len(quote) >= 6:
            facts.append({"evidence_id": ev.get("evidence_id"), "value": quote[:6]})
    card["facts"] = facts
    return json.dumps(card, ensure_ascii=False)


_KNOWN_ENTITIES = (
    ("李青", "character"), ("白芷", "character"), ("沈嬛", "character"),
    ("北墙", "place"), ("茶棚", "place"), ("城主府", "place"),
    ("旧册", "object"), ("铜扣", "object"),
    ("守军", "organization"), ("北境军", "organization"),
)


def _reply_block_evidence(prompt: str) -> str:
    """全书准备块卷：{"anchor":九字段,"entities":[{name,kind,excerpt}]}；
    引文与实体摘句逐字取自块原文（book_prepare_service._validate_block）。"""
    original = prompt.split("原文：\n", 1)[1] if "原文：\n" in prompt else prompt
    match = re.search(r"chapter=(\d+)", prompt)
    number = int(match.group(1)) if match else 1
    entities = []
    for name, kind in _KNOWN_ENTITIES:
        for piece in re.split(r"(?<=[。！？；])", original):
            piece = piece.strip()
            if name in piece and len(piece) >= len(name) + 2:
                entities.append({"name": name, "kind": kind, "excerpt": piece})
                break
    return json.dumps({
        "anchor": {
            "chapter": number, "title": "第%d章锚点" % number,
            "summary": _sentence(original), "events": ["主角入城", "城楼点灯"],
            "characters": ["李青", "白芷"], "world": "北境边城风雪连年。",
            "foreshadowing": ["城主的信"], "quotes": [_sentence(original)],
            "ripple": "复仇之局初开。",
        },
        "entities": entities,
    }, ensure_ascii=False)


def _reply_traverse_map(prompt: str) -> str:
    """穿越身份落定：按名单行生成附身对照（gender_guard 契约，严格 JSON）。"""
    rows = []
    for match in re.finditer(r"-\s*([^「\n]{1,6})「([^」]+)」", prompt):
        slot = match.group(1).strip()
        name = match.group(2).strip()
        rows.append({
            "穿越者": name,
            "栏位": slot if slot in ("主角", "伴侣", "伙伴", "宿敌") else "主角",
            "附身角色": name if name in ("李青", "白芷", "沈嬛") else "李青",
            "身份": "边城旧刀客，故事开篇身处北门内茶棚一代，可接入",
            "性别": "男",
        })
    if not rows:
        rows = [{"穿越者": "李青", "栏位": "主角", "附身角色": "李青",
                 "身份": "边城旧刀客，开篇入城当夜", "性别": "男"}]
    return json.dumps({"结果": rows}, ensure_ascii=False)


def _reply_cluster_skill(prompt: str) -> str:
    """类Agent生成簇 worker（generation_skills 契约）。

    所有 skill 的提示词首行固定为英文声明，其后是单行 canonical JSON：
    {"skill_id","snapshot_hash","context","artifacts","output_schema",...}。
    产物按 output_schema 构造：
    - plan  → {"summary", "claims":[]}（空 claims 合法，避免接地子集校验）
    - scene → {"narrative", "claims":[]}（polish 层 claims 必须与 scene_draft 相同）
    - critic → {"approved":true,"issues":[]}
    - options → 恰好 6 项 A–F，base_revision 复制 context，preconditions/effects 为空
    - decision → {"decision":"approve","reasons":[...]}
    """
    parts = prompt.split("\n", 1)
    if len(parts) < 2:
        raise ValueError("cluster prompt missing canonical payload")
    payload = json.loads(parts[1])
    skill = str(payload.get("skill_id") or "")
    schema = str(payload.get("output_schema") or "")
    context = payload.get("context") or {}
    artifacts = payload.get("artifacts") or {}
    action = str(((context.get("data") or {}).get("player_action")) or "").strip()
    if schema == "plan":
        return json.dumps({
            "summary": ("开局计划：以「%s」为目标，先立足北门茶棚，核对旧册与铜扣暗记，"
                        "在城主召见前取得可验证的第一条线索，避免过早暴露身份。" % (action or "开始第一幕")),
            "claims": [],
        }, ensure_ascii=False)
    if schema == "scene":
        upstream = str(((artifacts.get("story.scene_draft") or {}).get("narrative")) or "")
        if skill == "story.polish" and upstream:
            narrative = upstream + " 檐下灯火晃了一下，他把到嘴边的话咽了回去，只把袖口旧册又按了按。"
        else:
            narrative = _NARRATIVE_TAIL + ("（本回行动：%s）" % action if action else "")
        return json.dumps({"narrative": narrative, "claims": []}, ensure_ascii=False)
    if schema == "critic":
        return json.dumps({"approved": True, "issues": []}, ensure_ascii=False)
    if schema == "options":
        base_revision = context.get("base_revision")
        options = [{
            "option_id": "opt-%s" % letter.lower(),
            "label": letter,
            "text": item,
            "base_revision": base_revision,
            "preconditions": [],
            "effects": [],
            "knowledge_evidence_ids": [],
        } for letter, item in zip("ABCDEF", _OPTION_ITEMS)]
        return json.dumps({"options": options, "claims": []}, ensure_ascii=False)
    if schema == "decision":
        return json.dumps({
            "decision": "approve",
            "reasons": ["连续性与选项审查均通过，候选稿可提交"],
        }, ensure_ascii=False)
    raise ValueError("unknown cluster output_schema: %s" % schema)


# ---------------------------------------------------------------- 全模块扩展路由


def _reply_reader_dialogue(_: str) -> str:
    """阅读域角色对话（reader_chat_service 补充头 READER_CONTEXT_V1 契约）。

    结构化草稿：纯社交 utterance（问候/提问/礼貌），整段一个 social span，
    不引用任何 known_fact——避免事实接地校验，代码级 span 覆盖检查天然满足。
    """
    utterance = ("阁下想问什么？不妨先坐下，喝口热茶，想问的事一件一件说来便是；"
                 "我在这儿听着，你慢慢讲。茶刚续上，还烫着，急不得。")
    return json.dumps({
        "utterance": utterance,
        "grounded_claims": [],
        "social_spans": [{"start": 0, "end": len(utterance)}],
    }, ensure_ascii=False)


def _reply_reader_verify(prompt: str) -> str:
    """阅读域逐条核验卷（READER_DISCLOSURE_VERIFY_V1 契约）。

    提示词末尾是 canonical({"utterance","claims","social",...})；按清单
    长度逐项放行（claims→supported，social→nonfactual）。
    """
    blob = _last_json_blob(prompt) or {}
    claims = blob.get("claims") if isinstance(blob.get("claims"), list) else []
    social = blob.get("social") if isinstance(blob.get("social"), list) else []
    return json.dumps({
        "claims": [{"index": i, "supported": True} for i in range(len(claims))],
        "social": [{"index": i, "nonfactual": True} for i in range(len(social))],
        "no_unattributed_claims": True,
        "no_contradictions": True,
    }, ensure_ascii=False)


_DESIGNER_CARD = {
    "name": "程无咎", "work": "", "archetype": "游历医师",
    "one_line": "只想开一间小药铺却总被卷入麻烦的游方医师",
    "desire": "认可/证明自己", "fear": "重蹈创伤",
    "decision_principle": "规则/承诺",
    "voice": "短句冷峻，少言",
    "voice_samples": ["药要趁热。", "脉象不会说谎。", "账，回头再算。"],
    "unacceptable_actions": ["伤害无辜", "说谎"],
    "abilities": ["辨毒断症", "急救处理"],
    "ability_limits": "代价沉重（旧伤复发时手不稳）",
    "relationship_vector": {"李青": "互相欠过一次人情"},
    "knowledge_scope": ["草药辨识", "北地疫症"],
    "background": "游历北地的医师，途经边城时被卷入旧册风波。",
    "references": ["在北墙下为守军处理过冻伤", "认出旧册水渍是药汁", "替茶棚掌柜抓过药"],
    "gender": "male", "original_position": "配角",
    "source_medium": "原创", "source_region": "original",
    "slot_keys": {"主角栏": ["通用"], "伴侣栏": ["通用"],
                  "伙伴栏": ["军师智囊"], "宿敌栏": ["通用"]},
}


def _reply_designer_fusion(_: str) -> str:
    """角色设计器融合卷（character_designer 契约）：```json 角色卡 + ===PERSONA===。"""
    card = json.dumps(_DESIGNER_CARD, ensure_ascii=False, indent=2)
    persona = (
        "## 语言风格\n- 短句为主，动词前置，少形容词。\n- 台词样本：见角色卡；问诊时语速更慢。\n\n"
        "## 行为禁区\n- 绝不伤害无辜；不确定的药绝不开。\n\n"
        "## 决策原则\n- 先保活口与证据，再谈恩怨；承诺过的账一定认。\n\n"
        "## 关系边界与知情范围\n- 只掌握草药与北地疫症方面的知识；对边城旧案所知有限，不主动打探。"
    )
    return "```json\n%s\n```\n===PERSONA===\n%s" % (card, persona)


def _reply_gf_polish(prompt: str) -> str:
    """金手指设计器润色卷（gf_designer.polish_prompt 契约）：纯 JSON 规格。

    回显「当前规格」并只微调名称——机制边界/代价/冷却逐字保留，
    三条锁定限制由 apply_polish 强制回填。
    """
    base = {}
    if "当前规格：\n" in prompt:
        tail = prompt.split("当前规格：\n", 1)[1]
        for idx in range(len(tail) - 1, -1, -1):
            if tail[idx] != "{":
                continue
            try:
                parsed = json.loads(tail[idx:])
                if isinstance(parsed, dict):
                    base = parsed
                    break
            except ValueError:
                pass
    name = str(base.get("name") or "信息")
    if len(name) < 15:
        name = name + "·北墙"
    payload = dict(base)
    payload["name"] = name
    payload.setdefault("effect", "记录并预警关键线索的变动")
    payload.setdefault("cost", "精神负荷")
    payload.setdefault("cooldown", "每日一次")
    payload["limits"] = "不得抹除既成事实、不得越过世界上限、必须可验证"
    payload.setdefault("scope", "")
    payload.setdefault("fit", "")
    payload.setdefault("source", base.get("source") or "custom")
    return json.dumps(payload, ensure_ascii=False)


def _reply_ask(_: str) -> str:
    """规则问答（ask_service 契约）：只答规则文档覆盖的内容。"""
    return ("依据规则文档：本作分基础/强化两种模式；基础模式按开局窗口蒸馏原著，"
            "强化模式需要先完成全书准备任务；回合推进由 A–F 选项或自由输入驱动，"
            "任务、碎锚、锚点蒸馏分别由对应面板管理。规则未覆盖的细节我无法确认。")


def _reply_directive_register(prompt: str) -> str:
    """铁律登记卷（directives 契约）：结构化 JSON，affected 取自提示词白名单。"""
    text = ""
    if "## 玩家输入\n" in prompt:
        text = prompt.split("## 玩家输入\n", 1)[1].split("\n", 1)[0].strip()
    affected = ["全局"]
    if "实体名（严禁编造名字）\n" in prompt:
        line = prompt.split("实体名（严禁编造名字）\n", 1)[1].split("\n", 1)[0]
        items = [item.strip() for item in line.split("、") if item.strip()]
        if items:
            affected = [items[0]]
    return json.dumps({
        "fact_norm": (text or "北墙旧册的线索提前一页被人取走。")[:120],
        "scope": "world",
        "affected": affected,
        "conflicts": [],
        "affected_anchors": [],
    }, ensure_ascii=False)


def _reply_work_distill(prompt: str) -> str:
    """快速蒸馏卷（work_distiller 契约）：```json {archive, characters}```。"""
    card = json.loads(json.dumps(_DESIGNER_CARD, ensure_ascii=False))
    card["work"] = "《边城旧册》"
    return "```json\n%s\n```" % json.dumps({
        "archive": {
            "genre": "东方玄幻（复仇流）", "tier": "T5（系数 WS 9.5）",
            "language_style": "短句冷峻，意象密集。", "pacing": "事件密集，三章一大战。",
            "anchors": ["城主召见", "北境战起", "真相揭露"],
            "world_will": "旧案必须清算。", "golden_finger_fit": "刀意觉醒系。",
            "entry_point": "风雪夜入城。", "power_system": "刀意九品。",
            "factions": "城主府对北境军。", "timeline": "入城三日内。",
            "causal_rules": "杀戮必留涟漪。",
        },
        "characters": [card],
    }, ensure_ascii=False)


def _reply_autoplay(prompt: str) -> str:
    """托管选线卷（autoplay 契约）：{"choice": 合法字母, "reason"}。"""
    choice = "A"
    for line in prompt.splitlines():
        match = re.match(r"^([A-F])[.、]\s*", line.strip())
        if match:
            choice = match.group(1)
            break
    return json.dumps({
        "choice": choice,
        "reason": "冷静谨慎、先谋后动：选信息收益最高且暴露风险最低的一项。",
    }, ensure_ascii=False)


def _reply_copilot(user_text: str) -> str:
    """Copilot 对话（copilot_service 契约）：按最后一条 user 消息回工具 JSON 或终答。

    两步确定性协议：首轮按关键词回 {"tool":...,"args":...}；服务端执行后
    回填【工具结果】作为新的 user 消息，据此输出自然语言终答（不再调工具）。
    只看 user 消息本身——Copilot 系统提示词里也含「状态/手册」等词，不能参与判定。
    """
    if user_text.startswith("【工具结果】"):
        return ("已为你查询完成：以上是当前状态/检索结果。"
                "可以在界面上继续操作，或告诉我下一步想做什么。")
    text = user_text[-300:]
    if any(k in text for k in ("状态", "概览", "现在怎么样")):
        return json.dumps({"tool": "get_game_overview", "args": {}}, ensure_ascii=False)
    if any(k in text for k in ("文档", "手册", "帮助", "怎么用", "如何")):
        return json.dumps({"tool": "search_docs", "args": {"query": "开局"}}, ensure_ascii=False)
    if any(k in text for k in ("存档", "进度")):
        return json.dumps({"tool": "list_saves", "args": {}}, ensure_ascii=False)
    if any(k in text for k in ("入口", "导航", "能做什么", "功能")):
        return json.dumps({"tool": "list_entries", "args": {}}, ensure_ascii=False)
    return ("我是《书中织梦》Copilot 助手：可以查询对局状态、检索用户手册，"
            "并通过白名单工具替你执行存档、准备、托管等操作。")


def _reply_export_rewrite(prompt: str) -> str:
    """导出小说三遍改写（novel_exporter 契约）：回显草稿正文。"""
    # 三遍模板各自携带明确的草稿标记；优先按标记切。草稿内部的空行
    # （回合间分隔）不能当提示词边界，否则会把开局回合整段截掉。
    for marker in ("【跑团记录】\n", "【待改写章节】\n", "【待润色章节】\n"):
        if marker in prompt:
            draft = prompt.split(marker, 1)[1].strip()
            if draft:
                return draft
    for sep in ("\n\n", "\n"):
        if sep in prompt:
            draft = prompt.split(sep, 1)[1].strip()
            if draft:
                return draft
    return _NARRATIVE_TAIL


# ---------------------------------------------------------------- 路由表（顺序敏感）

ROUTES: list[tuple[str, str, object]] = [
    # 标记, 路由名, 应答函数
    # —— 全模块扩展路由（先于既有路由：reader 对话补充头与闲聊规则标记共存，
    #    必须先命中 READER_CONTEXT_V1 才能走结构化草稿分支）——
    ("READER_DISCLOSURE_VERIFY_V1", "reader_verify", _reply_reader_verify),
    ("READER_CONTEXT_V1", "reader_dialogue", _reply_reader_dialogue),
    ("你是角色蒸馏器", "designer_fusion", _reply_designer_fusion),
    ("你是金手指规格润色助手", "gf_polish", _reply_gf_polish),
    ("你是《书中行》命运引擎的规则问答助手", "ask_rules", _reply_ask),
    ("你是《书中行》命运引擎的设定执行者", "directive_register", _reply_directive_register),
    ("你是作品档案蒸馏器", "work_distill", _reply_work_distill),
    ("你是主角的行动代理", "autoplay_choice", _reply_autoplay),
    ("你是小说改写者", "export_plot", _reply_export_rewrite),
    ("你是文字风格编辑", "export_style", _reply_export_rewrite),
    ("你是终稿校对", "export_polish", _reply_export_rewrite),
    # —— 生成矩阵既有路由（顺序敏感）——
    ("Return only the declared JSON proposal.", "cluster_skill", _reply_cluster_skill),
    ("IDENTITY_PAIR_V1", "identity_pair", _reply_identity_pair),
    ("SCOPED_CHARACTER_FACTS_V1", "scoped_facts", _reply_scoped_facts),
    ("SEMANTIC_SCENE_V1", "semantic_scene", _reply_semantic_scene),
    ("FULLBOOK_RICH_CHARACTER_V1", "fullbook_rich_character", _reply_fullbook_rich),
    ("仅依据本块完整原文", "block_evidence", _reply_block_evidence),
    ("你是原著角色安排师", "traverse_map", _reply_traverse_map),
    ("你是角色语义蒸馏器", "character_semantic_dims", _reply_semantic_dims),
    ("剧情采样合并", "plot_merge", _reply_plot_merge),
    ("开局剧情采样", "plot_sample", _reply_plot_sample),
    ("开局角色抽取", "opening_characters", _reply_characters),
    ("角色卡重填", "character_refill", _reply_refill),
    ("开局作品档案", "opening_archive", _reply_archive),
    ("【任务】单章锚点蒸馏", "anchor_single", _reply_anchor_single),
    ("【任务】长章切块蒸馏", "anchor_block", _reply_block),
    ("长章锚点合并", "anchor_merge", _reply_anchor_merge),
    ("首章锚点核验", "anchor_verify", _reply_verify),
    ("option_seeds", "director", _reply_director),
    ("`options`", "options", _reply_options),
    ('"options"', "options", _reply_options),
    ("# 质量裁判卷", "quality_judge", _reply_quality_judge),
    ("# 段卷", "segment", _filler),
    ("【定向重填：第", "segment_refill", _filler),
    ("# 润色卷", "polish", _reply_polish),
    ("# 后处理整合卷", "post_polish", _reply_polish),
    ("【任务判定】", "quest_verdict", _reply_verdict),
    ("【碎锚阶段判定】", "break_anchor_verdict", _reply_verdict),
    ("【任务派发】", "quest_offer", _reply_quest_offer),
    ("【碎锚任务】", "break_anchor_offer", _reply_break_anchor_offer),
    ("质检审校员", "agent_self_check", _reply_self_check),
    ("剧情接手摘要器", "context_compressor", _reply_compressor),
    ("角色闲聊生成规则", "character_chat", _reply_chat),
    ("【系统校验】", "option_repair", _reply_option_repair),
    ("只做「开局核对」", "opening_check", _reply_opening_check),
    ("不要自己列选项", "opening_narrative", _reply_generic),
]


def route_prompt(prompt: str) -> tuple[str, str]:
    """返回 (路由名, 应答文本)。未命中走 LEGACY/通用判定。"""
    for marker, name, handler in ROUTES:
        if marker in prompt:
            return name, handler(prompt)
    # 多消息流式请求（带聊天历史）→ LEGACY 回合契约（正文 + A–F 选项）。
    if "可选行动" in prompt and re.search(r"[A-FＡ-Ｆ]", prompt):
        return "legacy_turn", _reply_legacy_turn(prompt)
    return "unmatched", _reply_generic(prompt)


# ---------------------------------------------------------------- HTTP 服务


class MockModelHandler(BaseHTTPRequestHandler):
    server_version = "MockModel/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默默认访问日志，改走 JSONL
        pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _write_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/health", "/v1/health", ""):
            self._write_json({"status": "ok", "routes": len(ROUTES)})
        elif path in ("/v1/models", "/models"):
            # OpenAI SDK models.list()（/api/models/fetch 与 /api/models/test 用）。
            now = int(time.time())
            self._write_json({"object": "list", "data": [
                {"id": "mock-model", "object": "model", "created": now, "owned_by": "mock"},
                {"id": "mock-flash", "object": "model", "created": now, "owned_by": "mock"},
            ]})
        else:
            self._write_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.path.rstrip("/").endswith("chat/completions"):
            self._write_json({"error": "not found"}, 404)
            return
        body = self._read_body()
        messages = body.get("messages") or []
        contents = [str(m.get("content") or "") for m in messages if isinstance(m, dict)]
        # 路由文本 = system + 最后一条 user（引擎把指令放在这两处；
        # 历史里的旧 JSON 不参与路由，防止误命中）。
        routing_parts = [c for m in messages if isinstance(m, dict)
                         and m.get("role") == "system" for c in [str(m.get("content") or "")]]
        user_parts = [str(m.get("content") or "") for m in messages
                      if isinstance(m, dict) and m.get("role") == "user"]
        routing_text = "\n".join(routing_parts + user_parts[-1:])
        full_text = "\n".join(contents)
        # Copilot 协议需要精确的「最后一条 user 消息」做意图判定；
        # 其系统提示词自身含关键词，不能混入展平路由文本。
        if any("COPILOT_SYSTEM_V1" in part for part in routing_parts):
            route, reply = "copilot_chat", _reply_copilot(user_parts[-1] if user_parts else "")
        else:
            route, reply = route_prompt(routing_text)
        model_name = str(body.get("model") or "mock-model")
        want_stream = bool(body.get("stream"))
        started = time.time()
        self._log_call(route, want_stream, len(full_text), len(reply), model_name,
                       round(time.time() - started, 4), routing_text[:200])
        if want_stream:
            self._send_stream(reply, model_name)
        else:
            self._send_nonstream(reply, model_name)

    # ---- 应答封装

    def _send_nonstream(self, text: str, model_name: str) -> None:
        self._write_json({
            "id": f"chatcmpl-mock-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion", "created": int(time.time()),
            "model": model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": max(1, len(text) // 2),
                      "total_tokens": 1 + max(1, len(text) // 2)},
        })

    def _send_stream(self, text: str, model_name: str) -> None:
        chunk_size = max(40, len(text) // 6 or 1)
        pieces = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or [text]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        chat_id = f"chatcmpl-mock-{uuid.uuid4().hex[:8]}"
        try:
            for piece in pieces:
                event = {"id": chat_id, "object": "chat.completion.chunk",
                         "created": int(time.time()), "model": model_name,
                         "choices": [{"index": 0, "delta": {"content": piece},
                                      "finish_reason": None}]}
                self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8")
                                 + b"\n\n")
                self.wfile.flush()
            final = {"id": chat_id, "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": model_name,
                     "choices": [{"index": 0, "delta": {},
                                  "finish_reason": "stop"}]}
            self.wfile.write(b"data: " + json.dumps(final).encode("utf-8") + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    # ---- 证据日志

    def _log_call(self, route: str, stream: bool, prompt_chars: int,
                  reply_chars: int, model_name: str, seconds: float,
                  preview: str = "") -> None:
        server = self.server.mock  # type: ignore[attr-defined]
        with server.lock:
            with server.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": round(time.time(), 3), "route": route, "stream": stream,
                    "prompt_chars": prompt_chars, "reply_chars": reply_chars,
                    "model": model_name, "seconds": seconds,
                    # 提示词预览（仅合成测试内容）：路由未命中时的诊断依据
                    "preview": re.sub(r"\s+", " ", preview)[:160],
                }, ensure_ascii=False) + "\n")


class MockModelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, log_path: Path):
        super().__init__(addr, MockModelHandler)
        self.mock = type("Ctx", (), {})()  # 简单上下文对象
        self.mock.lock = threading.Lock()
        self.mock.log_path = log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI 兼容 mock 模型服务（本机测试）")
    parser.add_argument("--port", type=int, default=21593)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--log", required=True, help="JSONL 调用日志路径（测试证据）")
    args = parser.parse_args()

    log_path = Path(args.log).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    server = MockModelServer((args.host, args.port), log_path)
    print(f"[mock-model] listening on http://{args.host}:{args.port} "
          f"log={log_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
