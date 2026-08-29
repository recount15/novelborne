import atexit
import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 直接以 `python tools/run_strengthened_playtest.py` 执行时，确保项目根可被导入。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import app
from core import engine
import core.engine.chapter_tools
import core.engine.plot_summary
import core.engine.anchor_distiller
import core.engine.persistence

BOOK = ROOT / "assets" / "data" / "samples" / "强化试玩书.txt"
# 运行时可写根目录隔离到系统临时目录，测试结束自动清理，不污染项目目录。
TMP = Path(tempfile.mkdtemp(prefix="fate_playtest_"))
atexit.register(shutil.rmtree, str(TMP), ignore_errors=True)

class Message:
    def __init__(self, role, content):
        self.role = role
        self.content = content

class Choice:
    def __init__(self, content):
        self.message = Message("assistant", content)

class Response:
    def __init__(self, content):
        self.choices = [Choice(content)]

class Delta:
    def __init__(self, content):
        self.content = content

class StreamChoice:
    def __init__(self, content):
        self.delta = Delta(content)

class Chunk:
    def __init__(self, content):
        self.choices = [StreamChoice(content)] if content else []
        self.usage = None

class FakeCompletions:
    def __init__(self):
        self.calls = []
        self.stream_count = 0
        self.bad_length_once = False
        self.bad_interaction_once = False
        self.raise_once = False

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("stream"):
            return Response(json.dumps({"genre":"边城悬疑","premise":"北墙裂痕牵出失踪军械","major_threads":["铜扣","旧册","回声"],"tone":"冷峻"}, ensure_ascii=False))
        if self.raise_once:
            self.raise_once = False
            raise RuntimeError("simulated transport failure")
        self.stream_count += 1
        prompt = str((kwargs.get("messages") or [{}])[-1].get("content") or "")
        if "当前第2章" in prompt:
            anchor_evidence = "城门风硬"
        elif "当前第3章" in prompt:
            anchor_evidence = "北墙的裂痕"
        else:
            anchor_evidence = "城门的风很硬"
        if self.bad_length_once:
            self.bad_length_once = False
            # 正文长度取丰富度区间中部：剥选项块统计后仍稳定落在 1000 档区间内。
            text = (f"{anchor_evidence}。阿岚点头，林秋守住退路，线索以代价收束。" * 20)[:950]
        elif self.bad_interaction_once:
            self.bad_interaction_once = False
            text = (f"{anchor_evidence}。守军交出北墙旧册，因此失踪军械的去向曝光，三处暗哨随后被撤换，线索以代价落定。" * 30)[:1000]
        else:
            seed = f"{anchor_evidence}。阿岚点头并交出北墙旧册，林秋守住退路，因此失踪军械的去向曝光，三处暗哨随后被撤换。回声指向旧水道，铜扣与旧册上的日期终于对上，线索以代价落定。"
            text = (seed * 20)[:1000]
            text += "\n\n1. 跟随回声进入旧水道\n2. 让阿岚封锁北门\n3. 请林秋审问信使\n4. 将铜扣交给老兵\n5. 伪造军械清单\n6. 观察远处马蹄\n7. 自由行动：描述你的决定"
        return [Chunk(text)]

class FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions()

fake = FakeClient()
app.fe.make_client = lambda api_key, provider, base_url: fake
# 将应用运行时可写根目录隔离到 outputs/tmp，避免污染项目运行产物。
app.fe.WRITABLE_DIR = str(TMP)

# 先真实走本地 TXT 切章和剧情摘要链
idx = engine.chapter_tools.split_file(BOOK, book_id="强化试玩书_playtest", output_root=TMP)
book_dir = TMP / "books" / "强化试玩书_playtest"
print("SPLIT", idx["source_encoding"], len(idx["chapters"]), [(x["idx"], x["chars"], x["turn_budget"]) for x in idx["chapters"]])
summary = engine.plot_summary.generate_plot_summary(book_dir, model=lambda prompt: {"premise":"北墙裂痕牵出失踪军械","genre":"边城悬疑"}, max_samples=3, max_chars_per_chapter=2500)
print("SUMMARY", summary["chapter_count"], summary["selected_chapters"], summary["summary"])

# 先验证本地切章和摘要链；正式锚点在 on_start 后写入应用实际生成的书籍目录。
print("ANCHOR_SOURCE_READY", book_dir)

args = dict(provider="custom", base_url="http://fake.local/v1", api_key="fake", remember=False, model="fake-model", thinking_mode="auto", thinking_param="", mode="强化模式", work="", novel_file=str(BOOK), fragment="", role="记录员", timepoint="故事开篇", difficulty="D4 普通", gf="先知/记忆（预知节点）", gf_custom="", persona_preset="苟道（稳健发育、保命优先）", persona_custom="", persona_file=None, distill_enabled=False, companion_roster=[{"name":"阿岚","skill":"守城","background":"边城守卫","participation":9}], heroine_roster=[{"name":"林秋","skill":"医术","background":"药铺掌柜","participation":8}], companion_count=1, heroine_count=1, heroine_mode="单女主", enable_nemesis=False, nemesis_select="", nemesis_file=None, story_richness=1000)

def last_state(gen):
    out = None
    for out in gen:
        pass
    return out

start_outputs = list(app.on_start(**args))
print("ON_START_YIELDS", len(start_outputs))
chat, state = start_outputs[-1][0], start_outputs[-1][1]
print("START_STATE", {k:state.get(k) for k in ("plot_ready","gf_confirmed","opening_confirmed","opening_phase","current_chapter","chapter_round","turn_budget","round","session")})
print("START_CHAT_LAST", chat[-1]["content"][:180].replace("\n"," / "))

# 应用会按上传文件名生成 book_id；锚点必须落入这一实际目录，不能沿用预切章目录。
app_book_dir = Path(app._book_dir(state["chapter_index"]))
for n, title, quote in [(1, "城门风硬", "城门的风很硬"), (2, "旧册暗记", "城门风硬"), (3, "北墙回声", "北墙的裂痕")]:
    text = (app_book_dir / "chapters" / f"{n:04d}.txt").read_text(encoding="utf-8")
    q = quote if quote in text else "城门的风很硬"
    anchor = {"chapter": n, "title": title, "summary": "守卫与同伴追查军械线索", "events": ["换岗", "查证"], "characters": ["阿岚", "沈砚", "林秋"], "world": "边城北墙", "foreshadowing": ["远处马蹄"], "quotes": [q], "ripple": "线索汇聚到北墙"}
    engine.anchor_distiller.validate_anchor(anchor, text, n)
    (app_book_dir / "anchors").mkdir(exist_ok=True)
    (app_book_dir / "anchors" / f"{n:04d}.json").write_text(json.dumps(anchor, ensure_ascii=False), encoding="utf-8")
print("ANCHORS", app_book_dir, sorted(p.name for p in (app_book_dir / "anchors").glob("*.json")))

# Confirmation gate: GF then opening, with an invalid interstitial action.
def send(msg, chat, state):
    vals = list(app.on_send("custom", "http://fake.local/v1", "fake", "fake-model", "auto", "", msg, chat, state))
    return vals[-1], vals

(chat, _, state, *_), vals = send("随便开始", chat, state)
print("PRE_GF_REJECT", vals[-1][0][-1]["content"], state.get("round"), state.get("gf_confirmed"))
(chat, _, state, *_), vals = send("确认金手指", chat, state)
print("GF_CONFIRM", state.get("gf_confirmed"), state.get("opening_confirmed"), state.get("round"), chat[-1]["content"])
(chat, _, state, *_), vals = send("确认开局", chat, state)
print("OPENING_AND_FIRST", {k:state.get(k) for k in ("gf_confirmed","opening_confirmed","opening_started","round","current_chapter","chapter_round","turn_budget","scene_gate")})
print("FIRST_REPLY_LEN", len(chat[-1]["content"]), "VALID", state.get("scene_validation"))

# 首幕已通过门禁；长度/交互失败先走自动定向重写（fake 第二次返回合格稿，
# 应救回并提交），模型异常仍回滚——三种路径都不得留下半提交状态。
def snapshot(current):
    return {k: copy.deepcopy(current.get(k)) for k in ("round", "current_chapter", "chapter_round", "turn_budget", "ledger", "state_memory", "ripples", "active_members", "tok_out")}

def assert_rolled_back(before, current, label):
    checks = {k: current.get(k) == value for k, value in before.items()}
    assert all(checks.values()), f"{label} rollback failed: {checks}"
    print(label, checks, "reason", current.get("scene_gate_reason"), "reply", chat[-1]["content"][:180])

fake.chat.completions.bad_length_once = True
streams_before = fake.chat.completions.stream_count
pre_fail = snapshot(state)
(chat, _, state, *_), vals = send("调查北墙裂痕并让阿岚与林秋协作", chat, state)
assert state.get("scene_gate") is True, state.get("scene_gate_reason")
assert fake.chat.completions.stream_count >= streams_before + 2  # 初稿 + 定向重写
assert state.get("round", 0) > pre_fail["round"], "重写救回后应正常提交推进"
print("FAIL_LENGTH_REGEN_OK streams", fake.chat.completions.stream_count - streams_before,
      "round", state.get("round"))

# 清除首幕写入的冷却，强制本用例拥有活跃角色，验证“点名+动作”机械门禁的重写救回。
for roster_key in ("companions", "heroines"):
    for member in state.get(roster_key) or []:
        if isinstance(member, dict):
            member.pop("last_appeared_round", None)
            member["cooldown_remaining"] = 0
fake.chat.completions.bad_interaction_once = True
streams_before = fake.chat.completions.stream_count
pre_fail = snapshot(state)
(chat, _, state, *_), vals = send("让阿岚与林秋协作查验北墙", chat, state)
assert state.get("scene_gate") is True, state.get("scene_gate_reason")
assert fake.chat.completions.stream_count >= streams_before + 2
print("FAIL_INTERACTION_REGEN_OK streams", fake.chat.completions.stream_count - streams_before,
      "round", state.get("round"))

fake.chat.completions.raise_once = True
pre_fail = snapshot(state)
(chat, _, state, *_), vals = send("继续追查北墙裂痕", chat, state)
assert state.get("scene_gate") is False
assert state.get("scene_gate_reason") == "模型服务调用失败，已回滚本回合运行状态。"
assert_rolled_back(pre_fail, state, "FAIL_MODEL_ROLLBACK")

# 三个有效回合，验证提交、章节预算和翻章。
for i, msg in enumerate(["跟随回声查验旧水道", "保护信使并核对旧册", "在北墙下设下暗号"], 1):
    (chat, _, state, *_), vals = send(msg, chat, state)
    assert state.get("scene_gate") is True
    print("TURN", i, {k:state.get(k) for k in ("round","current_chapter","chapter_round","turn_budget","scene_gate","scene_validation")}, "len", len(chat[-1]["content"]), "members", [m.get("name") for m in state.get("active_members",[])], "ripple", state.get("last_ripple"))

save_path = engine.persistence.save_state(state, save_id="playtest", root=TMP, start_params=state.get("start_params"))
loaded = engine.persistence.load_state("playtest", root=TMP)
print("SAVE", save_path, {k:loaded.get(k) for k in ("round","current_chapter","chapter_round","turn_budget")}, "ledger_keys", sorted((loaded.get("ledger") or {}).keys()))
# Bad save fallback must preserve current state.
(TMP / "saves" / "bad.json").write_text("{bad", encoding="utf-8")
fallback = engine.persistence.load_state("bad", root=TMP, current_state=state)
print("BAD_SAVE_FALLBACK", fallback.get("round"), fallback.get("current_chapter"), fallback.get("ledger") == state.get("ledger"))
print("FAKE_CALLS", len(fake.chat.completions.calls), "streams", fake.chat.completions.stream_count)
