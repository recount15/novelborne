from __future__ import annotations
import json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.engine.plot_threading import prepare, generation_brief
from core.engine.sequence_feedback import new_feedback, append_feedback, recent_feedback
from core.engine.sequence_review import review_turn


def run(rounds: int, seed: int = 17, failure_rate: float = 0.5):
    rng = random.Random(seed)
    state = {"round": 0, "current_chapter": 1, "sequence_feedback": [], "story_ledger": [], "story_graph": {"events": []}, "state_memory": {"scene": {"name": "起点"}, "knowledge": {"known": []}}, "objectives": [{"title": "推进主线", "status": "active"}]}
    degraded = 0
    for n in range(1, rounds + 1):
        state["round"] = n
        thread = prepare(state, user_input=f"行动 {n}")
        brief = generation_brief(thread)
        if rng.random() < failure_rate:
            row = new_feedback(round=n, turn_id=f"round-{n}", summary=f"回合 {n} 使用确定性降级", violations=[{"code":"injected_provider_failure"}], repairs=[{"action":"fallback"}], next_turn_directives=["继续保持时间、地点和角色状态连续"], degraded=True)
            degraded += 1
        else:
            row = review_turn(state=state, action=f"行动 {n}", narrative=f"角色完成行动 {n}并产生后果", options=[{"key": x, "text": f"选项{x}"} for x in "ABCDEF"], events=[{"event_id": f"e{n}", "round": n, "status": "resolved"}])
        state["sequence_feedback"] = append_feedback(state["sequence_feedback"], row)
        state["story_ledger"].append({"turn_id": f"round-{n}", "round": n, "narrative": f"行动 {n}", "committed": True})
        state["story_graph"]["events"].append({"event_id": f"e{n}", "round": n, "status": "resolved"})
        assert len(brief["must_progress"]) > 0
    recent = recent_feedback(state["sequence_feedback"], rounds + 1, 5)
    return {"rounds": rounds, "committed": len(state["story_ledger"]), "feedback": len(state["sequence_feedback"]), "degraded": degraded, "recent_rounds": [x["round"] for x in recent], "ok": len(state["story_ledger"]) == rounds and len(state["sequence_feedback"]) == rounds}

if __name__ == "__main__":
    print(json.dumps({str(n): run(n) for n in (5, 10, 30)}, ensure_ascii=False))
