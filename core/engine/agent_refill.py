# -*- coding: utf-8 -*-
"""逐空重填循环：批改 → 带错误清单定向重填（≤attempts 次）→ 兜底。

docs/REFACTOR_PLAN.md §3「重填预算」与 §0 原则 4（类 agent 批改-重填）
的机制层实现，与 agent_mode（整稿重写 + fail-open parse_issues 的历史
方案，已退役为反面教材）相对：本模块**只对单个空**做「批改 → 重填」，
任何一空的重填绝不影响其他空，模型抛错按该空失败处理、不中断整批。

机制复用（单一事实源，不复制逻辑）：
- ``turn_grader.grade_segment``：默认批改函数（段空口径）；
- ``turn_grader.build_refill_prompt``：默认重填提示词装配。

传输层无关：``model`` 是 ``str -> str`` 的 callable（由中台 services 注入，
通常为 ``engine.distill.distill_model`` 的包装）；本模块零 IO、零配置读取。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Sequence

from core.engine.turn_grader import (
    GradeResult,
    SegmentContract,
    build_refill_prompt,
    grade_segment,
)

# —— 可注入的机制签名（中台可换用选项空/角色空口径的批改与提示词）——
# 批改：contract, answer -> 错误列表（空列表 = 通过）
Grade = Callable[[Any, Any], list]
# 重填提示词：contract, errors -> str
RefillPrompt = Callable[[Any, Sequence[str]], str]
# 兜底答案：contract -> answer（重填预算耗尽仍不过时调用）
FallbackFactory = Callable[[Any], Any]
# 模型：prompt -> answer（传输层注入）
Model = Callable[[str], Any]

DEFAULT_ATTEMPTS = 2


def _as_errors(result: Any) -> list[str]:
    """把批改结果归一为错误列表：GradeResult 取 errors，list 直通，其余按空处理。"""
    if isinstance(result, GradeResult):
        return list(result.errors)
    if isinstance(result, (list, tuple)):
        return [str(item) for item in result if str(item or "").strip()]
    # 批改函数本身返回异常形状：视为该空失败（单条中文错误，不中断整批）
    return [f"批改函数返回了无法识别的结果：{type(result).__name__}"]


def _refill_slot(contract: Any, answer: Any, *, grade: Grade, refill_prompt: RefillPrompt,
                 model: Model, attempts: int,
                 fallback_factory: Optional[FallbackFactory]) -> dict[str, Any]:
    """单个空的批改-重填循环。

    返回 per_slot 观测行；重填尝试内模型抛错 / 返回非字符串都按本轮失败
    处理（保留上一稿），预算耗尽仍不过则调 fallback_factory 产兜底答案。
    """
    index = _contract_index(contract)
    current = answer
    errors: list[str] = []
    try:
        errors = _as_errors(grade(contract, current))
    except Exception as exc:  # noqa: BLE001 批改器异常按该空失败处理
        errors = [f"批改器调用失败：{exc}"]
    refills = 0
    ok = not errors

    for _ in range(max(0, int(attempts))):
        if ok:
            break
        try:
            prompt = refill_prompt(contract, errors)
        except Exception as exc:  # noqa: BLE001 提示词装配异常按本轮失败处理
            errors = [f"重填提示词装配失败：{exc}"]
            break
        try:
            candidate = model(prompt)
        except Exception as exc:  # noqa: BLE001 模型传输层异常按本轮失败处理
            errors = list(errors) + [f"模型重填调用失败：{exc}"]
            break  # 该空重填通道不可用，直接进入兜底判定，不影响其它空
        text = candidate if isinstance(candidate, str) else None
        if text is None or not str(text).strip():
            errors = list(errors) + ["模型重填返回了空内容"]
            break
        refills += 1
        current = text
        try:
            new_errors = _as_errors(grade(contract, current))
        except Exception as exc:  # noqa: BLE001
            errors = [f"批改器调用失败：{exc}"]
            continue
        errors = new_errors
        ok = not errors

    fallback = False
    if not ok and fallback_factory is not None:
        try:
            current = fallback_factory(contract)
            fallback = True
        except Exception as exc:  # noqa: BLE001 兜底工厂异常按该空最终失败处理
            errors = list(errors) + [f"兜底工厂调用失败：{exc}"]

    return {
        "index": index,
        "ok": bool(ok or fallback),
        "refills": int(refills),
        "fallback": bool(fallback),
        "errors": list(errors),
        "answer": current,
    }


def _contract_index(contract: Any) -> int:
    """从合约取空序号：SegmentContract 取 index，Mapping 取 index 键，缺省 -1。"""
    if isinstance(contract, SegmentContract):
        return int(contract.index)
    if isinstance(contract, Mapping):
        try:
            return int(contract.get("index", -1))
        except (TypeError, ValueError):
            return -1
    return -1


def run_refill_loop(contracts: Sequence[Any], answers: Sequence[Any], *,
                    grade: Grade = grade_segment,
                    refill_prompt: RefillPrompt = build_refill_prompt,
                    model: Optional[Model] = None,
                    attempts: int = DEFAULT_ATTEMPTS,
                    fallback_factory: Optional[FallbackFactory] = None) -> dict[str, Any]:
    """逐空批改-重填循环（类 agent 核心）。

    - ``contracts``：空合约序列（``SegmentContract`` 或任意 Mapping）；
    - ``answers``：对应初答序列（与 contracts 等长；不足的空按空答案处理）；
    - ``grade`` / ``refill_prompt``：可注入的批改与提示词装配（默认接
      ``turn_grader.grade_segment`` / ``build_refill_prompt``）；
    - ``model``：``prompt -> answer`` 的模型 callable（重填时调用）；
    - ``attempts``：每空重填预算上限（≤ 此值，默认 2）；
    - ``fallback_factory``：``contract -> answer`` 兜底工厂（预算耗尽仍
      不过时调用；为 None 时保留最后一稿并标记未通过）。

    - 返回 ``{"answers": [...], "per_slot": [...], "stats": {...}}``：
    每空独立循环——任何一空的重填/失败/兜底绝不影响其他空；模型抛错按
    该空本轮失败处理，不中断整批。
    """
    contract_list = list(contracts or ())
    answer_list = list(answers or ())
    if len(answer_list) < len(contract_list):
        answer_list = answer_list + [""] * (len(contract_list) - len(answer_list))

    final_answers: list[Any] = []
    per_slot: list[dict[str, Any]] = []
    for position, contract in enumerate(contract_list):
        record = _refill_slot(
            contract, answer_list[position],
            grade=grade, refill_prompt=refill_prompt,
            model=model if model is not None else _no_model,
            attempts=attempts, fallback_factory=fallback_factory,
        )
        record["index"] = position if record.get("index", -1) < 0 else record["index"]
        final_answers.append(_final_answer(record, answer_list[position]))
        per_slot.append(record)

    total = len(per_slot)
    refilled = sum(1 for row in per_slot if row["refills"] > 0)
    fell_back = sum(1 for row in per_slot if row["fallback"])
    stats = {
        "slots": total,
        "refilled": refilled,
        "fell_back": fell_back,
        "refill_rate": round(refilled / total, 4) if total else 0.0,
        "fallback_rate": round(fell_back / total, 4) if total else 0.0,
    }
    return {"answers": final_answers, "per_slot": per_slot, "stats": stats}


def _final_answer(record: Mapping[str, Any], original: Any) -> Any:
    """取该空的最终答案：``_refill_slot`` 附带的 ``answer``（通过稿/重填稿/兜底稿）。"""
    return record.get("answer", original)


def _no_model(prompt: str) -> str:
    """未注入模型时的占位通道：直接抛错 → 该空按失败处理（不中断整批）。"""
    raise RuntimeError("未注入模型 callable（run_refill_loop(model=...)），无法重填")


def refill_budget_meta(per_slot: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """把 per_slot 观测行压缩为 agent_meta 摘要（观测口径：重填率/兜底率）。"""
    rows = [dict(row) for row in (per_slot or ())]
    total = len(rows)
    refilled = sum(1 for row in rows if row.get("refills"))
    fell_back = sum(1 for row in rows if row.get("fallback"))
    passed = sum(1 for row in rows if row.get("ok"))
    return {
        "slots": total,
        "passed": passed,
        "refilled": refilled,
        "fell_back": fell_back,
        "refill_rate": round(refilled / total, 4) if total else 0.0,
        "fallback_rate": round(fell_back / total, 4) if total else 0.0,
        "max_refills": max((int(row.get("refills") or 0) for row in rows), default=0),
    }


__all__ = ["run_refill_loop", "refill_budget_meta", "DEFAULT_ATTEMPTS"]
