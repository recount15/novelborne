"""Shadow-capable specialist story agent facade.

It orchestrates deterministic planning metadata without changing the public API.
The current implementation deliberately delegates prose generation to the existing
turn pipeline and records a safe shadow plan for incremental rollout.
"""
from __future__ import annotations
from typing import Any, Mapping
from core.engine.story_snapshot import build_snapshot
from core.engine.story_graph import build_graph
from core.engine.narrative_objective import active
from core.engine.story_weighting import event_weight
from core.services.story_context import ContextLayer, fit_layers
from core.services import choice_agent
from core.engine.turn_transaction import TurnTransaction
from core.engine.story_invariants import validate_narrative, validate_options
from core.services import turn_pipeline
from core.engine.plot_threading import prepare as prepare_plot_threads, generation_brief
from core.engine.sequence_feedback import recent_feedback, digest as feedback_digest
from core.engine.story_skills import SkillContext, thread_planning_skill, scene_design_skill, choice_precondition_skill, choice_coverage_skill
from core.engine.chapter_arc import build_chapter_arc, chapter_generation_brief
from core.engine.task_director import select_tasks
from core.engine.story_skills.character_chat import chat_skill

class StoryAgent:
    def __init__(self, *, mode:str='shadow', provider_limit:int=200000): self.mode=mode; self.provider_limit=provider_limit
    def prepare(self, state:Mapping[str,Any])->dict[str,Any]:
        snap=build_snapshot(state); graph=build_graph(state); current=int(state.get('round') or 0)
        weighted=[{**dict(e),'weight':event_weight(e,current)} for e in graph['events']]
        thread_map = prepare_plot_threads(state)
        feedback = recent_feedback(state.get('sequence_feedback') or [], current, 5)
        brief = generation_brief(thread_map)
        arc = build_chapter_arc(state, thread_map.to_dict())
        chapter_brief = chapter_generation_brief(arc, brief)
        tasks = select_tasks(state.get("task_registry") or state.get("tasks") or [], state, arc)
        skill_ctx = SkillContext(thread_map=thread_map.to_dict(), snapshot=snap.facts, brief={**brief, **chapter_brief, "tasks": tasks})
        planning = thread_planning_skill(skill_ctx)
        scene = scene_design_skill(skill_ctx)
        candidates = [dict(x) for x in (state.get('choice_candidates') or []) if isinstance(x, Mapping)]
        preconditions = choice_precondition_skill(skill_ctx, candidates)
        coverage = choice_coverage_skill(skill_ctx, preconditions.proposal.get('accepted') or candidates)
        layers=[ContextLayer('facts',str(snap.facts),0,True),ContextLayer('recent_ledger',str(snap.recent_turns),5),ContextLayer('plot_thread_map',str(thread_map.to_dict()),0,True),ContextLayer('feedback',str(feedback_digest(feedback)),5)]
        bundle=fit_layers(layers,self.provider_limit)
        return {'mode':self.mode,'snapshot_hash':snap.state_hash,'weighted_events':weighted,'objectives':active(state),'plot_thread_map':thread_map.to_dict(),'chapter_arc_plan':arc,'generation_brief':{**brief, **chapter_brief, "tasks": tasks},'skill_results':{'planning':planning.to_dict(),'scene':scene.to_dict(),'choice_preconditions':preconditions.to_dict(),'choice_coverage':coverage.to_dict()},'feedback_digest':feedback_digest(feedback),'context_audit':{k:v for k,v in bundle.items() if k!='layers'},'choice_path':choice_agent.public_options(choice_agent.select_diverse(choice_agent.filter_candidates(state.get('choice_candidates') or [],state)))}

    def run_turn(self, state, *args, **kwargs):
        """Run the established pipeline transactionally; shadow mode is read-only."""
        if self.mode != 'agent':
            raise RuntimeError('run_turn is only available in active agent mode')
        transaction = TurnTransaction(state)
        before = dict(transaction.before)
        transaction.advance('PLANNED')
        try:
            result = turn_pipeline.run_turn(transaction.candidate, *args, **kwargs)
            transaction.advance('GENERATED')
            if result != turn_pipeline.LEGACY:
                checks = (validate_narrative(getattr(result, 'narrative', '')), validate_options(getattr(result, 'options', [])))
                if not all(check['ok'] for check in checks):
                    raise ValueError(checks)
            transaction.advance('VALIDATED'); transaction.advance('COMMITTED')
            return result
        except Exception:
            transaction.advance('FAILED'); transaction.rollback()
            if isinstance(state, dict): state.clear(); state.update(before)
            raise
