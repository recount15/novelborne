"""托管子智能体：主角性格驱动的自动选线。

纯计算、无 IO、不调用模型：本模块只负责组装选线提示词与解析选择结果，
模型调用由集成层注入。托管一次只推进一回合——本模块只产出「选哪一项」，
不修改任何运行态，也不代玩家连续推进。
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

_FENCE_PATTERN = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def build_autoplay_prompt(
    state: Mapping[str, Any] | None,
    options: Sequence[Mapping[str, Any]],
    recent_history: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """组装主角托管选线提示词：让子智能体代入主角性格从选项中选一项。

    主角性格优先取 ``state["persona_text"]``（完整性格），回退到
    ``state["persona"]``（标签）。选项按 A–F 编号原文给出，要求只回 JSON。
    """
    state = state if isinstance(state, Mapping) else {}
    persona = str(state.get("persona_text") or state.get("persona") or "").strip() or "未具体设定"

    lines = []
    for item in options:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or "").strip()
        text = str(item.get("text") or "").strip()
        if key:
            lines.append(f"{key}. {text}")
    options_block = "\n".join(lines) or "（无选项）"

    context = ""
    if recent_history:
        history_lines = []
        for msg in list(recent_history)[-6:]:
            if not isinstance(msg, Mapping):
                continue
            role = "玩家" if str(msg.get("role") or "") == "user" else "剧情"
            content = str(msg.get("content") or "").strip().replace("\n", " ")
            if content:
                history_lines.append(f"{role}：{content[:200]}")
        context = "\n".join(history_lines)

    return (
        "你是主角的行动代理。请完全代入主角的性格、动机与底线，从当前可选项中"
        "选出最符合其行事风格的一项。\n"
        f"主角性格：{persona}\n\n"
        f"近期局势：\n{context or '（暂无）'}\n\n"
        f"当前可选项：\n{options_block}\n\n"
        '请只输出 JSON：{"choice": "A", "reason": "一句话理由"}。'
        "choice 必须是选项中出现的字母；reason 需体现主角性格依据。"
    )


def parse_autoplay_choice(text: str, options: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """解析托管选择结果，校验 choice 必须是当前选项中的合法字母键。

    失败抛 ValueError，供集成层捕获后返回友好错误。同样输入必然得到同样结果。
    """
    content = str(text or "").strip()
    if not content:
        raise ValueError("托管选择为空")
    fenced = _FENCE_PATTERN.search(content)
    if fenced:
        content = fenced.group(1).strip()
    else:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("托管选择中找不到 JSON 对象")
        content = content[start:end + 1]
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ValueError(f"托管选择不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("托管选择必须是 JSON 对象")

    choice = str(data.get("choice") or "").strip().upper()
    keys = [str(item.get("key") or "").strip().upper() for item in options if isinstance(item, Mapping)]
    if choice not in keys:
        raise ValueError(f"托管选择 {choice!r} 不是合法选项（可选 {keys}）")
    reason = str(data.get("reason") or "").strip()
    return {"choice": choice, "reason": reason}


__all__ = ["build_autoplay_prompt", "parse_autoplay_choice"]
