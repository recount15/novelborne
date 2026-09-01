# -*- coding: utf-8 -*-
"""独立 HTTP 全功能检验（standalone 版）：走公开 API 对运行中的 api_server 做端到端检验。

收编自 outputs/real_playtest.py，泛化要点：
- 供应商 / base_url / 模型 / TXT 原著路径 / 回合数全部由 CLI 参数或环境变量注入；
- 检验覆盖：bootstrap→上传→开局→两步确认→N 回合主循环
  （任务短中长/托管/作弊码/存读档/压缩观察）→最终存档→导出小说。

用法：
  python -m tools.playtest_kit.standalone --rounds 50
  # 或旧行为等价：
  FATE_API_KEY=sk-xxx python tools/playtest_kit/standalone.py 50

Key 只经环境变量传入，绝不写入本文件与日志/报告。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent


def build_config(argv: list[str] | None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="真实 HTTP 全功能检验（通用多供应商）")
    parser.add_argument("rounds_pos", nargs="?", type=int, default=None,
                        help="位置参数形式的最大回合数（兼容旧调用）")
    parser.add_argument("--base", default=os.environ.get("FATE_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default=os.environ.get("FATE_API_KEY", ""))
    parser.add_argument("--provider", default=os.environ.get("FATE_PROVIDER", "custom"))
    parser.add_argument("--base-url", default=os.environ.get("FATE_BASE_URL",
                        "https://api.deepseek.com"))
    parser.add_argument("--model", default=os.environ.get("FATE_MODEL", "deepseek-chat"))
    parser.add_argument("--txt", default=os.environ.get(
        "FATE_TXT", "data/samples/边城档案.txt"),
        help="原著 TXT 路径（相对项目根或绝对路径）")
    parser.add_argument("--rounds", dest="rounds_opt", type=int, default=None,
                        help="最大回合数")
    parser.add_argument("--richness", type=int, default=int(os.environ.get("FATE_RICHNESS", "700")))
    parser.add_argument("--report", default=str(ROOT / "outputs" / "playtest_50_report.json"))
    parser.add_argument("--log", default=str(ROOT / "outputs" / "playtest_50.log"))
    args = parser.parse_args(argv)
    rounds = args.rounds_opt if args.rounds_opt is not None else (
        args.rounds_pos if args.rounds_pos is not None else 50)
    txt = Path(args.txt)
    if not txt.is_absolute():
        txt = ROOT / txt
    return {
        "base": args.base, "api_key": args.api_key, "provider": args.provider,
        "base_url": args.base_url, "model": args.model, "txt": str(txt),
        "max_rounds": max(1, rounds), "richness": args.richness,
        "report": args.report, "log": args.log,
    }


CFG: dict[str, Any] = {}

RESULTS: list[dict[str, Any]] = []
ROUND_LOG: list[dict[str, Any]] = []
START_TS = time.time()


def now() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def log(line: dict[str, Any]) -> None:
    line = dict(line)
    line["_t"] = now()
    ROUND_LOG.append(line)
    with open(CFG["log"], "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


def check(step: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append({"step": step, "ok": bool(cond), "detail": detail})
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {step}  {detail}", flush=True)
    return bool(cond)


def http(method: str, path: str, body: Any = None, timeout: int = 300) -> tuple[int, Any]:
    url = CFG["base"] + path
    data = None
    hdrs: dict[str, str] = {}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        else:
            data = body
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw.decode("utf-8"))
            except Exception:
                return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, {"__http_error__": e.read().decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return -1, {"__error__": str(e)}


def stream(path: str, body: dict[str, Any], timeout: int = 900) -> tuple[list[dict], dict | None, str | None]:
    url = CFG["base"] + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    events: list[dict] = []
    last_state: dict | None = None
    err: str | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                line = line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                events.append(ev)
                if ev.get("type") == "error":
                    err = str(ev.get("data", {}).get("message", "未知错误"))
                if ev.get("type") == "state":
                    st = ev.get("data", {}).get("state")
                    if isinstance(st, dict):
                        last_state = st
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        err = str(e)
    return events, last_state, err


def last_assistant_text(events: list[dict]) -> str:
    text = ""
    for ev in events:
        if ev.get("type") == "state":
            chat = ev.get("data", {}).get("chat") or []
            if isinstance(chat, list) and chat:
                last = chat[-1]
                if isinstance(last, dict) and last.get("role") == "assistant":
                    text = str(last.get("content") or "")
    return text


def upload_novel() -> tuple[str, str]:
    boundary = "----fateplaytest" + str(int(time.time()))
    with open(CFG["txt"], "rb") as f:
        file_bytes = f.read()
    filename = os.path.basename(CFG["txt"])
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nnovel\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: text/plain\r\n\r\n".encode(),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    url = CFG["base"] + "/api/uploads"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["session_id"], resp["upload"]["upload_id"]


def decide(options: list[dict], context: str) -> str:
    """用真实模型从当前 A–F 选项里选一项。"""
    opt_text = "\n".join(f"{o.get('key')}. {o.get('text')}" for o in options)
    prompt = (
        "你是互动剧情的玩家，扮演当前故事的主角。请根据最近剧情与自身处境，"
        "从下列行动选项中做出最合理的一项选择。只回复一个字母（A-F），不要任何解释。\n\n"
        f"最近剧情：\n{context[-1200:]}\n\n"
        f"行动选项：\n{opt_text}"
    )
    body = {
        "model": CFG["model"],
        "messages": [
            {"role": "system", "content": "你是玩家决策体，只回复选项字母。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 10,
        "temperature": 0.4,
    }
    url = CFG["base_url"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {CFG['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8"))
        content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
        letter = "".join(ch for ch in str(content).upper() if ch in "ABCDEF")
        if letter and any(o.get("key") == letter[0] for o in options):
            return letter[0]
    except Exception:
        pass
    return options[0]["key"]


def assert_public_state(state: dict, label: str) -> None:
    if not isinstance(state, dict):
        check(f"{label}:公开state为dict", False, "state 非 dict")
        return
    for key in ("system", "api_key", "request_kwargs", "persona_text", "nemesis_private"):
        check(f"{label}:脱敏[{key}]", key not in state,
              "" if key not in state else f"泄露 {key}")


def assert_no_raw_json(text: str, label: str) -> None:
    bad = [t for t in ('"genre"', '"premise"', '```json', '{ "') if t in text]
    check(f"{label}:无裸JSON", not bad, "" if not bad else f"出现 {bad}")


def main() -> None:
    global CFG, START_TS
    CFG = build_config(sys.argv[1:])
    START_TS = time.time()

    report_path = Path(CFG["report"])
    log_path = Path(CFG["log"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not CFG["api_key"]:
        sys.exit("缺少 FATE_API_KEY 环境变量（或 --api-key）")
    if not Path(CFG["txt"]).is_file():
        sys.exit(f"原著 TXT 不存在：{CFG['txt']}（用 --txt 指定）")

    # 旧日志直接截断复用（不删除文件，避免回收站不可用时阻断检验）。
    if log_path.exists():
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("")
    print("=== 真实 HTTP 全功能检验 ===", flush=True)
    print(f"模型: {CFG['model']} @ {CFG['base_url']}  原著: {os.path.basename(CFG['txt'])}"
          f"  最大回合: {CFG['max_rounds']}", flush=True)

    # ---------- 阶段0：bootstrap + 连接 + 上传 + 开局 ----------
    code, boot = http("GET", "/api/bootstrap")
    check("bootstrap:HTTP200", code == 200, f"code={code}")
    if isinstance(boot, dict):
        check("bootstrap:作品库非空", bool(boot.get("works")), f"works={len(boot.get('works') or [])}")
        check(f"bootstrap:{CFG['provider']}供应商在列",
              any(p.get("id") == CFG["provider"] for p in (boot.get("providers") or [])), "")

    code, tc = http("POST", "/api/models/test",
                    {"provider": CFG["provider"], "base_url": CFG["base_url"] or None,
                     "api_key": CFG["api_key"], "model": CFG["model"]})
    check("models/test:通过", code == 200 and isinstance(tc, dict) and tc.get("ok"),
          f"code={code} msg={tc.get('message') if isinstance(tc, dict) else tc}")

    print("[*] 上传原著 ...", flush=True)
    session_id, upload_id = upload_novel()
    check("上传:返回upload_id", bool(session_id and upload_id),
          f"session={session_id[:8]} upload={upload_id[:8]}")

    start_body = {
        "session_id": session_id,
        "provider": CFG["provider"], "base_url": CFG["base_url"], "api_key": CFG["api_key"],
        "model": CFG["model"],
        "thinking_mode": "off",
        "thinking_param": "",
        "mode": "强化模式",
        "novel_upload_id": upload_id,
        "role": "漂泊者", "timepoint": "故事开篇",
        "difficulty": "D4 普通",
        "golden_finger": "残卷箴言（洞悉一丝先机）",
        "persona_preset": "苟道（稳健发育、保命优先）",
        "persona_custom": "",
        "distill_enabled": True,
        "companion_roster": [{"name": "阿岚", "skill": "情报", "background": "同行的观测者", "participation": 8}],
        "heroine_roster": [{"name": "林秋", "skill": "医术", "background": "药铺掌柜", "participation": 7}],
        "heroine_mode": "单女主",
        "enable_nemesis": True,
        "nemesis_select": "幕后掮客（暗中操纵货物失踪案）",
        "convergence": "较高",
        "story_richness": CFG["richness"],
    }
    print("[*] 开局（强化模式，含首章同步蒸馏） ...", flush=True)
    evs, st, err = stream("/api/sessions/start", start_body, timeout=1200)
    check("start:无error事件", err is None, f"err={err}")
    check("start:返回state", isinstance(st, dict), "")
    if isinstance(st, dict):
        check("start:game_ready", st.get("game_ready") is True, f"game_ready={st.get('game_ready')}")
        check("start:plot_ready", st.get("plot_ready") is True, f"plot_ready={st.get('plot_ready')}")
        at = st.get("anchor_timeline") or {}
        cur = at.get("current") or {}
        check("start:首章锚点就绪", bool(cur.get("title")), f"current={cur.get('title')}")
        check("start:mode强化", str(st.get("mode") or "").startswith("强化"), f"mode={st.get('mode')}")
        check("start:story_richness一致", st.get("story_richness") == CFG["richness"],
              f"richness={st.get('story_richness')} scene_budget={st.get('scene_budget')}")
    first_text = last_assistant_text(evs)
    assert_no_raw_json(first_text, "start:剧情大概")
    assert_public_state(st or {}, "start")

    # ---------- 两步确认 ----------
    def send_msg(msg: str, label: str) -> tuple[list, dict | None, str | None]:
        evs2, st2, err2 = stream(f"/api/sessions/{session_id}/messages", {"message": msg}, timeout=600)
        if err2:
            check(f"{label}:无error", False, f"err={err2}")
        return evs2, st2, err2

    _, st, _ = send_msg("确认金手指", "确认金手指")
    check("确认金手指:gf_confirmed", isinstance(st, dict) and st.get("gf_confirmed") is True,
          f"gf_confirmed={st.get('gf_confirmed') if isinstance(st, dict) else None}")
    _, st, _ = send_msg("确认开局", "确认开局")
    check("确认开局:opening_confirmed", isinstance(st, dict) and st.get("opening_confirmed") is True,
          f"opening_confirmed={st.get('opening_confirmed') if isinstance(st, dict) else None}")
    check("确认开局:round=1", isinstance(st, dict) and st.get("round") == 1,
          f"round={st.get('round') if isinstance(st, dict) else None}")
    check("确认开局:scene_gate", isinstance(st, dict) and st.get("scene_gate") is True,
          f"scene_gate={st.get('scene_gate') if isinstance(st, dict) else None}")

    # ---------- 主循环 ----------
    quest_short_done = quest_medium_done = quest_long_done = False
    prev_convergence_pos = None
    prev_history_len = None
    autoplay_choice_letter = None
    for rnd in range(1, CFG["max_rounds"] + 1):
        choice = None
        print(f"\n===== 第 {rnd} 回合 =====", flush=True)

        # 特殊操作（不占回合，在行动前执行）
        if rnd == 4 and not quest_short_done:
            code, q = http("POST", f"/api/sessions/{session_id}/quests/offer",
                           {"kind": "short", "difficulty": 0.4})
            if code == 200 and isinstance(q, dict) and isinstance(q.get("quest"), dict):
                check("offer:short标题中文", bool((q["quest"].get("title") or "").strip()),
                      f"title={q['quest'].get('title')}")
                qs = q["quest"]
                check("offer:short动态时限>0", int(qs.get("deadline_span") or 0) > 0,
                      f"deadline_span={qs.get('deadline_span')}")
                check("offer:short含奖励", isinstance(q.get("reward"), dict) and bool(q["reward"].get("items")),
                      f"reward={json.dumps(q.get('reward'), ensure_ascii=False)[:80]}")
                check("offer:short收束松弛", isinstance(q.get("reward"), dict) and
                      "convergence_relief" in q["reward"], "")
            else:
                check("offer:short成功", False, f"code={code} resp={json.dumps(q, ensure_ascii=False)[:120]}")

        if rnd == 5 and not quest_short_done:
            code, q = http("POST", f"/api/sessions/{session_id}/quests/accept")
            if code == 200:
                quest_short_done = True
                st_code, stq = http("GET", f"/api/sessions/{session_id}/quests")
                qbox = stq.get("quest") if isinstance(stq, dict) else {}
                check("accept:short状态active", qbox.get("status") == "active", f"status={qbox.get('status')}")
            else:
                check("accept:short成功", False, f"code={code} resp={q}")

        if rnd == 6:
            code, ap = http("POST", f"/api/sessions/{session_id}/autoplay-choice")
            if code == 200 and isinstance(ap, dict):
                ch = ap.get("choice")
                autoplay_choice_letter = ch
                check("托管:返回合法选项", isinstance(ch, str) and ch in "ABCDEF",
                      f"choice={ch} reason={ap.get('reason')}")
            else:
                check("托管:成功", False, f"code={code} resp={ap}")

        if rnd == 14 and not quest_medium_done:
            code, q = http("POST", f"/api/sessions/{session_id}/quests/offer",
                           {"kind": "medium", "difficulty": 0.5})
            if code == 200:
                code2, _ = http("POST", f"/api/sessions/{session_id}/quests/accept")
                if code2 == 200:
                    quest_medium_done = True
                    qs = q.get("quest") if isinstance(q, dict) else {}
                    check("offer:medium动态时限", int(qs.get("deadline_span") or 0) > 0,
                          f"deadline_span={qs.get('deadline_span')}")
            else:
                print(f"  [INFO] medium offer 暂不可用: code={code}", flush=True)

        if rnd == 16:
            code, ask1 = http("POST", f"/api/sessions/{session_id}/ask",
                              {"question": "UUDDLLRRBABAWHOSLOMSTING"})
            check("作弊码:武装", code == 200 and isinstance(ask1, dict) and ask1.get("wish_armed") is True,
                  f"code={code} wish_armed={ask1.get('wish_armed') if isinstance(ask1, dict) else None}")
            code, ask2 = http("POST", f"/api/sessions/{session_id}/ask",
                              {"question": "让主角获得一枚疗伤丹药"})
            check("作弊码:许愿", code == 200 and isinstance(ask2, dict) and ask2.get("wish_granted") is True,
                  f"code={code} wish_granted={ask2.get('wish_granted') if isinstance(ask2, dict) else None}")

        if rnd == 25 and not quest_long_done:
            code, q = http("POST", f"/api/sessions/{session_id}/quests/offer",
                           {"kind": "long", "difficulty": 0.6})
            if code == 200:
                code2, _ = http("POST", f"/api/sessions/{session_id}/quests/accept")
                if code2 == 200:
                    quest_long_done = True
                    qs = q.get("quest") if isinstance(q, dict) else {}
                    check("offer:long动态时限", int(qs.get("deadline_span") or 0) > 0,
                          f"deadline_span={qs.get('deadline_span')}")
            else:
                print(f"  [INFO] long offer 暂不可用: code={code}", flush=True)

        if rnd == 30:
            code, sv = http("POST", f"/api/sessions/{session_id}/save", {"save_id": "real-50-r30"})
            check("存档:成功", code == 200 and isinstance(sv, dict) and sv.get("saved") is True, f"code={code}")
            code, ld = http("POST", "/api/saves/load", {"save_id": "real-50-r30"})
            if code == 200 and isinstance(ld, dict):
                lstate = ld.get("state") or {}
                check("读档:game_ready", lstate.get("game_ready") is True, "")
                check("读档:回合为正整数", isinstance(lstate.get("round"), int) and lstate.get("round") >= 1,
                      f"loaded_round={lstate.get('round')}")
                check("读档:任务面板一致", ("quest" in lstate), "")
                assert_public_state(lstate, "读档")
            else:
                check("读档:成功", False, f"code={code} resp={ld}")

        # ---------- 本回合行动 ----------
        options = (st or {}).get("options") if isinstance(st, dict) else []
        if rnd == 6 and autoplay_choice_letter:
            opt = next((o for o in options if o.get("key") == autoplay_choice_letter), None)
            action = (f"选择{autoplay_choice_letter}：{opt.get('text')}"
                      if opt else f"选择{autoplay_choice_letter}")
        else:
            context = last_assistant_text(evs)
            choice = decide(options, context) if options else None
            opt = next((o for o in options if o.get("key") == choice), None)
            action = f"选择{choice}：{opt.get('text')}" if opt else "自由行动：继续推进当前目标"

        evs, st, err = send_msg(action, f"第{rnd}回合")
        if err:
            print(f"  [RETRY] 第{rnd}回合 error: {err}", flush=True)
            evs, st, err = send_msg("自由行动：继续推进当前目标", f"第{rnd}回合重试")

        if not isinstance(st, dict):
            check(f"第{rnd}回合:有state", False, "无 state")
            continue

        round_no = st.get("round")
        check(f"第{rnd}回合:round推进", isinstance(round_no, int) and round_no >= rnd, f"round={round_no}")
        gate = st.get("scene_gate")
        check(f"第{rnd}回合:scene_gate", gate is True,
              f"scene_gate={gate} reason={str(st.get('scene_gate_reason'))[:60]}")
        opts = st.get("options") or []
        check(f"第{rnd}回合:有选项", len(opts) >= 1, f"options={len(opts)}")
        if opts:
            check(f"第{rnd}回合:选项键合法", all(o.get("key") in "ABCDEF" for o in opts),
                  f"keys={[o.get('key') for o in opts]}")

        cs = st.get("convergence_state") or {}
        pos = cs.get("position")
        if isinstance(pos, (int, float)) and prev_convergence_pos is not None:
            drift = abs(float(pos) - float(prev_convergence_pos))
            check(f"第{rnd}回合:收束力漂移有界", drift <= 0.051, f"drift={drift:.4f}")
        if isinstance(pos, (int, float)):
            prev_convergence_pos = float(pos)

        quest = st.get("quest") or {}
        if quest.get("status") == "completed":
            granted = quest.get("granted") or quest.get("reward") or {}
            print(f"  [QUEST] 任务完成: {quest.get('title')} "
                  f"granted={json.dumps(granted, ensure_ascii=False)[:120]}", flush=True)
            check("任务结算:有奖励记录", True, f"title={quest.get('title')}")

        hist = st.get("history") or []
        if prev_history_len is not None and rnd % 10 == 0:
            check(f"第{rnd}回合:压缩发生(history变化)", len(hist) != prev_history_len or len(hist) < 60,
                  f"history={len(hist)}")
        prev_history_len = len(hist)

        assert_public_state(st, f"第{rnd}回合")
        assert_no_raw_json(last_assistant_text(evs), f"第{rnd}回合")

        log({
            "round": round_no, "chapter": st.get("current_chapter"),
            "chapter_round": st.get("chapter_round"), "scene_gate": gate,
            "convergence": pos, "effective": cs.get("effective"),
            "options": [o.get("key") for o in opts],
            "choice": choice, "quest": quest.get("status"),
            "history_len": len(hist),
        })

    # ---------- 收尾 + 导出 ----------
    code, sv = http("POST", f"/api/sessions/{session_id}/save", {"save_id": "real-50-final"})
    check("最终存档:成功", code == 200 and isinstance(sv, dict) and sv.get("saved") is True, f"code={code}")

    print("\n[*] 导出小说（faithful 风格）...", flush=True)
    code, exp = http("POST", f"/api/sessions/{session_id}/export-novel", {"style": "faithful"}, timeout=1200)
    if code == 200 and isinstance(exp, dict):
        chapters = exp.get("chapters") or []
        full = exp.get("full_text") or ""
        check("导出:章节非空", len(chapters) >= 1, f"chapters={len(chapters)}")
        check("导出:全文含正文", len(full) > 500, f"full_text={len(full)}字")
        check("导出:manifest正确", isinstance(exp.get("manifest"), dict)
              and exp["manifest"].get("style") == "faithful", "")
        assert_no_raw_json(full, "导出:无裸JSON/选项残留")
    else:
        check("导出:成功", False, f"code={code} resp={json.dumps(exp, ensure_ascii=False)[:150]}")

    # ---------- 汇总报告 ----------
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = [r for r in RESULTS if not r["ok"]]
    total = len(RESULTS)
    report = {
        "model": CFG["model"], "base_url": CFG["base_url"], "provider": CFG["provider"],
        "novel": os.path.basename(CFG["txt"]), "max_rounds": CFG["max_rounds"],
        "elapsed_sec": round(time.time() - START_TS, 1),
        "total_checks": total, "passed": passed, "failed": len(failed),
        "failed_items": failed,
        "results": RESULTS,
        "round_log": ROUND_LOG,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n===== 检验完成：{passed}/{total} 通过，{len(failed)} 失败 =====", flush=True)
    if failed:
        print("失败项：", flush=True)
        for r in failed:
            print(f"  - {r['step']}: {r['detail']}", flush=True)
    print(f"报告: {report_path}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(2)
