"""统一结构化问题中台：问题由代码约束，模型只补充答案或解释。"""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Any, Callable, Mapping, Sequence

from core.engine import parallel

PURPOSES = frozenset({"pre_game_setup", "gf_clarification", "gf_correction", "reader_qa_clarification", "in_game_mechanism"})


def question_id(purpose: str, key: str) -> str:
    if purpose not in PURPOSES: raise ValueError("未知问题用途")
    return f"{purpose}.{str(key).strip()}"


def make_question(purpose: str, key: str, prompt: str, *, answer_type: str = "text", choices: Sequence[Mapping[str, Any]] = (), required: bool = True, dependencies: Sequence[Mapping[str, Any]] = (), evidence_refs: Sequence[Mapping[str, Any]] = (), validation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if answer_type not in {"text", "single_choice", "multi_choice", "number", "boolean", "json"}: raise ValueError("不支持的回答类型")
    return {"id": question_id(purpose, key), "purpose": purpose, "evidence_refs": [dict(x) for x in evidence_refs], "prompt": str(prompt).strip(), "answer_type": answer_type, "schema": {"type": "array" if answer_type == "multi_choice" else "string"}, "choices": [dict(x) for x in choices], "required": bool(required), "dependencies": [dict(x) for x in dependencies], "validation": dict(validation or {}), "confidence": 0.0, "insufficient_evidence": not bool(evidence_refs)}


def normalize_answer(question: Mapping[str, Any], answer: Any) -> Any:
    typ = question.get("answer_type")
    choices = list(question.get("choices") or [])
    lookup = {str(x.get("id")): x.get("id") for x in choices if isinstance(x, Mapping) and x.get("id") is not None}
    lookup.update({str(x.get("label")): x.get("id") for x in choices if isinstance(x, Mapping) and x.get("id") is not None})
    if typ == "multi_choice":
        values = answer if isinstance(answer, (list, tuple)) else [answer]
        out = []
        for value in values:
            key = lookup.get(str(value), str(value).strip())
            if key and key not in out: out.append(key)
        return out
    if typ == "single_choice": return lookup.get(str(answer), str(answer).strip())
    if typ == "number":
        try: return float(answer) if "." in str(answer) else int(answer)
        except (TypeError, ValueError): return answer
    if typ == "boolean": return answer if isinstance(answer, bool) else str(answer).strip().lower() in {"1", "true", "yes", "是"}
    return answer if isinstance(answer, (dict, list)) else str(answer or "").strip()


def validate_answer(question: Mapping[str, Any], answer: Any) -> list[str]:
    value = normalize_answer(question, answer); errors = []; typ = question.get("answer_type"); spec = question.get("validation") or {}; choices = {str(x.get("id")) for x in question.get("choices") or [] if isinstance(x, Mapping)}
    if question.get("required") and (value == "" or value == [] or value is None): errors.append("必填问题不能为空")
    if typ == "single_choice" and choices and str(value) not in choices: errors.append("选项不在允许集合中")
    if typ == "multi_choice":
        if not isinstance(value, list): errors.append("必须是选项数组")
        elif choices and any(str(x) not in choices for x in value): errors.append("存在非法选项")
    if typ == "text":
        if not isinstance(value, str): errors.append("必须是文本")
        elif len(value) < int(spec.get("min_length", 0)): errors.append("文本过短")
        elif len(value) > int(spec.get("max_length", 2000)): errors.append("文本过长")
    if typ == "number" and not isinstance(value, (int, float)): errors.append("必须是数字")
    return errors


def dedupe_key(question: Mapping[str, Any], context: Mapping[str, Any] | None = None, answer: Any = None) -> str:
    raw = json.dumps({"question": question, "context": context or {}, "answer": normalize_answer(question, answer)}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class StructuredQuestionService:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}; self._lock = threading.RLock()

    def answer(self, question: Mapping[str, Any], answer: Any, *, context: Mapping[str, Any] | None = None, source: str = "user") -> dict[str, Any]:
        normalized = normalize_answer(question, answer); errors = validate_answer(question, normalized); result = {"question": deepcopy(dict(question)), "answer": normalized, "valid": not errors, "confidence": 1.0 if not errors else 0.0, "insufficient_evidence": bool(question.get("insufficient_evidence")), "evidence_refs": deepcopy(question.get("evidence_refs") or []), "source": source, "cached": False, "dedupe_key": dedupe_key(question, context, normalized), "errors": errors}
        with self._lock: self._cache[result["dedupe_key"]] = deepcopy(result)
        return result

    def fallback(self, question: Mapping[str, Any], *, error: str = "") -> dict[str, Any]:
        choices = question.get("choices") or []; answer = choices[0].get("id") if question.get("answer_type") == "single_choice" and choices else [] if question.get("answer_type") == "multi_choice" else ""
        result = self.answer(question, answer, source="fallback"); result["insufficient_evidence"] = True; result["confidence"] = 0.0; result["errors"] = [error] if error else result["errors"]; return result

    def batch(self, questions: Sequence[Mapping[str, Any]], *, model: Callable[[Mapping[str, Any]], Any] | None = None, context: Mapping[str, Any] | None = None, max_concurrency: int = 10) -> list[dict[str, Any]]:
        jobs = list(questions); limit = max(1, min(10, int(max_concurrency))); out: list[dict[str, Any] | None] = [None] * len(jobs)
        def run(q):
            key = dedupe_key(q, context, None)
            with self._lock:
                if key in self._cache: cached = deepcopy(self._cache[key]); cached["cached"] = True; return cached
            if model is None: return self.fallback(q, error="未配置模型")
            try:
                ans = model(q); result = self.answer(q, ans, context=context, source="model")
                with self._lock: self._cache[key] = deepcopy(result)
                return result
            except Exception as exc: return self.fallback(q, error=str(exc)[:200])
        with ThreadPoolExecutor(max_workers=limit) as pool:
            futures = {pool.submit(run, q): i for i, q in enumerate(jobs)}
            for future in as_completed(futures): out[futures[future]] = future.result()
        return [x for x in out if x is not None]

    def clear(self) -> None:
        with self._lock: self._cache.clear()

__all__ = ["PURPOSES", "question_id", "make_question", "normalize_answer", "validate_answer", "dedupe_key", "StructuredQuestionService"]
