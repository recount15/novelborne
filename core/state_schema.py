# -*- coding: utf-8 -*-
"""state 键注册表：事务键与双读收敛的单一来源（重构 Phase 2）。

背景（测绘报告 map_app.md §3）：state 是 45+ 键的 God-dict，
- on_send 的异常回滚依赖手工维护的 25 键白名单——新增键漏加即回滚残缺；
- convergence / story_richness / story_agent_mode 等在顶层键与
  start_params 嵌套键双写双读，散落 5+ 处 `state.get(x) or start_params.get(x)`。

本模块只做两件事：
1. ``TRANSACTIONAL_KEYS``：回合事务键 frozenset（on_send 快照/回滚的唯一来源）；
2. ``start_setting(state, key, default=None)``：顶层键优先、start_params 兜底的
   统一读取口（收敛双读模式）。

键清单演进规则：新增“回合内可变、异常需回滚”的键时，必须同步加进
TRANSACTIONAL_KEYS（评审点：git diff 本文件即知事务面变化）。
"""
from __future__ import annotations

from typing import Any, Mapping

# 回合事务键：on_send 开局快照、门禁失败/异常时整体回滚的键集合。
# 来源：原 app.py on_send 手工白名单（2026-08-30 收编）。
TRANSACTIONAL_KEYS: frozenset[str] = frozenset({
    # 回合计数与章回推进
    "round", "current_chapter", "chapter_round", "turn_budget", "total_chapters",
    "chapter_index",
    # 账本与记忆
    "ledger", "state_memory", "state_panel", "ripples", "last_ripple",
    # 回合上下文
    "active_members", "companions", "heroines", "lore", "lore_hits", "progress",
    "last_style", "last_trope", "last_compatibility_k",
    # token 统计
    "tok_in", "tok_out", "tok_cache", "tok_last", "tok_est",
    # 回合管线：碎锚进度 / 积势扣减标志 / 性格 pending 若不回滚，
    # 会与已回滚的 round、ripples 脱节（凭空退积势、重复结算）。
    "skill_profiles", "break_anchor", "broken_anchors", "anchors_shattered_from",
    # 回合管线结算（Phase 2 收编：quest 与收束状态同为回合内结算面）
    "quest", "convergence_state",
    # 宿敌私密容器与传闻跨回合传递
    "nemesis_private", "nemesis_rumor",
})


def start_setting(state: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """顶层键优先、start_params 兜底的统一读取（收敛双写双读模式）。

    convergence / story_richness / story_agent_mode 等设定在旧存档里
    可能只存在于 start_params，新代码两者都写——读取必须双保险。
    """
    top = state.get(key)
    if top is not None:
        return top
    params = state.get("start_params") or {}
    value = params.get(key)
    return value if value is not None else default
