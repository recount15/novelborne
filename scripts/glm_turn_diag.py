# -*- coding: utf-8 -*-
"""单回合诊断：zhipu 通道 on_send 返回内容与内部日志落盘（不落密钥）。"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from tools.playtest_kit.agent_pilot import start_game  # noqa: E402
    from core import app  # noqa: E402

    key = (os.environ.get("FATE_API_KEY") or "").strip()
    provider = os.environ.get("FATE_PROVIDER", "zhipu")
    model = os.environ.get("FATE_MODEL", "glm-5.3-flash")

    state = start_game(True, provider, model, "", actions_count=2, book_id_hint="sample_linyuan")
    state["round"] = 0
    chatbot: list[dict] = []
    t0 = time.monotonic()
    outputs = list(app.on_send(provider, "", key, model, "auto", "",
                               "去码头打听最近沉船的传言，顺便留意有没有人盯梢", chatbot, state))
    elapsed = time.monotonic() - t0
    final_chat, last_state = outputs[-1][0], outputs[-1][2]

    diag = {
        "elapsed_s": round(elapsed, 1),
        "outputs_len": len(outputs),
        "round": last_state.get("round"),
        "save_stage": last_state.get("save_stage"),
        "options": len(last_state.get("options") or []),
        "history_len": len(last_state.get("history") or []),
        "tok": [last_state.get("tok_in"), last_state.get("tok_out")],
        "last_messages": [
            {"role": m.get("role"), "len": len(str(m.get("content") or "")),
             "head": str(m.get("content") or "")[:300]}
            for m in final_chat[-4:]
        ],
        "log_tail": str(last_state.get("log") or "")[-1500:],
        "wiring": [str(w)[:200] for w in (last_state.get("wiring_log") or [])[-10:]],
    }
    out = ROOT / "artifacts" / "real_acceptance" / "glm_turn_diag.json"
    out.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(diag, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
