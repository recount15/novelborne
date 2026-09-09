# -*- coding: utf-8 -*-
"""真实供应商 prompt cache 命中验证。

对 Anthropic / OpenAI 各发送两次带相同稳定前缀（≥1024 tokens）的请求：
  第 1 次：建立缓存（Anthropic 应报告 cache_creation_input_tokens > 0）；
  第 2 次：命中缓存（Anthropic 应报告 cache_read_input_tokens > 0；
          OpenAI 应报告 prompt_cache_hit_tokens / cached_tokens > 0）。

密钥经环境变量提供（REAL_*），不写入文件；产物只含脱敏计量数据。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.fate_engine import make_client  # noqa: E402
from core.services.native_gateway import native_complete  # noqa: E402

# 稳定前缀：约 2500+ tokens，跨两次调用逐字一致（缓存前提）。
STABLE_SYSTEM = (
    "你是 Novelborne 剧情引擎的规则锚。以下为作品世界设定，必须逐字遵守：\n"
) + "\n".join(
    f"设定条目 {i:03d}：灵脉纪元的东境有一座临海古城临渊城，城分九坊，"
    f"坊以星辰为名，各坊供奉不同的守护灵。第{i:03d}条：临渊城的潮汐随月而动，"
    f"每月十五大潮，诸坊闭门谢客；城主府藏有前朝留下的观星台，观星台每逢"
    f"流星雨之夜开启一次，仅允许持玉符者登台。江湖门派以听雨楼与铁衣堡为首，"
    f"听雨楼掌情报与银钱，铁衣堡掌兵甲与防务。两派明面和睦，暗中争夺城外"
    f"灵矿的开采权。凡入城者需在城门处登记名姓与来意，登记簿由坊市司掌管。"
    for i in range(1, 41)
)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _call_with_retry(client: Any, provider: str, model: str, tries: int = 3) -> dict:
    """单次调用带瞬时故障重试：仅对异常重试（超时/503/断线），最多 tries 次。"""
    last: dict = {}
    for try_index in range(1, tries + 1):
        start = time.monotonic()
        try:
            result = native_complete(
                client, provider, model,
                "用不超过十个字回答：临渊城的登记簿由谁掌管？",
                system=STABLE_SYSTEM, max_tokens=100, timeout=90.0)
            return {"ok": bool(result.text.strip()), "text": result.text.strip()[:30],
                    "elapsed_s": round(time.monotonic() - start, 2),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cached_tokens": result.cached_tokens,
                    "cache_creation_tokens": result.cache_creation_tokens}
        except Exception as exc:
            last = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:250],
                    "elapsed_s": round(time.monotonic() - start, 2),
                    "try_index": try_index}
            if try_index < tries:
                time.sleep(3.0 * try_index)
    return last


def run_pair(provider: str) -> dict:
    if provider == "anthropic":
        key, model = env("REAL_ANTHROPIC_KEY"), env("REAL_ANTHROPIC_MODEL")
        base = env("REAL_ANTHROPIC_BASE") or None
        fallbacks = [m for m in env("REAL_ANTHROPIC_FALLBACKS").split(",") if m]
    else:
        key, model = env("REAL_OPENAI_KEY"), env("REAL_OPENAI_MODEL")
        base = env("REAL_OPENAI_BASE") or None
        fallbacks = [m for m in env("REAL_OPENAI_FALLBACKS").split(",") if m]
    if not key or not model:
        return {"provider": provider, "ok": False, "error": "missing env"}
    client = make_client(key, provider, base)
    # 首选模型整对失败时降级到备用模型通道（中转站单通道故障不影响验收目标）。
    for candidate_model in [model] + fallbacks:
        calls = []
        for attempt in (1, 2):
            call = _call_with_retry(client, provider, candidate_model)
            call["attempt"] = attempt
            calls.append(call)
            if attempt == 1:
                time.sleep(2.0)
        first, second = calls[0], calls[1]
        if first.get("ok") and second.get("ok"):
            hit = int(second.get("cached_tokens") or 0) > 0
            created = int(first.get("cache_creation_tokens") or 0) > 0
            return {"provider": provider, "model": candidate_model, "calls": calls,
                    "cache_creation_confirmed": created, "cache_hit_confirmed": hit,
                    "ok": hit,
                    "fallback_used": candidate_model != model}
        result = {"provider": provider, "model": candidate_model, "calls": calls,
                  "cache_creation_confirmed": False, "cache_hit_confirmed": False, "ok": False}
    return result


def main() -> int:
    results = [run_pair("anthropic"), run_pair("openai")]
    out = ROOT / "artifacts" / "real_acceptance" / "cache_hit_test.json"
    payload = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "stable_prefix_chars": len(STABLE_SYSTEM), "results": results}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
