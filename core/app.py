# -*- coding: utf-8 -*-
"""对局编排层：on_start（开局装配+开场生成）与 on_send（回合主流程）。

不直接运行本文件：进程入口是根目录 run_app.py（FastAPI 见 core/server.py）。
历史上曾是 Gradio 单文件应用，模块内仍保留部分 gr.update 兼容构造。
"""
import os
import json
import copy
import re
import threading
from core.ui import gradio_compat as gr

from core import fate_engine as fe
from core import state_schema
from core.state_schema import start_setting
from core.services import (character_service, directives_service, game_setup,
                           opening_service, options_service, registries, turn_pipeline)
import core.engine.plot_summary
import core.engine.anchor_distiller
import core.engine.work_distiller
import core.engine.ledger as ledger_module
import core.engine.persistence
import core.engine.runtime_mechanics
import core.engine.quest
import core.engine.dynamic_convergence
import core.engine.nemesis_agent
import core.engine.skill_drift
import core.engine.break_anchor
import core.engine.context_compressor
from core.memory import blank_state, StateStore, render_panel
from core.memory import action_patch, apply_turn, extract_patch
from core.lore import LoreInjector
from core import engine
from core.ui import common as ui_common
from core.ui import golden_finger_panel, profiles_panel, status_bar
from core.ui.roster_form import MAX_ROSTER, SKILL_PRESETS, heroine_pool, normalize_count, skill_value
from core.ui import theme as ui_theme
from core.engine import opening_flow
from core.engine.roster_schema import normalize_roster

# 后台蒸馏器与会话解耦，避免线程对象进入 Gradio State 或存档。
# 注册表本体收编在 core.services.registries（中立层），server/engine 直接
# 读写该层；此处保留原名称作为兼容别名（注册表实现完整 dict 协议，
# 历史读写方式全部可用）。
_DISTILLERS = registries.distillers

# 稳定的纯助手由 ui 子模块维护；这里保留原名称作为兼容导出。
_new_session_log = ui_common._new_session_log
_append_log = ui_common._append_log
_accum_tokens = ui_common._accum_tokens
_mechanical_progress = ui_common._mechanical_progress
_book_dir = ui_common._book_dir
_chapter_text = ui_common._chapter_text
_chapter_state = ui_common._chapter_state
_next_chapter_state = ui_common._next_chapter_state
_score_action = ui_common._score_action
_load_lore_entries = ui_common._load_lore_entries
_lore_injector = ui_common._lore_injector

POWER_CHOICES = [("按设定推断", -1), ("未评估/凡人 0", 0), ("偏弱 1", 1),
                 ("相当 2", 2), ("偏强 3", 3), ("远强 4", 4)]
STYLE_TO_CHOICE = ui_common.STYLE_TO_CHOICE
_trope_store = ui_common._trope_store


def _distill_lamp(state):
    return status_bar._distill_lamp(state, _DISTILLERS)


def _progress_html(state):
    state = state or {}
    pct = _mechanical_progress(state)
    state["progress"] = pct
    r = int(state.get("round", 0) or 0)
    mode = str(state.get("mode") or "")
    extra = ""
    if mode.startswith("强化") and int(state.get("total_chapters", 0) or 0):
        chapter = int(state.get("current_chapter", 1) or 1)
        total = int(state.get("total_chapters", 0) or 0)
        used = int(state.get("chapter_round", 0) or 0)
        budget = int(state.get("turn_budget", 0) or 0)
        extra = f" ｜ 第 {chapter}/{total} 章 ｜ 本章回合 {used}/{budget or '?'}"
    elif mode.startswith("强化"):
        extra = " ｜ 作品库档案模式"
    else:
        extra = f" ｜ 片段内 {r}/30 回合"
    if mode.startswith("强化") and not state.get("plot_ready", False):
        extra += " ｜ 剧情准备未完成"
    lamp = _distill_lamp(state)
    if lamp:
        extra += f" ｜ {lamp}"
    ripple = (state.get("last_ripple") or {})
    if ripple:
        extra += f" ｜ 涟漪 {ripple.get('level', '')} {'通过' if ripple.get('allowed') else '阻挡'}"
    return (
        f"<div style='font-size:13px;margin-bottom:4px'>📖 剧情进度 "
        f"<b>{pct}%</b> ｜ 第 {r} 回合{extra}</div>"
        f"<div style='background:rgba(128,128,128,.25);border-radius:6px;height:14px;overflow:hidden'>"
        f"<div style='width:{pct}%;height:100%;transition:width .4s;"
        f"background:#a33a2b'></div></div>")


_token_md = status_bar._token_md
_token_title_md = status_bar._token_title_md


# ---------- 统一输出构造：yield 元组形状的唯一定义点 ----------
# on_start 输出 11 元组（contracts.py 取 state@[1]、status@[2]）、on_send 输出 7 元组
# （state@[2]）。u1-u4 为 Gradio 遗留 UI 对象，FastAPI 路径经 contracts 过滤。
# 显式参数未传时按主流路径计算默认值；错误分支显式传自己的常量。
def _out_start(chat, state, status, progress=None, token=None, title=None, panel=None,
               u1=None, u2=None, u3=None, u4=None):
    st = state if state is not None else {}
    return (chat, state, status,
            progress if progress is not None else _progress_html(st),
            token if token is not None else _token_md(st),
            title if title is not None else _token_title_md(st),
            panel if panel is not None else st.get("state_panel", ""),
            u1 if u1 is not None else gr.update(),
            u2 if u2 is not None else gr.update(),
            u3 if u3 is not None else gr.update(),
            u4 if u4 is not None else gr.update())


def _out_send(chat, state, msg_update=None, progress=None, token=None, title=None, panel=None):
    st = state if state is not None else {}
    return (chat,
            msg_update if msg_update is not None else gr.update(),
            state,
            progress if progress is not None else _progress_html(st),
            token if token is not None else _token_md(st),
            title if title is not None else _token_title_md(st),
            panel if panel is not None else st.get("state_panel", ""))

# 配置档状态和兼容导出统一来自 ui.profiles_panel。
PROFILES = profiles_panel.PROFILES
ACTIVE_PROFILE = profiles_panel.ACTIVE_PROFILE
_INITIAL_PROFILE = profiles_panel._INITIAL_PROFILE
_INITIAL_PROVIDER = profiles_panel._INITIAL_PROVIDER
_profile = profiles_panel._profile
_provider_key = profiles_panel._provider_key
_load_saved_key = profiles_panel._load_saved_key
_save_profile = profiles_panel._save_profile
_provider_defaults = profiles_panel._provider_defaults
_thinking_kwargs = profiles_panel._thinking_kwargs


def _ripple_block(state, message):
    progress = _mechanical_progress(state) / 100
    difficulty = engine.runtime_mechanics.difficulty_number(state.get("start_params", {}).get("difficulty", 4))
    # 积势门槛随收束力分档（较低/一般/较高），三档在实测中形成攒势速度差异。
    convergence = (state.get("convergence_state") or {}).get("base") or start_setting(state, "convergence") or "一般"
    previous = [item for item in (state.get("ripples") or []) if isinstance(item, dict)]
    ledger = engine.runtime_mechanics.RippleLedger(difficulty, convergence)
    if previous:
        ledger.effective_total = max(
            int(item.get("effective_total", item.get("total", 0)) or 0)
            for item in previous
        )
        ledger.attempt_total = max(
            int(item.get("attempt_total", item.get("total", 0)) or 0)
            for item in previous
        )
    breadth, persist, canon, pressure = _score_action(message)
    # 剧情相关度出口三：点名强相关成员的大动作 pressure +1（相关角色推动改变更省力）。
    try:
        _rel_report = state.get("roster_relevance") or {}
        if canon >= 2:
            for _n, _info in (_rel_report.get("members") or {}).items():
                if (_n and _n in str(message or "")
                        and isinstance(_info, dict)
                        and _info.get("tier") == "强" and not _info.get("scaled")):
                    pressure = min(3, int(pressure or 0) + 1)
                    break
    except Exception:  # noqa: BLE001
        pass
    entry = ledger.add(breadth, persist, canon, progress=progress, pressure=pressure, note=message[:80])
    record = ledger.transparent()[-1]
    record["step"] = len(previous) + 1
    previous.append(record)
    # 截尾 50 条：读取侧只用最近一条（播种/碎锚）与最近 3 条（压缩摘要）、
    # 最近 12 条（ledger.ripples），长对局中更早的记录无人消费，却要参与
    # 每回合存档序列化、每个流式 chunk 的 public_state 深拷贝与前端全量替换。
    # step 字段仍按真实累计序号递增，不受截尾影响。
    state["ripples"] = previous[-50:]
    state["last_ripple"] = {
        "raw_level": entry.raw_level.name,
        "effective_level": entry.effective_level.name,
        "level": entry.level.name,
        "allowed": entry.allowed,
        "pressure": entry.pressure,
        "attempt_total": entry.attempt_total,
        "effective_total": entry.effective_total,
        "total": entry.total,
        "threshold": entry.threshold,
        "note": entry.note,
    }
    ledger_data = state.get("ledger") or ledger_module.new_ledger()
    ledger_data["ripples"] = state["ripples"][-12:]
    player = dict(ledger_data.get("player_state") or {})
    player["last_action"] = message[:80]
    player["round"] = state.get("round", 0)
    ledger_data["player_state"] = player
    state["ledger"] = ledger_data
    return entry


def _runtime_character_constraints(state, message):
    """按池大小、角色配置和行动相关性计算本回合可出场角色。"""
    members = []
    for role, key in (("伙伴", "companions"), ("女主", "heroines")):
        pool = list(state.get(key) or [])
        for index, original in enumerate(pool):
            item = dict(original) if isinstance(original, dict) else {"name": str(original)}
            name = str(item.get("name") or "未命名角色")
            relevance = 1.0 if name and name in str(message or "") else 0.0
            # 剧情相关度加成：强相关成员出场概率 +0.15、中相关 +0.05
            # （选角相关度产生实际效果的出口一：相关角色更容易自然入戏）。
            _rel_report = state.get("roster_relevance") or {}
            _rel_entry = (_rel_report.get("members") or {}).get(name) or {}
            _rel_tier = str(_rel_entry.get("tier") or "")
            relevance = min(1.0, relevance + (0.15 if _rel_tier == "强" else
                                              (0.05 if _rel_tier == "中" else 0.0)))
            # 自建阵容未传 participation 时默认 5（中等参与度）：
            # 默认 1 会导致 threshold=1.0、角色永不出场（实测踩过）。
            try:
                configured_level = max(1, min(9, int(item.get("participation") or 5)))
            except (TypeError, ValueError):
                configured_level = 5
            decision = engine.compute_participation(
                pool_size=len(pool), chapter=state.get("current_chapter", 1),
                round_no=state.get("round", 1),
                last_appeared_round=item.get("last_appeared_round"),
                action_relevance=relevance,
                relationship_state=item.get("relationship", "普通"), role=role)
            # 玩家档位直接改变出场阈值：档位越高，所需规则概率越低。
            threshold = round((10 - configured_level) / 9.0, 4)
            decision = dict(decision)
            decision["configured_level"] = configured_level
            decision["threshold"] = threshold
            decision["appear"] = bool(decision.get("cooldown_remaining", 0) == 0 and
                                        decision.get("probability", 0) >= threshold)
            row = dict(item)
            row["participation_decision"] = decision
            row["participation_level"] = configured_level
            if decision.get("appear"):
                row["last_appeared_round"] = state.get("round", 1)
                row["cooldown_remaining"] = int(decision.get("cooldown", 0) or 0)
                members.append(row)
            # 回写原始名册，确保下一回合冷却和参与度决定可恢复、可存档。
            if isinstance(original, dict):
                original["last_participation"] = decision
                original["participation_decision"] = decision
                if decision.get("appear"):
                    original["last_appeared_round"] = state.get("round", 1)
                    original["cooldown_remaining"] = int(decision.get("cooldown", 0) or 0)
    return members


def _trope_hint(message):
    style = engine.runtime_mechanics.classify_style(message)
    choice = STYLE_TO_CHOICE.get(style, "试探")
    store = _trope_store()
    hits = engine.runtime_mechanics.search_tropes(store, style=choice, triggers=message, limit=1)
    if not hits:
        hits = engine.runtime_mechanics.search_tropes(store, triggers=message, limit=1)
    if not hits:
        return style, choice, ""
    trope = hits[0]
    reaction = engine.runtime_mechanics.render_reaction(trope.reaction, {"主角": "你", "交互角色": "对方"})
    return style, choice, f"{trope.id} · {reaction}"


def _stop_distillers(except_key=None):
    registries.distillers.stop_all(except_key=except_key)


def stop_all_distillers():
    """停止全部后台锚点蒸馏（碎锚 / RELAY 碎锚联动等特权路径用）。"""
    _stop_distillers()


def _distill_model(client, model, prompt, extra_kwargs=None, provider="deepseek"):
    """内部子调用统一通道（蒸馏/自检/托管/任务判定）。

    兼容性阶梯：别家服务的 OpenAI 兼容层参数支持差异很大，按
    完整参数 → 剥思考参数 → 剥采样参数 → 流式累积 四级降级重试，
    直到某一级拿到非空正文；全部失败才向上抛错（调用方自行降级处理）。
    实现已提炼至 engine.distill.distill_model，此处仅转发，避免双副本漂移。
    """
    return engine.distill.distill_model(client, model, prompt, extra_kwargs, provider)


def _anchor_text(state, chapter_numbers):
    key = state.get("distill_key") or _book_dir(state.get("chapter_index"))
    if not key:
        return ""
    anchor_dir = os.path.join(key, "anchors")
    values = []
    for number in chapter_numbers:
        path = os.path.join(anchor_dir, f"{int(number):04d}.json")
        try:
            with open(path, encoding="utf-8") as handle:
                values.append(json.dumps(json.load(handle), ensure_ascii=False))
        except (OSError, ValueError):
            continue
    return "\n".join(values)


def _merge_distill_status(state, status):
    """保留式更新蒸馏状态：不得覆盖 plot_summary 等既有蒸馏字段。"""
    distill_info = dict(state.get("distill") or {})
    distill_info["status"] = status
    state["distill"] = distill_info
    return distill_info


_DISTILL_ERROR_ZH = {
    "JSONDecodeError": "模型返回的内容无法解析为锚点数据",
    "ValueError": "锚点数据校验未通过",
    "TypeError": "模型服务返回了意外的数据格式",
    "TimeoutError": "模型响应超时",
    "ConnectionError": "无法连接模型服务",
}


def _safe_distill_error(exc):
    """返回可展示的蒸馏错误（纯中文），不把凭据、英文异常名或原始 JSON 写入状态/日志。"""
    message = str(exc or "").strip()
    message = re.sub(r"(?i)(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{32,})", "已脱敏", message)
    # 剥掉英文异常前缀（如 "JSONDecodeError: Expecting value: line 1 column 1 (char 0)"）
    # 只保留中文可读部分；整句无中文时替换为统一的中文说明。
    zh_hint = _DISTILL_ERROR_ZH.get(type(exc).__name__, "锚点蒸馏遇到问题")
    message = re.sub(r"^[A-Za-z]+Error\s*[:：]\s*", "", message).strip()
    if not message or not re.search(r"[\u4e00-\u9fff]", message):
        return zh_hint
    return f"{zh_hint}：{message}"


def _gameplay_briefing(mode: str, nemesis_on: bool = False) -> str:
    """开局前的玩法速览：在聊天界面简明说明各机制如何运作。

    面向玩家的第一课：只讲"怎么玩、机制怎么运作"，不暴露内部字段与术语表。
    强化模式与基础模式各有一版；未启用宿敌等机制时相应条目自动省略。
    """
    enhanced = str(mode or "").startswith("强化")
    lines = ["【玩法速览 · 开局前请读】"]
    lines.append("· 推进方式：在聊天框自由描述你的行动，或点击下方的字母选项快速选择；"
                 "重要节点（确认金手指、确认开局）按聊天框提示输入即可。")
    if enhanced:
        lines.append("· 原著主线：书中的大事会「尽力发生」——你能改变的是路径、代价与见证者，"
                     "而不是凭空删掉剧情；每章有回合预算，走完自然进入下一章。")
        lines.append("· 涟漪与积势：你的每个行动都会留下涟漪（影响分等级）；"
                     "朝同一个目标持续行动会积累「积势」，想推动大的改变（比如改写主线）必须积势先行。")
        lines.append("· 收束力：开局选定的档位——越高，剧情越贴近原著、你的偏离越容易被世界拉回；"
                     "越低，世界越容易接受你的改变，但代价与风险也随之增大。")
        lines.append("· 金手指：有作用范围、代价与冷却，不是无限外挂；开局确认后随设定锁定。")
        lines.append("· 伙伴与伴侣：各自独立行动、有独立记忆与关系账目，会主动做事，不只是挂件。")
        if nemesis_on:
            lines.append("· 宿敌：在你的世界之外同步推进自己的目标，多数时候你只见到传闻与异动；"
                         "其强度由双方阵营的势力差距决定。")
        lines.append("· 碎锚：积势攒满后可尝试「打碎」当前章的主线锚点、让剧情改道——有失败风险与代价。")
        lines.append("· 穿越对照：开局即为每位穿越者锁定附身角色——实际名字与具体身份以「穿越对照表」反馈；"
                     "附身不受性别限制，叙事以附身角色的生理性别为准。")
        lines.append("· 右侧面板：涟漪、积势、宿敌动向、任务与锚点蒸馏进度实时可见；随时可存档。")
    else:
        lines.append("· 片段体验：本局围绕你选定的片段展开（约十到三十回合），"
                     "收束于该片段的冲突或变化，不推进全书。")
        lines.append("· 涟漪与收束：行动会留下涟漪；偏离片段主线太远时会被世界以合理方式拉回，"
                     "收束力档位决定拉回的力度。")
        lines.append("· 金手指：有代价与冷却，不是无限外挂；开局确认后随设定锁定。")
        lines.append("· 右侧面板实时显示状态；本模式不启用宿敌与章节锚点机制。")
    return "\n".join(lines)


def _record_distill_failure(state, exc, chapter):
    """记录同步首章蒸馏失败；不改变剧情门禁，便于 UI 和存档诊断。"""
    error = _safe_distill_error(exc)
    info = dict(state.get("distill") or {})
    info.update({"status": "failed", "error": error, "failed_chapter": int(chapter)})
    state["distill"] = info
    state["distill_status"] = f"锚点蒸馏失败（第 {int(chapter)} 章）：{error}"
    return error


def _humanize_plot_summary(summary):
    """把剧情大概的原始返回值加工成玩家可读的通顺文本。

    后台结构化的 JSON/字典在展示给玩家之前必须经过本函数加工，转成中文句子；
    纯文本摘要则原样返回。绝不让玩家看到原始 JSON 符号或内部字段名。
    """
    def _clean(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    if summary is None:
        return "（剧情摘要暂缺）"

    data = summary
    if isinstance(summary, str):
        text = summary.strip()
        stripped = re.sub(r"^```(?:json|JSON)?\s*|\s*```$", "", text, flags=re.S).strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data = json.loads(stripped)
            except ValueError:
                return text
        else:
            return text

    if isinstance(data, dict):
        genre = _clean(data.get("genre") or data.get("类型"))
        premise = _clean(data.get("premise") or data.get("梗概") or data.get("简介"))
        threads = data.get("major_threads") or data.get("主线") or data.get("threads") or data.get("主要线索")
        tone = _clean(data.get("tone") or data.get("基调") or data.get("风格"))
        lines = []
        if genre:
            lines.append("题材：" + genre)
        if premise:
            lines.append("故事梗概：" + premise)
        if isinstance(threads, list):
            items = [_clean(item) for item in threads if _clean(item)]
            if items:
                lines.append("主要线索：" + "；".join(items))
        elif _clean(threads):
            lines.append("主要线索：" + _clean(threads))
        if tone:
            lines.append("整体基调：" + tone)
        if lines:
            return "\n".join(lines)
        parts = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                continue
            cleaned = _clean(value)
            if cleaned:
                parts.append(str(key) + "：" + cleaned)
        return "\n".join(parts) if parts else "（剧情摘要暂缺）"

    if isinstance(data, list):
        items = [_clean(item) for item in data if _clean(item)]
        return "；".join(items) if items else "（剧情摘要暂缺）"

    return _clean(data) or "（剧情摘要暂缺）"


def _finalize_options(state, acc, structured_options=None):
    """把选项结构化写入 state["options"]，返回 (正文, 文本选项块)。

    M2 起``structured_options``（options_service 门面产物，[{key,text,preview,
    factors}]）优先注入：正文里的残存选项块被剥离，文本块由代码渲染
    （含后果预告）。缺省走旧路径：从正文解析 A–F。纯函数式处理，不调用模型。
    """
    display = fe.strip_hidden(acc)
    factors = engine.collect_option_factors(state)
    if structured_options:
        items = [item for item in structured_options
                 if isinstance(item, dict) and str(item.get("text") or "").strip()]
        if items:
            state["options"] = [
                {"key": str(item.get("key") or "").strip().upper(),
                 "text": str(item.get("text") or "").strip(),
                 "preview": str(item.get("preview") or "").strip(),
                 "factor": str(item.get("factor") or "").strip(),
                 "factors": item.get("factors")
                 or engine.match_option_factors(str(item.get("text") or ""), factors)}
                for item in items]
            narrative = engine.strip_options_block(display) or display
            options_block = options_service.render_display_block(state["options"])
            return narrative, options_block
    parsed = engine.parse_options(display)
    state["options"] = [
        {"key": item["key"], "text": item["text"],
         "factors": engine.match_option_factors(item["text"], factors)}
        for item in parsed
    ]
    if not parsed:
        return display, ""
    return engine.strip_options_block(display), engine.render_options_block(parsed)


def _render_engine_log(state, message, narrative, round_no, rumor=None):
    """M2：引擎日志由代码渲染（模型不再书写 LOG 段）。

    字段全部取自状态与正则提取器（可证据化），格式与历史 <<<LOG>>> 契约
    逐字一致（turn_composer.render_log_line）；只写入日志文件，不进展示、
    不进模型输出。M3 起由蓝图 log_draft 提供更高质量的字段素材。
    """
    def _clip(text, limit=80):
        text = str(text or "").strip().replace("\n", " ")
        return text[:limit] + ("…" if len(text) > limit else "")

    player_bits = [_clip(message, 60) or "（无行动）"]
    try:
        patch = extract_patch(fe.strip_hidden(narrative), action=message,
                              current=state.get("state_memory") or {}, round_no=round_no) or {}
    except Exception:  # noqa: BLE001 提取失败不阻断日志
        patch = {}
    for category in ("body", "assets", "abilities"):
        box = patch.get(category) if isinstance(patch.get(category), dict) else {}
        for key, value in box.items():
            if isinstance(value, list) and value:
                player_bits.append(f"{key}×{len(value)}")
    decision = state.get("gf_decision") if isinstance(state.get("gf_decision"), dict) else {}
    gf_label = str(decision.get("label")
                   or (state.get("start_params") or {}).get("golden_finger") or "无")
    if rumor:
        nemesis_text = _clip(rumor, 60)
    elif state.get("nemesis"):
        nemesis_text = "已开启；本回合无公开动向"
    else:
        nemesis_text = "无"
    world_parts = [f"第{state.get('current_chapter', 1)}章 "
                   f"{state.get('chapter_round', 1)}/{state.get('turn_budget', 1)}"]
    lore_hits = state.get("lore_hits") or []
    if lore_hits:
        world_parts.append(f"世界书命中{len(lore_hits)}条")
    anchor_status = ((state.get("scene_validation") or {}).get("anchor") or {}).get("status")
    beat = f"锚点{anchor_status or 'pending'}"
    try:
        progress = int(state.get("progress") or 0)
    except (TypeError, ValueError):
        progress = 0
    return engine.turn_composer.render_log_line(
        round_no, "；".join(player_bits), _clip(gf_label, 40), nemesis_text,
        "；".join(world_parts), beat, progress)


def _known_anchor_text(state, chapter_number):
    """为模型提供当前章相邻锚点，支持局部因果衔接。"""
    chapter = max(1, int(chapter_number or 1))
    return _anchor_text(state, range(max(1, chapter - 1), chapter + 2))


def _current_anchor_text(state, chapter_number):
    """用于提交硬门禁；仅接受当前章节的已验证锚点。"""
    return _anchor_text(state, [max(1, int(chapter_number or 1))])


def _anchor_timeline(state, current_status="pending"):
    """组装锚点时间线：最近 1 个已发生锚点、当前锚点、最多 6 个后续锚点。

    后续最多 6 章对齐蒸馏窗口（当前章及后六章）；任务生成按档位从中取
    不同长度窗口。标题/摘要优先读 anchors/NNNN.json，未蒸馏时回退
    chapter_index 的章节标题。纯函数：同样输入必然得到同样时间线。
    """
    chapter_index = state.get("chapter_index") if isinstance(state.get("chapter_index"), dict) else {}
    chapters = chapter_index.get("chapters") or []
    current = max(1, int(state.get("current_chapter", 1) or 1))
    key = state.get("distill_key") or _book_dir(chapter_index)
    anchor_dir = os.path.join(key, "anchors") if key else ""

    def _entry(number, with_summary=False):
        title, summary = "", ""
        if anchor_dir:
            path = os.path.join(anchor_dir, f"{int(number):04d}.json")
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
                title = str(data.get("title") or "").strip()
                summary = str(data.get("summary") or "").strip()
            except (OSError, ValueError):
                pass
        if not title:
            row = next((r for r in chapters if int(r.get("idx", 0) or 0) == int(number)), None)
            if row:
                title = str(row.get("title") or "").strip()
        if not title:
            return None
        info = {"chapter": int(number), "title": title}
        if with_summary and summary:
            info["summary"] = summary
        return info

    past = []
    for number in range(current - 1, 0, -1):
        entry = _entry(number)
        if entry:
            entry["status"] = "fulfilled"
            past.append(entry)
            break
    current_entry = _entry(current)
    if current_entry:
        current_entry["status"] = str(current_status or "pending")
    upcoming = []
    total = len(chapters)
    for number in range(current + 1, total + 1):
        entry = _entry(number, with_summary=True)
        if entry:
            upcoming.append(entry)
        if len(upcoming) >= 6:
            break
    return {"past": past, "current": current_entry, "upcoming": upcoming}


def _start_background_distillation(state, client, model, sync_first=False):
    if state.get("distill_enabled") is False:
        state["distill_status"] = "锚点蒸馏已关闭"
        return
    chapter_index = state.get("chapter_index") or {}
    # 读档/重启恢复：state.distill_key 优先（存档带回的产物目录），
    # 缺失时才按 chapter_index 推导——两条路径必须命中同一 anchors/ 目录，
    # 否则读档后会误判"锚点全缺"而重新蒸馏已完成章节。
    book_dir = str(state.get("distill_key") or "").strip() or _book_dir(chapter_index)
    if not book_dir or not os.path.isdir(book_dir):
        return
    if engine.break_anchor.shattered_from(state):
        # 全局碎锚后锚点不再收束，后续锚点蒸馏无意义，直接停止/不再启动。
        _stop_distillers()
        state["distill_status"] = "锚点已全部失效，后续蒸馏停止"
        return
    key = os.path.abspath(book_dir)
    _stop_distillers(except_key=key)
    old = _DISTILLERS.get(key)
    if old:
        sync_error = None
        current_chapter = state.get("current_chapter", 1)
        # M0：蒸馏模型调用纳入全局并发预算（后台低优先级；同步补首章
        # 时在线程内临时提到开局优先级）。重复包装会被 budget_model 原样返回。
        old.model = engine.parallel.budget_model(old.model, engine.parallel.PRIORITY_BACKGROUND)
        if sync_first:
            try:
                with engine.parallel.priority_scope(engine.parallel.PRIORITY_OPENING):
                    old.distill_now(current_chapter)
            except Exception as exc:  # noqa: BLE001
                sync_error = exc
        old.enqueue(current_chapter, lookahead=6, lookback=-1, total=state.get("total_chapters"))
        old.start()
        state["distill_key"] = key
        state["distill_status"] = _distill_lamp(state) or "后台锚点蒸馏运行中"
        if sync_error is not None:
            _record_distill_failure(state, sync_error, current_chapter)
        return
    def model_fn(prompt):
        # 后台蒸馏不持会话锁，用 300s 超时：主流式生成与蒸馏共用同一 Key 时
        # 上游排队是常态，120s 会把排队中的请求掐死导致蒸馏停滞。
        # M0：经全局动态并发控制器（后台低优先级；同步救援线程可提级）。
        return engine.parallel.budget_model(
            lambda p: engine.distill.distill_model(
                client, model, p, state.get("request_kwargs"),
                state.get("provider", "deepseek"),
                timeout=engine.distill.BACKGROUND_SUBCALL_TIMEOUT),
            engine.parallel.PRIORITY_BACKGROUND)(prompt)
    distiller = engine.anchor_distiller.AnchorDistiller(book_dir, model_fn)
    sync_error = None
    current_chapter = state.get("current_chapter", 1)
    if sync_first:
        # 同步补齐首章锚点；失败回退到后台异步，但必须保留可诊断状态。
        try:
            with engine.parallel.priority_scope(engine.parallel.PRIORITY_OPENING):
                distiller.distill_now(current_chapter)
        except Exception as exc:  # noqa: BLE001
            sync_error = exc
    distiller.enqueue(current_chapter, lookahead=6, lookback=-1, total=state.get("total_chapters"))
    distiller.start()
    _DISTILLERS[key] = distiller
    state["distill_key"] = key
    state["distill_status"] = _distill_lamp(state) or "后台锚点蒸馏运行中"
    if sync_error is not None:
        _record_distill_failure(state, sync_error, current_chapter)


def _rescue_anchor(state, target_chapter, api_key):
    """锚点缺失救援（重构 M0）：模型一卷 → 失败用原文摘录锚点兜底放行。

    返回救援后的锚点文本（失败返回空串，调用方保持原阻断提示——只剩
    无书目录/无凭据/无章节文件等极端情形）。兜底版标记 origin=fallback，
    后台模型版以 force 精化覆盖（见 AnchorDistiller.rescue_now）。
    """
    if engine.break_anchor.shattered_from(state):
        return ""
    chapter_index = state.get("chapter_index") or {}
    book_dir = _book_dir(chapter_index)
    if not book_dir or not os.path.isdir(book_dir):
        return ""
    provider = state.get("provider") or "deepseek"
    if not (api_key or "").strip():
        return ""
    try:
        client = fe.make_client(api_key, provider, state.get("base_url"))
    except Exception:  # noqa: BLE001 无有效凭据时保持旧行为
        return ""
    key = os.path.abspath(book_dir)
    distiller = _DISTILLERS.get(key)
    if distiller is None:
        def model_fn(prompt):
            return engine.distill.distill_model(
                client, state.get("model") or "", prompt,
                state.get("request_kwargs"), provider,
                timeout=engine.distill.BACKGROUND_SUBCALL_TIMEOUT)
        distiller = engine.anchor_distiller.AnchorDistiller(
            book_dir, engine.parallel.budget_model(model_fn, engine.parallel.PRIORITY_BACKGROUND))
        _DISTILLERS[key] = distiller
        distiller.enqueue(target_chapter, lookahead=6, lookback=-1,
                          total=state.get("total_chapters"))
        distiller.start()
    try:
        with engine.parallel.priority_scope(engine.parallel.PRIORITY_OPENING):
            distiller.rescue_now(int(target_chapter))
    except Exception as exc:  # noqa: BLE001 救援失败：回退旧阻断提示
        _wiring_log(state, f"第 {target_chapter} 章锚点救援失败：{exc}")
        return ""
    state["distill_key"] = key
    _merge_distill_status(state, distiller.status())
    state["distill_status"] = _distill_lamp(state) or "后台锚点蒸馏运行中"
    _wiring_log(state, f"第 {target_chapter} 章锚点经救援补齐（模型一卷或摘录兜底），回合放行")
    return _current_anchor_text(state, int(target_chapter))


# ---------------------------------------------------------------------------
# 回合管线接线：动态收束力 / 任务结算 / 宿敌自主回合 / 上下文压缩。
# 纯模块接口由 engine.quest / engine.dynamic_convergence / engine.nemesis_agent /
# engine.context_compressor 提供；这里只做状态接线与模型调用，任一子环节失败都
# 只记日志、静默降级，绝不阻断主回合提交。
# ---------------------------------------------------------------------------


def _wiring_log(state, message):
    """子系统接线日志：追加到会话日志文件；日志本身失败也静默。"""
    try:
        _append_log(state.get("log", ""), f"\n- 接线: {message}\n")
    except Exception:  # noqa: BLE001
        pass


def _convergence_outcome(state):
    """从本回合锚点结算状态与 ripple/K 推导动态收束力 outcome，返回 (outcome, k)。

    - fulfilled 且 K<60（行动未参与，锚点按原因果履约）-> faithful
    - fulfilled 且 K>=60（行动以兼容路径偏移履约）      -> offset
    - partial（一般档放行）且有效积势达标                -> reversed（完全扭转锚点）
    - partial 但积势未达标                               -> offset
    - 本回合无锚点结算（基础模式/门禁未运行）            -> none
    """
    anchor = (state.get("scene_validation") or {}).get("anchor") or {}
    status = str(anchor.get("status") or "")
    try:
        k = int(state.get("last_compatibility_k") or 0)
    except (TypeError, ValueError):
        k = 0
    if status == "fulfilled":
        return ("offset" if k >= 60 else "faithful"), k
    if status == "partial":
        ripple = state.get("last_ripple") or {}
        try:
            effective = int(ripple.get("effective_total") or 0)
            threshold = int(ripple.get("threshold") or 0)
        except (TypeError, ValueError):
            effective, threshold = 0, 0
        if threshold > 0 and effective >= threshold:
            return "reversed", k
        return "offset", k
    return "none", k


def _settle_convergence(state, round_no):
    """动态收束力回合结算；旧存档缺 convergence_state 时按当前档位惰性初始化。"""
    try:
        conv = state.get("convergence_state")
        if not isinstance(conv, dict) or conv.get("base") not in engine.dynamic_convergence.TIERS:
            conv = engine.dynamic_convergence.init_state(
                engine.normalize_convergence(
                    start_setting(state, "convergence")))
        outcome, k = _convergence_outcome(state)
        # 权重取 K 与共存阈值 60 的距离：离阈值越远，本回合结算证据越强。
        weight = max(0.5, min(2.0, 1.0 + abs(k - 60) / 100.0))
        state["convergence_state"] = engine.dynamic_convergence.settle(
            conv, outcome, weight=weight, round=round_no)
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"收束力结算失败已跳过：{exc}")


def _quest_verdict_prompt(quest_box, message, reply_text):
    return (
        "【任务判定】你是任务结算子智能体。根据本回合玩家行动与叙事结果，判断任务"
        "是否已完成，只输出严格 JSON，不要输出任何其他文字。\n"
        f"任务目标：{quest_box.get('goal') or ''}\n"
        f"完成条件：{json.dumps(quest_box.get('requirements') or [], ensure_ascii=False)}\n"
        f"本回合玩家行动：{message}\n"
        f"本回合叙事摘要：{str(reply_text or '')[:1200]}\n"
        '输出 JSON 形状：{"completed": true 或 false, "evidence": "判定依据（不超过80字）"}'
    )


def _break_anchor_current_stage(state):
    """当前 pending 碎锚阶段；缺省返回空 dict。"""
    box = state.get("break_anchor") if isinstance(state.get("break_anchor"), dict) else {}
    stages = box.get("stages") if isinstance(box.get("stages"), list) else []
    try:
        index = int(box.get("current_stage") or 0)
    except (TypeError, ValueError):
        index = 0
    if 0 <= index < len(stages) and isinstance(stages[index], dict):
        return stages[index]
    return {}


def _break_anchor_verdict_prompt(stage, message, reply_text):
    """复用任务判定 JSON 形状，仅改提示标签，不新增模型调用类型。"""
    return (
        "【碎锚阶段判定】你是碎锚阶段结算子智能体。根据本回合玩家行动与叙事结果，判断当前阶段"
        "是否已完成，只输出严格 JSON，不要输出任何其他文字。\n"
        f"当前阶段：{(stage or {}).get('title') or ''}\n"
        f"完成条件：{(stage or {}).get('requirement') or ''}\n"
        f"本回合玩家行动：{message}\n"
        f"本回合叙事摘要：{str(reply_text or '')[:1200]}\n"
        '输出 JSON 形状：{"completed": true 或 false, "evidence": "判定依据（不超过80字）"}'
    )


def _local_break_anchor_verdict(stage, message, reply_text):
    """本地规则：正文已覆盖当前阶段 requirement 则完成，否则交给既有判定入口。"""
    req = str((stage or {}).get("requirement") or "").strip()
    if not req:
        return None
    blob = f"{message or ''}\n{reply_text or ''}"
    if req in blob:
        return {"completed": True, "evidence": "本回合正文已覆盖当前阶段要求"}
    snippet = req[:12]
    if len(snippet) >= 6 and snippet in blob:
        return {"completed": True, "evidence": "本回合正文命中当前阶段要求"}
    return None


def _inject_skill_and_break_prompts(state, llm_msg):
    """拼 prompt：碎锚 hint + 性格倾向 + 进行中碎锚阶段。不改人格底稿。"""
    try:
        from_chapter = engine.break_anchor.shattered_from(state)
        current_chapter = state.get("current_chapter", 1)
        if from_chapter and current_chapter >= from_chapter:
            llm_msg += ("\n\n【碎锚·自由航线】主线锚点已全部失效：本局注入的锚点、档案与世界观信息"
                        "仅供参考，剧情不再收束——由过往回合、角色性格与全局世界观自主生成推进，"
                        "不得把任何原著大事件当作必然发生；游戏机制（回合预算/选项/任务/积势账目）照常。")
        elif engine.break_anchor.is_anchor_broken(state, current_chapter):
            llm_msg += "\n\n【碎锚】本锚点已碎，仅作叙事提示，不再强制履约。"
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"碎锚提示注入失败已跳过：{exc}")
    try:
        hint = engine.skill_drift.prompt_block(state)
        if hint:
            llm_msg += "\n\n【性格倾向】" + hint
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"性格倾向注入失败已跳过：{exc}")
    try:
        box = state.get("break_anchor") if isinstance(state.get("break_anchor"), dict) else {}
        if box.get("status") == "active":
            stage = _break_anchor_current_stage(state)
            req = str(stage.get("requirement") or "").strip()
            if req:
                llm_msg += f"\n\n【碎锚阶段】{req}"
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"碎锚阶段提示注入失败已跳过：{exc}")
    return llm_msg


def _overlay_anchor_gate(state, chapter, check):
    """门禁 overlay：已碎锚点 valid 强制 True，并标记 hint_only。"""
    try:
        return engine.break_anchor.overlay_anchor_check(state, chapter, check)
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"碎锚门禁 overlay 失败已跳过：{exc}")
        return dict(check) if isinstance(check, dict) else {}


def _settle_break_anchor(state, client, model, request_kwargs, provider, message, reply_text, round_no):
    """碎锚阶段结算：仅 active 时触发；优先本地规则，否则复用任务判定入口。"""
    box = state.get("break_anchor")
    if not isinstance(box, dict) or box.get("status") != "active":
        return
    stage = _break_anchor_current_stage(state)
    verdict = _local_break_anchor_verdict(stage, message, reply_text)
    if verdict is None:
        try:
            prompt = _break_anchor_verdict_prompt(stage, message, reply_text)
            raw = _distill_model(client, model, prompt, request_kwargs, provider)
            verdict = _parse_quest_verdict(raw)
        except Exception as exc:  # noqa: BLE001
            _wiring_log(state, f"碎锚判定模型调用失败，按未完成处理：{exc}")
            verdict = {"completed": False, "evidence": "判定模型调用失败，按未完成处理"}
    try:
        result = engine.break_anchor.settle_stage(state, round_no, verdict)
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"碎锚阶段结算失败已跳过：{exc}")
        return
    box = state.get("break_anchor")
    if isinstance(box, dict):
        box["last_settlement"] = {
            "round": round_no, "verdict": verdict,
            "result": result.get("status"), "changed": bool(result.get("changed")),
        }
    if result.get("status") == "completed":
        # 全局碎锚生效：停止本书全部后续锚点蒸馏（不再收束，蒸馏无意义）。
        try:
            _stop_distillers()
            state["distill_status"] = "锚点已全部失效，后续蒸馏停止"
        except Exception as exc:  # noqa: BLE001
            _wiring_log(state, f"碎锚后停止蒸馏失败已跳过：{exc}")


def _parse_quest_verdict(text):
    """解析任务判定 JSON；任何失败都按未完成处理，绝不阻断主回合。"""
    try:
        content = str(text or "").strip()
        fenced = re.search(r"```(?:json|JSON)?\s*(.*?)```", content, re.DOTALL)
        if fenced:
            content = fenced.group(1).strip()
        else:
            start, end = content.find("{"), content.rfind("}")
            if start == -1 or end <= start:
                raise ValueError("判定结果中找不到 JSON 对象")
            content = content[start:end + 1]
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("判定结果必须是 JSON 对象")
        return {"completed": bool(data.get("completed")),
                "evidence": str(data.get("evidence") or "")[:200]}
    except Exception:  # noqa: BLE001
        return {"completed": False, "evidence": "判定结果无法解析，按未完成处理"}


def _relax_convergence(state, relief, round_no):
    """任务奖励：轻微减弱动态收束系数，让主角更自由（position 向「一般」漂移）。"""
    try:
        conv = state.get("convergence_state")
        if not isinstance(conv, dict) or conv.get("base") not in engine.dynamic_convergence.TIERS:
            conv = engine.dynamic_convergence.init_state(
                engine.normalize_convergence(
                    start_setting(state, "convergence")))
        engine.dynamic_convergence.settle(
            conv, "faithful", weight=float(relief) / 0.02, round=round_no)
        state["convergence_state"] = conv
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"收束松弛失败已跳过：{exc}")


def _grant_quest_reward(state, reward):
    """把任务奖励确定性写入账本/状态记忆，返回发放明细字符串列表。

    类型映射（代码固定，不让模型自由裁量）：
    - 积势     -> 抬高最近一条涟漪的有效积势（state.ripples / ledger.ripples）
    - 物资     -> state_memory.assets.items
    - 技能碎片 -> state_memory.abilities.skills
    - 关系进展 -> state_memory.relationships.characters
    - 关键情报 -> state_memory.knowledge.known
    全部奖励同时记入 ledger.cheat.quest_rewards 作为审计轨迹。
    """
    granted = []
    items = (reward or {}).get("items") or []
    ledger_data = state.get("ledger") or ledger_module.new_ledger()
    # new_ledger 是浅拷贝（cheat 默认字典跨实例共享）；写入前先换一份副本，
    # 避免奖励审计轨迹污染同进程其他对局的账本。
    cheat = dict(ledger_data.get("cheat") or {})
    ledger_data["cheat"] = cheat
    audit = cheat.setdefault("quest_rewards", [])
    memory = state.get("state_memory") or blank_state(state.get("mode", ""), "")
    round_no = int(state.get("round", 0) or 0)
    for item in items:
        if not isinstance(item, dict):
            continue
        rtype = str(item.get("type") or "")
        try:
            amount = int(item.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        unit = str(item.get("unit") or "")
        if amount <= 0:
            continue
        audit.append({"type": rtype, "amount": amount, "unit": unit, "round": round_no})
        granted.append(f"{rtype}×{amount}{unit}")
        if rtype == "积势":
            # 抬高最近涟漪的有效积势；_ripple_block 以 max 聚合历史，奖励自然继承。
            for pool in (state.get("ripples"), ledger_data.get("ripples")):
                if isinstance(pool, list) and pool and isinstance(pool[-1], dict):
                    pool[-1]["effective_total"] = int(pool[-1].get("effective_total") or 0) + amount
            last = state.get("last_ripple")
            if isinstance(last, dict) and last:
                last["effective_total"] = int(last.get("effective_total") or 0) + amount
        elif rtype == "物资":
            memory.setdefault("assets", {}).setdefault("items", []).append(
                {"name": f"任务奖励物资×{amount}", "source": "quest", "round": round_no})
        elif rtype == "技能碎片":
            memory.setdefault("abilities", {}).setdefault("skills", []).append(
                {"name": f"技能碎片×{amount}", "status": "fragment", "source": "quest", "round": round_no})
        elif rtype == "关系进展":
            memory.setdefault("relationships", {}).setdefault("characters", []).append(
                {"name": "任务关系进展", "delta": amount, "source": "quest", "round": round_no})
        elif rtype == "关键情报":
            memory.setdefault("knowledge", {}).setdefault("known", []).append(
                {"content": f"任务关键情报×{amount}", "source": "quest", "round": round_no})
    state["ledger"] = ledger_data
    state["state_memory"] = memory
    state["state_panel"] = render_panel(memory)
    # 收束松弛：任务完成轻微减弱动态收束系数，让主角更自由（偏离原著）。
    relief = (reward or {}).get("convergence_relief")
    if relief:
        try:
            amount = float(relief)
            _relax_convergence(state, amount, round_no)
            granted.append(f"收束松弛×{amount:.2f}")
        except (TypeError, ValueError):
            pass
    return granted


def _settle_quest(state, client, model, request_kwargs, provider, message, reply_text, round_no):
    """任务回合结算：仅 active 任务触发判定模型；模型/解析失败按未完成处理。"""
    box = state.get("quest")
    if not isinstance(box, dict) or box.get("status") != "active":
        return
    try:
        prompt = _quest_verdict_prompt(box, message, reply_text)
        raw = _distill_model(client, model, prompt, request_kwargs, provider)
        verdict = _parse_quest_verdict(raw)
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"任务判定模型调用失败，按未完成处理：{exc}")
        verdict = {"completed": False, "evidence": "判定模型调用失败，按未完成处理"}
    try:
        result = engine.quest.settle_round(state, round_no, verdict)
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"任务结算失败已跳过：{exc}")
        return
    settlement = {"round": round_no, "verdict": verdict, "result": result.get("status"),
                  "changed": bool(result.get("changed")), "granted": []}
    if result.get("status") == "completed" and result.get("reward"):
        try:
            settlement["granted"] = _grant_quest_reward(state, result["reward"])
        except Exception as exc:  # noqa: BLE001
            _wiring_log(state, f"任务奖励发放失败：{exc}")
    quest_box = state.get("quest")
    if isinstance(quest_box, dict):
        quest_box["last_settlement"] = settlement


def _run_nemesis_turn(state, client, model, request_kwargs, provider, round_no):
    """宿敌自主回合：仅强化模式且宿敌启用时执行；解析失败跳过本回合并记日志。

    私密信息只写入 state["nemesis_private"]，绝不进入任何玩家可见文本；对玩家
    世界的渗漏只以「传闻」形式存入 state["nemesis_rumor"]，由下一回合 llm_msg
    的动态世界书段注入（选此方案而非写 lore_hits：lore_hits 是世界书条目 ID
    列表，混入自由文本会污染注入器统计）。
    """
    if not str(state.get("mode") or "").startswith("强化") or not state.get("nemesis"):
        return
    params = state.get("start_params") or {}
    difficulty = params.get("difficulty", 4)
    try:
        private = state.get("nemesis_private")
        if not isinstance(private, dict):
            level = engine.runtime_mechanics.difficulty_number(difficulty)
            private = engine.nemesis_agent.init_private(
                state, {"name": "宿敌", "resources": {"资源点": 10 + 2 * level}})
        context = engine.nemesis_agent.build_nemesis_context(
            state, engine.nemesis_agent.player_info_level(difficulty))
        prompt = engine.nemesis_agent.build_nemesis_prompt(context, private)
        raw = _distill_model(client, model, prompt, request_kwargs, provider)
        turn = engine.nemesis_agent.parse_nemesis_turn(raw)
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"宿敌回合解析失败已跳过：{exc}")
        return
    try:
        outcome = engine.nemesis_agent.apply_nemesis_choice(state, turn)
        leak = (outcome or {}).get("leak")
        if leak:
            state["nemesis_rumor"] = str(leak)
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"宿敌选择落地失败已跳过：{exc}")
        return
    if round_no % 5 == 0:
        try:
            engine.nemesis_agent.summarize_nemesis(state, difficulty)
        except Exception as exc:  # noqa: BLE001
            _wiring_log(state, f"宿敌摘要失败已跳过：{exc}")


def _compress_context(state, client, model, request_kwargs, provider, history, round_no):
    """每 10 回合上下文压缩：生成接手摘要并重写 history，返回新的本地 history。

    fidelity 不通过时保留原 history，仅在 compression_record 标记 degraded；
    任何异常都保留原 history，不阻断主回合。
    """
    if not engine.context_compressor.should_compress(round_no):
        return history
    try:
        handoff = engine.context_compressor.build_handoff(state)
        prompt = engine.context_compressor.handoff_prompt(handoff)
        summary = _distill_model(client, model, prompt, request_kwargs, provider)
        if not str(summary or "").strip():
            raise ValueError("接手摘要为空")
        # apply_handoff 读 state["history"]；传入含本回合最新内容的视图。
        current_view = dict(state, history=history)
        applied = engine.context_compressor.apply_handoff(current_view, summary)
        new_history = applied["history"]
        record = applied["compression_record"]
        fidelity = engine.context_compressor.fidelity_check(current_view, new_history, handoff)
        if fidelity.get("ok"):
            record["fidelity"] = "ok"
            state["compression_record"] = record
            state["history"] = new_history
            result_history = new_history
        else:
            # 关键事实丢失：保留原 history，只标记降级。
            record["fidelity"] = "degraded"
            record["missing"] = list(fidelity.get("missing") or [])
            state["compression_record"] = record
            result_history = history
        # 轻量接手包随 state 由 persistence 自动入档。
        state["handoff"] = engine.context_compressor.handoff_for_save(current_view)
        return result_history
    except Exception as exc:  # noqa: BLE001
        _wiring_log(state, f"上下文压缩失败，保留原历史：{exc}")
        return history


_on_provider_change = golden_finger_panel._on_provider_change


def _character_pool_cards():
    """加载合并角色池（内置 + 用户自定义 + 用户替换版），失败回落纯内置。

    缓存本体收编在 core.services.registries（中立层）；engine 的
    character_library 改动角色库后直接失效该缓存，不再反向写 app 全局。
    """
    # 缓存值统一为 (cards, shadowed) 二元组——与 character_library.merged_pool_cached
    # 共用同一份缓存，两处写入格式必须一致。
    cached = registries.get_character_pool_cache()
    if isinstance(cached, tuple) and len(cached) == 2:
        return cached[0]
    cards: tuple = ()
    shadowed: frozenset = frozenset()
    try:
        from core.engine import character_library

        cards, _shadowed = character_library.merged_pool()
        shadowed = frozenset(_shadowed)
    except ImportError:
        cards = ()
    if not cards:
        try:
            from core.engine import catalog
            cards = tuple(catalog.load_character_pool())
        except (OSError, ValueError, ImportError):
            cards = ()
    cards = tuple(cards)
    registries.set_character_pool_cache((cards, shadowed))
    return cards


def _character_pool_choices(role="伙伴", heroine_mode="单女主", limit=400):
    """返回指定角色类型的蒸馏模型名称。"""
    if role == "伙伴":
        pool_role = "伙伴"
    else:
        pool_role = "multi_heroine" if str(heroine_mode or "").startswith("多") else "single_heroine"
    return [card.name for card in _character_pool_cards()
            if card.role == pool_role and card.name][:limit]


def _heroine_pool_choices(mode="单女主", limit=400):
    """兼容入口：单女主与多女主角色池严格隔离。"""
    names = _character_pool_choices("女主", mode, limit)
    if names:
        return names
    fallback_mode = "多女主" if str(mode or "").startswith("多") else "单女主"
    return [item.get("name", "") for item in heroine_pool(fallback_mode)
            if item.get("name")][:limit]


def _character_model_payload(name, role="伙伴", heroine_mode="单女主"):
    """把蒸馏角色模型转换为表单值和可持久化角色卡。"""
    name = str(name or "").strip()
    expected = "伙伴" if role == "伙伴" else (
        "multi_heroine" if str(heroine_mode or "").startswith("多") else "single_heroine")
    card = next((item for item in _character_pool_cards()
                 if item.name == name and item.role == expected), None)
    if card is None:
        return {
            "name": name, "skill": "按设定推断", "background": "",
            "character_card": {}, "source": "",
        }
    abilities = list(card.abilities)
    relations = dict(card.relationship_vector)
    skill = "、".join(abilities[:3]) or "按设定推断"
    background_parts = [part for part in (
        card.background,
        f"角色原型：{card.archetype}" if card.archetype else "",
        f"核心欲望：{card.desire}" if card.desire else "",
        f"核心恐惧：{card.fear}" if card.fear else "",
    ) if part]
    return {
        "name": card.name,
        "skill": skill,
        "background": "\n".join(background_parts),
        "character_card": {
            "goal": card.desire,
            "fear": card.fear,
            "abilities": abilities,
            "relationship_vector": relations,
            "knowledge_scope": list(card.knowledge_scope),
            "speech_style": card.voice,
            "unacceptable_behaviors": list(card.unacceptable_actions),
        },
        "source": card.source,
    }


def _character_model_updates(name, role="伙伴", heroine_mode="单女主"):
    """选择蒸馏模型后自动填入名称、技能、背景与预览。"""
    payload = _character_model_payload(name, role, heroine_mode)
    return (
        payload["name"],
        gr.update(value=payload["skill"] or "按设定推断"),
        payload["background"],
        _card_html(payload["name"], role, payload["skill"], payload["background"], 1, -1),
    )


def _on_heroine_mode_change(mode):
    """切换女主池时重置待配置名册，防止跨池角色混入。"""
    choices = _heroine_pool_choices(mode)
    single = not str(mode or "").startswith("多")
    note = (f"单女主池：目标数量最多 1 位；可选 {len(choices)} 个蒸馏模型。"
            if single else f"多女主池：数量由你填写；可选 {len(choices)} 个蒸馏模型。")
    return (
        gr.update(choices=choices, value=None), note, [], gr.update(value=0),
        "女主目标数量为 0，无需配置。", gr.update(visible=False),
        gr.update(value="女主名册已完成", interactive=False),
    )


def _roster_target_updates(target, rows, role="伙伴", heroine_mode="单女主"):
    """规范目标数量，并返回逐位配置进度和编辑器状态。"""
    maximum = 1 if role == "女主" and not str(heroine_mode or "").startswith("多") else MAX_ROSTER
    target = normalize_count(target, maximum)
    configured = len([item for item in (rows or []) if isinstance(item, dict)])
    complete = configured >= target
    if target <= 0:
        progress = f"{role}目标数量为 0，无需配置。"
    elif complete:
        progress = f"{role}已配置 {configured}/{target}，名册已完成。"
    else:
        progress = f"{role}已配置 {configured}/{target}，正在安排第 {configured + 1} 位。"
    button_text = f"保存第 {configured + 1}/{target} 位{role}" if target and not complete else f"{role}名册已完成"
    return (
        gr.update(value=target),
        progress,
        gr.update(visible=bool(target and not complete)),
        gr.update(value=button_text, interactive=bool(target and not complete)),
    )


def _card_html(name, role, skill, background, participation, power, scope="", cost="", cooldown="", limits=""):
    """返回当前编辑角色卡；文本全部转义，避免上传内容注入页面。

    卡片字段固定包含基本信息、技能、作用域、代价、冷却和限制；未填写项由
    参与度和角色类型推导出默认值，保证悬停时信息完整。
    """
    import html as _html
    level = max(1, min(9, int(participation or 1)))
    scope = scope or ("主线场景与关键冲突" if level >= 7 else ("阶段性支线与关键节点" if level >= 4 else "局部场景与低频协作"))
    cost = cost or ("每次出手需消耗自身资源或人情，并留下可追查痕迹" if role != "宿敌" else "施压需要动用阵营资源并暴露意图")
    cooldown = cooldown or f"{max(1, 10 - level)} 回合内不重复主导同类行动"
    limits = limits or "不得凭空获得未确认信息；不得替玩家做出关键选择；不得改写已入账事实"
    fields = [
        ("角色", name or "未命名"), ("类型", role), ("技能", skill or "按设定推断"),
        ("背景", background or "未填写"), ("参与度", f"{level}/9"),
        ("实力/影响力", str(power if power not in (None, "") else "按设定推断")),
        ("作用域", scope), ("代价", cost), ("冷却", cooldown), ("限制", limits),
    ]
    body = "".join(f"<div><b>{_html.escape(str(k))}</b>：{_html.escape(str(v))}</div>" for k, v in fields)
    return f"<div class='fe-character-card' tabindex='0'><strong>角色卡预览</strong>{body}</div>"


def _append_dynamic_slot(rows, name, skill, custom_skill, upload, background, power,
                         participation, role, heroine_mode, target_count=None,
                         model_name=""):
    """提交一个角色并清空编辑行；目标数量和单女主约束均在代码层执行。"""
    from core.engine.roster_schema import normalize_roster
    current = [dict(item) for item in (rows or []) if isinstance(item, dict)]
    maximum = 1 if role == "女主" and not str(heroine_mode or "").startswith("多") else MAX_ROSTER
    target = normalize_count(target_count, maximum) if target_count is not None else None
    if target is not None and len(current) >= target:
        return current, json.dumps(current, ensure_ascii=False), \
            f"⚠️ {role}目标数量为 {target}，当前名册已满。", \
            _card_html(name, role, skill, background, participation, power), *([gr.update()] * 7)
    name = str(name or "").strip()
    if not name:
        return current, json.dumps(current, ensure_ascii=False), "⚠️ 请先填写角色名称。", _card_html(name, role, skill, background, participation, power), *([gr.update()] * 7)
    upload_path = fe._to_path(upload) if upload else ""
    skill_label = os.path.basename(upload_path) if upload_path else skill_value(skill, custom_skill)
    model_payload = _character_model_payload(model_name, role, heroine_mode) if model_name else {}
    current.append({
        "role": role, "name": name, "skill": skill_label,
        "skill_custom": str(custom_skill or "").strip(),
        "skill_upload": upload_path, "skill_label": skill_label,
        "background": str(background or "").strip(), "power": power,
        "participation": participation or 1,
        "character_model": str(model_name or "").strip(),
        "character_model_source": model_payload.get("source", ""),
        "character_card": model_payload.get("character_card", {}),
    })
    try:
        normalized = normalize_roster({"heroine_mode": heroine_mode or "单女主", "slots": current})
    except ValueError as exc:
        current.pop()
        return current, json.dumps(current, ensure_ascii=False), f"⚠️ 名册未更新：{exc}", _card_html(name, role, skill_label, background, participation, power), *([gr.update()] * 7)
    current = normalized["slots"]
    summary = "\n".join(f"{i + 1}. {item.get('name') or '未命名'} ｜ skill: {item.get('skill') or '按设定推断'} ｜ 参与度 {item.get('participation', 1)}/9" for i, item in enumerate(current))
    clear = [gr.update(value=""), gr.update(value="按设定推断"), gr.update(value=""), gr.update(value=None), gr.update(value=""), gr.update(value=-1), gr.update(value=1)]
    return current, summary or "尚未加入角色。", f"✅ 已加入{role} {len(current)}：{name}", _card_html(name, role, skill_label, background, participation, power), *clear


def _append_roster_slot_ui(rows, target, model_name, name, skill, custom_skill,
                           upload, background, power, participation, role,
                           heroine_mode):
    """组合保存结果与下一槽位进度，供单编辑器流程直接绑定。"""
    before = len(rows or [])
    result = _append_dynamic_slot(
        rows, name, skill, custom_skill, upload, background, power,
        participation, role, heroine_mode, target_count=target,
        model_name=model_name,
    )
    current = result[0]
    target_update, progress, editor_update, button_update = _roster_target_updates(
        target, current, role, heroine_mode)
    model_update = gr.update(value=None) if len(current) > before else gr.update()
    return (*result[:4], model_update, *result[4:], target_update, progress,
            editor_update, button_update)


WORKS = fe.list_works()
DEFAULT_WORK = WORKS[0] if WORKS else None

# 角色性格模型来自 personas/standard（标准）与 personas/enhanced（超高还原）。
CHARACTER_MODELS = fe.list_character_models()
CHAR_PATH = {label: path for label, path in CHARACTER_MODELS}
_ENHANCED = [l for l, _ in CHARACTER_MODELS if "（超高还原）" in l]
_STANDARD = [l for l, _ in CHARACTER_MODELS if "（超高还原）" not in l]
# 魂穿性格顺序：自定义/内置预设 → 普通（标准层）→ 超高还原（增强层）
PERSONA_CHOICES = fe.PERSONAS + _STANDARD + _ENHANCED
DEFAULT_PERSONA = (_ENHANCED[0] if _ENHANCED else
                   (CHARACTER_MODELS[0][0] if CHARACTER_MODELS else fe.PERSONAS[1]))


def _commit_memory(state, patch, *, round_no, source):
    """统一状态提交口：校验失败时保留旧快照，不让坏数据进入面板与存档。"""
    current = state.get("state_memory") or blank_state(state.get("mode", ""), "")
    try:
        updated, _changes = apply_turn(current, patch, round_no=round_no, source=source)
    except (TypeError, ValueError):
        updated = current
    state["state_memory"] = updated
    state["state_panel"] = render_panel(updated)
    return updated


def _update_runtime_memory(state, message, *, round_no):
    """玩家提交行动：只登记意图到待结算，不预判结果。"""
    patch = action_patch(message, round_no=round_no,
                         chapter=state.get("current_chapter") if (state.get("mode") or "").startswith("强化") else None)
    return _commit_memory(state, patch, round_no=round_no, source="player_action")


def _extract_member_summaries(reply, members, max_chars=60):
    """活跃角色一句话本回合行为摘要（本地提取，零模型成本）。

    每位在场成员取其在回复正文中首次出现所在的句子（按。！？……切分），
    截断到 max_chars；未在正文出现的成员不产生摘要。返回 {name: sentence}。
    """
    import re as _re
    text_body = fe.strip_hidden(reply or "")
    summaries = {}
    if not text_body:
        return summaries
    sentences = [s.strip() for s in _re.split(r"[。！？!?…]{1,3}|\n", text_body) if s.strip()]
    for member in members or []:
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        for sentence in sentences:
            if name in sentence:
                summaries[name] = sentence[:max_chars]
                break
    return summaries


def _commit_reply_memory(state, reply, message, *, round_no, lore_hits=None):
    """模型回复落地后，按可验证证据更新时空、身体、资产、能力与认知。"""
    current = state.get("state_memory") or blank_state(state.get("mode", ""), "")
    patch = extract_patch(fe.strip_hidden(reply), action=message, current=current, round_no=round_no)
    scene = dict(patch.get("scene") or {})
    scene["pending"] = []
    if (state.get("mode") or "").startswith("强化"):
        scene["chapter"] = int(state.get("current_chapter", 1) or 1)
    patch["scene"] = scene
    if lore_hits is not None:
        patch["flags"] = {"last_worldbook": list(lore_hits)[:8]}
    
    # Character state updates: extract evidence from reply and update character states
    try:
        from core.services import character_state_service
        char_states = state.setdefault("character_states", {})
        
        # Update active character states based on narrative
        active_members = _runtime_character_constraints(state, message)
        for member in active_members:
            name = str(member.get("name") or "").strip()
            if not name:
                continue
            
            # Initialize character state if not exists
            if name not in char_states:
                char_states[name] = character_state_service.blank_character_state(
                    character_state_service.core_personality_projection(member.get("character_card"))
                )
            
            # Extract simple evidence from reply (basic implementation)
            reply_text = fe.strip_hidden(reply)
            if name in reply_text:
                # Simple evidence: character appeared this round
                char_states[name] = character_state_service.add_evidence(
                    char_states[name],
                    key="recent_activity",
                    value=f"第{round_no}回合活跃",
                    confidence=0.7,
                    provenance={"round": round_no, "action": message[:80]},
                    round_no=round_no
                )
        
        # Decay all character states slightly each round
        for name in list(char_states.keys()):
            char_states[name] = character_state_service.decay_assertions(
                char_states[name], steps=1
            )
    except Exception:  # noqa: BLE001  Character state updates are non-critical
        pass
    
    return _commit_memory(state, patch, round_no=round_no, source="engine_reply")


def on_start(provider, base_url, api_key, remember, model, thinking_mode, thinking_param,
             mode, work, novel_file, fragment, role, timepoint, difficulty, gf, gf_custom, persona_preset,
             persona_custom, persona_file, distill_enabled, companion_roster=None, heroine_roster=None,
             companion_count=0, heroine_count=0, heroine_mode="单女主", enable_nemesis=False,
             nemesis_select="", nemesis_file=None, convergence="较高", novel_display_name=None,
             nemesis_display_name=None, story_richness=None, story_agent_mode=False,
             paper_tier=None, roster_card_ids=None, protagonist_gender="unknown", **legacy):
    """开始 / 重置：装配规则并生成开场。生成器，流式更新聊天窗。

    旧三槽参数（companion_1..3 / heroine_1..3）已下线：全项目无调用方传入，
    名册一律走 companion_roster / heroine_roster 动态行。**legacy 仅吸收未知 kwargs。
    """
    hide, show, chat_on = gr.update(visible=False), gr.update(visible=True), gr.update(visible=True)
    chat_off = gr.update(visible=False)
    api_key = (api_key or "").strip() or _provider_key(provider)
    if not api_key:
        yield _out_start([], {"system": "", "history": [], "plot_ready": False,
                              "gf_stage": "pending", "gf_confirmed": False, "chapter_index": None},
                         "⚠️ 请先填入 API Key。",
                         progress=_progress_html(None), token=_token_md(None),
                         title="### 命运引擎", panel="### 状态记忆面板",
                         u3=chat_off, u4=gr.update(visible=False))
        return
    base_url = (base_url or "").strip() or fe.provider_config(provider)["base_url"]
    if mode and mode.startswith("强化"):
        timepoint = "故事开篇"
    # 金手指：推荐项直接生效；自定义必须已确认；选“无”则阻断其他角色金手指。
    gf_decision = engine.resolve(gf, gf_custom if isinstance(gf_custom, dict) else None)
    if not gf_decision["ready"]:
        yield _out_start([], {"system": "", "history": []},
                         "⚠️ 自定义金手指尚未确认：请先生成提案并点击『确认采用』。",
                         progress=_progress_html(None), token=_token_md(None),
                         title="### 命运引擎", panel="### 状态记忆面板",
                         u3=chat_off, u4=gr.update(visible=False))
        return
    gf = gf_decision["label"]
    gf_blocked = bool(gf_decision["blocked"])
    if remember:
        _save_profile(provider, api_key, base_url, model, thinking_mode, thinking_param)
    request_kwargs = _thinking_kwargs(provider, thinking_mode, thinking_param)

    # —— 作品来源：强化模式严格要求完整 TXT；作品库档案只服务基础模式 ——
    enhanced = bool(mode and str(mode).startswith("强化"))
    uploaded_path = fe._to_path(novel_file) if novel_file else None
    try:
        chapter_index, novel_excerpt, novel_name, work_label = game_setup.resolve_work_source(
            mode, work, novel_file, fragment, novel_display_name,
            gf_confirmed=bool(gf_decision["ready"]))
    except game_setup.WorkSourceError as exc:
        yield _out_start([], exc.state, str(exc), progress=_progress_html(None), token=_token_md(None),
                         title="### 命运引擎", panel="### 状态记忆面板", u3=chat_off, u4=gr.update(visible=False))
        return

    # —— 性格来源：上传 MD ＞ 角色模型 ＞ 自定义文本 ＞ 预设 ——
    if persona_file:
        persona_text = fe.read_upload_text(persona_file) or "（上传的性格文件为空）"
    elif persona_preset in CHAR_PATH:
        persona_text = fe.read_character_model(CHAR_PATH[persona_preset])
    elif persona_preset and persona_preset.startswith("自定义"):
        persona_text = (persona_custom or "").strip() or "未具体设定，由系统依难度与题材建议。"
    else:
        persona_text = persona_preset
    # 人格标签用于选项因素排查：预设直接用名称，上传用文件名，自定义取文本前 20 字。
    if persona_file:
        persona_label = os.path.splitext(os.path.basename(str(persona_file)))[0] + "（上传性格）"
    elif persona_preset and not str(persona_preset).startswith("自定义"):
        persona_label = str(persona_preset).strip()
    else:
        persona_label = (persona_custom or "").strip()[:20] or "自定义性格"
    convergence = engine.normalize_convergence(convergence)
    story_richness = engine.normalize_richness(story_richness)

    # —— 试卷档位（重构 M4）：paper_tier 显式优先；旧客户端按丰富度就近映射。
    #     档位门禁双侧校验的第二侧（server 已 400 拦截，这里兜底直接调用 app
    #     的路径）：普通模式限 1–2 档、第 6 档必须开类 agent；不合法回落默认档。 ——
    agent_mode_requested = bool(story_agent_mode) and enhanced
    mode_label = "强化" if enhanced else "普通"
    if paper_tier is None:
        paper_tier = engine.papers.map_legacy_richness(story_richness)
        if not enhanced:
            paper_tier = min(paper_tier, 2)
    tier_ok, tier_reason = engine.papers.validate_selection(
        int(paper_tier), mode_label, agent_mode_requested)
    tier_note = ""
    if not tier_ok:
        tier_note = f"剧情丰度 {paper_tier} 不可用（{tier_reason}），已回落默认档。"
        paper_tier = 3 if enhanced else 2

    # —— 宿敌机制（可选）：上传 MD ＞ 角色模型 ＞ 自定义文本 ——
    nemesis_label, nemesis_persona = game_setup.resolve_nemesis(
        enable_nemesis, mode, nemesis_file, nemesis_select, nemesis_display_name, CHAR_PATH)

    # UI 的动态 roster 为唯一名册来源。
    companions = game_setup.assemble_roster(companion_roster, companion_count, "伙伴", "伙伴")
    heroines = game_setup.assemble_roster(heroine_roster, heroine_count, "主线", "伴侣")

    # —— 同名监测与世界观测名（规格 §7）：四栏实际选中卡，同卡重复也算冲突 ——
    # roster_card_ids: [{"slot": "主角|主线|伙伴|宿敌", "card_id": ...}]；纯字符串按伙伴栏处理。
    _SLOT_ALIASES = {"女主": "主线", "男主": "主线", "主线搭档": "主线"}
    slot_entries: list[tuple[str, Any]] = []
    protagonist_card = None
    for entry in (roster_card_ids or []):
        if isinstance(entry, dict):
            slot_name = str(entry.get("slot") or "伙伴")
            card_id = str(entry.get("card_id") or entry.get("id") or "")
        else:
            slot_name, card_id = "伙伴", str(entry)
        slot_name = slot_name[:-1] if slot_name.endswith("栏") else slot_name
        slot_name = _SLOT_ALIASES.get(slot_name, slot_name)
        card = next((item for item in _character_pool_cards() if item.id == card_id), None)
        if card is not None:
            slot_entries.append((slot_name, card))
            if slot_name == "主角":
                protagonist_card = card
    _used_names = [row.get("name") for row in companions + heroines if row.get("name")]
    if nemesis_label:
        _used_names.append(nemesis_label)
    _rename_plan = engine.name_collision.plan_renames(
        slot_entries, theme=work_label or novel_name or "", used_names=_used_names)
    renames = _rename_plan["renames"]
    roster_cards = _rename_plan["cards"]
    # 宿敌栏卡选择位：当前流程无独立宿敌卡 UI，先在状态结构预留 nemesis_card。
    nemesis_card = next((rec for rec in roster_cards if rec["slot"] == "宿敌"), None)
    if nemesis_card:
        # 宿敌卡 persona 覆盖接入点（规格 §7）：nemesis_card 存在时以其 persona 与更名参与生成。
        nemesis_persona = nemesis_card.get("persona") or nemesis_persona
        nemesis_label = nemesis_card.get("name") or nemesis_label
    # 主角栏卡选择位：主角卡存在时，其角色卡 persona 优先生效（覆盖模型/自定义来源）。
    protagonist_card_record = next((rec for rec in roster_cards if rec["slot"] == "主角"), None)
    if protagonist_card is not None and protagonist_card_record is not None:
        _card_persona_text = protagonist_card_record.get("persona") or ""
        if _card_persona_text:
            persona_text = _card_persona_text
            persona_label = protagonist_card_record.get("name") or persona_label

    # 选择“无金手指”时，代码层抹除伙伴/女主/宿敌的金手指配置。
    companions = engine.apply_block(companions, gf_blocked)
    heroines = engine.apply_block(heroines, gf_blocked)
    faction_gap, computed_nemesis_difficulty = game_setup.compute_faction(
        nemesis_card, nemesis_label, nemesis_persona, companions, heroines,
        difficulty, work_label, novel_name)
    if gf_blocked:
        gf = (gf + "（本局全员无金手指：宿敌、伙伴、女主同样不得拥有金手指或等效超常能力）")

    # —— 穿越保障（traverse guard）：每位穿越者必须附身书中具体角色（禁止悬空）——
    # 名字规则：卡和性格都只是「魂」，未指定名字的槽位由模型在开局分配——
    # 主角→原著主角本人；伴侣/伙伴/宿敌→性格最类似（有所选性格时）或最贴合定位的原著角色。
    # 性别栏杆已破除：魂的性别（玩家选择/卡面）不构成任何限制，叙事以附身角色生理性别为准。
    _gender_entries = []
    _protagonist_named = bool((role or "").strip()) or protagonist_card is not None
    _gender_entries.append({
        "slot": "主角",
        "name": ((role or "").strip()
                 or (protagonist_card.name if protagonist_card is not None else "主角")),
        "gender": (protagonist_gender
                   if str(protagonist_gender or "").strip().lower() in ("male", "female", "男", "女")
                   else (protagonist_card.gender if protagonist_card is not None else "")),
        "assign": "" if _protagonist_named else "original_protagonist",
        "soul": "" if _protagonist_named else str(persona_text or ""),
    })
    for _packed in companions:
        _gender_entries.append({
            "slot": "伙伴", "name": _packed.get("name", ""),
            "gender": (_packed.get("character_card") or {}).get("gender", ""),
            "name_pending": bool(_packed.get("name_pending")),
            "soul": str(_packed.get("persona_preset") or "")})
    for _packed in heroines:
        _gender_entries.append({
            "slot": "主线", "name": _packed.get("name", ""),
            "gender": (_packed.get("character_card") or {}).get("gender", ""),
            "name_pending": bool(_packed.get("name_pending")),
            "soul": str(_packed.get("persona_preset") or "")})
    if nemesis_card:
        _gender_entries.append({"slot": "宿敌", "name": nemesis_card.get("name") or nemesis_label or "",
                                "gender": nemesis_card.get("gender", "")})
    elif nemesis_label:
        _nemesis_pending = nemesis_label in ("自定义宿敌", "宿敌")
        _gender_entries.append({"slot": "宿敌", "name": nemesis_label, "gender": "",
                                "name_pending": _nemesis_pending,
                                "soul": str(nemesis_persona or "") if _nemesis_pending else ""})
    _gender_book_text = ""
    if mode.startswith("强化") and chapter_index:
        _gender_book_text = "\n".join(
            (_chapter_text(chapter_index, _row.get("idx", 0)) or "")[:4000]
            for _row in (chapter_index.get("chapters") or [])[:3])
    elif novel_excerpt:
        _gender_book_text = str(novel_excerpt)
    elif work_label:
        _gender_book_text = fe.get_work_block(work_label) or ""
    _gender_report = engine.gender_guard.guard_entries(
        _gender_entries, book_text=_gender_book_text,
        protagonist_gender=protagonist_gender)

    # —— 开局前模型准备（在 system prompt 构建之前完成，让真名从一开始就写进提示词）——
    # 穿越身份落定：模型为每位穿越者指定附身角色（实际名字+身份），
    # 无名成员（占位名）与无名宿敌以分配结果写回——卡和性格都只是「魂」，
    # 名字交给模型确定魂穿对象；性别栏杆已破除，叙事以附身角色生理性别为准；
    # 失败静默降级为原「开场白交代」路径。
    work_desc = f"《{novel_name}》(上传)" if novel_name else work_label
    _traverse_map: list[dict] = []
    _traverse_constraint = ""
    _prestart_notes: list[str] = []
    try:
        client = fe.make_client(api_key, provider, base_url)
        try:
            _tm_entries = [e for e in (_gender_report.get("entries") or []) if e.get("name")]
            if _tm_entries:
                _tm_reply = _distill_model(
                    client, model,
                    engine.gender_guard.traverse_map_prompt(
                        _tm_entries, work_desc, book_hint=_gender_book_text),
                    request_kwargs, provider)
                _traverse_map = engine.gender_guard.parse_traverse_map(_tm_reply, _tm_entries)
                if _traverse_map:
                    _by_trav = {m["traverser"]: m for m in _traverse_map}
                    # 无名成员（占位名）写回分配到的书中角色名
                    for _members in (companions, heroines):
                        for _m in _members:
                            if _m.get("name_pending") and _m.get("name") in _by_trav:
                                _m["name"] = _by_trav[_m["name"]]["book_name"]
                                _m["name_pending"] = False
                    # 无名宿敌写回分配名（system prompt 尚未构建，直接生效）
                    if not nemesis_card and nemesis_label in ("自定义宿敌", "宿敌", None):
                        _nem = _by_trav.get(nemesis_label or "宿敌") or _by_trav.get("宿敌")
                        if _nem:
                            nemesis_label = _nem["book_name"]
                            nemesis_persona = nemesis_persona or _nem.get("identity") or "原著角色"
                    _traverse_constraint = engine.gender_guard.build_traverse_constraint(_traverse_map)
        except Exception as _tm_exc:  # noqa: BLE001
            _prestart_notes.append(f"穿越身份落定失败，降级为开场白交代：{_tm_exc}")
            _traverse_map = []
            _traverse_constraint = ""
    except Exception as _client_exc:  # noqa: BLE001 客户端创建失败：开局主流程仍会重试并报错
        _prestart_notes.append(f"开局前模型准备跳过（客户端不可用）：{_client_exc}")

    system = fe.build_system_prompt(
        mode, difficulty, gf, persona_text,
        work_label=work_label, novel_name=novel_name, novel_excerpt=novel_excerpt,
        role=role, timepoint=timepoint,
        nemesis_label=nemesis_label, nemesis_persona=nemesis_persona,
        companion_configs=companions if mode.startswith("强化") else None,
        heroine_configs=heroines if mode.startswith("强化") else None,
        faction_gap=faction_gap, nemesis_difficulty=computed_nemesis_difficulty,
        gender_constraints=_gender_report["constraint_text"])

    history = [{"role": "user", "content": fe.opening_user_message()}]
    status = (f"已锁定：{work_desc} ｜ {mode} ｜ {difficulty} ｜ "
              f"穿越:{role or '系统建议'}@{timepoint or '开篇'} ｜ 金手指:{gf}"
              + (f" ｜ 宿敌:{nemesis_label}" if nemesis_label else ""))
    # 开局回执：展示本局按世界观自动改名记录（renames=[{from,to,slot}]）。
    if renames:
        status += " ｜ 更名:" + "、".join(
            f"{item['from']}已依世界观更名为{item['to']}（{item['slot']}栏）" for item in renames)
    nemesis_on = bool(nemesis_label)
    log_path = _new_session_log(f"- 开局：{status}")
    chapter_runtime = _chapter_state(chapter_index, 1, 0) if mode.startswith("强化") else {}
    memory_state = blank_state(mode=mode, source=work_desc)
    memory_state["scene"].update({"chapter": 1, "round": 0, "name": "开局核对"})
    _gf_spec = dict(gf_decision.get("spec") or {})
    memory_state["abilities"]["golden_finger"] = {
        "name": _gf_spec.get("name") or gf_decision["label"],
        "status": "inactive" if gf_blocked else "ready",
        "cooldown": _gf_spec.get("cooldown", 0) or 0,
        "costs": [_gf_spec["cost"]] if _gf_spec.get("cost") else [],
        "blocked_for_others": gf_blocked,
    }
    lore_state = LoreInjector(_load_lore_entries(), budget_chars=2600, depth=6).snapshot()
    st0 = {"system": system, "history": history, "round": 0,
           "nemesis": nemesis_on, "mode": mode, "work": work_label or novel_name,
           "novel": novel_name, "log": log_path,
           "provider": provider, "base_url": base_url, "model": model,
           "thinking_mode": thinking_mode, "thinking_param": thinking_param,
           "progress": 0, "tok_in": 0, "tok_out": 0, "tok_cache": 0,
           "tok_last": (0, 0), "tok_est": False,
           "request_kwargs": request_kwargs, "chapter_index": chapter_index,
           "plot_ready": not enhanced, "gf_stage": "pending" if enhanced else "confirmed",
           "gf_confirmed": False if enhanced else bool(gf_decision["ready"]),
           "opening_confirmed": False if enhanced else True,
           "opening_phase": opening_flow.PHASE_INIT if enhanced else opening_flow.PHASE_STARTED,
           "start_params": {"provider": provider, "mode": mode, "difficulty": difficulty,
                            "work": work_label, "novel": novel_name, "role": role,
                            "protagonist_gender": protagonist_gender,
                            "timepoint": timepoint, "golden_finger": gf,
                            "heroine_mode": heroine_mode, "convergence": convergence,
                            "story_richness": story_richness,
                            "paper_tier": int(paper_tier),
                            "compose_mode": bool(enhanced),
                            "story_agent_mode": bool(story_agent_mode) and enhanced,
                            "persona": persona_label,
                            "companions": companions, "heroines": heroines,
                            "roster": {"heroine_mode": heroine_mode, "companions": companions, "heroines": heroines},
                            "renames": renames, "roster_card_ids": list(roster_card_ids or []),
                            "thinking_mode": thinking_mode, "thinking_param": thinking_param},
           "persona": persona_label, "convergence": convergence, "options": [],
           "story_richness": story_richness,
           "paper_tier": int(paper_tier),
           "paper_family": engine.papers.family_for_tier(int(paper_tier)),
           "compose_mode": bool(enhanced),
           "agent_mode": bool(story_agent_mode) and enhanced,
           "scene_budget": engine.scene_budget(richness=story_richness),
           "richness_tier": engine.richness_tier(story_richness),
           "persona_text": persona_text,
           "companions": companions, "heroines": heroines, "heroine_mode": heroine_mode,
           "renames": renames, "roster_cards": roster_cards, "nemesis_card": nemesis_card,
           "gender_guard": {"entries": _gender_report["entries"],
                            "pending": _gender_report["pending"]},
           "faction_gap": faction_gap, "nemesis_difficulty": computed_nemesis_difficulty,
           "ledger": ledger_module.new_ledger(), "ripples": [], "distill": {},
           "state_memory": memory_state, "state_panel": render_panel(memory_state),
           "lore": lore_state, "lore_hits": [],
           "distill_enabled": bool(distill_enabled) if enhanced else False,
           **chapter_runtime}
    # —— 回合管线初始化：动态收束力 / 任务状态 / 性格档案 / 碎锚 / 宿敌私密容器 ——
    st0["convergence_state"] = engine.dynamic_convergence.init_state(convergence)
    st0["quest"] = {"status": "none"}
    st0["break_anchor"] = engine.break_anchor.idle_box()
    st0["broken_anchors"] = []
    # 选角剧情相关度：伴侣+伙伴+宿敌统一评估并缩放（强相关超上限自动降档）。
    # 书上下文取剧情大概与开局窗口锚点事件；无蒸馏时退化为作品名+题材匹配。
    try:
        _rel_members = [m for m in (list(companions or []) + list(heroines or [])
                                    + ([{"name": nemesis_label, "skill": nemesis_persona or "",
                                         "background": "", "work": ""}] if nemesis_on else []))
                        if isinstance(m, dict) and str(m.get("name") or "").strip()]
        _rel_book = {"work": work_label or novel_name or "",
                     "premise": (st0.get("distill") or {}).get("plot_summary"),
                     "major_threads": [],
                     "anchor_events": [
                         {"title": e.get("title"), "summary": e.get("summary")}
                         for e in ((st0.get("anchor_timeline") or {}).get("events") or [])
                         if isinstance(e, dict)]}
        _rel_report = engine.roster_relevance.scale_roster(_rel_members, _rel_book)
        st0["roster_relevance"] = _rel_report
    except Exception as exc:  # noqa: BLE001 相关度评估失败不阻断开局
        _wiring_log(st0, f"选角相关度评估失败已跳过：{exc}")
        st0["roster_relevance"] = {"members": {}, "strong_count": 0, "scaled_names": []}
    if nemesis_on:
        # 强化模式 + 宿敌启用：私密容器只进 state，绝不进入玩家可见文本；
        # 资源点随世界难度确定性播种（D1=12 … D9=26），耗尽后宿敌行动渗漏传闻。
        # 宿敌卡 persona 覆盖已在上方 roster_card_ids 处理段完成（nemesis_card 存在即生效）。
        _nemesis_level = engine.runtime_mechanics.difficulty_number(difficulty)
        engine.nemesis_agent.init_private(st0, {
            "name": nemesis_label,
            "goal": str(nemesis_persona or "")[:200],
            "resources": {"资源点": 10 + 2 * _nemesis_level},
        })
    # 性格档案初始化必须晚于宿敌私密容器：init_profiles 会读 nemesis_private
    # 做宿敌弱推断（名字 + goal 关键词），顺序颠倒会得到占位名 + 全 0 向量。
    # init_profiles 幂等（已有档案不覆盖），此处一次到位。
    engine.skill_drift.init_profiles(st0)
    if enhanced:
        flow_result = opening_flow.mark_txt_uploaded(
            opening_flow.initial_state(), txt_path=uploaded_path or novel_name,
            chapters=(chapter_index or {}).get("chapters", []) if isinstance(chapter_index, dict) else [])
        st0["opening_state"] = flow_result["state"] if flow_result["ok"] else opening_flow.initial_state()
    else:
        st0["opening_state"] = opening_flow.initial_state(txt_uploaded=True, plot_ready=True,
                                                           gf_confirmed=True, gf_stage="confirmed",
                                                           opening_confirmed=True, started=True,
                                                           phase=opening_flow.PHASE_STARTED)
    # 每次开局都先保存完整配置和初始运行状态；API Key 由 persistence 统一脱敏。
    if not enhanced:
        try:
            engine.persistence.save_state(st0, root=fe.WRITABLE_DIR, start_params=st0.get("start_params"),
                           session_id=str(st0.get("session_id") or "") or None)
        except (OSError, TypeError, ValueError):
            pass
    try:
        client = fe.make_client(api_key, provider, base_url)
        # 开局前模型准备的降级日志在此落 wiring（st0 已存在）
        for _note in _prestart_notes:
            _wiring_log(st0, _note)
        # —— 穿越身份落定（已在 system prompt 构建前完成）：写入 state、注入硬约束、回执反馈 ——
        if _traverse_map:
            st0["traverse_map"] = _traverse_map
            st0["system"] += "\n\n" + _traverse_constraint
            st0["traverse_receipt"] = engine.gender_guard.format_traverse_receipt(_traverse_map)
            status += f" ｜ 穿越对照:已落定{len(_traverse_map)}人"
        if enhanced:
            # 蒸馏开始前先推送一次状态：前端立即显示"开局蒸馏进行中"，
            # 之后 /state 轮询读取 session 进度注册表刷新阶段文案。
            st0["opening_distill"] = {"status": "running", "stage": "正在读取原著章节",
                                      "stage_key": "start"}
            yield _out_start([], dict(st0, system="", history=[]),
                             "正在提取剧情与角色（开局蒸馏），完成后自动继续…",
                             progress=_progress_html(st0), token=_token_md(st0),
                             title=_token_title_md(st0), panel=st0.get("state_panel", ""),
                             u1=gr.update(visible=True), u2=gr.update(visible=False),
                             u3=chat_off, u4=gr.update(visible=False))
            try:
                # —— M1：开局蒸馏+角色入库流水线（opening_service 中台门面）——
                # plot 多采样并行合并 ∥ 角色抽取（质量门+防编造+入库）∥ 首 3 章
                # 锚点（第 1 章两遍法、长章 map-reduce 全文覆盖）∥ 作品档案 upsert。
                # 开局优先级满额并发，全程降级不抛错，结果保留式回写 state。
                st0.setdefault("provider", provider)
                st0.setdefault("request_kwargs", request_kwargs)
                st0.setdefault("novel_name", novel_name or "")
                opening_summary = opening_service.run_for_state(
                    st0, client, model, chapters_ahead=3)
                st0["opening_report"] = opening_summary
                summary = (st0.get("distill") or {}).get("plot_summary")
                if not summary:
                    raise ValueError(str(
                        (opening_summary.get("errors") or ["剧情提取未产出有效摘要"])[0]))
                st0["system"] = system + "\n\n# 剧情大概（开局低成本摘要）\n" + json.dumps(
                    summary, ensure_ascii=False)
                if _traverse_constraint:
                    # 上行以局部 system 变量重建，需把已锁定的穿越对照补回
                    st0["system"] += "\n\n" + _traverse_constraint
                st0["plot_ready"] = True
                flow_result = opening_flow.mark_plot_ready(st0.get("opening_state"), summary)
                st0["opening_state"] = flow_result["state"]
                st0["opening_phase"] = st0["opening_state"].get("phase")
                # 剧情就绪后精化选角相关度（初评仅作品名，此处带上真实剧情与锚点）。
                try:
                    _rel_members = [m for m in (list(st0.get("companions") or [])
                                                + list(st0.get("heroines") or []))
                                    if isinstance(m, dict) and str(m.get("name") or "").strip()]
                    if (st0.get("nemesis_private") or {}).get("name"):
                        _rel_members.append({"name": st0["nemesis_private"]["name"],
                                             "skill": st0["nemesis_private"].get("goal") or "",
                                             "background": "", "work": ""})
                    _rel_book = {"work": st0.get("work") or "",
                                 "premise": summary,
                                 "major_threads": list(summary.get("major_threads") or [])
                                 if isinstance(summary, dict) else [],
                                 "anchor_events": [
                                     {"title": e.get("title"), "summary": e.get("summary")}
                                     for e in ((st0.get("anchor_timeline") or {}).get("events") or [])
                                     if isinstance(e, dict)]}
                    st0["roster_relevance"] = engine.roster_relevance.scale_roster(_rel_members, _rel_book)
                except Exception as rel_exc:  # noqa: BLE001 精化失败保留初评
                    _wiring_log(st0, f"选角相关度精化失败已跳过：{rel_exc}")
            except Exception as summary_exc:  # noqa: BLE001
                st0["distill"] = {**(st0.get("distill") or {}),
                                  "plot_summary": (st0.get("distill") or {}).get("plot_summary"),
                                  "error": str(summary_exc)}
                st0["plot_ready"] = False
                yield _out_start([], dict(st0, system="", history=[]),
                                 f"⚠️ 强化模式剧情提取未完成，禁止正式开局：{summary_exc}",
                                 progress=_progress_html(st0), token=_token_md(st0),
                                 title=_token_title_md(st0), panel=st0.get("state_panel", ""),
                                 u1=gr.update(visible=True), u2=gr.update(visible=False),
                                 u3=chat_off, u4=gr.update(visible=False))
                return
            # M1：首 3 章锚点已由开局流水线产出（第 1 章两遍法），后台池从第 4 章
            # 起接续前瞻（enqueue 自动跳过已落盘章节），不再同步补首章。
            _start_background_distillation(st0, client, model, sync_first=False)
            # 强化模式在此只完成剧情准备；正式第一幕必须由聊天框两步确认后触发。
            st0["history"] = [
                {"role": "assistant", "content": _gameplay_briefing(mode, nemesis_on)},
                {"role": "assistant", "content": (
                    "✅ TXT 已切章，剧情提取已完成。\n\n"
                    "剧情大概：\n" + _humanize_plot_summary(summary) + "\n\n"
                    + ("" if st0.get("traverse_receipt") else
                       "开局核对将列出「穿越对照表」：逐一交代每位穿越者（主角、伴侣、伙伴、宿敌）"
                       "穿越成了书中哪位角色及其生理性别——附身不受性别限制，以书中身体为准。\n\n")
                    + "请在本聊天框输入“确认金手指”（或“确认无金手指”），然后再输入“确认开局”。"
                )},
            ]
            st0["system"] += "\n\n【开局门禁】等待正式游戏聊天框确认金手指与开局设定后，才生成第一幕。"
            st0["anchor_timeline"] = _anchor_timeline(st0)
            if st0.get("traverse_receipt"):
                st0["history"][1]["content"] += "\n\n" + st0["traverse_receipt"]
            # —— M1：作品档案 upsert 与角色卡入库（质量门+防编造+同名覆盖）已并入
            #     开局流水线（opening_service），旧的 quick_distill 后台线程下线；
            #     此处只把入库结果整理成玩家可见提示。 ——
            _wd = st0.get("work_distill") or {}
            if _wd.get("work_id"):
                st0["history"][1]["content"] += (
                    f"\n\n📚 本书已收录进基础模式作品库（{_wd.get('work_id')}）；"
                    f"书中主要角色过质量门入库 {int(_wd.get('character_count') or 0)} 张，"
                    "可在角色库中查看。")
            else:
                st0["history"][1]["content"] += (
                    "\n\n📚 作品档案与角色入库本次未完成（不影响开局），原因已记录在运行日志。")
            try:
                session = engine.persistence.create_session(fe.WRITABLE_DIR, state=st0, start_params=st0["start_params"])
                st0["session"] = session
            except (OSError, TypeError, ValueError):
                pass
            engine.persistence.save_state(st0, root=fe.WRITABLE_DIR, start_params=st0.get("start_params"),
                           session_id=str(st0.get("session_id") or "") or None)
            yield _out_start([{"role": "assistant", "content": st0["history"][0]["content"]}], st0,
                             "✅ 剧情准备完成，等待聊天框确认金手指与开局。",
                             u1=hide, u2=show, u3=chat_on, u4=gr.update(visible=True))
            return
        # 基础模式同样先给玩家一份玩法速览（进持久历史，加载存档后仍可回看）。
        opening_msgs = [{"role": "assistant", "content": _gameplay_briefing(mode, nemesis_on)}]
        if st0.get("traverse_receipt"):
            opening_msgs.append({"role": "assistant", "content": st0["traverse_receipt"]})
        history = history + opening_msgs
        st0["history"] = history
        yield _out_start(opening_msgs + [
               {"role": "assistant", "content": "…正在唤醒命运引擎，生成开场…"}], st0, status,
            u1=hide, u2=show, u3=chat_on, u4=gr.update(visible=True))
        acc = ""
        ub = {}
        for acc in fe.stream_reply_with_retry(client, model, st0["system"], history, usage_box=ub,
                                               extra_kwargs=request_kwargs, provider=provider,
                                               thinking_mode=thinking_mode, thinking_param=thinking_param):
            yield _out_start([{"role": "assistant", "content": fe.strip_hidden(acc) or "…"}],
                             dict(st0, history=history), status,
                             u1=hide, u2=show, u3=chat_on, u4=gr.update(visible=True))
        _accum_tokens(st0, ub, est_in=int((len(system) + len(fe.opening_user_message())) / 1.5),
                      est_out=int(len(acc) / 1.5))
        history = history + [{"role": "assistant", "content": acc}]
        entry = fe.extract_log(acc)
        _append_log(log_path, "\n## 开局核对\n- 引擎日志: " +
                    (entry or "（核对阶段无日志段）") + "\n")
        # 基础模式开场同样产出首批结构化选项；确认阶段回复不含选项时保持原文。
        opening_display, opening_options_block = _finalize_options(st0, acc)
        opening_content = (opening_display + "\n\n" + opening_options_block).strip() \
            if opening_options_block else opening_display
        yield _out_start([{"role": "assistant", "content": opening_content}],
                         dict(st0, history=history), status,
                         u1=hide, u2=show, u3=chat_on, u4=gr.update(visible=True))
    except Exception as e:  # noqa: BLE001
        yield _out_start([{"role": "assistant", "content": f"⚠️ 调用模型服务失败：{e}"}],
                         dict(st0, history=[]), "调用失败，请检查 Key 与网络。",
                         u1=gr.update(visible=True), u2=gr.update(visible=False),
                         u3=chat_on, u4=gr.update(visible=False))


def _chat_opening_confirmation(message):
    """识别正式聊天框中的两步开局确认，避免设置区确认提前生效。"""
    text = str(message or "").strip()
    if not text:
        return ""
    if any(token in text for token in ("确认金手指", "确认采用金手指", "采用这个金手指", "金手指确认", "确认无金手指")):
        return "gf"
    if any(token in text for token in ("确认开局", "确认设定", "开始第一幕", "正式开始", "确认进入游戏")):
        return "opening"
    return ""


def _opening_flow_state(state):
    flow = state.get("opening_state") if isinstance(state, dict) else None
    return opening_flow.normalize_state(flow if isinstance(flow, dict) else state)


def _anchor_gate_ok(state, chapter, check, chapter_round, budget):
    """三段式锚点门禁：与运行时规则对齐（锚点有未触发/前置/进行中/已发生等中间态）。

    - 本回合校验 fulfilled（含碎锚放行）→ 过；
    - 本章锚点已发生入账（ledger.anchors）→ 过（事件不必每幕重复）；
    - 章节预算尾声（chapter_round >= budget）→ 必须 fulfilled（防锚点被翻章跳过）；
    - 预算未到尾声 → 允许铺垫（pending/mentioned/partial 均可）。
    """
    if check.get("valid") or check.get("hint_only"):
        return True
    anchors = ((state.get("ledger") or {}).get("anchors") or {})
    try:
        chapter = int(chapter)
    except (TypeError, ValueError):
        chapter = 1
    if chapter in anchors or str(chapter) in anchors:
        return True
    try:
        budget = max(1, int(budget or 1))
        chapter_round = int(chapter_round or 1)
    except (TypeError, ValueError):
        budget, chapter_round = 1, 1
    return chapter_round < budget


def _record_anchor_fulfilled(state, chapter, round_no):
    """锚点事件落账（每章只记首次）：门禁通过且本回合 fulfilled 时调用。"""
    try:
        chapter = int(chapter)
        round_no = int(round_no)
    except (TypeError, ValueError):
        return
    ledger = state.setdefault("ledger", engine.ledger.new_ledger())
    anchors = ledger.setdefault("anchors", {})
    if chapter in anchors or str(chapter) in anchors:
        return
    outcome, k = _convergence_outcome(state)
    anchors[chapter] = {"round": round_no, "type": outcome or "faithful", "k": int(k)}


def _scene_gate_reasons(budget_check, interaction_check, anchor_check):
    """机械门禁失败原因（中文，供重写指令与玩家提示共用）。"""
    reason = []
    if not budget_check.get("valid"):
        reason.append(
            f"正文体量 {budget_check.get('chars', 0)} 不在当前故事丰富度允许区间"
            f"（{budget_check.get('minimum')}–{budget_check.get('maximum')}）")
    if not interaction_check.get("valid"):
        names = "、".join(interaction_check.get("active_names") or [])
        reason.append(f"活跃角色未形成可验证交互（需点名 {names} 中至少一人并给出回应或动作）")
    if not anchor_check.get("valid"):
        reason.append("尚未收束到当前锚点")
    return reason


def _regen_prompt_for_scene_gate(reasons, budget_check, interaction_check, current_anchor):
    """门禁失败的定向重写指令：失败原因 + 三条可执行修正要求。"""
    names = "、".join(interaction_check.get("active_names") or []) or "在场角色"
    anchor_hint = str(current_anchor or "").strip()[:120]
    return (
        "（系统重写指令）你上一稿未通过机械校验：%s。请完整重写本幕并直接输出正文，硬性要求：\n"
        "1) 正文（不含引擎日志与选项）字数控制在 %s–%s 字之间；\n"
        "2) 让「%s」中至少一人在场内被点名并做出回应或动作；\n"
        "3) 剧情收束到当前锚点（围绕「%s」这一事件自然呈现）。\n"
        "保持剧情连贯，结尾照常附上引擎日志段与 6 个编号选项。"
        % ("；".join(reasons), budget_check.get("minimum"), budget_check.get("maximum"),
           names, anchor_hint))


def on_send(provider, base_url, api_key, model, thinking_mode, thinking_param,
             message, chatbot, state):
    """发送玩家行动，流式续写；允许在不重置历史的情况下切换模型参数。"""
    message = (message or "").strip()
    if not message:
        yield _out_send(chatbot, state)
        return
    if not state or not state.get("system"):
        yield _out_send(chatbot + [{"role": "assistant", "content": "⚠️ 请先点击『开始模拟』完成开局。"}], state)
        return
    if str(state.get("mode") or "").startswith("强化"):
        flow = _opening_flow_state(state)
        state["opening_state"] = flow
        if not flow.get("txt_uploaded"):
            yield _out_send(chatbot + [{"role": "assistant", "content": "⚠️ 强化模式必须先上传 TXT 原著。"}], state)
            return
        if not flow.get("plot_ready"):
            yield _out_send(chatbot + [{"role": "assistant", "content": "⚠️ 剧情提取尚未完成，当前不能进入正式游戏。"}], state)
            return
        confirmation = _chat_opening_confirmation(message)
        if not flow.get("gf_confirmed"):
            if confirmation != "gf":
                yield _out_send(chatbot + [{"role": "assistant", "content": "⚠️ 请在正式游戏聊天框输入“确认金手指”或“确认无金手指”，完成剧情准备后的金手指确认。"}], state)
                return
            result = opening_flow.confirm_golden_finger(flow, True, state.get("gf_decision") or {"label": state.get("start_params", {}).get("golden_finger")})
            state["opening_state"] = result["state"]
            state["gf_confirmed"] = True
            state["gf_stage"] = "confirmed"
            chatbot = chatbot + [{"role": "user", "content": message}, {"role": "assistant", "content": "✅ 金手指已在正式游戏聊天框确认。请继续输入“确认开局”，我才会生成第一幕。"}]
            yield _out_send(chatbot, state, msg_update=gr.update(value=""))
            return
        if not flow.get("opening_confirmed"):
            if confirmation != "opening":
                yield _out_send(chatbot + [{"role": "assistant", "content": "⚠️ 金手指已确认，请在正式游戏聊天框输入“确认开局”后再开始第一幕。"}], state)
                return
            opening_anchor = _current_anchor_text(state, state.get("current_chapter", 1))
            if not opening_anchor:
                # M0：开局确认零阻塞——首章锚点缺失先走救援（模型一卷→
                # 摘录兜底放行，后台 force 精化），仅极端失败才提示等待。
                opening_anchor = _rescue_anchor(state, state.get("current_chapter", 1), api_key)
            if not opening_anchor:
                state["scene_validation"] = {
                    "length": None,
                    "interaction": None,
                    "anchor": {"valid": False, "reason": "anchor_missing", "anchor_mentioned": False, "causal_marker": False},
                }
                state["scene_gate"] = False
                state["scene_gate_reason"] = "首章缺少已验证剧情锚点，无法确认开局。"
                distill_error = ((state.get("distill") or {}).get("error") or "").strip()
                detail = f"（{distill_error}，系统稍后会自动重试）" if distill_error else ""
                yield _out_send(chatbot + [{"role": "assistant", "content": "⚠️ 首章剧情锚点尚未准备完成，暂不能确认开局；请稍候，待右侧锚点蒸馏进度显示当前章已完成后重试。" + detail}], state)
                return
            result = opening_flow.confirm_opening(flow, True)
            state["opening_state"] = result["state"]
            state["opening_confirmed"] = True
            state["opening_phase"] = opening_flow.PHASE_OPENING_CONFIRMED
            chatbot = chatbot + [{"role": "user", "content": message}, {"role": "assistant", "content": "✅ 开局设定已确认，正在生成第一幕。"}]
            yield _out_send(chatbot, state, msg_update=gr.update(value=""))
            message = "开始第一幕。请依据已确认设定生成正式开场。"
        provider = (provider or state.get("provider") or "deepseek").strip()
    base_url = (base_url or state.get("base_url") or fe.provider_config(provider)["base_url"]).strip()
    api_key = (api_key or "").strip() or _provider_key(provider)
    state["provider"], state["base_url"], state["model"] = provider, base_url, model
    state["thinking_mode"] = thinking_mode or state.get("thinking_mode", "auto")
    state["thinking_param"] = thinking_param or state.get("thinking_param", "")
    request_kwargs = _thinking_kwargs(provider, state["thinking_mode"], state["thinking_param"])
    state["request_kwargs"] = request_kwargs
    # —— 回合计数与机械章节预算 ——
    # 强化回合必须是事务：锚点缺失、模型异常或输出未通过硬门禁时，运行态均要回滚。
    enhanced = (state.get("mode") or "").startswith("强化")
    target_chapter_state = _next_chapter_state(state) if enhanced else {}
    target_chapter = target_chapter_state.get("current_chapter", state.get("current_chapter", 1))
    current_anchor = _current_anchor_text(state, target_chapter) if enhanced else ""
    convergence = engine.normalize_convergence(
        start_setting(state, "convergence"))
    # 故事丰富度：玩家可拖动的单回合体量刻度，决定门禁区间与提示块目标。
    story_richness = engine.normalize_richness(
        start_setting(state, "story_richness"))
    state["story_richness"] = story_richness
    state["scene_budget"] = engine.scene_budget(richness=story_richness)
    state["richness_tier"] = engine.richness_tier(story_richness)
    if enhanced and not current_anchor and not engine.break_anchor.shattered_from(state):
        # —— M0：锚点缺失不再整回合阻断。救援顺序：同步蒸馏一卷 →
        # 原文摘录锚点兜底放行（origin=fallback，后台模型版 force 精化覆盖）。
        # 仅在无书目录/无凭据等极端情形才回退到旧的阻断提示。
        current_anchor = _rescue_anchor(state, target_chapter, api_key)
        if not current_anchor:
            state["scene_validation"] = {
                "length": None,
                "anchor": {
                    "valid": False,
                    "reason": "anchor_missing",
                    "target_chapter": target_chapter,
                    "anchor_mentioned": False,
                    "causal_marker": False,
                },
            }
            state["scene_gate"] = False
            state["scene_gate_reason"] = f"目标章节第 {target_chapter} 章缺少已验证剧情锚点，无法提交回合。"
            chatbot = chatbot + [{"role": "user", "content": message}, {"role": "assistant", "content": f"⚠️ 目标章节第 {target_chapter} 章缺少已验证剧情锚点，已阻断本回合；请等待锚点准备完成后重试。"}]
            yield _out_send(chatbot, state, msg_update=gr.update(value=""))
            return
    # 回合事务键与回滚白名单的单一来源：core/state_schema.py（Phase 2 收编）
    transaction_snapshot = {
        key: copy.deepcopy(state.get(key)) for key in state_schema.TRANSACTIONAL_KEYS
    }
    state["round"] = state.get("round", 0) + 1
    r = state["round"]
    if enhanced:
        state.update(target_chapter_state)

    llm_msg = message
    nemesis_rumor = None  # 强化分支内 pop 覆盖；基础模式保持 None（M2 LOG 渲染用）
    pre_turn_memory = copy.deepcopy(state.get("state_memory"))
    pre_turn_round = r
    _update_runtime_memory(state, message, round_no=r)
    if enhanced:
        injector = _lore_injector(state)
        lore_box = injector.inject(message + "\n" + str(state.get("history", [])[-2:]), round_no=r)
        state["lore"] = injector.snapshot()
        state["lore_hits"] = lore_box.get("ids", [])
        if lore_box.get("text"):
            llm_msg += "\n\n【本回合动态世界书】\n" + lore_box["text"]
        # 宿敌渗漏传闻：上一回合宿敌大手笔/资源耗尽产生的公开传闻，注入世界动态段。
        nemesis_rumor = state.pop("nemesis_rumor", None)
        if nemesis_rumor:
            llm_msg += "\n\n【传闻】" + str(nemesis_rumor)
        ripple = _ripple_block(state, message)
        active_members = _runtime_character_constraints(state, message)
        state["active_members"] = active_members
        style, choice, trope_hint = _trope_hint(message)
        state["last_style"] = style
        state["last_trope"] = trope_hint
        chapter_text = _chapter_text(state.get("chapter_index"), state.get("current_chapter", 1))
        llm_msg += "\n\n" + fe.pacing_hint(
            r, state.get("current_chapter", 1), state.get("chapter_round", 1),
            state.get("turn_budget", 0), chapter_text,
            _known_anchor_text(state, state.get("current_chapter", 1)))
        llm_msg += "\n\n" + engine.build_scene_budget_prompt(
            chapter=state.get("current_chapter", 1), round_no=r, richness=story_richness)
        llm_msg += "\n\n" + engine.build_interaction_constraint_block(
            active_members, action=message, relationship_state="按角色当前状态")
        anchor_context = _known_anchor_text(state, state.get("current_chapter", 1))
        compatibility = engine.runtime_mechanics.compatibility_k(
            message, current_anchor, style=style,
        )
        state["last_compatibility_k"] = compatibility
        llm_msg += "\n\n" + engine.build_anchor_constraint_block(
            anchor_context, action=message, compatibility=compatibility,
            convergence=convergence,
            reference_only=bool(engine.break_anchor.shattered_from(state)))
        llm_msg = _inject_skill_and_break_prompts(state, llm_msg)
        option_factors_block = engine.build_option_factors_block(
            engine.collect_option_factors(state))
        if option_factors_block:
            llm_msg += "\n\n" + option_factors_block
        llm_msg += (
            f"\n\n（系统提示：本回合涟漪 {ripple.level.name}（原始 {ripple.raw_level.name}），"
            f"{'通过' if ripple.allowed else '阻挡'}，有效积势 {ripple.effective_total}/{ripple.threshold}，"
            f"尝试压力累计 {ripple.attempt_total}，相容性 K={compatibility}。"
            "不得用橡皮筋抹除玩家已通过的成果；阻挡则改为代价、回响或推迟，不得假装没发生。"
            f"行动风格 {style} → 选项风格 {choice}。"
            + (f"可参考桥段：{trope_hint}。" if trope_hint else "")
            + "）"
        )
    else:
        llm_msg = _inject_skill_and_break_prompts(state, llm_msg)
        option_factors_block = engine.build_option_factors_block(
            engine.collect_option_factors(state))
        if option_factors_block:
            llm_msg += "\n\n（系统提示）" + option_factors_block
    state.pop("agent_meta", None)  # 每回合重置类Agent元数据，防止残留误导前端
    if state.get("nemesis") and r % 5 == 0:  # 宿敌：每 5 回合动向摘要
        llm_msg += (f"\n\n（系统提示：现在已是第 {r} 回合，请在本次回复末尾附上"
                    f"「宿敌动向摘要（第 {r-4}–{r} 回合）」。）")
    if r % 10 == 0:  # 每 10 回合：玩家评价（展示）+ 压缩存档（隐藏）
        llm_msg += "\n\n" + fe.eval_archive_message(r)

    history = state["history"] + [{"role": "user", "content": llm_msg}]
    chatbot = chatbot + [{"role": "user", "content": message},
                         {"role": "assistant", "content": "…"}]
    yield _out_send(chatbot, dict(state, history=history))
    try:
        client = fe.make_client(api_key, provider, base_url)
        # —— M5：铁律账本按相关性选择注入（未命中不注入，解决 relay 无限累积）。
        #     旧 wish_facts/relay_facts 在门面内惰性迁移，前端契约不变。 ——
        turn_system = state["system"]
        _memory = state.get("state_memory") if isinstance(state.get("state_memory"), dict) else {}
        _location = (_memory.get("location") or {}) if isinstance(_memory.get("location"), dict) else {}
        _directive_selection = directives_service.select_for_turn(
            state,
            anchor_words=engine.turn_blueprint.extract_anchor_terms(current_anchor or ""),
            present_members=state.get("active_members") or [],
            locations=[str(_location.get("name") or ""), str(_location.get("region") or "")],
        )
        _directives = str(_directive_selection.get("block") or "")
        state["directive_meta"] = {
            "selected": len(_directive_selection.get("selected") or []),
            "total": int(_directive_selection.get("total") or 0),
            "migrated": _directive_selection.get("migrated") or {},
        }
        if _directives and _directives not in turn_system:
            turn_system = turn_system + "\n\n" + _directives
        # —— M3：强化模式试卷管线（feature flag compose_mode）。
        #     管线内完成导演卷→段卷∥选项卷→逐空批改重填→组装；旧 on_send
        #     尾部结算/持久化/压缩原样复用。free 阶段返回 LEGACY 走旧单卷路径。
        assembled_result = None
        compose_mode = bool(enhanced and state.get("compose_mode"))
        option_factors = engine.collect_option_factors(state)
        factors_block = engine.build_option_factors_block(option_factors)
        context_tail = str(((state.get("history") or [{}])[-1] or {}).get("content") or "")[-600:]
        if compose_mode:
            _pipeline_result = turn_pipeline.run_turn(
                state, client, model, request_kwargs, provider,
                message=message, system_prompt=turn_system, context_blocks=llm_msg,
                active_members=state.get("active_members") or [],
                anchor_text=current_anchor, factors=option_factors,
                tier=int(state.get("paper_tier") or 0) or None,
            )
            if _pipeline_result != turn_pipeline.LEGACY:
                assembled_result = _pipeline_result
                state["scene_validation"] = dict(assembled_result.scene_validation or {})
                _format = (assembled_result.agent_meta or {}).get("format_gate") or {}
                state["scene_gate"] = bool(_format.get("valid", _format.get("ok", True)))
                state["scene_gate_reason"] = None if state["scene_gate"] else "组装正文格式门禁未通过"
                state["agent_meta"] = dict(assembled_result.agent_meta or {})
                state["options_source"] = assembled_result.options_source or "pipeline"
                state["paper_key"] = assembled_result.paper_key

        options_box: dict = {}
        if assembled_result is not None:
            options_box.update({"options": list(assembled_result.options or []),
                                "source": assembled_result.options_source or "pipeline"})
            options_thread = threading.Thread(target=lambda: None,
                                              name="options-gen-complete", daemon=True)
            options_thread.start()
        else:
            # M2 legacy 路径：选项与正文并行生成。
            def _generate_options_task():
                try:
                    options_box.update(options_service.generate_options(
                        client, model, request_kwargs, provider,
                        action=message, factors_block=factors_block,
                        context_tail=context_tail, factors=option_factors))
                except Exception as exc:  # noqa: BLE001 选项失败回退正文解析/代码合成
                    options_box["error"] = str(exc)

            options_thread = threading.Thread(target=_generate_options_task,
                                              name="options-gen", daemon=True)
            options_thread.start()

        acc = ""
        ub = {}
        if assembled_result is not None:
            acc = str(assembled_result.narrative or "")
            chatbot[-1] = {"role": "assistant", "content": acc or "…"}
            yield _out_send(chatbot, dict(state, history=history))
        else:
            for acc in fe.stream_reply_with_retry(client, model, turn_system, history, usage_box=ub,
                                                   extra_kwargs=request_kwargs, provider=provider,
                                                   thinking_mode=state["thinking_mode"],
                                                   thinking_param=state["thinking_param"]):
                chatbot[-1] = {"role": "assistant", "content": fe.strip_hidden(acc) or "…"}
                yield _out_send(chatbot, dict(state, history=history))
        _accum_tokens(state, ub,
                      est_in=int((len(state["system"]) + sum(len(h["content"]) for h in history)) / 1.5),
                      est_out=int(len(acc) / 1.5))
        state["progress"] = _mechanical_progress(state)
        clean_acc = fe.strip_hidden(acc)
        # —— 类 Agent 质量循环：draft → 自检 → 定向修订 → 重过机械门禁 ——
        # 只在强化模式且玩家开启时启用；自检失败不阻断主线，修订最多一轮。
        agent_mode = bool(assembled_result is None and enhanced and (state.get("agent_mode")
                                        or start_setting(state, "story_agent_mode")))
        length_text = engine.strip_options_block(clean_acc)
        if agent_mode:
            budget_probe = engine.validate_scene_length(length_text, richness=story_richness)
            interaction_probe = engine.validate_character_interaction(
                clean_acc, state.get("active_members") or [])
            anchor_probe = engine.validate_anchor_convergence(
                clean_acc, current_anchor, convergence=convergence)
            anchor_probe = _overlay_anchor_gate(state, state.get("current_chapter", 1), anchor_probe)
            findings = engine.agent_mode.machine_findings(
                budget_probe, interaction_probe, anchor_probe)
            # 本地零模型软伤检查：重复凑字/体量/点名提前预警。
            local_issues = engine.agent_mode.local_findings(
                clean_acc, state.get("scene_budget") or {},
                [str(m.get("name") or "") for m in (state.get("active_members") or [])])
            known = {item["kind"] for item in findings}
            findings = findings + [i for i in local_issues if i["kind"] not in known]
            if not findings:  # 机械+本地均无伤才做语义自检（因果/橡皮筋），省一次子调用
                try:
                    raw_check = _distill_model(client, model, engine.agent_mode.build_self_check_prompt(
                        draft=clean_acc, style=state.get("last_style") or "",
                        budget=state.get("scene_budget") or {},
                        active_names=[str(m.get("name") or "") for m in (state.get("active_members") or [])],
                        anchor_text=current_anchor, convergence=convergence,
                    ), request_kwargs, provider)
                    findings = engine.agent_mode.parse_issues(raw_check)
                except Exception:  # noqa: BLE001 自检故障视为通过；本地检查已兜底
                    findings = []
            for _attempt in range(engine.agent_mode.MAX_REVISIONS):
                if not findings:
                    break
                revise_msg = engine.agent_mode.build_revise_prompt(
                    findings, state.get("scene_budget") or {})
                rev_history = history + [{"role": "assistant", "content": acc},
                                         {"role": "user", "content": revise_msg}]
                chatbot[-1] = {"role": "assistant", "content": fe.strip_hidden(acc) + "\n\n（质检发现问题，正在定向修订…）"}
                yield _out_send(chatbot, dict(state, history=rev_history))
                acc = ""
                ub = {}
                for acc in fe.stream_reply_with_retry(client, model, turn_system, rev_history, usage_box=ub,
                                                      extra_kwargs=request_kwargs, provider=provider,
                                                      thinking_mode=state["thinking_mode"],
                                                      thinking_param=state["thinking_param"]):
                    chatbot[-1] = {"role": "assistant", "content": fe.strip_hidden(acc) or "…"}
                    yield _out_send(chatbot, dict(state, history=rev_history))
                _accum_tokens(state, ub,
                              est_in=int((len(state["system"]) + sum(len(h["content"]) for h in rev_history)) / 1.5),
                              est_out=int(len(acc) / 1.5))
                state["progress"] = _mechanical_progress(state)
                clean_acc = fe.strip_hidden(acc)
                # 修订稿重跑机械三校验；仍有硬伤则带着问题进入下方正式门禁（如实回滚）。
                regen_length_text = engine.strip_options_block(clean_acc)
                budget_check = engine.validate_scene_length(regen_length_text, richness=story_richness)
                interaction_check = engine.validate_character_interaction(
                    clean_acc, state.get("active_members") or [])
                anchor_check = engine.validate_anchor_convergence(
                    clean_acc, current_anchor, convergence=convergence)
                anchor_check = _overlay_anchor_gate(state, state.get("current_chapter", 1), anchor_check)
                residual = engine.agent_mode.machine_findings(
                    budget_check, interaction_check, anchor_check)
                state["agent_meta"] = {
                    "enabled": True,
                    "revised": True,
                    "issues": [dict(item) for item in findings],
                    "resolved": not residual,
                }
                findings = residual
            else:
                # 修订额度用尽仍有残伤：如实记录，交由下方正式门禁回滚。
                pass
            if "agent_meta" not in state:
                state["agent_meta"] = {"enabled": True, "revised": False, "issues": [], "resolved": True}
        if enhanced and assembled_result is None:
            budget_check = engine.validate_scene_length(length_text, richness=story_richness)
            interaction_check = engine.validate_character_interaction(
                clean_acc, state.get("active_members") or [])
            anchor_text = current_anchor
            anchor_check = engine.validate_anchor_convergence(
                clean_acc, anchor_text, convergence=convergence)
            anchor_check = _overlay_anchor_gate(state, state.get("current_chapter", 1), anchor_check)
            state["scene_validation"] = {
                "length": budget_check,
                "interaction": interaction_check,
                "anchor": anchor_check,
            }
            # 三段式锚点门禁：已发生入账→过；预算未尾声→允许铺垫；
            # 尾声（chapter_round >= budget）→必须收束。第一幕天然处于
            # 预算初期，随铺垫规则放行，无需单独特例。
            state["scene_gate"] = bool(
                budget_check.get("valid")
                and interaction_check.get("valid")
                and _anchor_gate_ok(state, state.get("current_chapter", 1), anchor_check,
                                    state.get("chapter_round", 1), state.get("turn_budget", 1))
            )
            # 不合格回复不得提交状态、存档或翻章；先自动定向重写一次，
            # 仍不合格才回滚并告知（避免玩家被一稿体量问题直接卡死回合）。
            if not state["scene_gate"]:
                reasons = _scene_gate_reasons(budget_check, interaction_check, anchor_check)
                regen_prompt = _regen_prompt_for_scene_gate(
                    reasons, budget_check, interaction_check, current_anchor)
                rev_history = history + [
                    {"role": "assistant", "content": fe.strip_hidden(acc)},
                    {"role": "user", "content": regen_prompt}]
                chatbot[-1] = {"role": "assistant",
                               "content": fe.strip_hidden(acc) + "\n\n（未过机械门禁，正在按约束自动重写…）"}
                yield _out_send(chatbot, dict(state, history=rev_history))
                acc = ""
                ub = {}
                for acc in fe.stream_reply_with_retry(client, model, turn_system, rev_history, usage_box=ub,
                                                      extra_kwargs=request_kwargs, provider=provider,
                                                      thinking_mode=state["thinking_mode"],
                                                      thinking_param=state["thinking_param"]):
                    chatbot[-1] = {"role": "assistant", "content": fe.strip_hidden(acc) or "…"}
                    yield _out_send(chatbot, dict(state, history=rev_history))
                _accum_tokens(state, ub,
                              est_in=int((len(state["system"]) + sum(len(h["content"]) for h in rev_history)) / 1.5),
                              est_out=int(len(acc) / 1.5))
                state["progress"] = _mechanical_progress(state)
                clean_acc = fe.strip_hidden(acc)
                # 重写稿重过机械三校验（字数按剥离选项块后的纯正文统计）。
                budget_check = engine.validate_scene_length(
                    engine.strip_options_block(clean_acc), richness=story_richness)
                interaction_check = engine.validate_character_interaction(
                    clean_acc, state.get("active_members") or [])
                anchor_check = engine.validate_anchor_convergence(
                    clean_acc, current_anchor, convergence=convergence)
                anchor_check = _overlay_anchor_gate(state, state.get("current_chapter", 1), anchor_check)
                state["scene_validation"] = {
                    "length": budget_check,
                    "interaction": interaction_check,
                    "anchor": anchor_check,
                }
                state["scene_gate"] = bool(
                    budget_check.get("valid")
                    and interaction_check.get("valid")
                    and _anchor_gate_ok(state, state.get("current_chapter", 1), anchor_check,
                                        state.get("chapter_round", 1), state.get("turn_budget", 1))
                )
                state["scene_regen"] = True
                # 软放行：定向重写后仍失败时允许两类弹性收束，防止死锁——
                # 1) 仅剩锚点未收束：按规则「锚点尽力发生、允许改变时间窗口」
                #    放行并标记延后，锚点事件由后续剧情自然补偿；
                # 2) 仅剩体量超界且幅度 ≤15%：模型字数控制精度有限（实测
                #    差 1–100 字被整轮拒绝卡死多回合），弹性放行并透明标记。
                if not state["scene_gate"]:
                    _residual = _scene_gate_reasons(budget_check, interaction_check, anchor_check)
                    if all("锚点" in item for item in _residual):
                        state["scene_gate"] = True
                        state["scene_gate_reason"] = "锚点延后收束（已定向重写仍未收束，剧情放行）"
                    elif (len(_residual) == 1
                          and "正文体量" in _residual[0]
                          and not budget_check.get("valid")):
                        _lo, _hi = int(budget_check.get("minimum") or 0), int(budget_check.get("maximum") or 0)
                        _chars = int(budget_check.get("chars") or 0)
                        if _hi and _lo and _lo * 0.85 <= _chars <= _hi * 1.15:
                            state["scene_gate"] = True
                            state["scene_gate_reason"] = (
                                f"体量弹性放行（正文 {_chars} 字，目标区间 {_lo}–{_hi}）")
            if not state["scene_gate"]:
                # —— 弹性门限（2026-08-30）：两稿（原稿+定向重写）仍不过时不再
                # 回滚整回合，改为代码层确定性修复后放行——
                # 1) 剥离模型残留的非叙事块（代码围栏/JSON/系统自检段，思考型
                #    模型偶发，占用体量且污染展示）；
                # 2) 修复选项数量（解析不足 6 个时代码合成补足，见下方选项段）。
                # 剧情事实由模型两稿背书，代码只修形式——重写稿即使体量/锚点
                # 仍不达标也放行，代价透明记录在 scene_gate_reason。
                _residual = _scene_gate_reasons(budget_check, interaction_check, anchor_check)
                _repaired = engine.elastic_repair(clean_acc, engine.parse_options(clean_acc))
                if _repaired["repaired_fences"]:
                    clean_acc = _repaired["narrative"]
                    acc = clean_acc
                    length_text = engine.strip_options_block(clean_acc)
                    budget_check = engine.validate_scene_length(length_text, richness=story_richness)
                state["scene_gate"] = True
                state["scene_gate_reason"] = (
                    "弹性放行（重写一稿仍"
                    + "；".join(_residual)
                    + "；已代码修复形式问题后放行）")
                state["elastic_repair"] = {
                    "repaired_fences": _repaired["repaired_fences"],
                    "repaired_options": _repaired["repaired_options"],
                }
        if enhanced:
            if (state.get("scene_validation") or {}).get("anchor", {}).get("status") == "fulfilled":
                _record_anchor_fulfilled(state, target_chapter, r)
            # 本回合全部校验硬性通过：清除上一轮的门禁原因残留。
            if not state.get("scene_gate_reason"):
                pass
            elif "放行" not in str(state.get("scene_gate_reason")):
                state["scene_gate_reason"] = None
        _commit_reply_memory(state, acc, message, round_no=r, lore_hits=state.get("lore_hits") or [])
        # —— M4：组装模式角色状态 patch（中台门面）。evidence 必须是最终正文
        #     精确子串，校验通过才写 state_memory.relationships；失败只记日志。
        if assembled_result is not None:
            _character_meta = character_service.generate_patch(
                state, client, model, request_kwargs, provider,
                narrative=clean_acc, active_members=state.get("active_members") or [],
                round_no=r)
            state.setdefault("agent_meta", {})["character_patch"] = _character_meta
            if not _character_meta.get("ok"):
                _wiring_log(state, f"角色状态 patch 失败已跳过：{_character_meta.get('error') or '未通过校验'}")
        # 活跃角色一句话行为摘要：legacy 路径本地提取；组装路径优先用角色 patch 摘要。
        try:
            _summaries = ((state.get("active_summaries") or {})
                          if assembled_result is not None
                          else _extract_member_summaries(
                              clean_acc, state.get("active_members") or []))
            if _summaries:
                state["active_summaries"] = _summaries
                state["state_panel"] = render_panel(state.get("state_memory")) + "\n" + \
                    "### 本回合在场角色\n" + "\n".join(
                        "- **%s**：%s" % (name, sentence)
                        for name, sentence in _summaries.items())
        except Exception as exc:  # noqa: BLE001 摘要失败不影响回合
            _wiring_log(state, f"活跃角色摘要提取失败已跳过：{exc}")
        try:
            engine.skill_drift.tick_after_action(state, message)
        except Exception as exc:  # noqa: BLE001
            _wiring_log(state, f"性格累计失败已跳过：{exc}")
        history = history + [{"role": "assistant", "content": acc}]
        # —— 回合管线：收束力结算 → 任务结算 → 碎锚结算 → 宿敌回合 → 上下文压缩 ——
        # 记忆已提交、存档未发生；各子环节独立防御，单个失败不拖垮主回合，
        # 模型调用只在对应功能激活时发生（active 任务 / 碎锚 active / 宿敌启用 / 每 10 回合）。
        _settle_convergence(state, r)
        _settle_quest(state, client, model, request_kwargs, provider, message, clean_acc, r)
        _settle_break_anchor(state, client, model, request_kwargs, provider, message, clean_acc, r)
        _run_nemesis_turn(state, client, model, request_kwargs, provider, r)
        history = _compress_context(state, client, model, request_kwargs, provider, history, r)
        if enhanced and not state.get("opening_started"):
            started = opening_flow.start_game(state.get("opening_state"))
            if started.get("ok"):
                state["opening_state"] = started["state"]
                state["opening_phase"] = started["state"].get("phase")
                state["opening_started"] = True
                state["opening_confirmed"] = True
        chatbot[-1] = {"role": "assistant", "content": fe.strip_hidden(acc)}
        # 历史必须先写回持久 state：流事件可携带临时副本，但重启恢复只读取 state。
        state["history"] = history
        yield _out_send(chatbot, state)

        # —— 运行日志：提取引擎日志段写入临时文件；缺失则程序补记（代码级保证不漏回合）——
        if enhanced:
            state["distill_status"] = "后台锚点蒸馏运行中"
            key = state.get("distill_key")
            if key and key not in _DISTILLERS and state.get("distill_enabled") is not False:
                # 读档/进程重启后蒸馏器不在注册表：按存档 distill_key 重建。
                # AnchorDistiller 磁盘感知（已有 anchors/NNNN.json 直接标记完成），
                # 只蒸馏缺失章——恢复前瞻蒸馏，绝不重蒸已完成章节。
                _start_background_distillation(state, client, model, sync_first=False)
                key = state.get("distill_key")
            if key and key in _DISTILLERS:
                distiller = _DISTILLERS[key]
                distiller.enqueue(state.get("current_chapter", 1), lookahead=6, lookback=-1,
                                  total=state.get("total_chapters"))
                # 保留式更新：不得覆盖 plot_summary 等既有蒸馏字段。
                _merge_distill_status(state, distiller.status())
            anchor_status = ((state.get("scene_validation") or {}).get("anchor") or {}).get("status")
            state["anchor_timeline"] = _anchor_timeline(state, anchor_status or "pending")
        log_path = state.get("log", "")
        # —— M2：引擎日志改由代码渲染（模型不再书写 <<<LOG>>>）；
        #     模型若仍按旧契约附带则优先采用（读档/旧会话兼容）。 ——
        entry = (str(assembled_result.log_line or "")
                 if assembled_result is not None else fe.extract_log(acc))
        if not entry:
            entry = _render_engine_log(state, message, clean_acc, r, rumor=nemesis_rumor)
        if entry:
            _append_log(log_path, f"\n## 第{r}回合\n- 玩家行动: {message}\n- 引擎日志: {entry}\n")
        else:
            excerpt = fe.strip_hidden(acc).replace("\n", " ")[:200]
            _append_log(log_path, f"\n## 第{r}回合\n- 玩家行动: {message}\n"
                                  f"- 程序补记: （引擎未按格式附日志）{excerpt}…\n")

        # —— 每 10 回合：提取压缩存档并压缩历史（降低后续 token 消耗、防止遗忘）——
        if r % 10 == 0:
            archive = fe.extract_archive(acc)
            if not archive:  # 引擎未给存档 → 隐式补发一次仅存档请求
                history = history + [{"role": "user", "content": (
                    "（系统指令：请仅补发压缩存档，格式 <<<ARCHIVE>>>...<<<END>>>，"
                    "不超过 500 字，概括截至目前一切关键既成事实。不要输出其它内容。）")}]
                acc_arc = ""
                ub_arc = {}
                for acc_arc in fe.stream_reply(client, model, state["system"], history, usage_box=ub_arc,
                                                provider=provider, thinking_mode=state.get("thinking_mode", "auto"),
                                                thinking_param=state.get("thinking_param", "")):
                    pass
                _accum_tokens(state, ub_arc,
                              est_in=int((len(state["system"]) + sum(len(h["content"]) for h in history)) / 1.5),
                              est_out=int(len(acc_arc) / 1.5))
                history = history + [{"role": "assistant", "content": acc_arc}]
                archive = fe.extract_archive(acc_arc)
            if archive:
                _append_log(log_path, f"- 十回合评价: 已随第{r}回合回复展示\n"
                                      f"- 压缩存档: {archive}\n")
                history = [
                    {"role": "user", "content": (
                        "（系统存档：以下为截至目前既成事实的压缩存档，后续回合以此为事实基础，"
                        "不得遗忘或篡改。）\n" + archive)},
                    {"role": "assistant", "content": "收到。我以该存档为既成事实基础继续推进。"},
                    {"role": "user", "content": llm_msg},
                    {"role": "assistant", "content": acc},
                ]
            else:
                _append_log(log_path, f"- 十回合评价: 已随第{r}回合回复展示（存档补发失败，本周期不压缩）\n")

        # —— M2：选项由结构化填空生成（与正文并行的线程），join 后注入组装。
        #     门面内已有回退链（正文残存解析→弹性合成）；此处仅在门面彻底
        #     失败且模型仍按旧契约输出选项块时走旧的纯代码修复保险丝。 ——
        options_thread.join(timeout=90)
        structured_options = (options_box.get("options") or []) if isinstance(options_box, dict) else []
        if structured_options:
            state["options_source"] = options_box.get("source", "model")
        final_display = fe.strip_hidden(acc)
        if not structured_options and not engine.options_ok(final_display):
            base = engine.truncate_partial_options(final_display)
            parsed_now = engine.parse_options(base)
            _rep = engine.repair_options(base, parsed_now)
            final_display = (base + "\n\n"
                             + engine.render_options_block(_rep)).strip() if _rep else base
            state["elastic_repair"] = {
                **(state.get("elastic_repair") or {}),
                "repaired_options": True,
            }
        # —— 选项结构化：选项块从叙事正文剥离写入 state["options"]，由前端按钮渲染；
        #    旧界面在正文末尾重挂规范化文本选项，保持可读。——
        narrative, options_block = _finalize_options(state, final_display, structured_options or None)
        chatbot[-1] = {"role": "assistant",
                       "content": (narrative + "\n\n" + options_block).strip() if options_block else narrative}
        # 十回合压缩可能改变 history；最终落盘和事件必须使用同一版本。
        state["history"] = history
        engine.persistence.save_state(state, root=fe.WRITABLE_DIR, start_params=state.get("start_params"),
                           session_id=str(state.get("session_id") or "") or None)
        yield _out_send(chatbot, state)
    except Exception as e:  # noqa: BLE001
        # M3 前置修复：回滚前先收拢选项生成线程——不 join 会泄漏在途模型调用，
        # 用户立刻重试时叠加多个并发额度占用。短超时后放弃等待（daemon 线程）。
        try:
            options_thread.join(timeout=10)
        except Exception:  # noqa: BLE001 线程未启动/已结束均忽略
            pass
        if enhanced:
            for key, value in transaction_snapshot.items():
                state[key] = copy.deepcopy(value)
            state["scene_gate"] = False
            state["scene_gate_reason"] = "模型服务调用失败，已回滚本回合运行状态。"
        chatbot[-1] = {"role": "assistant", "content": f"⚠️ 调用模型服务失败：{e}"}
        yield _out_send(chatbot, state)


_fetch_models_ui = golden_finger_panel._fetch_models_ui
_test_connection_ui = golden_finger_panel._test_connection_ui
_refresh_golden_fingers = golden_finger_panel._refresh_golden_fingers
_toggle_custom_gf = golden_finger_panel._toggle_custom_gf


def _propose_gf(text, work, persona_preset, difficulty, proposal):
    attempt = int((proposal or {}).get("attempt", 0) or 0) + 1
    if attempt > engine.MAX_ATTEMPTS:
        return proposal or {}, f"⚠️ 已用完 {engine.MAX_ATTEMPTS} 次提交机会，请改选推荐项或“无”。"
    try:
        result = engine.propose_custom(text, world=str(work or ""), persona=str(persona_preset or ""),
                                       difficulty=str(difficulty or ""), attempt=attempt)
    except ValueError as exc:
        return proposal or {}, f"⚠️ {exc}"
    spec = result["spec"]
    md = (f"**第 {attempt}/{engine.MAX_ATTEMPTS} 次提案（待确认）**\n\n"
          f"- 名称：{spec['name']}\n- 作用：{spec['effect']}\n- 作用域：{spec['scope']}\n"
          f"- 代价：{spec['cost']}\n- 冷却：{spec['cooldown']}\n- 限制：{spec['limits']}\n"
          f"- 适配：{spec['fit']}\n\n确认后才会写入开局；剩余重提次数 {result['remaining']}。")
    return result, md


def _confirm_gf(proposal):
    try:
        result = engine.confirm_custom(proposal or {}, True)
    except ValueError as exc:
        return proposal or {}, f"⚠️ {exc}"
    return result, f"✅ 已确认：{result['spec']['name']}。可以开始模拟。"


def _save_ui(state):
    if not state or not state.get("system"):
        return "⚠️ 当前没有可保存的对局。"
    if not str(state.get("mode") or "").startswith("强化"):
        return "⚠️ 普通模式不提供跨片段存档。"
    try:
        path = engine.persistence.save_state(state, root=fe.WRITABLE_DIR,
                                      start_params=state.get("start_params"))
        return f"已保存强化模式存档：{os.path.basename(path)}"
    except Exception as exc:
        return f"保存失败：{exc}"


def _load_ui(state):
    loaded = engine.persistence.load_state(root=fe.WRITABLE_DIR, current_state=state)
    if not loaded or not loaded.get("system"):
        return ([], state or {}, "⚠️ 未找到可恢复的存档。", _progress_html(state), _token_md(state),
                _token_title_md(state), (state or {}).get("state_panel", ""), gr.update(), gr.update(), gr.update(), gr.update())
    if loaded.get("state_memory"):
        loaded["state_panel"] = render_panel(loaded["state_memory"])
    if str(loaded.get("mode") or "").startswith("强化"):
        loaded.setdefault("plot_ready", bool(loaded.get("distill", {}).get("plot_summary")) and bool(loaded.get("chapter_index")))
        loaded.setdefault("gf_stage", "confirmed" if loaded.get("gf_confirmed") else "pending")
        loaded.setdefault("gf_confirmed", loaded.get("gf_stage") == "confirmed")
        loaded.setdefault("chapter_index", loaded.get("chapter_index"))
    else:
        loaded.setdefault("plot_ready", True)
        loaded.setdefault("gf_stage", "confirmed")
        loaded.setdefault("gf_confirmed", True)
        loaded.setdefault("chapter_index", loaded.get("chapter_index"))
    history = loaded.get("history", [])
    chatbot = []
    for item in history:
        if item.get("role") in ("user", "assistant"):
            chatbot.append({"role": item["role"], "content": fe.strip_hidden(item.get("content", ""))})
    return (chatbot, loaded, "已读取 latest 存档。API Key 未写入存档，请确认当前配置。",
            _progress_html(loaded), _token_md(loaded), _token_title_md(loaded), loaded.get("state_panel", ""),
            gr.update(visible=False), gr.update(visible=True), gr.update(visible=True), gr.update(visible=True))


def build_app():
    """历史 Gradio 界面（源码可选）。生产 FastAPI/Vue 与 Windows 包不调用。"""
    # 动态导入避免 PyInstaller 静态收编整套 Gradio 可选生态；需要旧界面时
    # 安装 requirements-legacy.txt 后直接在源码环境调用即可。
    from importlib import import_module

    gr = import_module("gradio")
    with gr.Blocks(
        title="书中行 · 命运引擎",
        css=ui_theme.gradio_css(),
        fill_width=True,
    ) as demo:
        gr.HTML(
            "<header class='fe-app-header'>"
            "<div><p class='fe-kicker'>INTERACTIVE FICTION WORKBENCH</p>"
            "<h1>书中行</h1><p>命运引擎 · 长篇叙事控制台</p></div>"
            f"<div class='fe-library-count'><strong>{len(WORKS)}</strong><span>作品档案</span></div>"
            "</header>"
        )
        state = gr.State({"system": "", "history": []})

        with gr.Row(elem_classes=["fe-workbench"]):
            with gr.Column(scale=4, min_width=340, elem_classes=["fe-config-panel"]) as left_col:
                gr.Markdown("### 开局控制台")
                with gr.Accordion("模型与连接", open=False, elem_classes=["fe-settings-accordion"]):
                    provider = gr.Dropdown(fe.PROVIDER_CHOICES, value=_INITIAL_PROVIDER, label="模型提供商")
                    base_url = gr.Textbox(label="Base URL", value=_INITIAL_PROFILE.get("base_url") or fe.provider_config(_INITIAL_PROVIDER)["base_url"],
                                          placeholder="OpenAI 兼容 API 地址")
                    remember = gr.Checkbox(label="记住非敏感设置（API Key 不保存）", value=False)
                    with gr.Row():
                        _initial_models = fe.provider_config(_INITIAL_PROVIDER).get("models", [])
                        model = gr.Dropdown(_initial_models,
                                            value=_INITIAL_PROFILE.get("model") or (_initial_models[0] if _initial_models else None),
                                            label="模型", scale=3, allow_custom_value=True)
                        fetch_btn = gr.Button("拉取模型", scale=1)
                    api_key = gr.Textbox(
                        label="API Key（仅当前进程使用）", type="password", value=_provider_key(_INITIAL_PROVIDER),
                        placeholder="仅用于调用所选服务，不会上传他处")
                    token_info = gr.Markdown("**Token**：未开始")
                    with gr.Row():
                        test_btn = gr.Button("测试连接")
                        connection_status = gr.Markdown("")
                    thinking_mode = gr.Dropdown(
                        [("自动", "auto"), ("关闭", "off"), ("低", "low"), ("中", "mid"), ("高", "high")],
                        value="auto", label="思考强度")
                    thinking_param = gr.Textbox(label="思考适配参数（可选）",
                                                placeholder="例如 reasoning_effort=high；留空使用默认")
                mode = gr.Radio(["基础模式", "强化模式"], value="基础模式",
                                label="叙事模式", elem_classes=["fe-mode-switch"])

                gr.Markdown("**作品来源**")
                work = gr.Dropdown(
                    WORKS,
                    value=DEFAULT_WORK,
                    label=f"作品库（{len(WORKS)} 项，仅基础模式）",
                    info="强化模式不使用作品档案，必须上传可切章的完整 TXT 原著。",
                    filterable=True,
                )
                novel_file = gr.File(
                    label="上传 TXT 原著（强化模式必填）",
                    file_types=[".txt"],
                    type="filepath",
                )
                work_source_note = gr.Markdown(
                    "基础模式可选择作品库，也可上传 TXT 覆盖作品库。"
                )
                fragment = gr.Textbox(label="普通模式具体片段（10–30 回合内收束）", lines=4,
                                      placeholder="普通模式填写要进入的具体片段、场景或冲突；留空则由系统给出候选。")
                distill_enabled = gr.Checkbox(label="开局后后台蒸馏锚点（仅强化模式）", value=True, visible=False)

                gr.Markdown("**穿越设定**")
                role = gr.Textbox(label="穿越成为角色",
                                  placeholder="留空：系统给出 2–3 个候选并与你确认后再定")
                timepoint = gr.Textbox(label="穿越时间点 / 锚点",
                                       placeholder="留空则为故事开篇（强化模式固定为故事开篇）")
                mode.change(
                    lambda value: gr.update(value="故事开篇", interactive=False)
                    if str(value).startswith("强化") else gr.update(interactive=True),
                    mode,
                    timepoint,
                )
                mode.change(
                    lambda value: (
                        gr.update(value=None, interactive=False)
                        if str(value).startswith("强化")
                        else gr.update(value=DEFAULT_WORK, interactive=True),
                        "强化模式已锁定为完整原著流程：请上传可切章的 TXT，作品库不可用。"
                        if str(value).startswith("强化")
                        else "基础模式可选择作品库，也可上传 TXT 覆盖作品库。",
                    ),
                    mode,
                    [work, work_source_note],
                )
                difficulty = gr.Dropdown(fe.DIFFICULTIES, value=fe.DIFFICULTIES[3],
                                         label="难度 D1–D9")
                _gf_init = engine.choices(DEFAULT_WORK or "", DEFAULT_PERSONA, fe.DIFFICULTIES[3])
                gf = gr.Dropdown(_gf_init, value=_gf_init[0],
                                 label="金手指（5 项推荐 + 无 + 自定义，随作品/性格/难度刷新）",
                                 allow_custom_value=False)
                gf_refresh = gr.Button("刷新推荐", size="sm")
                with gr.Group(visible=False) as gf_custom_group:
                    gf_custom_text = gr.Textbox(label="描述你想要的金手指（由系统正式化后再确认）",
                                                lines=2, placeholder="例如：能记住已经读过的所有情报，但每次调用都会头痛…")
                    with gr.Row():
                        gf_propose_btn = gr.Button("生成正式提案")
                        gf_confirm_btn = gr.Button("确认采用", variant="primary")
                    gf_proposal_md = gr.Markdown("尚未提交自定义金手指。最多可提交 3 次。")
                gf_custom = gr.State({})

                gr.Markdown("**魂穿性格**（角色模型 / 下拉 / 自定义 或 上传 MD）")
                persona_preset = gr.Dropdown(PERSONA_CHOICES, value=DEFAULT_PERSONA,
                                             label="性格预设 / 角色模型（可选自定义）", allow_custom_value=True)
                persona_custom = gr.Textbox(label="自定义性格（选『自定义』时生效）", lines=2,
                                            placeholder="描述穿越者性格、口癖、目标、底线…")
                persona_file = gr.File(label="拖入性格 MD 文件（上传后覆盖上方性格）",
                                       file_types=[".md", ".markdown", ".txt"], type="filepath")

                status = gr.Markdown("")
                with gr.Group(visible=False) as mechanism_group:
                    gr.Markdown("**伙伴 / 女主机制**（仅强化模式可用）")
                    gr.Markdown("先填写目标数量，再按顺序配置。每个角色保存稳定槽位 ID、技能来源、蒸馏角色卡和 1–9 档剧情参与度。")
                    companion_count = gr.Number(value=0, precision=0, minimum=0, label="伙伴目标数量", interactive=True)
                    companion_progress = gr.Markdown("伙伴目标数量为 0，无需配置。")
                    companion_rows = gr.State([])
                    with gr.Group(visible=False, elem_classes=["fe-roster-editor"]) as companion_editor:
                        gr.Markdown("**配置当前伙伴**")
                        companion_model = gr.Dropdown(
                            _character_pool_choices("伙伴"), value=None,
                            label="蒸馏角色模型（可搜索，可留空自定义）",
                            filterable=True, allow_custom_value=False,
                        )
                        companion_name = gr.Textbox(label="伙伴名称")
                        companion_skill = gr.Dropdown(SKILL_PRESETS, value="按设定推断", label="skill（单行）", allow_custom_value=True)
                        companion_custom_skill = gr.Textbox(label="自定义 skill（可选，优先显示）", max_lines=1)
                        companion_upload = gr.File(label="上传 skill（可选）", file_types=[".md", ".txt"], type="filepath")
                        companion_background = gr.Textbox(label="故事背景", lines=2)
                        with gr.Row():
                            companion_participation = gr.Slider(1, 9, value=1, step=1, label="剧情参与度 1–9")
                            companion_power = gr.Dropdown(POWER_CHOICES, value=-1, label="实力/影响力")
                        companion_card = gr.HTML(_card_html("", "伙伴", "按设定推断", "", 1, -1), elem_classes=["fe-card-preview"])
                        companion_add = gr.Button("伙伴名册已完成", variant="primary", interactive=False)
                    companion_list = gr.Markdown("尚未加入伙伴。")

                    heroine_mode = gr.Radio(["单女主", "多女主"], value="单女主", label="女主类型")
                    heroine_pool_note = gr.Markdown(
                        f"单女主池：目标数量最多 1 位；可选 {len(_heroine_pool_choices('单女主'))} 个蒸馏模型。")
                    heroine_count = gr.Number(value=0, precision=0, minimum=0, maximum=1,
                                              label="女主目标数量", interactive=True)
                    heroine_progress = gr.Markdown("女主目标数量为 0，无需配置。")
                    heroine_rows = gr.State([])
                    with gr.Group(visible=False, elem_classes=["fe-roster-editor"]) as heroine_editor:
                        gr.Markdown("**配置当前女主**")
                        heroine_pick = gr.Dropdown(
                            _heroine_pool_choices("单女主"), value=None,
                            label="蒸馏角色模型（可搜索，可留空自定义）",
                            filterable=True, allow_custom_value=False,
                        )
                        heroine_name = gr.Textbox(label="女主名称")
                        heroine_skill = gr.Dropdown(SKILL_PRESETS, value="按设定推断", label="skill（单行）", allow_custom_value=True)
                        heroine_custom_skill = gr.Textbox(label="自定义 skill（可选，优先显示）", max_lines=1)
                        heroine_upload = gr.File(label="上传 skill（可选）", file_types=[".md", ".txt"], type="filepath")
                        heroine_background = gr.Textbox(label="故事背景", lines=2)
                        with gr.Row():
                            heroine_participation = gr.Slider(1, 9, value=1, step=1, label="剧情参与度 1–9")
                            heroine_power = gr.Dropdown(POWER_CHOICES, value=-1, label="实力/影响力")
                        heroine_card = gr.HTML(_card_html("", "女主", "按设定推断", "", 1, -1), elem_classes=["fe-card-preview"])
                        heroine_add = gr.Button("女主名册已完成", variant="primary", interactive=False)
                    heroine_list = gr.Markdown("尚未加入女主。")

                    _companion_editor_inputs = [companion_name, companion_skill, companion_custom_skill, companion_upload, companion_background, companion_power, companion_participation]
                    _heroine_editor_inputs = [heroine_name, heroine_skill, heroine_custom_skill, heroine_upload, heroine_background, heroine_power, heroine_participation]
                    companion_count.change(
                        lambda target, rows, mode_value: _roster_target_updates(target, rows, "伙伴", mode_value),
                        [companion_count, companion_rows, heroine_mode],
                        [companion_count, companion_progress, companion_editor, companion_add],
                    )
                    heroine_count.change(
                        lambda target, rows, mode_value: _roster_target_updates(target, rows, "女主", mode_value),
                        [heroine_count, heroine_rows, heroine_mode],
                        [heroine_count, heroine_progress, heroine_editor, heroine_add],
                    )
                    companion_model.change(
                        lambda picked: _character_model_updates(picked, "伙伴", "单女主"),
                        companion_model,
                        [companion_name, companion_skill, companion_background, companion_card],
                    )
                    heroine_pick.change(
                        lambda picked, mode_value: _character_model_updates(picked, "女主", mode_value),
                        [heroine_pick, heroine_mode],
                        [heroine_name, heroine_skill, heroine_background, heroine_card],
                    )
                    companion_add.click(
                        lambda rows, target, model_pick, name, skill, custom, upload, background, power, participation, mode_value: _append_roster_slot_ui(
                            rows, target, model_pick, name, skill, custom, upload, background, power, participation, "伙伴", mode_value),
                        [companion_rows, companion_count, companion_model] + _companion_editor_inputs + [heroine_mode],
                        [companion_rows, companion_list, status, companion_card, companion_model]
                        + _companion_editor_inputs
                        + [companion_count, companion_progress, companion_editor, companion_add],
                    )
                    heroine_add.click(
                        lambda rows, target, model_pick, name, skill, custom, upload, background, power, participation, mode_value: _append_roster_slot_ui(
                            rows, target, model_pick, name, skill, custom, upload, background, power, participation, "女主", mode_value),
                        [heroine_rows, heroine_count, heroine_pick] + _heroine_editor_inputs + [heroine_mode],
                        [heroine_rows, heroine_list, status, heroine_card, heroine_pick]
                        + _heroine_editor_inputs
                        + [heroine_count, heroine_progress, heroine_editor, heroine_add],
                    )
                    heroine_mode.change(
                        _on_heroine_mode_change,
                        heroine_mode,
                        [heroine_pick, heroine_pool_note, heroine_rows, heroine_count,
                         heroine_progress, heroine_editor, heroine_add],
                    )
                    heroine_mode.change(
                        lambda value: gr.update(maximum=1 if value == "单女主" else None),
                        heroine_mode,
                        heroine_count,
                    )

                    gr.Markdown("**宿敌机制**（仅强化模式可用）")
                    enable_nemesis = gr.Checkbox(label="开启宿敌机制", value=False)
                    with gr.Group(visible=False) as nemesis_group:
                        nemesis_select = gr.Dropdown(
                            PERSONA_CHOICES, value=DEFAULT_PERSONA,
                            label="宿敌（从角色模型库选择）", allow_custom_value=True)
                        nemesis_file = gr.File(label="或上传宿敌 MD（上传后覆盖上方选择）",
                                               file_types=[".md", ".markdown", ".txt"], type="filepath")
                    enable_nemesis.change(lambda on: gr.update(visible=bool(on)),
                                          enable_nemesis, nemesis_group)

                mode.change(
                    lambda value: (
                        gr.update(visible=str(value).startswith("强化")),
                        gr.update(visible=not str(value).startswith("强化")),
                        gr.update(visible=str(value).startswith("强化")),
                    ),
                    mode, [mechanism_group, fragment, distill_enabled])

                with gr.Row():
                    start_btn = gr.Button("开始模拟 / 重置", variant="primary")
                    save_btn = gr.Button("保存存档", visible=False)
                    load_btn = gr.Button("读取存档")

            with gr.Column(scale=8, visible=False, elem_classes=["fe-story-panel"]) as chat_col:
                chat_title = gr.Markdown("### 命运引擎")
                import inspect as _inspect
                _cb_kwargs = {"height": 620, "label": "命运引擎", "autoscroll": False}
                if "type" in _inspect.signature(gr.Chatbot.__init__).parameters:
                    _cb_kwargs["type"] = "messages"
                if "autoscroll" not in _inspect.signature(gr.Chatbot.__init__).parameters:
                    _cb_kwargs.pop("autoscroll", None)
                chatbot = gr.Chatbot(**_cb_kwargs)
                msg = gr.Textbox(label="你的行动 / 选择",
                                 placeholder="输入你的行动、对话或选择编号，回车发送…", lines=2)
                send_btn = gr.Button("发送", variant="primary")
                reopen_btn = gr.Button("⚙️ 修改设定 / 重新开局", visible=False)
                progress_bar = gr.HTML(value=_progress_html(None))
                state_panel = gr.Markdown("### 状态记忆面板\n- 尚未开始")

        _gf_sources = [work, novel_file, persona_preset, persona_custom, difficulty]
        gf_refresh.click(_refresh_golden_fingers, _gf_sources, gf)
        for _component in (work, difficulty, persona_preset):
            _component.change(_refresh_golden_fingers, _gf_sources, gf)
        gf.change(_toggle_custom_gf, gf, gf_custom_group)
        gf_propose_btn.click(_propose_gf,
                             [gf_custom_text, work, persona_preset, difficulty, gf_custom],
                             [gf_custom, gf_proposal_md])
        gf_confirm_btn.click(_confirm_gf, gf_custom, [gf_custom, gf_proposal_md])

        provider.change(_on_provider_change, provider, [base_url, model, api_key])
        fetch_btn.click(_fetch_models_ui, [provider, base_url, api_key], [model, connection_status])
        test_btn.click(_test_connection_ui, [provider, base_url, api_key, model], connection_status)
        start_btn.click(
            on_start,
            [provider, base_url, api_key, remember, model, thinking_mode, thinking_param,
             mode, work, novel_file, fragment, role, timepoint, difficulty, gf, gf_custom, persona_preset,
             persona_custom, persona_file, distill_enabled, companion_rows, heroine_rows,
             companion_count, heroine_count, heroine_mode, enable_nemesis, nemesis_select, nemesis_file],
            [chatbot, state, status, progress_bar, token_info, chat_title, state_panel, left_col, reopen_btn, chat_col, save_btn])
        reopen_btn.click(
            lambda: (gr.update(visible=True), gr.update(visible=False)),
            None, [left_col, reopen_btn])
        send_inputs = [provider, base_url, api_key, model, thinking_mode, thinking_param,
                       msg, chatbot, state]
        send_btn.click(on_send, send_inputs,
                       [chatbot, msg, state, progress_bar, token_info, chat_title, state_panel])
        msg.submit(on_send, send_inputs,
                   [chatbot, msg, state, progress_bar, token_info, chat_title, state_panel])
        save_btn.click(_save_ui, state, status)
        load_btn.click(_load_ui, state,
                       [chatbot, state, status, progress_bar, token_info, chat_title, state_panel, left_col, reopen_btn, chat_col, save_btn])
    return demo


def launch_app():
    """启动界面。支持环境变量配置 host/port，并自动打开浏览器。"""
    demo = build_app()
    host = os.environ.get("FE_HOST", "127.0.0.1")
    kwargs = dict(server_name=host, inbrowser=True, show_error=True)
    port_env = os.environ.get("FE_PORT")
    if port_env:
        kwargs["server_port"] = int(port_env)   # 显式指定端口；不指定则 Gradio 自动寻找空闲端口
    demo.launch(**kwargs)


if __name__ == "__main__":
    launch_app()
