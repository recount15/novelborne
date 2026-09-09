"""Specialist story facade: read-only shadow planning or actual active cluster.

Active mode routes through turn_pipeline's explicit agent_cluster branch and
returns only a validated candidate. The existing application owns persistence.
"""
from __future__ import annotations
from typing import Any, Mapping
from core.engine.story_snapshot import build_snapshot
from core.engine.story_graph import build_graph
from core.engine.narrative_objective import active
from core.engine.story_weighting import event_weight
from core.services.story_context import ContextLayer, fit_layers
from core.services import choice_agent
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
        """Run the actual cluster and return a candidate; app owns the commit."""
        if self.mode != 'agent':
            raise RuntimeError('run_turn is only available in active agent mode')
        # Selection is explicit, independent of enhanced mode and paper stage.
        # A shallow outer copy preserves live nested revision/source observations;
        # the cluster freezes and deep-detaches everything passed to workers.
        candidate = dict(state)
        candidate['generation_strategy'] = 'agent_cluster'
        result = turn_pipeline.run_turn(candidate, *args, **kwargs)
        from core.services.generation_skills import validate_turn_output, require
        require(result != turn_pipeline.LEGACY, 'explicit_agent_legacy_rejected')
        validate_turn_output(result.narrative, result.options)
        return result
