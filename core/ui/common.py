# -*- coding: utf-8 -*-
"""通用 UI 纯助手：会话日志 / 章节工具 / 涟漪动作评分 / 桥段库。"""
from __future__ import annotations

import json
import os
import time

from core import fate_engine as fe
from core import engine
import core.engine.chapter_tools
import core.engine.runtime_mechanics
from core.lore import LoreInjector, load_entries

LOG_DIR = os.path.join(fe.WRITABLE_DIR, "logs")
# 资源路径：assets 布局（源码与 PyInstaller 捆绑一致）。
# 旧路径 BASE_DIR/lore、BASE_DIR/data 不存在，曾致世界书与桥段库静默加载为空。
WORLD_BOOK_PATH = os.path.join(fe.BASE_DIR, "assets", "lore", "default_worldbook.json")
DATA_DIR = os.path.join(fe.BASE_DIR, "assets", "data")

# ---------- 会话日志 ----------


def _new_session_log(settings_line):
    """每局创建一个运行日志文件（logs/ 下），返回路径。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, time.strftime("session_%Y%m%d_%H%M%S") + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 命运引擎 · 运行日志\n\n" + settings_line + "\n")
        return path
    except Exception:
        return ""


def _append_log(path, text):
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


# ---------- Token 统计 ----------


def _accum_tokens(state, usage_box, est_in=0, est_out=0):
    """累计 token，并保留实测与估算来源，避免最后一次调用篡改整局口径。"""
    measured_in = int(usage_box.get("prompt") or 0)
    measured_out = int(usage_box.get("completion") or 0)
    if measured_in or measured_out:
        state["tok_in"] = state.get("tok_in", 0) + measured_in
        state["tok_out"] = state.get("tok_out", 0) + measured_out
        state["tok_cache"] = state.get("tok_cache", 0) + int(usage_box.get("cache_hit") or 0)
        state["tok_measured_in"] = state.get("tok_measured_in", 0) + measured_in
        state["tok_measured_out"] = state.get("tok_measured_out", 0) + measured_out
        state["tok_last"] = (measured_in, measured_out)
    else:
        estimated_in = max(0, int(est_in or 0))
        estimated_out = max(0, int(est_out or 0))
        state["tok_in"] = state.get("tok_in", 0) + estimated_in
        state["tok_out"] = state.get("tok_out", 0) + estimated_out
        state["tok_estimated_in"] = state.get("tok_estimated_in", 0) + estimated_in
        state["tok_estimated_out"] = state.get("tok_estimated_out", 0) + estimated_out
        state["tok_last"] = (estimated_in, estimated_out)
    has_estimate = bool(state.get("tok_estimated_in") or state.get("tok_estimated_out"))
    has_measured = bool(state.get("tok_measured_in") or state.get("tok_measured_out"))
    state["tok_est"] = has_estimate and not has_measured
    state["tok_mixed"] = has_estimate and has_measured


# ---------- 章节预算 / 机械进度 ----------


def _mechanical_progress(state):
    state = state or {}
    mode = str(state.get("mode") or "")
    r = int(state.get("round", 0) or 0)
    if mode.startswith("强化"):
        total = int(state.get("total_chapters", 0) or 0)
        if total:
            chapter = max(1, int(state.get("current_chapter", 1) or 1))
            used = max(0, int(state.get("chapter_round", 0) or 0))
            budget = max(1, int(state.get("turn_budget", 1) or 1))
            frac = (chapter - 1 + min(1.0, used / budget)) / total
            return max(0, min(100, int(frac * 100)))
        return min(100, r * 2)
    return min(100, int(r / 30 * 100))


def _chapter_state(chapter_index, chapter_number=1, chapter_round=0):
    chapters = (chapter_index or {}).get("chapters", []) if isinstance(chapter_index, dict) else []
    total = len(chapters)
    number = max(1, min(int(chapter_number or 1), total or 1))
    item = next((row for row in chapters if int(row.get("idx", 0)) == number), None) or {}
    budget = int(item.get("turn_budget") or engine.chapter_tools.turn_budget(int(item.get("chars", 0) or 0)))
    return {"current_chapter": number, "total_chapters": total,
            "chapter_round": max(0, int(chapter_round or 0)), "turn_budget": budget}


def _next_chapter_state(state):
    """纯计算下一回合应使用的目标章节，调用方决定何时提交。"""
    state = state or {}
    chapter = max(1, int(state.get("current_chapter", 1) or 1))
    chapter_round = max(0, int(state.get("chapter_round", 0) or 0)) + 1
    budget = max(1, int(state.get("turn_budget", 1) or 1))
    total = max(chapter, int(state.get("total_chapters", chapter) or chapter))
    if chapter_round > budget and chapter < total:
        chapter += 1
        return _chapter_state(state.get("chapter_index"), chapter, 1)
    return {
        "current_chapter": chapter,
        "total_chapters": total,
        "chapter_round": chapter_round,
        "turn_budget": budget,
    }


# ---------- 上传书籍 / 章节文本 ----------


def _split_uploaded_book(novel_file):
    path = fe._to_path(novel_file)
    if not path:
        return None
    try:
        book_id = os.path.splitext(os.path.basename(path))[0]
        return engine.chapter_tools.split_file(path, book_id=book_id, output_root=fe.WRITABLE_DIR)
    except (OSError, ValueError, UnicodeError):
        return None


def _book_dir(chapter_index):
    book_id = (chapter_index or {}).get("book_id") if isinstance(chapter_index, dict) else None
    return os.path.join(fe.WRITABLE_DIR, "books", str(book_id)) if book_id else ""


def _chapter_text(chapter_index, chapter_number):
    book_dir = _book_dir(chapter_index)
    if not book_dir:
        return ""
    path = os.path.join(book_dir, "chapters", f"{int(chapter_number):04d}.txt")
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


# ---------- 涟漪动作评分（配合 engine.ripple） ----------


def _score_action(message):
    text = str(message or "")
    canon = 4 if any(word in text for word in ("改朝", "灭世", "改天命", "覆灭", "颠覆王朝", "改写结局")) else (
        3 if any(word in text for word in ("杀死", "结盟", "公开", "揭露", "夺权")) else (
            2 if any(word in text for word in ("改变", "阻止", "破坏", "离开")) else 0))
    persistent_markers = ("永久", "从此", "长期", "终身", "彻底", "不可逆", "永远")
    broad_markers = ("所有", "全城", "全军", "整个", "天下", "王朝", "世界")
    persist = 3 if canon >= 3 else (2 if any(word in text for word in persistent_markers) else (1 if canon == 2 else 0))
    breadth = 3 if canon >= 3 else (2 if any(word in text for word in broad_markers) else (1 if canon == 2 else 0))
    pressure = 3 if canon >= 4 else (2 if canon >= 3 else (1 if canon == 2 else 0))
    return breadth, persist, canon, pressure


# ---------- 桥段库（懒加载，模块级缓存；键固定，无需额外 key） ----------

STYLE_TO_CHOICE = {
    "行动型": "强硬", "谋略型": "智取", "苟稳型": "隐忍", "规则型": "试探",
    "义守型": "斡旋", "乐趣型": "反将", "探索型": "试探", "情感型": "示弱", "成长型": "借势",
}
_TROPE_STORE = None


def _trope_store():
    global _TROPE_STORE
    if _TROPE_STORE is None:
        tropes = []
        for name in ("tropes_biz.json", "tropes_combat.json", "tropes_life.json",
                     "tropes_mystery.json", "tropes_romance.json"):
            path = os.path.join(DATA_DIR, name)
            if os.path.isfile(path):
                tropes.extend(engine.runtime_mechanics.TropeStore.from_json(path).tropes)
        _TROPE_STORE = engine.runtime_mechanics.TropeStore(tropes)
    return _TROPE_STORE


# ---------- 动态世界书（lore）glue：模块级懒缓存，避免每回合重复读盘 ----------

_LORE_ENTRIES = None


def _load_lore_entries():
    """世界书条目只读一次并缓存到模块级，供本进程所有会话复用。"""
    global _LORE_ENTRIES
    if _LORE_ENTRIES is None:
        try:
            _LORE_ENTRIES = load_entries(WORLD_BOOK_PATH) if os.path.isfile(WORLD_BOOK_PATH) else []
        except (OSError, ValueError, TypeError):
            _LORE_ENTRIES = []
    return _LORE_ENTRIES


def _lore_injector(state):
    injector = LoreInjector(_load_lore_entries(), budget_chars=2600, depth=6)
    injector.restore((state or {}).get("lore") or {})
    return injector
