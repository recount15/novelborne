# -*- coding: utf-8 -*-
"""真实供应商 API 冒烟测试：连通性 + 基础调用。

密钥必须通过环境变量提供：
  REAL_ANTHROPIC_KEY / REAL_ANTHROPIC_BASE / REAL_ANTHROPIC_MODEL
  REAL_OPENAI_KEY / REAL_OPENAI_BASE / REAL_OPENAI_MODEL
密钥不写入任何文件、日志或产物。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.fate_engine import make_client  # noqa: E402
from core.services.native_gateway import native_complete  # noqa: E402


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def smoke(provider: str) -> dict:
    if provider == "anthropic":
        key, model = env("REAL_ANTHROPIC_KEY"), env("REAL_ANTHROPIC_MODEL")
        base = env("REAL_ANTHROPIC_BASE") or None
    else:
        key, model = env("REAL_OPENAI_KEY"), env("REAL_OPENAI_MODEL")
        base = env("REAL_OPENAI_BASE") or None
    if not key or not model:
        return {"provider": provider, "ok": False, "error": "missing key/model env"}
    client = make_client(key, provider, base)
    start = time.monotonic()
    try:
        result = native_complete(client, provider, model,
                                 "回复两个字：正常", max_tokens=20, timeout=60.0)
        elapsed = time.monotonic() - start
        return {"provider": provider, "ok": bool(result.text.strip()), "model": model,
                "text": result.text.strip()[:20], "elapsed_s": round(elapsed, 2),
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "cached_tokens": result.cached_tokens,
                "cache_creation_tokens": result.cache_creation_tokens}
    except Exception as exc:
        return {"provider": provider, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                "elapsed_s": round(time.monotonic() - start, 2)}


def main() -> int:
    results = [smoke("anthropic"), smoke("openai")]
    out = ROOT / "artifacts" / "real_acceptance" / "smoke_test.json"
    payload = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
