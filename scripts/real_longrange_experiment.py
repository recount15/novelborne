# -*- coding: utf-8 -*-
"""真实模型长程实验：N 回合 on_send 全链路 + ledger 覆盖率 + 导出源校验。

由环境变量注入凭据（REAL_ANTHROPIC_KEY 等），STORY_AGENT_MODE 决定
legacy/shadow 或 agent 路径。产物写 artifacts/real_acceptance/。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import story_ledger as ledger_module  # noqa: E402
from core.engine import novel_exporter  # noqa: E402


def _ledger_metrics(state: dict) -> dict:
    rows = state.get("story_ledger") or []
    rounds = sorted(int(r.get("round") or 0) for r in rows if isinstance(r, dict))
    check = ledger_module.validate_ledger(rows)
    return {"len": len(rows), "round_min": rounds[0] if rounds else 0,
            "round_max": rounds[-1] if rounds else 0,
            "continuity_ok": bool(check.get("ok", True)),
            "gaps": check.get("gaps") or []}


def main() -> int:
    from tools.playtest_kit.agent_pilot import start_game, _SAMPLE_ACTIONS  # noqa: E402
    from core import app  # noqa: E402

    provider = os.environ.get("FATE_PROVIDER", "anthropic")
    base_url = os.environ.get("FATE_BASE_URL") or os.environ.get("REAL_ANTHROPIC_BASE", "")
    model_name = os.environ.get("FATE_MODEL") or os.environ.get("REAL_ANTHROPIC_MODEL", "")
    key = (os.environ.get("FATE_API_KEY") or os.environ.get("REAL_ANTHROPIC_KEY") or "")
    rounds = int(os.environ.get("EXP_ROUNDS", "4"))
    tag = os.environ.get("EXP_TAG", "longrange")

    actions = ([
        "我去坊市司附近查访登记簿的底册，尽量不引人注意",
        "带阿潮去听雨楼找柳三更，问清潮生门与玉符的来历",
        "当铺方向传来动静，我决定跟进查看，并让阿潮在巷口望风",
        "把三样线索拼在一起，写一封信请柳三更转交守备官",
    ])[:rounds]

    state = start_game(True, provider, model_name, base_url,
                       actions_count=rounds, book_id_hint="sample_linyuan")
    # 真实流程对齐：开局不占回合，首个玩家行动即 round 1（app.on_send: round=round0+1）。
    # pilot 夹具默认 round=1 会让 ledger 从 2 起，导出连续性校验会误报缺口。
    state["round"] = 0
    report = {"tag": tag, "provider": provider, "model": model_name,
              "agent_env": os.environ.get("STORY_AGENT_MODE", "shadow"),
              "rounds": [], "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    chatbot: list[dict] = []
    for i, action in enumerate(actions, start=1):
        t0 = time.monotonic()
        try:
            outputs = list(app.on_send(provider, base_url, key, model_name,
                                       "auto", "", action, chatbot, state))
            final_chat, last_state = outputs[-1][0], outputs[-1][2]
            body = ""
            for m in reversed(final_chat):
                if m.get("role") == "assistant":
                    body = m["content"]
                    break
            row = {"round": i, "ok": bool(body.strip()),
                   "elapsed_s": round(time.monotonic() - t0, 1),
                   "body_chars": len(body.replace("\n", "")),
                   "options": len(last_state.get("options") or []),
                   "round_state": last_state.get("round"),
                   "save_stage": last_state.get("save_stage"),
                   "ledger": _ledger_metrics(last_state),
                   "tokens_total": [last_state.get("tok_in", 0), last_state.get("tok_out", 0)],
                   "error": None}
            body_sample = body[:400]
        except Exception as exc:  # noqa: BLE001 单回合失败不中断实验
            row = {"round": i, "ok": False,
                   "elapsed_s": round(time.monotonic() - t0, 1),
                   "error": f"{type(exc).__name__}: {exc}"[:300]}
            body_sample = ""
        report["rounds"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        (ROOT / "outputs").mkdir(exist_ok=True)
        (ROOT / "outputs" / f"{tag}_r{i}.txt").write_text(body_sample, encoding="utf-8")
        chatbot = final_chat if row["ok"] else chatbot

    # 导出源校验：ledger 覆盖所有已提交回合（不跑文学改写，只查数据链）
    try:
        _segments, source_meta, source_check = novel_exporter.prepare_source(state)
        report["export_source"] = {"source_kind": source_meta.get("source_kind"),
                                   "round_min": source_meta.get("source_round_min") or source_meta.get("round_min"),
                                   "round_max": source_meta.get("source_round_max") or source_meta.get("round_max"),
                                   "turn_count": source_meta.get("source_turn_count") or source_meta.get("turn_count"),
                                   "gaps": source_meta.get("source_gaps") or source_meta.get("gaps"),
                                   "check_ok": source_check.get("ok")}
    except Exception as exc:  # noqa: BLE001
        report["export_source"] = {"error": str(exc)[:200]}

    report["final_ledger"] = _ledger_metrics(state)
    report["ok"] = all(r.get("ok") for r in report["rounds"]) and \
        report["final_ledger"]["continuity_ok"]
    out = ROOT / "artifacts" / "real_acceptance" / f"{tag}_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("== report saved:", str(out), "==", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
