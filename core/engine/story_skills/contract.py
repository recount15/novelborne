"""Common contract for internal generation skills."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping

@dataclass(frozen=True)
class SkillContext:
    thread_map: Mapping[str, Any]
    snapshot: Mapping[str, Any] = field(default_factory=dict)
    brief: Mapping[str, Any] = field(default_factory=dict)
    user_input: str = ""
    deadline: float | None = None

@dataclass
class SkillResult:
    name: str
    proposal: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    repairs: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"skill": self.name, "proposal": self.proposal, "evidence_refs": self.evidence_refs, "warnings": self.warnings, "repairs": self.repairs, "checkpoint": self.checkpoint, "degraded": self.degraded}
