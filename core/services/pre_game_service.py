"""Deterministic pre-game preparation state machine.

The service works from a *prepared script package* and short evidence excerpts.  It
never pretends to have the whole book: every derived value carries evidence refs.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

STAGES = ("book_selected", "preparing_script", "script_ready", "difficulty_ready",
          "gf_draft_ready", "gf_corrected", "gf_confirmed", "game_ready")


class PreGameError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _evidence(kind: str, text: str, source: str = "prepared_script") -> dict[str, str]:
    clean = _text(text)[:240]
    return {"kind": kind, "source": source, "excerpt": clean,
            "ref": hashlib.sha1(clean.encode("utf-8")).hexdigest()[:12] if clean else ""}


def _as_evidence(raw: Any, kind: str) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping):
        raw = raw.get("evidence") or raw.get("items") or []
    if isinstance(raw, str):
        raw = [raw]
    result = []
    for item in raw or []:
        if isinstance(item, Mapping):
            excerpt = _text(item.get("excerpt") or item.get("text") or item.get("summary"))
            if excerpt:
                result.append({"kind": item.get("kind") or kind, "source": item.get("source") or "prepared_script",
                               "excerpt": excerpt[:240], "ref": item.get("ref") or _evidence(kind, excerpt)["ref"]})
        elif _text(item):
            result.append(_evidence(kind, _text(item)))
    return result[:8]


def _level(value: Any, default: int = 4) -> int:
    match = re.search(r"[1-9]", _text(value))
    return max(1, min(9, int(match.group()) if match else default))


@dataclass
class PreGameService:
    state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.state = dict(self.state)
        self.state.setdefault("stage", "book_selected")
        if self.state["stage"] not in STAGES:
            raise PreGameError("未知开局阶段")

    @property
    def stage(self) -> str:
        return self.state["stage"]

    def _move(self, target: str, **updates: Any) -> dict[str, Any]:
        expected = STAGES[STAGES.index(target) - 1] if target != "book_selected" else None
        if expected and self.stage != expected:
            raise PreGameError(f"阶段顺序错误：需要 {expected}，当前为 {self.stage}")
        self.state.update(updates)
        self.state["stage"] = target
        return dict(self.state)

    def select_book(self, book: Mapping[str, Any] | str) -> dict[str, Any]:
        if self.stage != "book_selected":
            raise PreGameError("书籍已经选择")
        return self._move("preparing_script", book=dict(book) if isinstance(book, Mapping) else {"title": _text(book)})

    def prepare_script(self, package: Mapping[str, Any]) -> dict[str, Any]:
        if self.stage != "preparing_script":
            raise PreGameError("必须先进入 preparing_script")
        data = dict(package or {})
        script = _text(data.get("script") or data.get("opening_script") or data.get("text"))
        if not script:
            raise PreGameError("准备脚本不能为空")
        evidence = {
            "world": _as_evidence(data.get("world_evidence") or data.get("world"), "world"),
            "stage": _as_evidence(data.get("stage_evidence") or data.get("stage"), "stage"),
            "player": _as_evidence(data.get("player_evidence") or data.get("player"), "player"),
        }
        if not any(evidence.values()):
            evidence["script"] = [_evidence("script", script[:240])]
        prepared = {"script": script[:12000], "title": _text(data.get("title")),
                    "evidence": evidence, "context_scope": "prepared_script_only"}
        return self._move("script_ready", prepared_script=prepared)

    def derive_difficulty(self, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.stage != "script_ready":
            raise PreGameError("必须先准备脚本")
        prepared = self.state["prepared_script"]
        ev = prepared.get("evidence", {})
        supplied = dict(overrides or {})
        world = _text(supplied.get("world") or (ev.get("world") or [{}])[0].get("excerpt") if ev.get("world") else "未确定世界")
        stage = _text(supplied.get("stage") or (ev.get("stage") or [{}])[0].get("excerpt") if ev.get("stage") else "开局阶段")
        player = _text(supplied.get("player") or (ev.get("player") or [{}])[0].get("excerpt") if ev.get("player") else "普通玩家")
        # Explicit difficulty is authoritative; otherwise deterministic keyword scoring.
        script = prepared["script"]
        def score(words: Sequence[str], base: int) -> int:
            return max(1, min(9, base + sum(1 for w in words if w in script)))
        player_level = _level(supplied.get("player_difficulty"), score(("伤", "弱", "新手"), 3))
        stage_level = _level(supplied.get("stage_difficulty"), score(("追杀", "战斗", "危机", "禁忌"), 4))
        world_level = _level(supplied.get("world_difficulty"), score(("末日", "诡异", "超凡", "战争"), 4))
        difficulty = max(1, min(9, round((player_level + stage_level + world_level) / 3)))
        result = {"label": f"D{difficulty}", "level": difficulty,
                  "world": world, "stage": stage, "player": player,
                  "evidence": ev, "scope": "prepared_script_only"}
        return self._move("difficulty_ready", difficulty=result)

    def mark(self, stage: str, **updates: Any) -> dict[str, Any]:
        if stage not in STAGES:
            raise PreGameError("未知开局阶段")
        return self._move(stage, **updates)


def prepare_state(book: Mapping[str, Any] | str, package: Mapping[str, Any], difficulty: Mapping[str, Any] | None = None) -> dict[str, Any]:
    svc = PreGameService()
    svc.select_book(book)
    svc.prepare_script(package)
    svc.derive_difficulty(difficulty)
    return svc.state


__all__ = ["STAGES", "PreGameError", "PreGameService", "prepare_state"]
