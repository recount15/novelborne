# -*- coding: utf-8 -*-
"""逐条验证用户在对话中提出的全部需求；每条给出 PASS/FAIL 与实测证据。"""
from __future__ import annotations
import inspect, json, re, pathlib, collections

ROOT = pathlib.Path(__file__).resolve().parent
RESULTS: list[tuple[str, str, str]] = []


def check(name, ok, evidence):
    RESULTS.append((name, "PASS" if ok else "FAIL", str(evidence)))


def r01_enhanced_requires_txt():
    from core import app
    src = inspect.getsource(app.on_start)
    ok = "强化模式必须上传 TXT 原著" in src and "不能使用作品库" in src
    check("1 强化模式必须上传TXT，不用作品库", ok, "on_start 含 TXT 门禁分支")


def r02_split_then_summary():
    from core import app
    src = inspect.getsource(app.on_start)
    ok = src.index("_split_uploaded_book") < src.index("generate_plot_summary")
    check("2 先本地切章再剧情摘要", ok, "切章调用位于摘要之前")


def r03_plot_ready_gate():
    from core.engine import opening_flow
    st = opening_flow.initial_state()
    blocked = not opening_flow.gate(st, "started")["ok"]
    after = opening_flow.mark_txt_uploaded(st, txt_path="a.txt", chapters=[{"idx": 1}])["state"]
    still = not opening_flow.gate(after, "gf_confirmed")["ok"]
    check("3 剧情准备完成前禁止进入正式游戏", blocked and still, f"init阻断={blocked}, txt后仍阻断={still}")


def r04_chat_gf_confirm():
    from core import app
    ok = app._chat_opening_confirmation("确认金手指") == "gf" and \
         app._chat_opening_confirmation("确认无金手指") == "gf" and \
         app._chat_opening_confirmation("确认开局") == "opening" and \
         app._chat_opening_confirmation("随便走走") == ""
    src = inspect.getsource(app.on_send)
    gated = "请在正式游戏聊天框输入“确认金手指”" in src
    check("4 金手指在正式聊天框确认", ok and gated, f"识别={ok}, on_send拦截={gated}")


def r05_works_1000():
    import fate_engine as fe
    works = fe.list_works()
    check("5 普通模式作品库1000部", len(works) >= 1000, f"list_works={len(works)}")


def r06_protagonist_models():
    import fate_engine as fe
    models = fe.list_character_models()
    standard = list((ROOT / "personas" / "standard").glob("*.md"))
    enhanced = list((ROOT / "personas" / "enhanced").glob("*.md"))
    labels = [label for label, _ in models]
    ok = (len(models) == len(standard) + len(enhanced) and len(models) > 0
          and len(labels) == len(set(labels)))
    check("6 主角性格模型按目录可用", ok,
          f"模型={len(models)}, 标准={len(standard)}, 增强={len(enhanced)}")


def r07_08_pools():
    from core.engine import catalog
    cards = catalog.load_character_pool()
    c = collections.Counter(x.role for x in cards)
    total = sum(c.values())
    ok = total > 0 and c["伙伴"] > 0 and c["single_heroine"] > 0 and c["multi_heroine"] > 0
    check("7 配角(伙伴)性格池可用", ok, f"总数={total}, 伙伴={c['伙伴']}")
    check("8 单女主/多女主性格池可用", ok,
          f"single={c['single_heroine']}, multi={c['multi_heroine']}")


def r09_no_abstract_tags():
    bad = ("莽夫", "苟道", "乐子人")
    hits = []
    targets = list((ROOT / "data").glob("*.json")) + list((ROOT / "personas" / "standard").glob("*.md"))
    for p in targets:
        text = p.read_text(encoding="utf-8", errors="ignore")
        for token in bad:
            if token in text:
                hits.append(f"{p.relative_to(ROOT)}:{token}")
    check("9 删除抽象标签改具体行为", not hits, f"命中={hits[:5] or '无'}")


def r10_corpus_100x():
    d = json.loads((ROOT / "data" / "layered_corpus.json").read_text(encoding="utf-8"))
    n = len(d["templates"])
    check("10 语料库扩充约100倍", n >= 800, f"templates={n}（种子8条，约{n // 8}倍）")


def r11_three_layer_random():
    from core.engine import catalog
    d = catalog.load_layered_corpus()
    idx = d["index"]
    a = catalog.sample_layered_corpus(theme="边城生计", mechanism="线索递进", style="冷静纪实", seed=11)
    b = catalog.sample_layered_corpus(theme="边城生计", mechanism="线索递进", style="冷静纪实", seed=11)
    stable = a and b and a["id"] == b["id"]
    filtered = a and a["theme"] == "边城生计" and a["mechanism"] == "线索递进" and a["style"] == "冷静纪实"
    ok = len(idx.get("theme", ())) >= 3 and len(idx.get("mechanism", ())) >= 3 and len(idx.get("style", ())) >= 3
    check("11 三层分类+代码随机抽取", ok and stable and filtered,
          f"轴={ {k: len(v) for k, v in idx.items()} }, 稳定={stable}, 过滤={filtered}")


def r12_unlimited_count():
    from core.engine.roster_schema import normalize_roster
    rows = [{"name": f"角色{i}", "role": "伙伴"} for i in range(37)]
    n = len(normalize_roster(rows)["slots"])
    from core.engine.roster import build_companion_block
    block = build_companion_block([{"name": f"伙伴{i}"} for i in range(9)])
    kept = block.count("- 伙伴")
    check("12 伙伴/女主数量不受限", n == 37 and kept == 9, f"schema={n}, 提示块保留={kept}")


def r13_one_slot_at_a_time():
    from core import app
    src = inspect.getsource(app.build_app)
    single_editor = src.count("fe-roster-editor") >= 2
    no_fixed = "companion_2 = gr" not in src and "heroine_3 = gr" not in src
    rows, summary, status, card, *cleared = app._append_dynamic_slot([], "甲", "情报搜集", "", None, "背景", -1, 5, "伙伴", "单女主")
    cleared_name = getattr(cleared[0], "get", lambda k, d=None: None)("value") if hasattr(cleared[0], "get") else None
    check("13 逐个显示角色输入槽位", single_editor and no_fixed and len(rows) == 1,
          f"单编辑行={single_editor}, 无固定三槽={no_fixed}, 加入后行数={len(rows)}")


def r14_skill_sources():
    from core.engine.roster_schema import normalize_skill_source, RosterConfig
    preset = normalize_skill_source("情报搜集")
    custom = normalize_skill_source("情报搜集", custom="谈判施压")
    upload = normalize_skill_source("", upload="skills/a.md")
    slot = RosterConfig.from_mapping([{"name": "甲", "skill_upload": "skills/a.md", "skill_label": "文件技能"}]).slots[0]
    ok = preset["source"] == "preset" and custom["source"] == "custom" and upload["source"] == "upload" and slot.skill == "文件技能"
    check("14 预设/自定义/上传三类skill", ok, f"{preset['source']}/{custom['source']}/{upload['source']}, 上传标签={slot.skill}")


def r15_skill_one_line():
    from ui.roster_form import skill_value
    v = skill_value("情报搜集", "谈判施压")
    theme = (ROOT / "ui" / "theme.py").read_text(encoding="utf-8")
    css = "fe-skill-row" in theme and "white-space: nowrap" in theme
    check("15 skill只占一行", "\n" not in v and css, f"值={v!r}, 单行CSS={css}")


def r16_card_fields():
    from core import app
    html = app._card_html("甲", "伙伴", "情报搜集", "背景", 5, 2)
    need = ("角色", "技能", "作用域", "代价", "冷却", "限制")
    missing = [k for k in need if k not in html]
    theme = (ROOT / "ui" / "theme.py").read_text(encoding="utf-8")
    hover = ":hover" in theme or ":focus" in theme
    check("16 角色卡含基本信息/技能/作用域/代价/冷却/限制", not missing and hover,
          f"缺失={missing or '无'}, 悬停或聚焦样式={hover}")


def r17_heroine_mode_pool():
    from core.engine.roster_schema import RosterConfig, RosterValidationError
    try:
        RosterConfig.from_mapping({"heroine_mode": "单女主", "slots": [{"name": "甲", "role": "女主"}, {"name": "乙", "role": "女主"}]})
        single_guard = False
    except RosterValidationError:
        single_guard = True
    multi = RosterConfig.from_mapping({"heroine_mode": "多女主", "slots": [{"name": "甲", "role": "女主"}, {"name": "乙", "role": "女主"}]})
    from core import app
    pool_single = app._heroine_pool_choices("单女主")
    pool_multi = app._heroine_pool_choices("多女主")
    disjoint = pool_single and pool_multi and not (set(pool_single) & set(pool_multi))
    check("17 先选单/多女主再从对应池选择", single_guard and len(multi.slots) == 2 and disjoint,
          f"单女主限制={single_guard}, 多女主={len(multi.slots)}, 单池={len(pool_single)}, 多池={len(pool_multi)}, 不重叠={bool(disjoint)}")


def r18_participation_1_9():
    from core.engine.roster_schema import normalize_participation
    ok = normalize_participation(0) == 1 and normalize_participation(99) == 9 and normalize_participation(6) == 6
    check("18 参与度1-9档", ok, "0→1, 99→9, 6→6")


def r19_participation_dynamic():
    import engine
    low = engine.compute_participation(pool_size=8, chapter=1, round_no=5, last_appeared_round=4,
                                       action_relevance=0.0, relationship_state="普通", role="伙伴")
    high = engine.compute_participation(pool_size=8, chapter=1, round_no=5, last_appeared_round=None,
                                        action_relevance=1.0, relationship_state="亲密", role="女主")
    keys = {"level", "probability", "appear", "cooldown", "cooldown_remaining", "relevance"}
    from core import app
    state = {"companions": [{"name": "甲", "participation": 9}], "heroines": [], "round": 3, "current_chapter": 1}
    members = app._runtime_character_constraints(state, "甲 与我同行")
    wrote_back = "last_participation" in state["companions"][0]
    ok = keys <= set(high) and high["probability"] > low["probability"] and wrote_back
    check("19 参与度动态影响出场/冷却/相关性", ok,
          f"低相关p={low['probability']}, 高相关p={high['probability']}, 出场={len(members)}, 冷却回写={wrote_back}")


def r20_scene_length():
    import engine
    ok = engine.validate_scene_length("x" * 1000)["valid"] and \
         not engine.validate_scene_length("x" * 849)["valid"] and \
         not engine.validate_scene_length("x" * 1151)["valid"]
    check("20 每回合正文850-1150字", ok, f"目标={engine.SCENE_TARGET}, 区间={engine.SCENE_MIN}-{engine.SCENE_MAX}")


def r21_interaction_and_anchor():
    import engine
    block = engine.build_interaction_constraint_block([{"name": "甲"}, {"name": "乙"}], action="联手查案", relationship_state="协作")
    anchor_block = engine.build_anchor_constraint_block("城门失守", action="联手查案", compatibility=3)
    good = engine.validate_anchor_convergence(
        "守军关闭内门却迟了一步，叛军撞开城门涌入街巷；城门失守后，守军被迫退守钟楼，代价是三处哨点断联。",
        "城门失守",
    )["valid"]
    bad = engine.validate_anchor_convergence("这一夜风平浪静，什么都没有变化。", "城门失守")["valid"]
    check("21 角色交互生成并收束锚点", "甲" in block and "城门失守" in anchor_block and good and not bad,
          f"交互块含角色={'甲' in block}, 锚点块={'城门失守' in anchor_block}, 收束判定={good}/{bad}")


def r22_hard_gate():
    from core import app
    src = inspect.getsource(app.on_send)
    has_gate = "scene_gate" in src and "未通过机械门禁" in src
    rollback = "transaction_snapshot" in src and "for key, value in transaction_snapshot.items()" in src
    no_commit = src.index("if not state[\"scene_gate\"]") < src.index("_commit_reply_memory(state, acc")
    check("22 字数/交互/锚点由代码门禁", has_gate and rollback and no_commit,
          f"门禁={has_gate}, 回滚={rollback}, 失败早于提交={no_commit}")


def r23_opening_autosave():
    import engine.persistence, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        state = {"mode": "强化模式", "system": "s", "history": [], "round": 1,
                 "companions": [{"name": "甲", "participation": 5}], "heroines": [],
                 "heroine_mode": "多女主", "start_params": {"difficulty": "D4", "golden_finger": "无"}}
        engine.persistence.save_state(state, root=tmp, start_params=state["start_params"])
        loaded = engine.persistence.load_state(root=tmp)
        ok = loaded and loaded.get("mode") == "强化模式" and loaded.get("heroine_mode") == "多女主" and loaded.get("companions")
        check("23 每次开局完整自动存档", bool(ok), f"恢复字段={sorted(list(loaded or {}))[:8]}")


def r24_api_key_masked():
    import engine.persistence, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        state = {"mode": "基础模式", "system": "s", "history": [], "api_key": "sk-secret-123",
                 "start_params": {"api_key": "sk-secret-123", "nested": {"access_token": "tok"}}}
        engine.persistence.save_state(state, root=tmp, start_params=state["start_params"])
        blob = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in pathlib.Path(tmp).rglob("*") if p.is_file())
        check("24 API Key脱敏不写入存档", "sk-secret-123" not in blob and "tok" not in blob,
              f"存档含明文Key={'sk-secret-123' in blob}")


def r25_modern_ui():
    from ui import theme
    css = theme.get_css()
    need = ["--fe-bg", "--fe-text", "fe-state-error", "fe-state-confirm", "fe-character-card", "@media"]
    missing = [k for k in need if k not in css]
    check("25 现代化UI/错误态/确认态/角色卡/移动端", not missing, f"缺失={missing or '无'}")


def r26_module_split():
    counts = {p.name: len(p.read_text(encoding="utf-8").splitlines())
              for p in [ROOT / "app.py", ROOT / "fate_engine.py"]}
    pkgs = [d.name for d in ROOT.iterdir() if d.is_dir() and (d / "__init__.py").exists()]
    ok = {"engine", "ui", "memory", "lore", "prompts"} <= set(pkgs)
    check("26 代码按职责拆分", ok, f"包={sorted(pkgs)}, app.py={counts['app.py']}行")


def r27_no_android():
    hits = []
    excluded = {ROOT / "verify_requirements.py"}
    allowed_suffixes = {".py", ".md", ".bat", ".spec", ".txt"}
    for p in ROOT.rglob("*"):
        relative_parts = p.relative_to(ROOT).parts
        if (not p.is_file() or p in excluded or "__pycache__" in p.parts
                or relative_parts[:1] in {("outputs",), ("runtime",), ("saves",)}
                or p.suffix not in allowed_suffixes):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for token in ("Termux", "termux", "pwa=True", "安卓"):
            if token in text:
                hits.append(f"{p.relative_to(ROOT)}:{token}")
    check("27 不再支持安卓端", not hits, f"命中={hits[:5] or '无'}")


def main():
    for fn in (r01_enhanced_requires_txt, r02_split_then_summary, r03_plot_ready_gate, r04_chat_gf_confirm,
               r05_works_1000, r06_protagonist_models, r07_08_pools, r09_no_abstract_tags, r10_corpus_100x,
               r11_three_layer_random, r12_unlimited_count, r13_one_slot_at_a_time, r14_skill_sources,
               r15_skill_one_line, r16_card_fields, r17_heroine_mode_pool, r18_participation_1_9,
               r19_participation_dynamic, r20_scene_length, r21_interaction_and_anchor, r22_hard_gate,
               r23_opening_autosave, r24_api_key_masked, r25_modern_ui, r26_module_split, r27_no_android):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            RESULTS.append((fn.__name__, "ERROR", f"{type(exc).__name__}: {exc}"))
    width = max(len(n) for n, _, _ in RESULTS)
    for name, status, evidence in RESULTS:
        print(f"[{status:5}] {name.ljust(width)}  {evidence}")
    bad = [r for r in RESULTS if r[1] != "PASS"]
    print(f"\n合计 {len(RESULTS)} 项，通过 {len(RESULTS) - len(bad)}，未通过 {len(bad)}")


if __name__ == "__main__":
    main()
