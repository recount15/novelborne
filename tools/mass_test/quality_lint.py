# -*- coding: utf-8 -*-
"""质量瑕疵检测：乱码、正文吐 JSON、中文语境吐英文串。

三类问题对应用户报告的"小瑕疵"：
- mojibake：U+FFFD、UTF-8 被按 Latin/GBK 误读的典型字符、异常控制字符；
- json_leak：叙述文本里出现工具协议（{"tool":...}）、NDJSON 帧（{"type":...}）、
  ```json 代码块、键值对样式的裸 JSON；
- english_run：中文语境里出现连续 4 个以上英文单词的句子片段（白名单豁免
  术语与模型/协议词）。

检查入口 lint_text(text, where) 返回发现列表；低频正常术语不误报。
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------- 乱码
_MOJIBAKE_CHARS = (
    "æ", "ç", "å", "è", "ä", "ö", "ü", "ð", "ñ", "î", "ï", "ù", "û",
    "Ě", "ě", "Ĺ", "ľ", "Ř", "ř", "Å", "Ã", "Â", "Ð", "Þ", "ß", "€",
    "聽", "啟", "畲", "槻", "燾", "鎸", "锛", "浣", "鏄", "挧", "遜",
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _lint_mojibake(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    replacement = text.count("\ufffd")
    if replacement:
        found.append({"type": "mojibake", "subtype": "replacement_char",
                      "count": replacement,
                      "excerpt": _around(text, text.find("\ufffd"))})
    hits = {ch for ch in _MOJIBAKE_CHARS if ch in text}
    # 单个西文带音符号字符在正常中文文本几乎不出现；出现 2 种以上判乱码。
    if len(hits) >= 2:
        sample = min(text.find(ch) for ch in hits if text.find(ch) >= 0)
        found.append({"type": "mojibake", "subtype": "encoding_artifacts",
                      "chars": "".join(sorted(hits))[:12],
                      "excerpt": _around(text, sample)})
    control = _CONTROL_RE.findall(text)
    if len(control) >= 3:
        found.append({"type": "mojibake", "subtype": "control_chars",
                      "count": len(control), "excerpt": text[:120]})
    return found


# ---------------------------------------------------------------- 吐 JSON
_TOOL_PROTO_RE = re.compile(r'\{\s*"(?:tool|name)"\s*:\s*"', )
_FRAME_RE = re.compile(r'\{\s*"(?:type|delta|operation)"\s*:\s*"')
_KV_PAIR_RE = re.compile(r'\{\s*"[^"]{1,40}"\s*:\s*(?:"[^"]{0,80}"|\d+(?:\.\d+)?|(?:true|false|null))\s*[,}]')
_FENCE_RE = re.compile(r"```(?:json|jsonc)?\s")


def _lint_json_leak(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for name, pattern in (("tool_protocol", _TOOL_PROTO_RE),
                          ("stream_frame", _FRAME_RE),
                          ("code_fence", _FENCE_RE)):
        match = pattern.search(text)
        if match:
            found.append({"type": "json_leak", "subtype": name,
                          "excerpt": _around(text, match.start())})
    # 键值对样式连续出现 3 次以上（单次可能是引用/示例）。
    if len(_KV_PAIR_RE.findall(text)) >= 3:
        first = _KV_PAIR_RE.search(text)
        found.append({"type": "json_leak", "subtype": "kv_pairs",
                      "excerpt": _around(text, first.start())})
    return found


# ---------------------------------------------------------------- 英文串
_ENGLISH_RUN_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9'’\-]*(?:\s+/?[A-Za-z][A-Za-z0-9'’\-]*){3,}")
# 术语白名单：命中这些词为主的片段不算英文串（协议/产品/模型名）。
_TERM_WHITELIST_RE = re.compile(
    r"(?i)(api\s*key|base\s*url|openai|deepseek|anthropic|gemini|flash|gpt|"
    r"json|http|https|uuid|sse|ndjson|token|prompt|token|chat\.completions|"
    r"copilot| FateEngine | fenghuang |ok\b|idle|running|pending|done|error)",
)


def _lint_english_run(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cjk < 50:
        return found  # 全英文字段（如模型名列表）不在"中文语境吐英文"范围。
    for match in _ENGLISH_RUN_RE.finditer(text):
        snippet = match.group(0)
        if _TERM_WHITELIST_RE.search(snippet):
            continue
        found.append({"type": "english_run", "length": len(snippet),
                      "excerpt": _around(text, match.start())})
        break  # 同一段文本报一处即可，避免刷屏。
    return found


def _around(text: str, index: int, width: int = 100) -> str:
    start = max(0, index - width // 2)
    return text[start:start + width].replace("\n", "\\n")


def lint_text(text: str, where: str) -> list[dict[str, Any]]:
    """对一段输出文本做三类瑕疵检查；返回带 where 标注的发现列表。"""
    if not text or not isinstance(text, str):
        return []
    findings = _lint_mojibake(text) + _lint_json_leak(text) + _lint_english_run(text)
    for item in findings:
        item["where"] = where
    return findings


def lint_payload(payload: Any, where: str, _depth: int = 0) -> list[dict[str, Any]]:
    """递归检查 JSON 响应中的全部字符串字段（跳过键名与已知二进制字段）。"""
    if _depth > 6:
        return []
    findings: list[dict[str, Any]] = []
    if isinstance(payload, str):
        return lint_text(payload, where)
    if isinstance(payload, dict):
        skip = {"system", "api_key", "request_kwargs", "persona_text"}
        for key, value in payload.items():
            if key in skip:
                continue
            findings += lint_payload(value, f"{where}.{key}", _depth + 1)
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload[:200]):
            findings += lint_payload(value, f"{where}[{index}]", _depth + 1)
    return findings
