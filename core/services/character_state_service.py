"""Small, deterministic character-state service.

The service keeps a character's core personality as a read-only projection and
stores only evidence-backed, changeable assertions in ``character_state``.
All operations return new values; callers may safely retain prior snapshots.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
from typing import Any, Mapping, Sequence


SCHEMA = "character-state"
VERSION = 1
_DEFAULT_DECAY = 0.98


def _text(value: Any) -> str:
    return str(value or "").strip()


def _confidence(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(1.0, number)), 6)


def _provenance(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return deepcopy(list(value))
    return [deepcopy(value)]


def _assertion_id(key: str, value: Any) -> str:
    raw = f"{key}\0{value!r}".encode("utf-8")
    return sha1(raw).hexdigest()[:16]


def core_personality_projection(character: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a detached core personality projection.

    ``core`` is preferred; legacy character cards commonly place these fields
    at the top level or under ``personality``.  The returned object is a deep
    copy and is never used as an update target.
    """
    if not isinstance(character, Mapping):
        return {}
    source = character.get("core")
    if not isinstance(source, Mapping):
        source = character.get("personality")
    if not isinstance(source, Mapping):
        source = character
    keys = ("traits", "values", "temperament", "speech_style", "boundaries",
            "catchphrases", "personality", "core_locked")
    result = {key: deepcopy(source[key]) for key in keys if key in source}
    if "core_locked" not in result:
        result["core_locked"] = True
    return result


def blank_character_state(core: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"schema": SCHEMA, "version": VERSION, "core": deepcopy(dict(core or {})),
            "assertions": [], "revision": 0}


def migrate_character_state(value: Any, character: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize current and small legacy shapes without mutating input."""
    if not isinstance(value, Mapping):
        return blank_character_state(core_personality_projection(character))
    raw = value.get("character_state") if isinstance(value.get("character_state"), Mapping) else value
    if not isinstance(raw, Mapping):
        raw = {}
    core = raw.get("core") if isinstance(raw.get("core"), Mapping) else None
    if core is None:
        core = core_personality_projection(character)
    assertions = raw.get("assertions", raw.get("evidence", []))
    if isinstance(assertions, Mapping):
        assertions = list(assertions.values())
    if not isinstance(assertions, list):
        assertions = []
    normalized: list[dict[str, Any]] = []
    for item in assertions:
        if not isinstance(item, Mapping):
            continue
        key = _text(item.get("key") or item.get("trait") or item.get("name"))
        if not key:
            continue
        row = dict(item)
        row["key"] = key
        row["confidence"] = _confidence(row.get("confidence"))
        row["provenance"] = _provenance(row.get("provenance", row.get("source")))
        row["contradictions"] = _provenance(row.get("contradictions"))
        row["decay"] = _confidence(row.get("decay"), _DEFAULT_DECAY)
        row.setdefault("id", _assertion_id(key, row.get("value")))
        normalized.append(deepcopy(row))
    try:
        revision = max(0, int(raw.get("revision", 0) or 0))
    except (TypeError, ValueError):
        revision = 0
    return {"schema": SCHEMA, "version": VERSION, "core": deepcopy(dict(core)),
            "assertions": normalized, "revision": revision}


def add_evidence(state: Mapping[str, Any] | None, key: str, value: Any, *,
                 confidence: float = 0.5, provenance: Any = None,
                 contradiction: Any = None, round_no: int | None = None,
                 decay: float = _DEFAULT_DECAY) -> dict[str, Any]:
    """Add or reinforce an assertion, returning a fresh state snapshot."""
    result = migrate_character_state(state)
    name = _text(key)
    if not name:
        raise ValueError("assertion key must not be empty")
    rows = result["assertions"]
    row = next((x for x in rows if x.get("key") == name and x.get("value") == value), None)
    if row is None:
        row = {"id": _assertion_id(name, value), "key": name, "value": deepcopy(value),
               "confidence": _confidence(confidence), "provenance": [],
               "contradictions": [], "decay": _confidence(decay, _DEFAULT_DECAY)}
        rows.append(row)
    else:
        row["confidence"] = _confidence(max(row.get("confidence", 0), _confidence(confidence)))
    # Assertions on the same key with a different value are contradictory;
    # retain both pieces of evidence rather than silently replacing either one.
    for other in rows:
        if other is not row and other.get("key") == name and other.get("value") != value:
            if other["id"] not in row["contradictions"]:
                row["contradictions"].append(other["id"])
            if row["id"] not in other["contradictions"]:
                other["contradictions"].append(row["id"])
    row["provenance"].extend(_provenance(provenance))
    row["contradictions"].extend(_provenance(contradiction))
    if round_no is not None:
        row["last_round"] = int(round_no)
    result["revision"] += 1
    return result


def decay_assertions(state: Mapping[str, Any] | None, *, steps: int = 1) -> dict[str, Any]:
    result = migrate_character_state(state)
    count = max(0, int(steps))
    for row in result["assertions"]:
        row["confidence"] = _confidence(row.get("confidence", 0.0) * row.get("decay", _DEFAULT_DECAY) ** count)
    if count:
        result["revision"] += 1
    return result


def retrieve_assertions(state: Mapping[str, Any] | None, *, min_confidence: float = 0.0) -> list[dict[str, Any]]:
    result = migrate_character_state(state)
    threshold = _confidence(min_confidence, 0.0)
    return [deepcopy(row) for row in result["assertions"] if row["confidence"] >= threshold]


def public_projection(state: Mapping[str, Any] | None, *, min_confidence: float = 0.0) -> dict[str, Any]:
    result = migrate_character_state(state)
    return {"core": deepcopy(result["core"]),
            "assertions": retrieve_assertions(result, min_confidence=min_confidence),
            "revision": result["revision"]}


class CharacterStateService:
    core_projection = staticmethod(core_personality_projection)
    migrate = staticmethod(migrate_character_state)
    add_evidence = staticmethod(add_evidence)
    decay = staticmethod(decay_assertions)
    retrieve = staticmethod(retrieve_assertions)
    public = staticmethod(public_projection)


__all__ = ["SCHEMA", "VERSION", "CharacterStateService", "blank_character_state",
           "core_personality_projection", "migrate_character_state", "add_evidence",
           "decay_assertions", "retrieve_assertions", "public_projection"]
