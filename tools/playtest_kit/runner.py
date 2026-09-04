# -*- coding: utf-8 -*-
"""真实模型全流程检验 runner（通用多供应商版）：由 tools/playtest_kit 调度。

覆盖：开局→两步确认→选项/自由行动→任务短中长→托管→作弊码→存读档→压缩→导出。
供应商 / 模型 / 思考模式 / base_url 全部由启动配置决定，与主程序同一套
fate_engine.thinking_kwargs 映射；决策体与剧情引擎分开调用，模拟真实玩家。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = ROOT / "outputs"
# 原著 TXT 不随工具包分发：必须通过 PLAYTEST_TXT 环境变量或启动配置 txt_path 注入。
TXT_PATH = Path(os.environ["PLAYTEST_TXT"]) if os.environ.get("PLAYTEST_TXT") else None
REPORT_PATH = OUT_DIR / "playtest_monitor_report.json"


def _resolve_txt(cfg: dict[str, Any] | None = None) -> Path:
    """解析本轮检验用的原著 TXT 路径：启动配置 > 环境变量 > 默认样本。"""
    if cfg:
        p = str(cfg.get("txt_path") or "").strip()
        if p:
            return Path(p)
    return TXT_PATH


def _http(base: str, method: str, path: str, body: Any = None,
          timeout: int = 300) -> tuple[int, Any]:
    url = base + path
    data = None
    hdrs: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
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


def _stream_events(base: str, path: str, body: dict[str, Any],
                   timeout: int = 1200) -> tuple[list[dict], dict | None, str | None]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="POST",
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


def _upload_novel(base: str, txt: Path) -> tuple[str, str]:
    boundary = "----fateplaytest" + str(int(time.time()))
    file_bytes = txt.read_bytes()
    filename = txt.name
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nnovel\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: text/plain\r\n\r\n".encode(),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        base + "/api/uploads", data=b"".join(parts), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["session_id"], resp["upload"]["upload_id"]


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


def run(rep, should_stop: Callable[[], bool]) -> None:
    from tools.playtest_kit.pipeline import wait_for_stop
    cfg = rep.config
    import os
    base = str(cfg.get("base") or "http://127.0.0.1:8000")
    api_key = str(getattr(rep, "private_config", {}).get("api_key") or
                  cfg.get("api_key") or cfg.get("_api_key") or
                  os.environ.get("FATE_API_KEY", ""))
    provider = str(cfg.get("provider") or "deepseek")
    model = str(cfg.get("model") or "")
    base_url = str(cfg.get("base_url") or "")
    thinking_mode = str(cfg.get("thinking_mode") or "auto")
    rounds_total = int(cfg.get("rounds") or 30)
    richness = int(cfg.get("story_richness") or 700)

    # 与主程序同源：内链 fate_engine 的思考参数映射，保证检验=真实玩法。
    import sys
    sys.path.insert(0, str(ROOT))
    from core import fate_engine as fe
    think_cfg = fe.provider_config(provider, base_url or None)
    final_base_url = (base_url or think_cfg["base_url"]).rstrip("/")
    internal_kwargs = fe.thinking_kwargs(provider, thinking_mode, "")
    # 决策体不需要思考链：直接给一个不带思考参数的最小请求体模板。
    decide_thinking: dict[str, Any] = {}
    for key in ("thinking", "extra_body"):
        pass

    rep.note(f"开始检验：{model} @ {final_base_url} ({provider}) · "
             f"思考模式={thinking_mode} · 计划 {rounds_total} 回合 · 故事丰富度 {richness}")
    rep.config.update({"display_model": f"{model} @ {final_base_url}"})

    # ---------- LLM 决策体（模拟真实玩家，直连同一服务） ----------
    def decide(options: list[dict], context: str) -> tuple[str, str]:
        opt_text = "\n".join(f"{o.get('key')}. {o.get('text')}" for o in options)
        prompt = (
            "你是互动剧情的玩家。根据最近剧情从下列行动选项中做出最合理的一项选择。"
            "第一行只回复一个字母（A-F），第二行用一句话说明理由。\n\n"
            f"最近剧情：\n{context[-1000:]}\n\n行动选项：\n{opt_text}"
        )
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
        }
        # 决策体关闭思考（deepseek/qwen 等用最小代价），zhipu 显式 disabled；
        # 不支持该字段的提供商会在服务端报错后自动去掉重试一次。
        if provider == "zhipu":
            body["thinking"] = {"type": "disabled"}
        url = final_base_url + "/chat/completions"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"})
        for attempt in (0, 1):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                content = str((resp.get("choices") or [{}])[0].get("message", {}).get("content", ""))
                letters = [ch for ch in content.upper() if ch in "ABCDEF"]
                for letter in letters:
                    if any(o.get("key") == letter for o in options):
                        reason_line = content.strip().splitlines()[-1][:60]
                        return letter, reason_line
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 0 and "thinking" in body:
                    body.pop("thinking")
                    req = urllib.request.Request(
                        url, data=json.dumps(body).encode("utf-8"), method="POST",
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"Bearer {api_key}"})
                    continue
                rep.note(f"决策体异常（fallback 首选项）：{exc}")
        return options[0]["key"], "决策失败默认首项"

    def public_clean(state: dict | None, label: str) -> bool:
        ok_all = True
        if not isinstance(state, dict):
            return rep.check(f"{label}:state有效", False, "state 非 dict")
        for key in ("system", "api_key", "request_kwargs", "persona_text", "nemesis_private"):
            ok_all &= rep.check(f"{label}:脱敏[{key}]", key not in state, "")
        body = last_texts[-1] if last_texts else ""
        if "```json" in body or '"genre"' in body:
            ok_all &= rep.check(f"{label}:无裸JSON", False, "出现裸 JSON")
        else:
            ok_all &= rep.check(f"{label}:无裸JSON", True, "")
        return ok_all

    last_texts: list[str] = []

    # ---------- 阶段0：bootstrap / 连接 / 上传 ----------
    rep.phase("阶段0", f"bootstrap 与模型连接（{model}）")
    code, boot = _http(base, "GET", "/api/bootstrap")
    rep.check("bootstrap:HTTP200", code == 200, f"code={code}")
    if isinstance(boot, dict):
        sr = boot.get("story_richness") or {}
        rep.check("bootstrap:故事丰富度配置下发", bool(sr), json.dumps(sr, ensure_ascii=False)[:80] if sr else "")
        zprov = next((p for p in (boot.get("providers") or []) if p.get("id") == provider), None)
        rep.check(f"bootstrap:{provider}供应商在列", zprov is not None,
                  f"models={len((zprov or {}).get('models') or [])}")
    code, tc = _http(base, "POST", "/api/models/test",
                     {"provider": provider, "base_url": base_url or None,
                      "api_key": api_key, "model": model})
    rep.check("models/test:连通", code == 200 and isinstance(tc, dict) and tc.get("ok"),
              str(tc.get("message"))[:100] if isinstance(tc, dict) else str(tc)[:100])
    if should_stop():
        return

    rep.phase("阶段1", "上传原著与开局")
    session_id, upload_id = _upload_novel(base, _resolve_txt(cfg))
    rep.session_id = session_id
    rep.check("上传TXT:成功", bool(session_id and upload_id), f"session={session_id[:12]}…")
    # 开局角色设定：与工具包同级的纯工具默认值（中性名字，不绑定任何具体作品）。
    start_body: dict[str, Any] = {
        "provider": provider, "base_url": base_url or None,
        "api_key": api_key, "model": model,
        "thinking_mode": thinking_mode, "thinking_param": "",
        "mode": "强化模式", "novel_upload_id": upload_id,
        "role": cfg.get("role") or "漂泊者", "timepoint": "故事开篇",
        "difficulty": "D4 普通",
        "golden_finger": cfg.get("golden_finger") or "残卷箴言（洞悉一丝先机）",
        "persona_preset": "苟道（稳健发育、保命优先）", "persona_custom": "",
        "distill_enabled": True,
        "companion_roster": [cfg.get("companion") or
                             {"name": "苏叶", "skill": "情报",
                              "background": "同行的观测者", "participation": 8}],
        "heroine_roster": [cfg.get("heroine") or
                           {"name": "周桐", "skill": "医术",
                            "background": "药铺掌柜", "participation": 7}],
        "heroine_mode": "单女主", "enable_nemesis": True,
        "nemesis_select": cfg.get("nemesis") or "幕后掮客（暗中操纵货物失踪案）",
        "convergence": "较高", "story_richness": richness,
    }
    t0 = time.time()
    evs, st, err = _stream_events(base, "/api/sessions/start", start_body, timeout=1800)
    rep.check("开局:无error事件", err is None, str(err)[:150] if err else "")
    rep.check("开局:game_ready", isinstance(st, dict) and st.get("game_ready") is True,
              f"耗时 {time.time()-t0:.0f}s")
    rep.check("开局:plot_ready", isinstance(st, dict) and st.get("plot_ready") is True, "")
    anchor_cur = ((st or {}).get("anchor_timeline") or {}).get("current") or {}
    rep.check("开局:首章锚点同步就绪", bool(anchor_cur.get("title")), f"current={anchor_cur.get('title')}")
    rep.check("开局:故事丰富度贯通", (st or {}).get("story_richness") == richness,
              f"scene_budget={json.dumps((st or {}).get('scene_budget'), ensure_ascii=False)}")
    first_text = last_assistant_text(evs)
    last_texts.append(first_text[-400:])
    rep.check("开局:剧情大概为通顺中文", bool(first_text.strip()) and '```' not in first_text,
              f"{len(first_text)}字")
    public_clean(st, "开局")
    if not isinstance(st, dict) or st.get("game_ready") is not True:
        rep.error = "开局失败，终止检验"
        return
    if should_stop():
        return

    # ---------- 两步确认 ----------
    rep.phase("阶段2", "金手指与开局确认")
    def send_msg(msg: str, label: str) -> tuple[list, dict | None, str | None]:
        evs2, st2, err2 = _stream_events(base, f"/api/sessions/{session_id}/messages", {"message": msg}, timeout=900)
        if err2:
            rep.check(f"{label}:无error", False, str(err2)[:160])
        return evs2, st2, err2

    _, st, _ = send_msg("确认金手指", "确认金手指")
    rep.check("确认金手指:gf_confirmed", isinstance(st, dict) and st.get("gf_confirmed") is True, "")
    t1 = time.time()
    evs, st, _ = send_msg("确认开局", "确认开局")
    gate_ok = isinstance(st, dict) and st.get("scene_gate") is True
    rep.check("确认开局:scene_gate通过", gate_ok,
              f"{time.time()-t1:.0f}s reason={str((st or {}).get('scene_gate_reason'))[:80]}")
    scene_v = (st or {}).get("scene_validation") or {}
    length_v = scene_v.get("length") or {}
    rep.round_update({"round": st.get("round") if st else None,
                      "chapter": st.get("current_chapter") if st else None,
                      "gate": "PASS" if gate_ok else "FAIL",
                      "chars": length_v.get("chars"),
                      "budget": f"{length_v.get('minimum')}–{length_v.get('maximum')}" if length_v else "",
                      "convergence": ((st or {}).get("convergence_state") or {}).get("position"),
                      "quest": ((st or {}).get("quest") or {}).get("status"),
                      "options": len((st or {}).get("options") or [])})
    if should_stop():
        return

    # ---------- 主循环 ----------
    quest_flags = {"short": False, "medium": False, "long": False}
    autoplay_letter = None
    prev_pos = None
    save_done = False
    cheats_done = False
    autoplay_done = False
    long_offer_tried = False
    for rnd in range(1, rounds_total + 1):
        if should_stop():
            return
        rep.emit("phase_live", {"text": f"第 {rnd}/{rounds_total} 回合推演中"})
        options = (st or {}).get("options") if isinstance(st, dict) else []

        if rnd == 3 and not quest_flags["short"]:
            code, q = _http(base, "POST", f"/api/sessions/{session_id}/quests/offer", {"kind": "short", "difficulty": 0.4})
            if code == 200 and isinstance(q, dict) and isinstance(q.get("quest"), dict):
                qs = q["quest"]
                rep.check("任务short:生成+动态时限", int(qs.get("deadline_span") or 0) > 0,
                          f"title={qs.get('title')} span={qs.get('deadline_span')}")
                rep.check("任务short:奖励含收束松弛", isinstance(q.get("reward"), dict) and "convergence_relief" in q["reward"], "")
                code2, _ = _http(base, "POST", f"/api/sessions/{session_id}/quests/accept")
                quest_flags["short"] = rep.check("任务short:接受为active", code2 == 200, f"code={code2}")
            else:
                rep.note(f"short 任务暂不可生成（可能上一任务 active）：code={code}")

        if rnd == 6 and not autoplay_done:
            code, ap = _http(base, "POST", f"/api/sessions/{session_id}/autoplay-choice")
            if code == 200 and isinstance(ap, dict) and isinstance(ap.get("choice"), str):
                autoplay_letter = ap["choice"]
                autoplay_done = rep.check("托管:返回合法选项", autoplay_letter in "ABCDEF",
                                          f"choice={autoplay_letter} reason={str(ap.get('reason'))[:50]}")
            else:
                rep.check("托管:成功", False, f"code={code}")

        if rnd == 8 and not cheats_done:
            code, a1 = _http(base, "POST", f"/api/sessions/{session_id}/ask", {"question": "UUDDLLRRBABAWHOSLOMSTING"})
            armed = code == 200 and isinstance(a1, dict) and a1.get("wish_armed") is True
            rep.check("作弊码:许愿闸门开启", armed, f"code={code}")
            if armed:
                code, a2 = _http(base, "POST", f"/api/sessions/{session_id}/ask", {"question": "让主角获得一枚疗伤丹药"})
                rep.check("作弊码:愿望实现", code == 200 and isinstance(a2, dict) and a2.get("wish_granted") is True, f"code={code}")
            cheats_done = True

        if rnd == 10 and not quest_flags["medium"]:
            code, q = _http(base, "POST", f"/api/sessions/{session_id}/quests/offer", {"kind": "medium", "difficulty": 0.5})
            if code == 200 and isinstance(q, dict) and isinstance(q.get("quest"), dict):
                qs = q["quest"]
                rep.check("任务medium:动态时限约3章", int(qs.get("deadline_span") or 0) > 0,
                          f"title={qs.get('title')} span={qs.get('deadline_span')}")
                code2, _ = _http(base, "POST", f"/api/sessions/{session_id}/quests/accept")
                quest_flags["medium"] = rep.check("任务medium:接受", code2 == 200, "")
            else:
                rep.note("medium 任务暂不可生成")

        if rnd >= max(12, rounds_total // 2) and not quest_flags["long"] and not long_offer_tried:
            long_offer_tried = True
            code, q = _http(base, "POST", f"/api/sessions/{session_id}/quests/offer", {"kind": "long", "difficulty": 0.6})
            if code == 200 and isinstance(q, dict) and isinstance(q.get("quest"), dict):
                qs = q["quest"]
                rep.check("任务long:动态时限约6章", int(qs.get("deadline_span") or 0) > 0,
                          f"title={qs.get('title')} span={qs.get('deadline_span')}")
                code2, _ = _http(base, "POST", f"/api/sessions/{session_id}/quests/accept")
                quest_flags["long"] = rep.check("任务long:接受", code2 == 200, "")
            else:
                rep.note("long 任务暂不可生成")

        if rnd == 14 and not save_done:
            code, sv = _http(base, "POST", f"/api/sessions/{session_id}/save", {"save_id": "monitor-save"})
            saved = code == 200 and isinstance(sv, dict) and sv.get("saved") is True
            rep.check("存档:成功", saved, f"code={code}")
            if saved:
                code, ld = _http(base, "POST", "/api/saves/load", {"save_id": "monitor-save"})
                lstate = ld.get("state") if code == 200 and isinstance(ld, dict) else None
                rep.check("读档:game_ready一致", isinstance(lstate, dict) and lstate.get("game_ready") is True, "")
                rep.check("读档:回合一致", isinstance(lstate, dict) and lstate.get("round") == (st or {}).get("round"),
                          f"saved_round={(st or {}).get('round')}")
                rep.check("读档:丰富度保持", isinstance(lstate, dict) and lstate.get("story_richness") == richness, "")
                rep.check("读档:任务面板在位", isinstance(lstate, dict) and "quest" in lstate, "")
                public_clean(lstate, "读档")
            save_done = True

        # —— 行动决策 ——
        if rnd % 4 == 3 and options:
            action = "自由行动：深入调查当前线索，推进主线目标"
            choice_label = "自由"
        elif autoplay_letter and rnd == 6:
            opt = next((o for o in options if o.get("key") == autoplay_letter), None)
            action = f"选择{autoplay_letter}：{opt.get('text')}" if opt else f"选择{autoplay_letter}"
            choice_label = f"托管{autoplay_letter}"
            autoplay_letter = None
        else:
            letter, reason = decide(options or [], last_texts[-1] if last_texts else "")
            opt = next((o for o in options if o.get("key") == letter), None)
            action = f"选择{letter}：{opt.get('text')}" if opt else "自由行动：继续推进当前目标"
            choice_label = f"{letter}|{reason}"

        t2 = time.time()
        evs, st, err = send_msg(action, f"第{rnd}回合")
        if err and "未通过机械门禁" not in str(err):
            evs, st, err = send_msg("自由行动：继续推进当前目标", f"第{rnd}回合重试")
        took = time.time() - t2
        text = last_assistant_text(evs)
        last_texts.append(text[-400:])
        if len(last_texts) > 6:
            last_texts.pop(0)

        st = st or {}
        gate = st.get("scene_gate") is True
        scene_v = st.get("scene_validation") or {}
        length_v = scene_v.get("length") or {}
        pos = ((st.get("convergence_state") or {}).get("position"))
        drift_note = ""
        if isinstance(pos, (int, float)) and prev_pos is not None:
            d = abs(float(pos) - prev_pos)
            drift_note = f"drift={d:.3f}" + ("" if d <= 0.051 else " ⚠️超界")
            if d > 0.051:
                rep.check(f"第{rnd}回合:收束力漂移有界", False, f"drift={d:.4f}")
        if isinstance(pos, (int, float)):
            prev_pos = float(pos)

        detail = f"{took:.0f}s 正文{length_v.get('chars', '?')}字 区间{length_v.get('minimum','?')}–{length_v.get('maximum','?')}"
        if not gate:
            detail += f" ⛔{str(st.get('scene_gate_reason'))[:70]}"
        rep.check(f"第{rnd}回合:门禁提交", gate, detail)
        rep.check(f"第{rnd}回合:选项产出", len((st.get("options") or [])) >= 1, choice_label)
        rep.round_update({"round": st.get("round"), "chapter": st.get("current_chapter"),
                          "chapter_round": st.get("chapter_round"),
                          "turn_budget": st.get("turn_budget"),
                          "gate": "PASS" if gate else "FAIL",
                          "chars": length_v.get("chars"),
                          "budget": f"{length_v.get('minimum')}–{length_v.get('maximum')}" if length_v else "",
                          "convergence": pos, "drift": drift_note,
                          "quest": (st.get("quest") or {}).get("status"),
                          "options": len(st.get("options") or []),
                          "action": choice_label, "took_sec": round(took, 1),
                          "preview": text.replace("\n", " ")[:90]})
        public_clean(st, f"第{rnd}回合")

        q_status = (st.get("quest") or {}).get("status")
        if q_status == "completed":
            rep.note(f"任务结算完成：{(st.get('quest') or {}).get('title')}（第{rnd}回合）")

    # ---------- 收尾导出 ----------
    if not should_stop():
        rep.phase("收尾", "最终存档与小说导出")
        code, sv = _http(base, "POST", f"/api/sessions/{session_id}/save", {"save_id": "monitor-final"})
        rep.check("最终存档:成功", code == 200 and isinstance(sv, dict) and sv.get("saved") is True, "")
        t3 = time.time()
        code, exp = _http(base, "POST", f"/api/sessions/{session_id}/export-novel", {"style": "faithful"}, timeout=1200)
        if code == 200 and isinstance(exp, dict):
            chapters = exp.get("chapters") or []
            full = exp.get("full_text") or ""
            rep.check("导出:章节非空", len(chapters) >= 1, f"chapters={len(chapters)} 用时{time.time()-t3:.0f}s")
            rep.check("导出:全文含正文", len(full) > 500, f"{len(full)}字")
            rep.check("导出:无选项残留", not any(k in full for k in ("<<<LOG>>>", "【强化模式场景预算】")), "")
        else:
            rep.check("导出:成功", False, f"code={code} {json.dumps(exp, ensure_ascii=False)[:100]}")

    # ---------- 报告落盘 ----------
    passed = sum(1 for c in rep.checks if c["ok"])
    failed = [c for c in rep.checks if not c["ok"]]
    report = {
        "model": model, "provider": provider, "base_url": final_base_url,
        "thinking_mode": thinking_mode, "base": base, "story_richness": richness,
        "rounds_planned": rounds_total, "elapsed_min": round((time.time() - (rep.started_at or 0)) / 60, 1),
        "total_checks": len(rep.checks), "passed": passed, "failed": len(failed),
        "failed_items": failed, "phases": rep.phases, "round_log": rep.rounds,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rep.note(f"报告已写入 outputs/playtest_monitor_report.json（{passed}/{len(rep.checks)} 通过）")


__all__ = ["run"]
