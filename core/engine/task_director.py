"""Precise, feasibility-aware task surfacing."""
from __future__ import annotations
from typing import Any, Mapping

def score_task(task: Mapping[str, Any], state: Mapping[str, Any], chapter_plan: Mapping[str, Any] | None = None) -> float:
    score = 0.0
    text = str(task.get("title") or task.get("objective") or task.get("text") or "").lower()
    chapter = str((chapter_plan or {}).get("title") or "").lower()
    if text and chapter and any(token in text for token in chapter.split() if token): score += 0.2
    if task.get("chapter") in (None, state.get("current_chapter")): score += 0.2
    if task.get("location") in (None, (state.get("state_memory") or {}).get("location", {}).get("name")): score += 0.15
    if task.get("player_relevant", True): score += 0.2
    if task.get("repeat_count", 0): score -= min(0.3, 0.08 * int(task.get("repeat_count", 0)))
    if task.get("feasibility") is not None: score += 0.25 * float(task.get("feasibility") or 0)
    return max(0.0, min(1.0, score))

def rank_tasks(tasks, state: Mapping[str, Any], chapter_plan: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = []
    for task in tasks or []:
        if isinstance(task, Mapping):
            row = dict(task); row["relevance_score"] = round(score_task(row, state, chapter_plan), 4); rows.append(row)
    return sorted(rows, key=lambda x: (-x["relevance_score"], str(x.get("id") or x.get("title") or "")))

def select_tasks(tasks, state: Mapping[str, Any], chapter_plan: Mapping[str, Any] | None = None, limit: int = 3) -> list[dict[str, Any]]:
    return rank_tasks(tasks, state, chapter_plan)[:max(0, int(limit))]
