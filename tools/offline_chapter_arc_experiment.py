from __future__ import annotations
import json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.engine.chapter_arc import build_chapter_arc, chapter_generation_brief, advance_chapter_arc
from core.engine.plot_threading import prepare
from core.engine.task_director import select_tasks
from core.engine.task_acceptance import evaluate

def run(rounds=30, seed=11):
    rng = random.Random(seed)
    state = {"round": 0, "current_chapter": 1, "chapter_index": {"chapters": [{"idx": 1, "title": "第一回 样章"}, {"idx": 2, "title": "第二回 续章"}]}, "sequence_feedback": [], "story_ledger": [], "objectives": [{"title": "推进主线", "status": "active"}], "task_registry": [{"id": "rescue", "title": "救援", "chapter": 1, "location": "城门", "feasibility": 1.0, "hard_facts": ["找到"], "soft_signals": ["救援"]}, {"id": "noise", "title": "远方杂事", "chapter": 9, "feasibility": 0.1}], "state_memory": {"scene": {"name": "城门"}, "location": {"name": "城门"}, "knowledge": {"known": []}}}
    plan = build_chapter_arc(state, {})
    degraded = 0
    for n in range(1, rounds + 1):
        state["round"] = n
        if n == 16:
            state["current_chapter"] = 2
            plan = build_chapter_arc(state, {})
        thread = prepare(state, user_input=f"行动{n}")
        brief = chapter_generation_brief(plan, thread.to_dict())
        ranked = select_tasks(state["task_registry"], state, plan)
        if rng.random() < 0.5:
            degraded += 1
        else:
            evaluate(ranked[0] if ranked else {}, action="找到并救援", narrative="推进主线", state=state)
        plan = advance_chapter_arc(plan, {"round": n})
        state["sequence_feedback"].append({"round": n, "next_turn_directives": ["保持章节目标"]})
        state["story_ledger"].append({"round": n, "committed": True})
        assert brief["internal"] and ranked and len(state["story_ledger"]) == n
    return {"rounds": rounds, "committed": len(state["story_ledger"]), "degraded": degraded, "chapter": state["current_chapter"], "ok": True}

if __name__ == "__main__":
    print(json.dumps({str(n): run(n) for n in (5, 10, 30)}, ensure_ascii=False))
