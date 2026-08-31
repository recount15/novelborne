# -*- coding: utf-8 -*-
"""回合组装器：把批改通过的段空/选项空组装为最终展示文本与隐藏日志行。

纯函数渲染、零模型、零 IO（Wave D「纯代码组装」的机制层）：
- :func:`compose_display`：正文 = 段拼接（段间空行）；选项块 = ``A. 文本
  （后果：preview）`` 每行一条（行格式与 ``engine.options.render_options_block``
  一致）+ 末尾固定自由输入提示行（口径同 ``assets/prompts/rounds_rule.md``
  「玩家也可以自由输入其它行动」）；展示 = 正文 + 附加段 + 选项块。
  LOG/存档等隐藏段**绝不**进入正文与展示。
- :func:`render_log_line`：产出与既有 ``<<<LOG>>>…<<<END>>>`` 格式逐字一致的
  单行日志（供代码直接写日志文件，不进模型输出、不进展示）。
- :func:`render_archive_message`：历史注入用的存档系统消息（文案对齐
  ``core/app.py`` 十回合压缩处的「（系统存档：…）」）。
- :func:`split_for_history`：从展示文本反拆 ``(正文, 选项块)``，与 compose
  互逆（history 只存最终展示文本时的拆分工具）。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

# —— 复用 options 的字母选项行正则（parse_options 同源，反拆时识别选项行）——
from core.engine.options import _OPTION_LINE

# 选项块末尾的自由输入提示行（第 7 个「自己输入」选项；rounds_rule.md 第 8 行口径）
FREE_INPUT_HINT = "玩家也可以自由输入其它行动（即第 7 个「自己输入」的选项）"

_LOG_FIELDS = ("玩家", "金手指", "宿敌", "世界", "节拍")


def _render_options_block(options: Sequence[Mapping[str, Any]]) -> str:
    """把结构化选项渲染为文本选项块：``A. 文本（后果：preview）`` 每行一条。

    行首格式与 ``engine.options.render_options_block`` 完全一致（``{key}. {body}``），
    这里额外挂后果预告并追加自由输入提示行；无合法选项时返回空串。
    """
    lines: list[str] = []
    for item in options or ():
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or "").strip().upper()
        body = str(item.get("text") or "").strip()
        if not (key and body):
            continue
        line = f"{key}. {body}"
        preview = str(item.get("preview") or "").strip()
        if preview:
            line += f"（后果：{preview}）"
        lines.append(line)
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n" + FREE_INPUT_HINT


def compose_display(segments: Sequence[str], options: Sequence[Mapping[str, Any]], *,
                    eval_block: str = "", nemesis_digest: str = "") -> dict[str, str]:
    """组装最终展示：narrative / options_block / display 三件分离。

    - ``narrative``：段拼接（段间空行），只含正文，绝不混入隐藏段；
    - ``eval_block`` / ``nemesis_digest``：如有，作为独立段前置在正文之后、
      选项块之前（不并入 narrative 字段）；
    - ``display``：narrative + 空行 + 附加段（如有）+ 空行 + options_block。
    """
    narrative = "\n\n".join(str(part).strip() for part in (segments or ())
                            if str(part or "").strip())
    options_block = _render_options_block(options)
    body_parts = [narrative]
    for extra in (eval_block, nemesis_digest):
        text = str(extra or "").strip()
        if text:
            body_parts.append(text)
    body = "\n\n".join(part for part in body_parts if part)
    display = (body + "\n\n" + options_block).strip() if options_block else body
    return {"narrative": narrative, "options_block": options_block, "display": display}


def render_log_line(round_no: int, player: str, golden_finger: str, nemesis: str,
                    world: str, beat: str, progress: int) -> str:
    """渲染单行回合日志，与既有 ``<<<LOG>>>…<<<END>>>`` 格式逐字一致。

    格式：``<<<LOG>>>第N回合｜玩家:…｜金手指:…｜宿敌:…｜世界:…｜节拍:…｜进度:N<<<END>>>``
    （分隔符为全角｜，字段冒号为半角:；progress 钳制 0–100；各字段压平为
    单行——该行供代码直接写日志文件，不进模型输出、不进展示）。
    """

    def _flat(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    fields = [f"第{int(round_no)}回合"]
    fields.extend(f"{label}:{_flat(value)}"
                  for label, value in zip(_LOG_FIELDS, (player, golden_finger, nemesis, world, beat)))
    fields.append(f"进度:{max(0, min(100, int(progress)))}")
    return "<<<LOG>>>" + "｜".join(fields) + "<<<END>>>"


def render_archive_message(archive_text: str) -> str:
    """历史注入用的存档系统消息（文案对齐 core/app.py 现有「（系统存档：…）」）。"""
    return ("（系统存档：以下为截至目前既成事实的压缩存档，后续回合以此为事实基础，"
            "不得遗忘或篡改。）\n" + str(archive_text or "").strip())


def split_for_history(display: str) -> tuple[str, str]:
    """从展示文本反拆 ``(正文, 选项块)``，与 :func:`compose_display` 互逆。

    从尾部回扫：末尾的选项行（A–F 编号）与自由输入提示行（含其间空行）
    归入选项块，其余为正文。附加段（评价/宿敌摘要）随正文返回；没有
    选项块时返回 ``(原文, "")``。
    """
    content = str(display or "").strip()
    if not content:
        return "", ""
    lines = content.splitlines()
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    start = end
    while start > 0:
        line = lines[start - 1].strip()
        if not line:
            start -= 1  # 选项块内部的空行（提示行与选项行之间）
            continue
        if line == FREE_INPUT_HINT or _OPTION_LINE.match(line):
            start -= 1
            continue
        break
    if start == end:
        return content, ""
    options_block = "\n".join(lines[start:end]).strip()
    narrative = "\n".join(lines[:start]).rstrip()
    return narrative, options_block


__all__ = [
    "FREE_INPUT_HINT",
    "compose_display", "render_log_line", "render_archive_message",
    "split_for_history",
]
