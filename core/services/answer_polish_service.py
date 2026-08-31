# -*- coding: utf-8 -*-
"""答卷整合润色中台（重构 M3/M4 后处理阶段）。

该阶段不是质量门禁，也不重新裁决剧情：它只负责把分段答卷整合成自然正文。
模型在提示词内部按「事实锁定 → 接缝整合 → 文学润色 → 输出整理」四步工作，
外部只收到最终正文。代码仅做**非阻断式**的格式洁净检查：润色调用失败、
返回空文本或残留 JSON/围栏时，调用方继续使用初步组装稿。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from core.engine import parallel, turn_grader
from core.engine.distill import distill_model
from core.prompts import render

Model = Callable[[str], Any]


@dataclass(frozen=True)
class PolishResult:
    """润色阶段结果；``used`` 为 False 表示调用失败或结果被安全回退。"""

    text: str
    used: bool
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _member_contracts(active_members: Sequence[Any]) -> str:
    lines: list[str] = []
    for item in active_members or ():
        if not isinstance(item, Mapping):
            continue
        name = _clip(item.get("name"), 40)
        if not name:
            continue
        card = item.get("character_card") if isinstance(item.get("character_card"), Mapping) else {}
        bits = [
            f"角色：{name}",
            f"目标：{_clip(card.get('goal') or item.get('background'), 80)}",
            f"恐惧：{_clip(card.get('fear'), 60)}",
            f"语言风格：{_clip(card.get('speech_style') or item.get('persona_preset'), 60)}",
            f"禁忌：{_clip(card.get('unacceptable_behaviors'), 100)}",
        ]
        lines.append("；".join(bits))
    return "\n".join(lines) or "（本回合无强制角色）"


def build_polish_prompt(draft: str, *, blueprint: Any = None,
                        anchor_terms: Sequence[str] = (),
                        active_members: Sequence[Any] = (),
                        quest_break: str = "", window: tuple[int, int] = (0, 0)) -> str:
    """装配四步后处理卷；仅传短约束和答卷，避免再次塞入全量系统提示。"""
    if hasattr(blueprint, "beat"):
        blueprint_text = (
            f"节拍：{_clip(blueprint.beat, 100)}\n"
            f"目标：{_clip(blueprint.goal, 100)}\n"
            f"冲突：{_clip(blueprint.conflict, 100)}\n"
            f"世界节拍：{'；'.join(_clip(x, 80) for x in (blueprint.world_beats or ())) or '无'}\n"
            f"悬念钩子：{_clip(blueprint.cliffhanger, 100)}")
    else:
        blueprint_text = _clip(blueprint, 500) or "（无额外蓝图）"
    return render(
        "answer_polish.md",
        BLUEPRINT=blueprint_text,
        ANCHOR="、".join(_clip(x, 40) for x in (anchor_terms or ()) if _clip(x, 40)) or "（无锚点硬词）",
        CHARACTERS=_member_contracts(active_members),
        QUEST_BREAK=_clip(quest_break, 400) or "（本回合无活动任务或碎锚阶段）",
        WINDOW="%d–%d 字" % (int(window[0] or 0), int(window[1] or 0)),
        DRAFT=str(draft or "").strip(),
    )


def _clean_candidate(raw: Any) -> str:
    """只剥最外层空白；不做内容级改写，格式判断交给 turn_grader。"""
    text = str(raw or "").strip()
    if text.startswith("```") and text.endswith("```"):
        # 这里不把围栏当成功；调用方会回退，避免“清理后误把模型说明当正文”。
        return text
    return text


def polish_answer(draft: str, *, client=None, model: str = "",
                  request_kwargs: Optional[dict] = None,
                  provider: str = "deepseek", blueprint: Any = None,
                  anchor_terms: Sequence[str] = (), active_members: Sequence[Any] = (),
                  quest_break: str = "", window: tuple[int, int] = (0, 0),
                  model_fn: Optional[Model] = None) -> PolishResult:
    """执行一次非阻断式润色。

    安全策略：模型异常/空返回/格式污染/明显把正文缩成说明文字时均返回原稿。
    **不检查字数、锚点、角色、任务的语义质量**；这些已经在答卷空级完成，
    本阶段不能重新变成整回合门禁。
    """
    original = str(draft or "").strip()
    if not original:
        return PolishResult("", False, "empty_draft")
    prompt = build_polish_prompt(
        original, blueprint=blueprint, anchor_terms=anchor_terms,
        active_members=active_members, quest_break=quest_break, window=window)
    raw_model = model_fn or (lambda p: distill_model(
        client, model, p, request_kwargs, provider))
    budgeted = parallel.budget_model(raw_model, parallel.PRIORITY_TURN)
    try:
        candidate = _clean_candidate(budgeted(prompt))
    except Exception as exc:  # noqa: BLE001 非阻断阶段，安全回退原稿
        return PolishResult(original, False, "model_error", {"error": str(exc)[:200]})
    if not candidate:
        return PolishResult(original, False, "empty_result")
    gate = turn_grader.format_gate(candidate)
    if not gate.get("valid"):
        return PolishResult(original, False, "format_residue", {"gate": gate})
    # 编辑完整性保护（不是质量门禁）：后处理不能破坏用户选择的剧情丰度。
    # 若润色把合格组装稿压缩/扩张出当前窗口，则视为编辑失败并保留原稿；
    # 回合照常提交，不触发重填、回滚或错误提示。
    low, high = (int(window[0] or 0), int(window[1] or 0))
    if low and len(candidate) < low:
        return PolishResult(original, False, "window_drift",
                            {"candidate_chars": len(candidate), "minimum_hint": low,
                             "maximum_hint": high})
    if high and len(candidate) > high:
        return PolishResult(original, False, "window_drift",
                            {"candidate_chars": len(candidate), "minimum_hint": low,
                             "maximum_hint": high})
    return PolishResult(candidate, True, "ok", {"gate": gate, "candidate_chars": len(candidate)})


__all__ = ["PolishResult", "build_polish_prompt", "polish_answer"]
