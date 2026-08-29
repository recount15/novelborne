# -*- coding: utf-8 -*-
"""状态栏渲染：进度条 / Token 指示器 / 蒸馏灯。纯文本/HTML 生成，无 gradio 依赖。"""
from __future__ import annotations


def _distill_lamp(state, distillers):
    """锚点蒸馏后台状态灯；distillers 为由 app 层托管的注册表。"""
    distiller = (distillers or {}).get((state or {}).get("distill_key"))
    if not distiller:
        if (state or {}).get("distill_enabled") is False:
            return "锚点蒸馏 已关闭"
        return ""
    rows = list((distiller.status() or {}).values())
    counts = {"pending": 0, "in_progress": 0, "done": 0, "failed": 0}
    for row in rows:
        counts[row.get("status", "pending")] = counts.get(row.get("status", "pending"), 0) + 1
    return (f"锚点 {counts['done']}/{max(1, len(rows))} · 进行 {counts['in_progress']} · "
            f"失败 {counts['failed']}")


def _token_md(state):
    """底部状态栏的 Token 消耗汇总。"""
    state = state or {}
    ti, to = state.get("tok_in", 0), state.get("tok_out", 0)
    cache = state.get("tok_cache", 0)
    rate = f"{cache / ti * 100:.0f}%" if ti else "—"
    est = "（约）" if state.get("tok_est") else ""
    li, lo = state.get("tok_last", (0, 0))
    if not ti:
        return "🪙 **Token**：未开始"
    return (f"🪙 **Token{est}**：本回合 输入 {li:,} / 输出 {lo:,} ｜ "
            f"累计 输入 {ti:,}（缓存命中 {rate}）/ 输出 {to:,}")


def _token_title_md(state):
    """聊天区顶部的紧凑 Token 指示器，开始后持续可见。"""
    state = state or {}
    if not state.get("system"):
        return "### 命运引擎"
    ti, to = int(state.get("tok_in", 0) or 0), int(state.get("tok_out", 0) or 0)
    li, lo = state.get("tok_last", (0, 0))
    approx = "约 " if state.get("tok_est") else ""
    return f"### 命运引擎　`Token {approx}{ti + to:,}`　· 本回合 {int(li) + int(lo):,}"
