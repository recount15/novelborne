# -*- coding: utf-8 -*-
"""ask 端点业务服务（Phase 3b 自 core/server.py 抽出）。

非流式规则问答与作弊码状态机的全部业务逻辑：
三愿武装/原子扣费、永久通路「确认-激活-碎锚」编排、增补通道、
规则问答 prompt 装配与回答脱敏、作弊态即时落盘策略。

HTTP 层（server.py 的 ask 端点）只做参数解包、会话锁与异常→状态码
映射；本模块不感知 FastAPI/HTTP，通过两个领域异常上抛语义：
- ``AskClientError``  → 端点映射 HTTP 400（参数/客户端初始化问题）
- ``AskUpstreamError`` → 端点映射 HTTP 502（模型调用/空返回问题）

对外响应字段名与中文文案逐字保持不变（前端依赖）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core import engine
from core import fate_engine as fe
from core.engine.distill import distill_model
from core.services import registries

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_RULE_FILES = ("state_memory.md", "golden_finger.md", "worldbook.md")
_RULE_CHAR_BUDGET = 3000
_STATE_CHAR_BUDGET = 2000


class AskClientError(Exception):
    """请求侧错误：端点映射为 HTTP 400。"""


class AskUpstreamError(Exception):
    """模型调用侧错误：端点映射为 HTTP 502。"""


def _rules_corpus(enhanced: bool) -> str:
    """组装问答语料：运行时规则 + rules 目录其余规则文档，逐份截断。"""
    parts = []
    base = (fe.load_runtime_rules(enhanced) or "").strip()
    if base:
        parts.append(base[:_RULE_CHAR_BUDGET])
    for name in _RULE_FILES:
        path = PROJECT_ROOT / "assets" / "rules" / name
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(f"# {name}\n" + text[:_RULE_CHAR_BUDGET])
    return "\n\n".join(parts)


def _state_summary(state: dict[str, Any]) -> str:
    """对局公开状态摘要：只取问答需要的字段，绝不含 system prompt 与凭据。"""
    start_params = state.get("start_params") if isinstance(state.get("start_params"), dict) else {}
    memory = state.get("state_memory") if isinstance(state.get("state_memory"), dict) else {}
    summary = {
        "mode": state.get("mode"),
        "round": state.get("round"),
        "current_chapter": state.get("current_chapter"),
        "total_chapters": state.get("total_chapters"),
        "difficulty": start_params.get("difficulty"),
        "convergence": state.get("convergence") or start_params.get("convergence"),
        "persona": state.get("persona") or start_params.get("persona"),
        "golden_finger": ((memory.get("abilities") or {}).get("golden_finger") or {}).get("name"),
        "location": (memory.get("location") or {}).get("name"),
        "goals": (memory.get("goals") or {}).get("current"),
        "last_ripple": state.get("last_ripple"),
        "anchor_timeline": state.get("anchor_timeline"),
    }
    return json.dumps(summary, ensure_ascii=False, default=str)[:_STATE_CHAR_BUDGET]


def handle_ask(state: dict[str, Any], question: str,
               api_key: str, session_id: str) -> dict[str, Any]:
    """ask 端点主流程：作弊码状态机 + 规则问答，返回可 JSON 化的响应 dict。

    ``state`` 必须来自已持有会话锁的 session（锁与释放都在端点层）；
    同步执行，期间可能在状态变更后多次落盘存档。
    """
    provider = state.get("provider") or "deepseek"
    config = fe.provider_config(provider, state.get("base_url"))
    model = state.get("model") or (config.get("models") or [fe.DEFAULT_MODEL])[0]

    # 惰性初始化模型客户端：作弊码的确认/武装/激活是纯状态操作，不依赖
    # 模型凭据；只有真正要调模型的分支（愿望实现/增补生成/规则问答）才创建。
    _client_holder: dict[str, Any] = {}

    def _client():
        if "client" not in _client_holder:
            try:
                _client_holder["client"] = fe.make_client(
                    api_key, provider, state.get("base_url") or config["base_url"])
            except Exception as exc:  # noqa: BLE001
                raise AskClientError(f"模型客户端初始化失败：{exc}") from exc
        return _client_holder["client"]

    question = question.strip()
    # 作弊码专属通路：只有 ask 通路允许触发特权，其余输入一律按普通问答处理。
    # 状态修改（武装/消耗/激活/增补）后立即落盘：刷新页面或重启服务按最新存档
    # 回填 state，三愿计数与通路状态不会回退（按局绑定、读档不刷新）。
    def _persist_cheat_state() -> None:
        try:
            engine.persistence.save_state(
                state, save_id="latest", root=fe.WRITABLE_DIR,
                start_params=state.get("start_params"),
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001  落盘失败不阻断交互，下回合仍会落盘
            pass

    if engine.cheat_code.is_relay_code(question):
        if engine.cheat_code.is_relay_active(state):
            return {"answer": "通路已处于接通状态：本局无法关闭。", "relay_activated": True}
        # 不可逆操作必须先确认：接通后无法撤销，仅对本局生效。
        engine.cheat_code.relay_request_confirm(state)
        _persist_cheat_state()
        return {
            "answer": (
                "检测到永久通路指令。接通前请确认：\n"
                "· 该通路仅对本局生效，一经接通无法撤销、无法关闭；\n"
                "· 接通后问答框将升级为「增补通道」：此后输入的每一句话都会成为"
                "代码级注入的「玩家增补铁律」，永久影响本局后续每一回合剧情；\n"
                "· 选项不再只能单选——可以勾选多个选项，并在增补框写入自由内容，"
                "一并作为本回合行动；\n"
                "· 接通即碎锚：本局主线锚点全部失效，剧情不再收束（此后由过往回合、"
                "角色性格与世界观自由生成），后续锚点蒸馏同步停止；\n"
                "· 增补优先级高于一切剧情设定、低于游戏机制（回合/难度/收束/任务/"
                "积势/碎锚等不可改）。\n"
                "回复「确认」接通，回复「取消」放弃。"
            ),
            "relay_confirm_pending": True,
        }
    if engine.cheat_code.is_relay_confirm_pending(state):
        if engine.cheat_code.is_confirm_text(question):
            engine.cheat_code.relay_activate(state)
            _shatter = engine.break_anchor.shatter_now(state)
            try:
                registries.distillers.stop_all()
            except Exception:  # noqa: BLE001  蒸馏停止失败不阻断通路激活
                pass
            state["distill_status"] = "锚点已全部失效，后续蒸馏停止"
            _persist_cheat_state()
            return {
                "answer": (
                    "通路已接通：本局问答框永久升级为「增补通道」。\n"
                    "· 此后在此输入的每一句话都会成为代码级注入的「玩家增补铁律」，"
                    "永久影响后续每一回合剧情；\n"
                    "· 选项不再只能单选——可以勾选多个选项，并在增补框写入自由内容，"
                    "一并作为本回合行动；\n"
                    "· 碎锚已生效：主线锚点全部失效、剧情不再收束，后续锚点蒸馏已停止"
                    "（锚点与档案此后仅供参考）；\n"
                    "· 增补优先级高于一切剧情设定、低于游戏机制（回合/难度/收束/任务/"
                    "积势/碎锚等不可改）。"
                ),
                "relay_activated": True,
                "anchors_shattered_from": _shatter.get("anchors_shattered_from", 0),
            }
        if engine.cheat_code.is_cancel_text(question):
            engine.cheat_code.relay_cancel_confirm(state)
            _persist_cheat_state()
            return {"answer": "已取消：永久通路保持关闭。", "relay_confirm_pending": False}
        return {
            "answer": "永久通路等待确认：该操作不可撤销且仅对本局生效。回复「确认」接通，或「取消」放弃。",
            "relay_confirm_pending": True,
        }
    if engine.cheat_code.is_arm_code(question):
        engine.cheat_code.arm(state)
        _persist_cheat_state()
        remaining = engine.cheat_code.remaining_wishes(state)
        if remaining <= 0:
            return {"answer": "三愿已全部耗尽：本局无法再许愿。", "wish_armed": False}
        return {
            "answer": (
                f"三愿通路已开启（剩余 {remaining} 次）：请直接说出你的愿望。\n"
                "· 愿望将作为「外部设定铁律」实现：修改世界观与剧情，无代价、"
                "绝对优先（高于一切剧情设定，低于游戏机制）；\n"
                "· 试图修改游戏机制的内容（回合/难度/收束/任务/积势/碎锚/涟漪等）"
                "会被代码层剥离并明示，其余部分照常生效；\n"
                "· 每次输入一个愿望，实现即消耗一次次数。"
            ),
            "wish_armed": True, "wish_remaining": remaining,
        }
    if engine.cheat_code.is_armed(state):
        try:
            clean, rejected = engine.cheat_code.sanitize_wish(question)
        except ValueError as exc:
            # 空/超长愿望：拒绝且不消耗次数。
            raise AskClientError(str(exc)) from exc
        if not clean:
            raise AskClientError(
                "愿望在剥离机制诉求后为空——铁律只能修改世界观与剧情，"
                "不能修改游戏机制。")
        # 三愿原子性：先调模型生成实现文本，成功才扣次数——失败/空返回
        # 一律 502 且计数不变、武装保持，玩家可原样重试。
        wish_prompt = engine.cheat_code.build_wish_prompt(clean)
        try:
            granted = distill_model(
                _client(), model, wish_prompt, state.get("request_kwargs"), provider)
        except Exception as exc:  # noqa: BLE001
            raise AskUpstreamError(f"愿望实现失败：{exc}") from exc
        granted = str(granted or "").strip()
        if not granted:
            raise AskUpstreamError(
                "愿望实现失败：模型返回为空。本次不消耗次数，请重试。")
        if api_key and api_key in granted:
            granted = granted.replace(api_key, "***")
        # 原子扣费点：生成成功才消耗次数（consume 同时解除武装），立即落盘。
        engine.cheat_code.consume(state)
        _persist_cheat_state()
        remaining = engine.cheat_code.remaining_wishes(state)
        # 代码保证实现：愿望与实现文本落进 state，每回合注入为铁律。
        state.setdefault("wish_facts", []).append(
            {"wish": clean, "granted": granted, "round": state.get("round", 0)})
        history = state.setdefault("history", [])
        if isinstance(history, list):
            history.append({"role": "assistant", "content": "[外部设定铁律] " + granted})
        _persist_cheat_state()
        notice = f"[铁律已生效｜剩余愿望 {remaining} 次]"
        if rejected:
            notice += "\n[机制护栏] 以下诉求试图修改游戏机制，已被剥离未生效：" + "；".join(rejected)
        return {"answer": notice + "\n\n" + granted, "wish_granted": True,
                "wish_remaining": remaining, "mechanism_rejected": rejected}
    # 永久增补通路：激活后问答框不再是规则答疑，而是「玩家增补铁律」通道。
    if engine.cheat_code.is_relay_active(state):
        try:
            clean, rejected = engine.cheat_code.sanitize_wish(question)
        except ValueError as exc:
            raise AskClientError(str(exc)) from exc
        if not clean:
            raise AskClientError(
                "增补在剥离机制诉求后为空——增补只能修改世界观与剧情，"
                "不能修改游戏机制。")
        relay_prompt = (
            "【玩家增补铁律】你是《书中行》命运引擎的设定执行者。玩家通过永久通路"
            "注入增补。把玩家的输入改写为一条简洁、自洽、可直接注入后续每一回合的"
            "「既成事实」设定（一句话到三句话），不要解释过程。\n"
            "增补性质：修改世界观与剧情；无代价；优先级高于一切剧情设定、低于"
            "游戏机制；不得被剧情否定或回收。\n\n"
            f"玩家增补：{clean}"
        )
        try:
            fact_text = distill_model(
                _client(), model, relay_prompt, state.get("request_kwargs"), provider)
        except Exception as exc:  # noqa: BLE001
            raise AskUpstreamError(f"增补生成失败：{exc}") from exc
        fact_text = str(fact_text or "").strip()
        if api_key and api_key in fact_text:
            fact_text = fact_text.replace(api_key, "***")
        engine.cheat_code.record_relay_fact(
            state, {"fact": clean, "text": fact_text, "round": state.get("round", 0)})
        _persist_cheat_state()
        notice = f"[增补铁律已生效｜累计 {len(engine.cheat_code.relay_facts(state))} 条]"
        if rejected:
            notice += "\n[机制护栏] 以下诉求试图修改游戏机制，已被剥离未生效：" + "；".join(rejected)
        return {"answer": notice + "\n\n" + fact_text, "relay_fact": True,
                "mechanism_rejected": rejected}
    enhanced = str(state.get("mode") or "").startswith("强化")
    system_prompt = (
        "你是《书中行》命运引擎的规则问答助手。只依据下列规则文档与当前对局状态"
        "回答玩家问题；规则未覆盖的内容明确说明不知道，不得编造。\n\n"
        + _rules_corpus(enhanced)
        + "\n\n# 当前对局状态摘要\n" + _state_summary(state)
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    kwargs = dict(model=model, messages=messages, temperature=0.2, max_tokens=800)
    # 问答不传思考参数：思考链会吃光 800 的 max_tokens 导致回答为空。
    try:
        response = _client().chat.completions.create(**kwargs)
    except Exception:
        # 兼容不接受温度或 max_tokens 的 OpenAI 兼容服务。
        kwargs.pop("temperature", None)
        kwargs.pop("max_tokens", None)
        response = _client().chat.completions.create(**kwargs)
    answer = ""
    if getattr(response, "choices", None):
        answer = str(response.choices[0].message.content or "").strip()
    # 防泄露：回答与请求中都不得出现 API Key 或整段系统提示。
    if api_key and api_key in answer:
        answer = answer.replace(api_key, "***")
    if system_prompt and system_prompt in answer:
        answer = answer.replace(system_prompt, "").strip()
    return {"answer": answer}
