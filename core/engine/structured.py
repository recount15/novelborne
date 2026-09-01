# -*- coding: utf-8 -*-
"""结构化填空基础件：统一 JSON 提取 + 声明式字段校验 + 带错误反馈的重试调用。

重构方案 §九：把散落在 character_designer / gf_designer / quest /
anchor_distiller / work_distiller / agent_mode / gender_guard 的 7 处手写
JSON 提取收敛为一个最宽容的实现；校验用声明式 :class:`FieldSpec` 表达；
:func:`structured_call` 在解析/校验失败时把错误清单附回提示词定向重试，
全部尝试失败返回 ``(None, meta)`` 由调用方走确定性兜底（模型传输层连续
异常则上抛最后一个异常）。

传输层无关：``model`` 是 ``str -> str`` 的 callable（通常是
``engine.distill.distill_model`` 的包装）；``response_format`` 优先策略属于
调用通道（distill 层），不在此处感知。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

Model = Callable[[str], Any]

_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.S)

_KIND_LABELS = {
    "str": "字符串", "int": "整数", "float": "数值", "bool": "布尔值",
    "list": "字符串数组", "strlist": "字符串数组", "dict": "对象", "any": "任意值",
}


def extract_json(value: Any) -> dict:
    """从模型输出宽容提取 JSON 对象；失败抛 ``ValueError``（中文信息）。

    阶梯：dict 直通 → 剥 markdown 围栏 → 首个 ``{`` 到最后一个 ``}`` 切片。
    收敛自 7 处重复实现中最宽容的 anchor_distiller 版本。
    """
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        raise ValueError("模型输出必须是 JSON 对象或 JSON 字符串")
    text = value.strip()
    if not text:
        raise ValueError("模型返回了空内容")
    fence = _JSON_FENCE_RE.search(text)
    candidate = fence.group(1) if fence else text
    try:
        parsed = json.loads(candidate)
    except ValueError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型输出中未找到 JSON 对象") from None
        try:
            parsed = json.loads(candidate[start:end + 1])
        except ValueError as exc:
            raise ValueError("模型输出的 JSON 无法解析：%s" % exc) from exc
    if not isinstance(parsed, dict):
        raise ValueError("模型输出 JSON 顶层必须是对象")
    return parsed


@dataclass(frozen=True)
class FieldSpec:
    """声明式字段规格：validate 按此产出中文错误清单，spec_prompt 据此渲染要求。"""

    name: str
    kind: str = "str"                 # str/int/float/bool/list/dict/any
    required: bool = True
    enum: Tuple[str, ...] = ()        # 允许取值（str/list 逐项校验）
    min_len: int = 0                  # str：去首尾空白后的最小长度
    max_len: Optional[int] = None     # str：最大长度
    min_items: int = 0                # list：最小项数
    max_items: Optional[int] = None   # list：最大项数
    item_max_len: Optional[int] = None  # list：每项字符串最大长度
    hint: str = ""                    # 提示词中的字段口径说明
    default: Any = None               # 可选字段缺省值（apply_defaults 填充）


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "any":
        return True
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind in ("list", "strlist"):
        return isinstance(value, list)
    if kind == "dict":
        return isinstance(value, Mapping)
    return True


def validate(specs: Sequence[FieldSpec], data: Any) -> List[str]:
    """按声明式规格校验 ``data``，返回中文错误清单（空列表 = 通过）。

    未知多余字段不算错误（消费方按名取字段），数据原样返回，调用方自行取舍。
    """
    if not isinstance(data, Mapping):
        return ["输出必须是 JSON 对象"]
    errors: List[str] = []
    for spec in specs:
        missing = spec.name not in data or data[spec.name] is None
        if missing:
            if spec.required:
                errors.append("缺少必填字段 %s" % spec.name)
            continue
        value = data[spec.name]
        if not _type_ok(value, spec.kind):
            errors.append("字段 %s 类型不符：需要%s" % (
                spec.name, _KIND_LABELS.get(spec.kind, spec.kind)))
            continue
        if spec.kind == "str":
            text = str(value).strip()
            if not text:
                errors.append("字段 %s 不得为空字符串" % spec.name)
                continue
            if spec.min_len and len(text) < spec.min_len:
                errors.append("字段 %s 过短：至少 %d 字（当前 %d）" % (
                    spec.name, spec.min_len, len(text)))
            if spec.max_len is not None and len(text) > spec.max_len:
                errors.append("字段 %s 超长：最多 %d 字（当前 %d）" % (
                    spec.name, spec.max_len, len(text)))
            if spec.enum and text not in spec.enum:
                errors.append("字段 %s 取值必须是：%s" % (
                    spec.name, "、".join(spec.enum)))
        elif spec.kind == "int":
            try:
                int(value)
            except (TypeError, ValueError):
                errors.append("字段 %s 必须是整数" % spec.name)
        elif spec.kind == "float":
            try:
                float(value)
            except (TypeError, ValueError):
                errors.append("字段 %s 必须是数值" % spec.name)
        elif spec.kind in ("list", "strlist"):
            items = value
            if spec.min_items and len(items) < spec.min_items:
                errors.append("字段 %s 项数不足：至少 %d 项（当前 %d）" % (
                    spec.name, spec.min_items, len(items)))
            if spec.max_items is not None and len(items) > spec.max_items:
                errors.append("字段 %s 项数过多：最多 %d 项（当前 %d）" % (
                    spec.name, spec.max_items, len(items)))
            for index, item in enumerate(items):
                if not isinstance(item, str) or not item.strip():
                    errors.append("字段 %s 第 %d 项必须是非空字符串" % (
                        spec.name, index + 1))
                    continue
                if spec.item_max_len is not None and len(item) > spec.item_max_len:
                    errors.append("字段 %s 第 %d 项超长（最多 %d 字）" % (
                        spec.name, index + 1, spec.item_max_len))
                if spec.enum and item.strip() not in spec.enum:
                    errors.append("字段 %s 第 %d 项取值必须是：%s" % (
                        spec.name, index + 1, "、".join(spec.enum)))
    return errors


def apply_defaults(specs: Sequence[FieldSpec], data: dict) -> dict:
    """把可选字段的缺省值填进 ``data`` 的浅拷贝（不改原对象）。"""
    merged = dict(data or {})
    for spec in specs:
        if not spec.required and spec.name not in merged:
            merged[spec.name] = spec.default
    return merged


def spec_prompt(specs: Sequence[FieldSpec]) -> str:
    """把字段规格渲染成提示词里的输出要求段（中文）。"""
    lines = ["【输出格式】只输出一个 JSON 对象，不要输出任何解释、前后缀或代码围栏。字段要求："]
    for spec in specs:
        bits = [_KIND_LABELS.get(spec.kind, spec.kind)]
        if spec.required:
            bits.append("必填")
        else:
            if spec.default is not None:
                bits.append("可选（缺省 %s）" % json.dumps(
                    spec.default, ensure_ascii=False))
            else:
                bits.append("可选")
        if spec.kind == "str":
            if spec.min_len or spec.max_len is not None:
                low = spec.min_len or 1
                high = spec.max_len if spec.max_len is not None else "不限"
                bits.append("%s–%s 字" % (low, high))
            if spec.enum:
                bits.append("取值∈{%s}" % "、".join(spec.enum))
        if spec.kind in ("list", "strlist"):
            if spec.min_items or spec.max_items is not None:
                low = spec.min_items or 1
                high = spec.max_items if spec.max_items is not None else "不限"
                bits.append("%s–%s 项" % (low, high))
            if spec.item_max_len is not None:
                bits.append("每项 ≤%d 字" % spec.item_max_len)
            if spec.enum:
                bits.append("每项取值∈{%s}" % "、".join(spec.enum))
        line = "- %s（%s）" % (spec.name, "，".join(bits))
        if spec.hint:
            line += "：%s" % spec.hint
        lines.append(line)
    return "\n".join(lines)


def structured_call(model: Model, prompt: str, specs: Sequence[FieldSpec],
                    attempts: int = 2) -> Tuple[Optional[dict], dict]:
    """结构化填空调用：解析失败/校验失败 → 错误清单附回重试。

    返回 ``(data, meta)``：
    - 成功：``data`` 为通过校验的 dict（可选字段已填缺省值）；
    - 校验始终不过：``data`` 为 ``None``，``meta["errors"]`` 汇总各轮错误，
      调用方走确定性兜底；
    - 模型传输层连续异常（一次有效正文都没拿到）：上抛最后一个异常——
      与既有 distill 通道「全部失败才抛错」的约定一致。
    """
    attempts = max(1, int(attempts))
    meta: dict = {"attempts": 0, "errors": [], "raw_chars": 0}
    previous_errors: List[str] = []
    last_exc: Optional[BaseException] = None

    for attempt in range(attempts):
        meta["attempts"] = attempt + 1
        full_prompt = prompt if attempt == 0 else (
            prompt
            + "\n\n【上次输出存在问题，必须修正后重新输出】\n"
            + "\n".join("- " + item for item in previous_errors)
            + "\n请重新输出完整 JSON 对象（只输出 JSON，不要解释与围栏）。"
        )
        try:
            raw = model(full_prompt)
        except Exception as exc:  # noqa: BLE001 传输层错误：记录后重试
            last_exc = exc
            meta["errors"].append("模型调用失败：%s" % exc)
            continue
        meta["raw_chars"] = max(meta["raw_chars"], len(str(raw or "")))
        try:
            data = extract_json(raw)
        except ValueError as exc:
            previous_errors = [str(exc)]
            meta["errors"].append(str(exc))
            continue
        errors = validate(specs, data)
        if not errors:
            return apply_defaults(specs, data), meta
        previous_errors = errors
        meta["errors"].extend(errors)

    if last_exc is not None and not meta["raw_chars"]:
        raise last_exc
    return None, meta


__all__ = [
    "FieldSpec", "extract_json", "validate", "apply_defaults",
    "spec_prompt", "structured_call",
]
