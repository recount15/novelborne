"""Read-only, chapter-end reader context. Providers are trusted server dependencies.

card_provider(character_id, revision) returns a complete v2 repository record.
Only exact, scoped observations enter the projection; full-card prose never does.
Offsets are Unicode code points, not browser UTF-16 offsets.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


class ReaderContextError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReaderContext:
    """Canonical JSON is the immutable snapshot; to_dict returns a detached copy."""
    payload: str

    def to_dict(self) -> dict:
        return json.loads(self.payload)

    @property
    def context_hash(self) -> str:
        return hashlib.sha256(self.payload.encode("utf-8")).hexdigest()


def read_reader_source(book_dir: str | Path, chapter_no: int) -> dict:
    """Validate inventory and hashes without building indexes or writing book files."""
    from core.engine.book_index import checksum, load_index
    from core.services.book_prepare_service import _hash

    root = Path(book_dir).resolve(strict=True)
    index = load_index(root)
    chapters = index["chapters"]
    numbers = [c["chapter_no"] for c in chapters]
    if (type(chapter_no) is not int or chapter_no not in numbers
            or any(type(n) is not int or n < 1 for n in numbers)
            or numbers != sorted(set(numbers))):
        raise ReaderContextError("invalid_cutoff", "Chapter is not in the authoritative inventory")
    inventory = json.loads((root / "chapter_index.json").read_text(encoding="utf-8"))
    files = {int(p.stem) for p in (root / "chapters").glob("*.txt") if p.stem.isdigit()}
    if [c["idx"] for c in inventory["chapters"]] != numbers or files != set(numbers):
        raise ReaderContextError("source_changed", "Chapter inventory changed")
    texts, sources = {}, []
    for chapter in chapters:
        number = chapter["chapter_no"]
        path = (root / "chapters" / f"{number:04d}.txt").resolve(strict=True)
        if not path.is_relative_to(root):
            raise ReaderContextError("invalid_source", "Chapter escapes book directory")
        text = path.read_text(encoding="utf-8")
        if checksum(text) != chapter["source"]["checksum"] or len(text) != chapter["chars"]:
            raise ReaderContextError("source_changed", "Chapter source changed")
        texts[number] = text
        sources.append({"chapter_no": number, "checksum": checksum(text), "chars": len(text)})
    source_hash = _hash(sources)  # Same book-wide hash as preparation.
    return {"book_id": index["book_id"], "source_hash": source_hash, "texts": texts,
            "cutoff": {"chapter_no": chapter_no, "offset": len(texts[chapter_no]), "source_hash": source_hash}}


def _repository_card(character_id, revision):
    from core.engine.character_db import get_character_record
    return get_character_record(character_id, revision)


def build_reader_context(book_dir: str | Path, character_id: str, chapter_no: int, *,
                         card_revision: int | None = None, source_hash: str | None = None,
                         card_provider: Callable | None = None) -> ReaderContext:
    source = read_reader_source(book_dir, chapter_no)
    return _build_scoped_context(source, character_id, card_revision=card_revision,
                                 source_hash=source_hash, card_provider=card_provider)


def _build_scoped_context(source: dict, character_id: str, *, card_revision=None,
                          source_hash=None, card_provider=None) -> ReaderContext:
    """Shared evidence projection; source cutoff is validated by the caller."""
    chapter_no = source["cutoff"]["chapter_no"]
    if source_hash is not None and source_hash != source["source_hash"]:
        raise ReaderContextError("source_changed", "Source hash does not match")
    if not isinstance(character_id, str) or not character_id.strip():
        raise ReaderContextError("invalid_character", "Stable character ID is required")
    if card_revision is not None and (type(card_revision) is not int or card_revision < 0):
        raise ReaderContextError("invalid_revision", "Revision must be a nonnegative integer")
    card = (card_provider or _repository_card)(character_id, card_revision)
    missing = lambda: ReaderContextError("preparation_required", "No grounded character card applies at this boundary")
    if not isinstance(card, Mapping):
        raise missing()
    origin = card.get("source")
    if (card.get("schema_version") != 2 or not isinstance(origin, Mapping)
            or origin.get("book_id") != source["book_id"]
            or origin.get("source_hash") != source["source_hash"]
            or card.get("character_id", card.get("id")) != character_id
            or card.get("quality", {}).get("state") != "ready"
            or type(card.get("revision")) is not int or card["revision"] < 0
            or (card_revision is not None and card["revision"] != card_revision)):
        raise missing()

    cutoff = (chapter_no, source["cutoff"]["offset"])
    evidence = {}
    for row in card.get("evidence", []):
        number, start, end = row.get("chapter_no"), row.get("start"), row.get("end")
        if (type(number) is not int or type(start) is not int or type(end) is not int
                or number not in source["texts"] or not 0 <= start < end <= len(source["texts"][number])
                or (number, end) > cutoff or row.get("book_id") != source["book_id"]
                or row.get("source_hash") != source["source_hash"]
                or source["texts"][number][start:end] != row.get("quote")):
            continue
        eid = row.get("evidence_id")
        if not isinstance(eid, str) or not eid or eid in evidence:
            raise ReaderContextError("invalid_evidence", "Evidence IDs must be unique and nonempty")
        evidence[eid] = {k: row[k] for k in ("evidence_id", "book_id", "source_hash", "chapter_no", "start", "end", "quote")}
        evidence[eid]["speaker_id"] = row.get("speaker_id")

    def applicable(row):
        for key, is_end in (("valid_from", False), ("valid_until", True)):
            boundary = row.get(key)
            if boundary is None:
                continue
            if not isinstance(boundary, Mapping):
                return False
            n, offset = boundary.get("chapter_no"), boundary.get("offset")
            if (type(n) is not int or type(offset) is not int or n not in source["texts"]
                    or not 0 <= offset <= len(source["texts"][n])
                    or boundary.get("source_hash", source["source_hash"]) != source["source_hash"]):
                return False
            if ((not is_end and (n, offset) > cutoff) or (is_end and (n, offset) <= cutoff)):
                return False
        return True

    def supported(row):
        ids = row.get("evidence_ids")
        value = row.get("value")
        return (isinstance(ids, list) and bool(ids) and all(isinstance(i, str) and i in evidence for i in ids)
                and row.get("epistemic_kind", "observation") == "observation"
                and isinstance(value, str) and bool(value.strip())
                and any(value in evidence[i]["quote"] for i in ids) and applicable(row))

    facts, status_facts = [], []
    for row in card.get("facts", []):
        if not supported(row) or row.get("status") not in ("confirmed", "known", "established"):
            continue
        safe = {k: row.get(k) for k in ("fact_id", "domain", "predicate", "value", "subject_id", "knowledge_holder_id", "evidence_ids")}
        if row.get("subject_id") == character_id and row.get("predicate") in ("alive", "life_status", "生死"):
            status_facts.append(safe)
        # Objectively true is not necessarily known by this character.
        if row.get("knowledge_holder_id") == character_id:
            facts.append(safe)
    name = card.get("name")
    if not isinstance(name, str) or not name or not any(name in e["quote"] for e in evidence.values()) or not facts:
        raise missing()

    dead = any(f["value"] in ("dead", "deceased", "死亡", "已死", "身亡", "已亡") for f in status_facts)
    # In interview mode only evidence no later than the attested death may be used.
    if dead:
        death = min((evidence[i]["chapter_no"], evidence[i]["end"])
                    for f in status_facts if f["value"] in ("dead", "deceased", "死亡", "已死", "身亡", "已亡")
                    for i in f["evidence_ids"])
        evidence = {i: e for i, e in evidence.items() if (e["chapter_no"], e["end"]) <= death}
        facts = [f for f in facts if all(i in evidence for i in f["evidence_ids"])]
        if not facts:
            raise missing()

    semantic = {}
    for field in ("mind_model", "decision_policy", "voice_transfer", "behavior_boundaries"):
        rows = card.get("semantic", {}).get(field, [])
        if isinstance(rows, Mapping):
            rows = [rows]
        if not isinstance(rows, list):
            continue
        semantic[field] = [{"value": r["value"], "evidence_ids": r["evidence_ids"]}
                           for r in rows if isinstance(r, Mapping) and supported(r)]
    # Only a character's own scoped speech is a voice example, never whole-card voice.
    voice = [e for e in evidence.values() if e["speaker_id"] == character_id]
    used = {i for f in facts for i in f["evidence_ids"]}
    used.update(i for rows in semantic.values() for r in rows for i in r["evidence_ids"])
    used.update(e["evidence_id"] for e in voice)
    used.update(i for f in status_facts for i in f["evidence_ids"] if i in evidence)
    result = {"scope": "reader", "book_id": source["book_id"], "character_id": character_id,
              "source_hash": source["source_hash"], "card_revision": card["revision"], "cutoff": source["cutoff"],
              "identity": {"name": name}, "stable_core": semantic, "known_facts": facts,
              "effective_state": {"life_status": "dead" if dead else "unknown", "basis": "last_known_facts" if dead else "scoped_evidence"},
              "active_goals": [], "effective_relationships": [], "voice_examples": voice,
              "mode": "interview" if dead else "reader", "provenance": [evidence[i] for i in sorted(used)],
              "hard_constraints": ["reader_only", "no_game_state", "no_future_knowledge", "no_resurrection", "unknown_is_not_false"],
              "omissions": ["Unscoped profile, inferred semantics and unknown facts excluded; not a complete character model."],
              "grounding": "partial_exact_evidence"}
    return ReaderContext(canonical(result))


def build_game_context(state: Mapping, card_provider: Callable | None = None,
                       cutoff: Mapping | None = None, *, character_id: str | None = None) -> ReaderContext:
    """Project trusted committed branch state, never the complete private state.

    Stable character ID is explicit or state['character_id']; assertions come from
    state['character_states'][ID]. No name-based identity joins. Scoped source is
    optional: state['book_dir'], state['book_id'] and a validated exact cutoff
    (0 <= offset <= chapter length) enable the reader evidence filter over only
    that source prefix. Reader conversations still default to chapter end.
    Missing source stays unknown.
    Callers must pass committed state, not a worker's proposed patch.
    """
    if not isinstance(state, Mapping):
        raise ReaderContextError("invalid_state", "Committed branch state is required")
    cid = character_id or state.get("character_id")
    if not isinstance(cid, str) or not cid.strip():
        raise ReaderContextError("invalid_character", "Stable character ID is required")
    revision = state.get("state_revision", state.get("revision"))
    if type(revision) is not int or revision < 0:
        raise ReaderContextError("invalid_revision", "Committed state revision is required")
    branch_id = state.get("branch_id") or state.get("session_id")
    if not isinstance(branch_id, str) or not branch_id:
        raise ReaderContextError("invalid_branch", "Stable branch or session ID is required")
    result = {"scope": "game", "character_id": cid, "branch_id": branch_id,
              "state_revision": revision, "identity": {"character_id": cid},
              "book_id": state.get("book_id"), "source_hash": None, "card_revision": None,
              "cutoff": None, "source_status": "unknown", "stable_core": {},
              "known_facts": [], "effective_state": {"basis": "committed_branch"},
              "active_goals": [], "effective_relationships": [], "voice_examples": [],
              "provenance": [], "hard_constraints": ["game_branch_only", "source_is_not_branch_truth", "unknown_is_not_false"],
              "omissions": ["Private resources, inventory, enemies, raw history and unscoped card metadata excluded."],
              "grounding": "partial_committed_branch"}
    if cutoff is not None:
        if (not isinstance(cutoff, Mapping) or type(cutoff.get("chapter_no")) is not int
                or type(cutoff.get("offset")) is not int or not isinstance(cutoff.get("source_hash"), str)
                or not cutoff["source_hash"]):
            raise ReaderContextError("invalid_cutoff", "Explicit source coordinates and hash are required")
        if state.get("book_dir") and state.get("book_id"):
            source = read_reader_source(state["book_dir"], cutoff["chapter_no"])
            if source["book_id"] != state["book_id"] or source["source_hash"] != cutoff["source_hash"]:
                raise ReaderContextError("source_changed", "Source identity or hash does not match")
            if not 0 <= cutoff["offset"] <= len(source["texts"][cutoff["chapter_no"]]):
                raise ReaderContextError("invalid_cutoff", "Offset is outside the source chapter")
            source["cutoff"] = {k: cutoff[k] for k in ("chapter_no", "offset", "source_hash")}
            # Retain full validated text internally for validity-boundary checks;
            # only exact evidence ending within this prefix enters the projection.
            result.update(source_hash=source["source_hash"], cutoff=source["cutoff"])
            try:
                source_context = _build_scoped_context(source, cid,
                    card_revision=state.get("card_revision"), source_hash=cutoff["source_hash"],
                    card_provider=card_provider).to_dict()
            except ReaderContextError as exc:
                if exc.code != "preparation_required":
                    raise
                result["omissions"].append("No applicable grounded source card.")
            else:
                for key in ("book_id", "source_hash", "card_revision", "cutoff", "identity", "stable_core", "voice_examples", "provenance"):
                    result[key] = source_context[key]
                # Keep original facts explicitly separate: source death or ownership
                # must never overwrite a deviated game branch.
                result["source_facts"] = source_context["known_facts"]
                result["source_status"] = "scoped_reference_only"
    entries = state.get("character_states")
    entry = entries.get(cid, {}) if isinstance(entries, Mapping) else {}
    if not isinstance(entry, Mapping):
        entry = {}
    allowed = {"alive", "life_status", "生死", "presence", "在场", "location", "condition", "appearance", "action"}
    facts = []
    for row in entry.get("assertions", []):
        if not isinstance(row, Mapping):
            continue
        predicate = row.get("key", row.get("predicate"))
        value = row.get("value")
        if (predicate not in allowed or not isinstance(value, (str, bool, int, float))
                or value == "" or not row.get("provenance") or row.get("contradictions")
                or row.get("branch_id", branch_id) != branch_id
                or row.get("status", "confirmed") != "confirmed"):
            continue
        row_revision = row.get("state_revision", revision)
        if type(row_revision) is not int or not 0 <= row_revision <= revision:
            continue
        confidence = row.get("confidence")
        if type(confidence) not in (int, float) or not 0.8 <= confidence <= 1:
            continue
        fact_id = row.get("id", row.get("fact_id"))
        if not isinstance(fact_id, str) or not fact_id:
            continue
        fact = {"fact_id": fact_id, "subject_id": cid, "predicate": predicate, "value": value,
                "branch_id": branch_id, "state_revision": row_revision, "origin": "committed_branch"}
        facts.append(fact)
        if row.get("knowledge_holder_id") == cid:
            result["known_facts"].append(fact)
    result["effective_state"]["assertions"] = facts
    return ReaderContext(canonical(result))
