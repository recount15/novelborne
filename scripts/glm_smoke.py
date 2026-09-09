# -*- coding: utf-8 -*-
"""GLM 官方 API 冒烟测试：兼容性 + 生成速度 + usage 字段形状。

密钥只从环境变量 GLM_API_KEY 读取，绝不落盘。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai import OpenAI  # noqa: E402


def main() -> int:
    key = os.environ.get("GLM_API_KEY", "").strip()
    if not key:
        print("GLM_API_KEY not set", file=sys.stderr)
        return 2
    base_url = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("GLM_MODEL", "glm-5.3-flash")
    client = OpenAI(api_key=key, base_url=base_url, timeout=120.0, max_retries=2)

    results = {"provider": "glm", "base_url": base_url, "model": model, "calls": []}

    # 1) 短 JSON 结构调用（模拟蓝图/选项卷形状）
    t0 = time.perf_counter()
    r1 = client.chat.completions.create(
        model=model, max_tokens=500,
        messages=[{"role": "system", "content": "只输出 JSON 数组，不要多余文本。"},
                  {"role": "user", "content": '生成 3 条武侠行动选项，每条 {"text","factor"}，factor 取"金手指"或"性格"。'}])
    dt1 = time.perf_counter() - t0
    results["calls"].append({
        "kind": "short_json", "seconds": round(dt1, 1),
        "text_head": (r1.choices[0].message.content or "")[:120],
        "usage": _usage(r1.usage),
    })

    # 2) 长正文生成（模拟回合正文 ~1000 字）
    t0 = time.perf_counter()
    r2 = client.chat.completions.create(
        model=model, max_tokens=3000,
        messages=[{"role": "user", "content":
                   "写一段 900 字左右的武侠小说正文：主角沈砚初到临渊城码头，"
                   "察觉有人跟踪，掌心水纹印记发烫。要求场景具体、有对话，以动作收尾。"}])
    dt2 = time.perf_counter() - t0
    text2 = r2.choices[0].message.content or ""
    results["calls"].append({
        "kind": "long_narrative", "seconds": round(dt2, 1), "chars": len(text2),
        "text_head": text2[:120],
        "usage": _usage(r2.usage),
    })

    # 3) 二次相同前缀调用（观察隐式缓存字段是否出现）
    t0 = time.perf_counter()
    r3 = client.chat.completions.create(
        model=model, max_tokens=3000,
        messages=[{"role": "user", "content":
                   "写一段 900 字左右的武侠小说正文：主角沈砚初到临渊城码头，"
                   "察觉有人跟踪，掌心水纹印记发烫。要求场景具体、有对话，以动作收尾。"}])
    dt3 = time.perf_counter() - t0
    results["calls"].append({
        "kind": "repeat_prefix", "seconds": round(dt3, 1),
        "usage": _usage(r3.usage),
    })

    out = ROOT / "artifacts" / "real_acceptance" / "glm_smoke.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def _usage(usage) -> dict:
    raw = getattr(usage, "model_extra", None) or {}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "cached_tokens": getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None),
        "extra_keys": sorted(k for k in raw.keys()) if isinstance(raw, dict) else [],
    }


if __name__ == "__main__":
    sys.exit(main())
