# -*- coding: utf-8 -*-
"""中转站模型可用性探测：每个候选模型发一次最小请求，记录可用性。"""
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


def main() -> int:
    models = ["gpt-5.4-mini", "gpt-5.4", "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra"]
    client = make_client(os.environ["REAL_OPENAI_KEY"], "openai", os.environ.get("REAL_OPENAI_BASE") or None)
    results = []
    for model in models:
        start = time.monotonic()
        try:
            result = native_complete(client, "openai", model, "回复：好",
                                     max_tokens=10, timeout=45.0)
            ok = bool(result.text.strip())
            results.append({"model": model, "ok": ok, "text": result.text.strip()[:10],
                            "elapsed_s": round(time.monotonic() - start, 1)})
        except Exception as exc:
            results.append({"model": model, "ok": False,
                            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                            "elapsed_s": round(time.monotonic() - start, 1)})
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    out = ROOT / "artifacts" / "real_acceptance" / "openai_model_probe.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
