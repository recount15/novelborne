# -*- coding: utf-8 -*-
"""F10 三层长期记忆 30/50/100 回合长程验收（GLM 官方 API）。

设计文档验收点（考虑新增机制的实施.md）：
- 阶段 3 验收：压缩 10/20/…/100 回合后，锚点、角色、目标、因果和关键事实
  可检索且不丢失（context_compressor 每 10 回合触发 + fidelity_check）；
- 22.11.3：历史高权重事件持续影响、玩家输入改变局部权重、断线后从
  checkpoint 继续、100 回合后导出仍覆盖全部 ledger；
- 15.3 指标：提交成功率 / 锚点连续性 / 未完成目标丢失率 / 选项有效率 /
  平均与 P95 延迟 / 每回合 token（GLM OpenAI 兼容端点不回报缓存字段，
  cache ratio 记 null）。

单次连续 100 回合运行，在 30/50/100 回合做检查点核验（该会话在检查点
处即 30/50/100 回合态，覆盖三档实验长度）。密钥仅经环境变量注入。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import novel_exporter  # noqa: E402
from core.engine import story_ledger as ledger_module  # noqa: E402

CHECKPOINTS = (30, 50, 100)
#: 开局小说既有关键事实：长程后必须仍可检索（不丢失）。
PROBE_FACTS = ("沈砚", "柳三更", "阿潮", "潮生门", "临渊城", "玉符")
#: 引用早期事实的回合（触发动态权重变化 + 记忆召回）。
CALLBACK_ROUNDS = {31: "我想起阿潮那半块玉符，仔细追问潮生门与玉符的来历",
                   61: "去听雨楼找柳三更，把玉符和潮生门的事再对一遍",
                   91: "带阿潮重回码头，按柳三更的说法查玉符的下落"}


def _inputs(total: int) -> list[str]:
    """100 回合玩家输入：围绕开局事实变化推进，穿插 CALLBACK_ROUNDS 召回探针。"""
    pool = [
        "去坊市司附近查访登记簿的底册，尽量不引人注意",
        "在码头打听最近沉船的传言，顺便留意有没有人盯梢",
        "沿着观星台外围走一圈，看看夜里有没有异常灯火",
        "找间茶棚坐下，听跑船人讲潮生门的旧事",
        "检查自己的掌纹印记，试着感知潮水的异动",
        "把听来的消息记下，回客栈仔细梳理线索",
        "去当铺问半块玉符能当多少，试探掌柜的反应",
        "夜里潜回码头，看看谁在暗中接应沉货",
        "请柳三更帮忙查一封旧船契的存档",
        "带阿潮去修船铺，借工匠的口打听水鬼的传闻",
        "在集市上买些伤药和干粮，备着走水路",
        "循着湿漉漉的脚印追到巷尾，查看留下的记号",
        "找守备官的文书搭句话，看看官面上对潮生门的态度",
        "把玉符的花纹描下来，去古董摊找识货的人问",
        "雇条小船沿河探一趟，看沉船位置有没有标记",
        "回听雨楼还人情，顺便问柳三更接下来去哪查",
        "在客栈大堂故意放出风声，说半块玉符在我手上",
        "躲进暗处，看看放风之后有谁坐不住了",
        "对照潮汐时辰，推算印记发烫的规律",
        "给阿潮置办身干练衣裳，带他认认城里的门路",
    ]
    actions = []
    for i in range(1, total + 1):
        if i in CALLBACK_ROUNDS:
            actions.append(CALLBACK_ROUNDS[i])
        else:
            actions.append(pool[(i - 1) % len(pool)])
    return actions


def _ledger_metrics(state: dict) -> dict:
    rows = state.get("story_ledger") or []
    rounds = sorted(int(r.get("round") or 0) for r in rows if isinstance(r, dict))
    check = ledger_module.validate_ledger(rows)
    return {"len": len(rows), "round_min": rounds[0] if rounds else 0,
            "round_max": rounds[-1] if rounds else 0,
            "continuity_ok": bool(check.get("ok", True)),
            "gaps": check.get("gaps") or []}


def _state_facts(state: dict) -> dict:
    """关键事实检索探针 + 目标/权重快照（F10：可检索且不丢失）。"""
    state_json = json.dumps(state, ensure_ascii=False, default=str)
    facts = {fact: (fact in state_json) for fact in PROBE_FACTS}
    rows = state.get("story_ledger") or []
    round1_rows = [r for r in rows if isinstance(r, dict) and int(r.get("round") or 0) == 1]
    objectives = state.get("objectives") or []
    weights = []
    for item in objectives:
        if isinstance(item, dict):
            weights.append({"title": str(item.get("title") or item.get("text") or "")[:40],
                            "weight": item.get("weight"), "status": item.get("status")})
    weights.sort(key=lambda w: -float(w.get("weight") or 0))
    return {"facts_present": facts, "round1_ledger_rows": len(round1_rows),
            "objectives_total": len(objectives),
            "objectives_open": sum(1 for w in weights if str(w.get("status") or "").lower()
                                   not in ("done", "resolved", "closed", "完成", "已解决")),
            "top_weights": weights[:5]}


def _compression_snapshot(state: dict) -> dict:
    rec = state.get("compression_record")
    history = state.get("history") or []
    first = str((history[0] or {}).get("content") or "") if history else ""
    return {"record": rec if isinstance(rec, dict) else None,
            "history_len": len(history),
            "history_head_is_summary": first.startswith("[接手摘要] ")}


def _export_check(state: dict) -> dict:
    try:
        _segments, meta, check = novel_exporter.prepare_source(state)
        return {"source_kind": meta.get("source_kind"),
                "round_min": meta.get("source_round_min") or meta.get("round_min"),
                "round_max": meta.get("source_round_max") or meta.get("round_max"),
                "turn_count": meta.get("source_turn_count") or meta.get("turn_count"),
                "gaps": meta.get("source_gaps") or meta.get("gaps"),
                "check_ok": bool(check.get("ok"))}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def _weights_snapshot(state: dict) -> dict:
    snap = {}
    for item in state.get("objectives") or []:
        if isinstance(item, dict):
            snap[str(item.get("title") or item.get("text") or "")[:40]] = item.get("weight")
    return snap


def _weight_shift(before: dict, after: dict) -> list[dict]:
    rows = []
    for title, w in after.items():
        if title in before and before[title] != w:
            rows.append({"title": title, "before": before[title], "after": w})
    return rows


def _checkpoint(state: dict, tag_round: int) -> dict:
    return {"round": tag_round, "ledger": _ledger_metrics(state),
            "facts": _state_facts(state), "compression": _compression_snapshot(state),
            "export": _export_check(state)}


def main() -> int:
    from tools.playtest_kit.agent_pilot import start_game  # noqa: E402
    from core import app  # noqa: E402
    from core import fate_engine as fe  # noqa: E402

    provider = os.environ.get("FATE_PROVIDER", "zhipu")
    model_name = os.environ.get("FATE_MODEL", "glm-5.3-flash")
    base_url = os.environ.get("FATE_BASE_URL", "")  # 空 = 用引擎内置 zhipu 端点
    key = (os.environ.get("FATE_API_KEY") or os.environ.get("GLM_API_KEY") or "")
    total = int(os.environ.get("F10_ROUNDS", "100"))
    tag = os.environ.get("F10_TAG", "f10_glm")
    if not key:
        print("FATE_API_KEY/GLM_API_KEY not set", file=sys.stderr)
        return 2

    actions = _inputs(total)
    state = start_game(True, provider, model_name, base_url,
                       actions_count=min(total, 6), book_id_hint="sample_linyuan")
    state["round"] = 0  # 真实流程对齐：首个玩家行动即 round 1

    report = {"tag": tag, "provider": provider, "model": model_name,
              "agent_env": os.environ.get("STORY_AGENT_MODE", "shadow"),
              "rounds_planned": total, "checkpoints": sorted(CHECKPOINTS),
              "rounds": [], "compressions": [], "checkpoint_results": {},
              "weight_shifts": {}, "resume_test": None,
              "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    chatbot: list[dict] = []
    prev_tok = [0, 0]
    body_sample = ""
    progress_path = ROOT / "outputs" / f"{tag}_progress.json"
    progress_path.parent.mkdir(exist_ok=True)

    for i, action in enumerate(actions, start=1):
        before_weights = _weights_snapshot(state) if i in CALLBACK_ROUNDS else None
        t0 = time.monotonic()
        row = None
        for attempt in (1, 2):  # 单回合失败重试一次（中转/网络瞬时故障）
            try:
                outputs = list(app.on_send(provider, base_url, key, model_name,
                                           "auto", "", action, chatbot, state))
                final_chat, last_state = outputs[-1][0], outputs[-1][2]
                body = ""
                for m in reversed(final_chat):
                    if m.get("role") == "assistant":
                        body = m["content"]
                        break
                tok = [int(last_state.get("tok_in", 0) or 0), int(last_state.get("tok_out", 0) or 0)]
                row = {"round": i, "ok": bool(body.strip()) and len(body) >= 200, "attempt": attempt,
                       "elapsed_s": round(time.monotonic() - t0, 1),
                       "body_chars": len(body.replace("\n", "")),
                       "options": len(last_state.get("options") or []),
                       "round_state": last_state.get("round"),
                       "ledger": _ledger_metrics(last_state),
                       "tok_delta": [tok[0] - prev_tok[0], tok[1] - prev_tok[1]],
                       "error": None}
                body_sample = body[:400]
                state, chatbot = last_state, final_chat
                prev_tok = tok
                if row["ok"]:
                    break
            except Exception as exc:  # noqa: BLE001
                row = {"round": i, "ok": False, "attempt": attempt,
                       "elapsed_s": round(time.monotonic() - t0, 1),
                       "error": f"{type(exc).__name__}: {exc}"[:300]}
        report["rounds"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        (ROOT / "outputs" / f"{tag}_r{i}.txt").write_text(body_sample, encoding="utf-8")

        # 每 10 回合捕获压缩事件（F10：压缩后关键事实可检索）
        if i % 10 == 0 and row.get("ok"):
            snap = _compression_snapshot(state)
            snap["round"] = i
            report["compressions"].append(snap)

        # 玩家输入改变局部权重（22.11.3）：召回探针前后对比
        if i in CALLBACK_ROUNDS and before_weights is not None and row.get("ok"):
            report["weight_shifts"][str(i)] = _weight_shift(before_weights, _weights_snapshot(state))

        # 断线恢复测试（22.11.3）：40 回合后存档→冷读档→继续
        if i == 40 and row.get("ok"):
            try:
                fe.persistence.save_state(state, save_id=f"{tag}_ckpt", root=fe.WRITABLE_DIR,
                                          start_params=state.get("start_params"))
                reloaded = fe.persistence.load_state_strict(f"{tag}_ckpt", root=fe.WRITABLE_DIR)
                ok = bool(reloaded) and int(reloaded.get("round") or 0) == state.get("round") \
                    and len(reloaded.get("story_ledger") or []) == len(state.get("story_ledger") or [])
                if ok:
                    state = reloaded  # 之后回合跑在冷读档状态上
                report["resume_test"] = {"ok": ok,
                                         "round": int(reloaded.get("round") or 0) if reloaded else None,
                                         "ledger_len": len(reloaded.get("story_ledger") or []) if reloaded else 0}
            except Exception as exc:  # noqa: BLE001
                report["resume_test"] = {"ok": False, "error": str(exc)[:200]}

        # 检查点核验（F10：30/50/100 回合）
        if i in CHECKPOINTS and row.get("ok"):
            cp = _checkpoint(state, i)
            report["checkpoint_results"][str(i)] = cp
            print("== checkpoint", i, json.dumps(cp, ensure_ascii=False)[:500], flush=True)

        # 崩溃安全：每回合落盘进度
        progress_path.write_text(json.dumps(
            {"done": i, "ok_count": sum(1 for r in report["rounds"] if r.get("ok")),
             "checkpoint_results": report["checkpoint_results"],
             "compressions": report["compressions"]}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 最终指标（15.3）
    oks = [r for r in report["rounds"] if r.get("ok")]
    lats = sorted(r["elapsed_s"] for r in oks)
    p95 = lats[min(len(lats) - 1, int(round(0.95 * len(lats))) - 1)] if lats else None
    report["summary"] = {
        "success_rate": round(len(oks) / max(1, len(report["rounds"])), 4),
        "avg_latency_s": round(sum(lats) / len(lats), 1) if lats else None,
        "p95_latency_s": p95,
        "options_six_rate": round(sum(1 for r in oks if r.get("options") == 6) / max(1, len(oks)), 4),
        "tokens_in_total": prev_tok[0], "tokens_out_total": prev_tok[1],
        "cache_ratio": None,  # GLM OpenAI 兼容端点 usage 不含缓存字段
        "final_ledger": _ledger_metrics(state),
    }
    report["ok"] = (report["summary"]["success_rate"] >= 0.9
                    and report["summary"]["final_ledger"]["continuity_ok"]
                    and all((c.get("record") or {}).get("fidelity") != "degraded"
                            for c in report["compressions"])
                    and report["checkpoint_results"].get("100", {}).get("export", {}).get("check_ok"))
    out = ROOT / "artifacts" / "real_acceptance" / f"{tag}_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("== report saved:", str(out), "==", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
