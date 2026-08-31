# -*- coding: utf-8 -*-
"""类Agent模式真实可行性实测（通用 CLI 版）：开/关对比 pilot。

流程：
1. 同参数开局（强化模式 + 已蒸馏锚点书），仅 agent_mode 不同；
2. 各跑 N 回合固定行动，记录：耗时 / 子调用次数 / 门禁是否触发修订 / 体量；
3. 输出 JSON 报告到 outputs/agent_mode_report.json。

与旧版 tools/run_agent_mode_pilot.py 的差异：
- 供应商 / base_url / 模型 / 回合数 / 行动列表全部由 CLI 参数或环境变量注入，
  不写死任何业务常量；原文样本仍落盘供人工评估，但路径可用 --samples-dir 调整。

用法：
  python -m tools.playtest_kit.agent_pilot \
      --provider zhipu --model glm-5.3-flash --rounds 2
  # key 从 ZHIPUAI_API_KEY / FATE_API_KEY 环境变量读取，
  # 或经 runtime/.env 自动载入。

只测 on_send 链路本身；不经过 api_server。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_SAMPLE_ACTIONS = [
    "我先去码头的旧仓库查探那批失踪货物的线索，尽量隐蔽行事",
    "带着阿岚去酒馆打听昨夜信使的下落，顺便观察有没有人跟踪我们",
    "整理今日线索，写一封信给守卫队长说明目前的疑点",
]

_SUBCALLS = [0]


def _load_env(env_path: Path | None = None) -> None:
    """轻量加载 runtime/.env（不引入第三方依赖）。"""
    path = env_path or (ROOT / "runtime" / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        for prefix in ("ZHIPUAI_API_KEY=", "FATE_API_KEY=", "DEEPSEEK_API_KEY="):
            if line.startswith(prefix):
                os.environ.setdefault(prefix[:-1], line.split("=", 1)[1].strip())


def _pick_book(prefer: str | None) -> tuple[str, dict]:
    """找一本已蒸馏的书（WRITABLE_DIR/books 下带 chapter_index + 锚点）。"""
    from core import fate_engine as fe

    books_root = Path(fe.WRITABLE_DIR) / "books"
    fallbacks: list[tuple[str, str]] = []
    for d in sorted(books_root.glob("*")):
        idx_file = d / "chapter_index.json"
        anchors = d / "anchors"
        if not (idx_file.is_file() and anchors.is_dir()):
            continue
        index = json.loads(idx_file.read_text(encoding="utf-8"))
        if list(anchors.glob("0001.json")):
            if prefer and prefer in d.name:
                return d.name, index
            fallbacks.append((d.name, index))
    if not fallbacks:
        raise SystemExit("没有找到带锚点的书，先跑一次强化模式开局蒸馏")
    return fallbacks[0]


def start_game(agent_mode: bool, provider: str, model: str, base_url: str,
               actions_count: int, book_id_hint: str | None = None,
               richness: int = 700) -> dict:
    """开局并返回 state。手工构造 state 绕过 opening_flow 上传链路。"""
    from core import fate_engine as fe
    from core import app
    from core.engine import opening_flow
    from core.engine.ledger import new_ledger
    from core.engine.participation import scene_budget
    from core.memory import blank_state

    key = (os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("FATE_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY") or "")
    book_id, index = _pick_book(book_id_hint)
    memory = blank_state(mode="强化模式", source="agent_pilot")
    persona_path = ROOT / "personas" / "苟道.txt"
    system_parts = [fe.prompts.load("system_header.md")]
    system_parts.append(fe.prompts.render(
        "rounds_rule_enhanced.md", difficulty="D4 普通"))
    state = {
        "system": "\n\n".join(system_parts),
        "history": [{"role": "user", "content": (
            f"【开局】我穿越成{book_id}世界的一名码头小吏，身边有同伴阿岚。"
            "最近一批军械货物离奇失踪，守卫队长正在彻查。开局交代环境与人物关系，并以六个选项收尾。")}],
        "round": 1, "mode": "强化模式", "log": "",
        "provider": provider, "base_url": base_url, "model": model,
        "thinking_mode": "auto", "thinking_param": "",
        "request_kwargs": fe.thinking_kwargs(provider, "auto", ""),
        "convergence": "较高", "story_richness": richness,
        "agent_mode": agent_mode,
        "start_params": {"mode": "强化模式", "difficulty": "D4 普通",
                         "convergence": "较高", "golden_finger": "无",
                         "story_agent_mode": agent_mode, "work": book_id},
        "quest": {"status": "none"},
        "ledger": new_ledger(),
        "ripples": [], "state_memory": memory, "state_panel": "",
        "lore_hits": [], "options": [], "companions": [], "heroines": [],
        "tok_in": 0, "tok_out": 0, "tok_cache": 0, "tok_last": (0, 0),
        "distill_enabled": False, "active_members": [],
        # 绕过 opening_flow：直接置满开启链路所有标记
        "txt_uploaded": True,
        "plot_ready": True,
        "gf_stage": "confirmed",
        "gf_confirmed": True,
        "opening_confirmed": True,
        "opening_started": True,
        "opening_phase": opening_flow.PHASE_OPENING_CONFIRMED,
        "opening_state": opening_flow.initial_state(
            txt_uploaded=True, plot_ready=True, gf_confirmed=True,
            gf_stage="confirmed", opening_confirmed=True, started=True),
        "chapter_index": {"book_id": book_id,
                          **{k: v for k, v in index.items() if k != "book_id"}},
        "distill_key": str(Path(fe.WRITABLE_DIR) / "books" / book_id),
    }
    state.update(app._chapter_state(index, 1, 0))
    state["scene_budget"] = scene_budget(richness=richness)
    return state


def run_rounds(state: dict, tag: str, provider: str, model: str, base_url: str,
               actions: list[str], samples_dir: Path) -> dict:
    """跑固定回合并记录指标。单回合异常不断开整个对比。"""
    from core import app
    from core import fate_engine as fe

    key = (os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("FATE_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY") or "")
    client = fe.make_client(key, provider, base_url)
    out_dir_sample = Path(samples_dir)
    out_dir_sample.mkdir(parents=True, exist_ok=True)
    report = {"tag": tag, "agent_mode": state["agent_mode"], "rounds": []}
    chatbot: list[dict] = []
    for i, action in enumerate(actions, start=1):
        t0 = time.time()
        tok_before = (state.get("tok_in", 0), state.get("tok_out", 0))
        subcalls_before = _SUBCALLS[0]
        try:
            outputs = list(app.on_send(
                provider, base_url, key, model, "auto", "", action, chatbot, state))
        except Exception as exc:  # noqa: BLE001 - 单回合故障不断开整个对比
            elapsed = time.time() - t0
            report["rounds"].append({
                "round": i, "elapsed_s": round(elapsed, 1),
                "gate_pass": False, "gate_reason": f"exception: {exc}",
                "body_chars": 0, "revised": False, "issues": [],
                "resolved": False, "anchor_status": None,
                "subcalls_delta": _SUBCALLS[0] - subcalls_before,
                "tokens_delta": [0, 0],
            })
            print(f"[{tag}] round {i} crashed: {exc}", flush=True)
            continue
        elapsed = time.time() - t0
        final_chat, last_state = outputs[-1][0], outputs[-1][2]
        assistant_msgs = [m for m in final_chat if m.get("role") == "assistant"]
        body = assistant_msgs[-1]["content"] if assistant_msgs else ""
        meta = last_state.get("agent_meta") or {}
        validation = last_state.get("scene_validation") or {}
        row = {
            "round": i,
            "elapsed_s": round(elapsed, 1),
            "gate_pass": bool(last_state.get("scene_gate")),
            "gate_reason": last_state.get("scene_gate_reason", ""),
            "body_chars": len(body.replace("\n", "")),
            "revised": bool(meta.get("revised")),
            "issues": [x.get("kind") for x in (meta.get("issues") or [])],
            "resolved": bool(meta.get("resolved", True)),
            "anchor_status": (validation.get("anchor") or {}).get("status"),
            "subcalls_delta": _SUBCALLS[0] - subcalls_before,
            "tokens_delta": [last_state.get("tok_out", 0) - tok_before[1],
                             last_state.get("tok_in", 0) - tok_before[0]],
            "stream_fail_hint": ("模型服务调用失败" in str(body)) or None,
        }
        report["rounds"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        sample_path = out_dir_sample / f"{tag}_r{i}.txt"
        sample_path.write_text(body, encoding="utf-8")
        chatbot = final_chat
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="类Agent模式开/关对比实测")
    parser.add_argument("--provider", default=os.environ.get("FATE_PROVIDER", "zhipu"))
    parser.add_argument("--base-url", default=None,
                        help="留空则用 fate_engine.provider_config 的默认值")
    parser.add_argument("--model", default=os.environ.get("FATE_MODEL", ""))
    parser.add_argument("--rounds", type=int, default=2, help="每组回合数")
    parser.add_argument("--action-file", default=None,
                        help="行动列表 JSON 文件（字符串数组）；缺省用内置样例行动")
    parser.add_argument("--book", default=None, help="优先匹配的已蒸馏书名关键字")
    parser.add_argument("--richness", type=int, default=700)
    parser.add_argument("--samples-dir", default=str(ROOT / "outputs" / "agent_mode_samples"))
    parser.add_argument("--report", default=str(ROOT / "outputs" / "agent_mode_report.json"))
    args = parser.parse_args(argv)

    _load_env()
    provider = args.provider
    if not args.model:
        from core import fate_engine as fe
        cfg = fe.provider_config(provider, args.base_url)
        args.model = cfg.get("default_model") or ""
    if not args.model:
        parser.error("未指定模型（--model 或 FATE_MODEL）")

    base_url = args.base_url
    if not base_url:
        from core import fate_engine as fe
        base_url = fe.provider_config(provider, None).get("base_url") or ""

    if args.action_file:
        actions = json.loads(Path(args.action_file).read_text(encoding="utf-8"))
    else:
        actions = _SAMPLE_ACTIONS[:max(1, args.rounds)]
    actions = actions[:args.rounds]

    from core import app

    global _SUBCALLS
    _SUBCALLS = [0]
    raw_distill = app._distill_model

    def counting(client, model, prompt, *a, **kw):
        _SUBCALLS[0] += 1
        return raw_distill(client, model, prompt, *a, **kw)

    results = {}
    client_used = None
    for tag, flag in (("baseline", False), ("agent_on", True)):
        print(f"\n===== {tag} =====", flush=True)
        state = start_game(flag, provider, args.model, base_url,
                           actions_count=len(actions), book_id_hint=args.book,
                           richness=args.richness)
        app._distill_model = counting
        _SUBCALLS[0] = 0
        try:
            results[tag] = run_rounds(state, tag, provider, args.model,
                                      base_url, actions, Path(args.samples_dir))
        except Exception as exc:  # noqa: BLE001
            print(f"[{tag}] aborted: {exc}", flush=True)
            results[tag] = {"tag": tag, "agent_mode": flag, "error": str(exc), "rounds": []}
    app._distill_model = raw_distill

    results["_meta"] = {
        "provider": provider, "base_url": base_url, "model": args.model,
        "rounds": args.rounds, "story_richness": args.richness,
        "note": "纯工具实测脚本产出；数据口径详见报告 _note 与 feasibility 文档。",
    }
    out_dir = Path(args.report).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nreport →", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
