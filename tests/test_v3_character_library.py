"""DB-only foundation regressions; collection isolation is provided by V00."""
import importlib
import sqlite3

import pytest

from core.engine import catalog, character_db as db, character_library as library


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    old = db.DATABASE_PATH
    db.set_database_path(tmp_path / "db" / "characters.db")
    yield
    db.set_database_path(old)


def test_v3_v02_empty_restart_never_reads_json(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("implicit JSON import")
    monkeypatch.setattr(db, "migrate_from_json", forbidden)
    monkeypatch.setattr(catalog, "_load_json", forbidden)
    monkeypatch.setattr(library, "_scan_cards", forbidden)
    db.ensure_database()
    assert catalog.load_character_pool() == ()
    assert library.merged_pool() == ((), set())
    saved = library.save_card({"name": "Synthetic", "role": "伙伴"})
    library.delete_card(saved["record"]["id"])
    db.ensure_database()
    assert catalog.load_character_pool() == ()
    assert library.merged_pool() == ((), set())


def test_v3_v03_stable_identity_and_rich_roundtrip():
    payload = {"name": "Synthetic", "role": "伙伴", "profile": {"identity": {"status": "unknown"}},
               "facts": [{"value": None, "status": "unknown"}], "aliases": ["Alias"],
               "mind_model": {"rules": ["Think"], "evidence": ["Quote"]},
               "custom_extension": {"unicode": "𠀀"},
               "slot_keys": {"伙伴栏": ["甲", "乙"]}}
    first = library.save_card(payload)["record"]
    second = library.save_card(payload)["record"]
    assert first["id"] != second["id"]
    edited = library.update_card(first["id"], {"name": "Renamed"})["record"]
    assert edited["id"] == first["id"]
    for field in ("profile", "facts", "aliases", "mind_model", "custom_extension", "slot_keys"):
        assert edited[field] == first[field]
    assert db.get_character_by_id(first["id"]).slot_keys["伙伴栏"] == ("甲", "乙")
    assert db.get_character_record(first["id"], first["revision"])["name"] == "Synthetic"
    assert library.export_payload([first["id"]])["characters"] == [edited]
    assert db.get_character_stats()["total"] == 2


def test_v3_v03_snapshot_failure_rolls_back_projection(monkeypatch):
    initial = library.save_card({"name": "Original", "role": "伙伴"})["record"]
    conn = db.get_connection()
    conn.execute("CREATE TRIGGER fail_snapshot BEFORE INSERT ON character_record_snapshots BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    conn.commit()
    conn.close()
    with pytest.raises(library.LibraryError):
        library.update_card(initial["id"], {"name": "MustNotCommit"})
    assert db.get_character_record(initial["id"]) == initial
    conn = db.get_connection()
    assert conn.execute("SELECT name FROM characters WHERE id=?", (initial["id"],)).fetchone()[0] == "Original"
    conn.close()


def test_v3_v03_migration_idempotent_and_revision_conflict():
    initial = library.save_card({"name": "Original", "role": "伙伴"})["record"]
    db.ensure_database()
    db.ensure_database()
    assert db.get_character_record(initial["id"]) == initial
    library.update_card(initial["id"], {"name": "New", "base_revision": initial["revision"]})
    with pytest.raises(library.LibraryError, match="修订冲突"):
        library.update_card(initial["id"], {"name": "Stale", "base_revision": initial["revision"]})


def test_v3_v02_database_failure_never_falls_back(monkeypatch):
    def broken():
        raise db.DatabaseError("synthetic unavailable")
    monkeypatch.setattr(db, "get_all_characters", broken)
    with pytest.raises(db.DatabaseError):
        catalog.load_character_pool()
    with pytest.raises(db.DatabaseError):
        library.merged_pool()


def test_v3_v02_import_restart_does_not_copy_seed(monkeypatch, tmp_path):
    runtime = tmp_path / "new-instance"
    monkeypatch.setenv("FATE_VAR_DIR", str(runtime))
    importlib.reload(db)
    assert db.DATABASE_PATH == runtime / "db" / "fate_engine.db"
    assert db.get_character_stats()["total"] == 0
    saved = library.save_card({"name": "Temporary", "role": "伙伴"})["record"]
    db.delete_character(saved["id"], soft_delete=False)
    importlib.reload(db)
    assert db.get_character_stats()["total"] == 0
    assert library.merged_pool() == ((), set())


def test_v3_v04_no_file_write_and_cache_invalidation(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "USER_LIBRARY_DIR", tmp_path / "never-created")
    monkeypatch.setattr(library, "OVERRIDES_DIR", tmp_path / "never-created" / "overrides")
    assert library.merged_pool_cached() == ((), set())
    saved = library.save_card({"name": "Synthetic", "role": "伙伴"})
    assert len(library.merged_pool_cached()[0]) == 1
    library.delete_card(saved["character_id"])
    assert library.merged_pool_cached() == ((), set())
    assert not library.USER_LIBRARY_DIR.exists()


def test_v3_v03_import_does_not_merge_names():
    rows = [{"name": "Same", "role": "伙伴", "profile": {"identity": n}} for n in (1, 2)]
    assert len(library.import_records(rows)["imported"]) == 2
    cards = library.export_payload()["characters"]
    assert len({c["id"] for c in cards}) == 2
    assert len(library.import_records(cards)["failed"]) == 2
    assert len(library.import_records(cards, overwrite=True)["replaced"]) == 2
    assert db.get_character_stats()["total"] == 2


def test_v3_v04_server_full_save_reload_and_legacy_adapter():
    from core import server
    request = server.CharacterLibraryUpsertRequest(name="Server", role="伙伴", profile={"identity": {"status": "unknown"}}, aliases=["Alias"])
    saved = server.character_library_create(request)
    assert saved["saved"] is True
    loaded = server.character_library_detail(saved["character_id"])
    assert loaded["card"]["profile"] == request.profile
    assert loaded["card"]["aliases"] == ["Alias"]
    adapted = server.character_designer_save(server.DesignerSaveRequest(filename="Legacy", persona_markdown="# Persona", card={"name": "Legacy", "facts": []}))
    assert adapted["saved"] is True
    assert server.character_library_detail(adapted["character_id"])["card"]["persona_markdown"] == "# Persona"


def test_v3_v04_reject_hidden_secrets_and_invalid_v2():
    from core import server
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        server.CharacterLibraryUpsertRequest(name="Bad", api_key="secret")
    with pytest.raises(library.LibraryError, match="凭据"):
        library.save_card({"name": "Bad", "profile": {"api_key": "secret"}})
    with pytest.raises(library.LibraryError, match="结构字段"):
        library.save_card({"name": "Bad", "schema_version": 2})
    with pytest.raises(library.LibraryError, match="证据范围"):
        library.save_card({"name": "Bad", "evidence": [{"evidence_id": "e1", "book_id": "b", "start": 0, "end": 0, "chapter_no": 1}]})


def test_v3_v04_server_db_failure_is_not_success(monkeypatch):
    from core import server
    from fastapi import HTTPException
    def broken(*args, **kwargs):
        raise db.DatabaseError("synthetic write failure")
    monkeypatch.setattr(db, "save_character_record", broken)
    with pytest.raises(HTTPException) as caught:
        server.character_library_create(server.CharacterLibraryUpsertRequest(name="Failure"))
    assert caught.value.status_code == 500


def test_v3_v03_create_idempotency_and_conflict():
    payload = {"name": "Retry", "idempotency_key": "synthetic-create-1"}
    first = library.save_card(payload)
    second = library.save_card(payload)
    assert first["character_id"] == second["character_id"]
    assert db.get_character_stats()["total"] == 1
    with pytest.raises(library.LibraryError, match="冲突"):
        library.save_card({**payload, "name": "Different"})


def test_v3_v03_unicode_evidence_exact_slice(tmp_path):
    import json
    from core.engine.book_index import checksum
    from core.services.book_prepare_service import _hash
    book = db.DATABASE_PATH.parent.parent / "books" / "synthetic-book"
    (book / "chapters").mkdir(parents=True)
    text = "甲𠀀乙"
    (book / "chapters" / "0001.txt").write_text(text, encoding="utf-8")
    (book / "chapter_index.json").write_text(json.dumps({"book_id": "synthetic-book", "chapters": [{"idx": 1}]}), encoding="utf-8")
    from core.engine.book_index import build_book_index
    index = build_book_index(book)
    evidence = {"evidence_id": "e1", "book_id": "synthetic-book", "chapter_no": 1,
                "start": 1, "end": 2, "quote": "𠀀", "block_id": index["leaves"][0]["id"],
                "source_hash": _hash([{"chapter_no": 1, "checksum": checksum(text), "chars": len(text)}])}
    saved = library.save_card({"name": "Evidence", "evidence": [evidence]})
    assert saved["card"]["evidence"][0]["quote"] == "𠀀"
    with pytest.raises(library.LibraryError, match="block_id"):
        library.save_card({"name": "Forged", "evidence": [{**evidence, "block_id": "forged-block"}]})
    with pytest.raises(library.LibraryError, match="原文不一致"):
        library.save_card({"name": "Invalid", "evidence": [{**evidence, "end": 3}]})


def test_v3_v04_generated_field_contract():
    from core import server
    from core.engine.character_designer import CARD_FIELDS
    assert set(CARD_FIELDS).issubset(server.CharacterLibraryUpsertRequest.model_fields)
    payload = {"name": "RichGenerated", "decision_principle": "Protect", "voice_samples": ["Hello"],
               "ability_limits": {"cost": "unknown"}, "references": ["Authored example"]}
    saved = server.character_library_create(server.CharacterLibraryUpsertRequest(**payload))
    loaded = server.character_library_detail(saved["character_id"])["card"]
    for key, value in payload.items():
        assert loaded[key] == value


def test_v3_v03_meaningful_v2_semantics():
    payload = {"name": "Structured", "schema_version": 2, "source": {"origin": "user_created"},
               "profile": {"identity": {"status": "unknown"}}, "semantic": {"mind_model": [
                   {"epistemic_kind": "authored", "evidence_ids": [], "claim": "Protect allies"}]},
               "aliases": [], "facts": [], "relationships": [], "evidence": [], "quality": {"state": "draft"}}
    saved = library.save_card(payload)
    assert db.get_character_record(saved["character_id"])["semantic"] == payload["semantic"]
    with pytest.raises(library.LibraryError, match="必须引用证据"):
        library.save_card({**payload, "semantic": {"mind_model": [{"epistemic_kind": "observation", "evidence_ids": []}]}})
    with pytest.raises(library.LibraryError, match="fact 缺少"):
        library.save_card({**payload, "facts": [{"fact_id": "incomplete"}]})


def test_v3_v03_structured_source_never_uses_legacy_text_limit():
    source = {"origin": "source_extracted", "book_id": "book", "source_hash": "abc",
              "extraction_run_id": "run", "model_version": "model", "prompt_version": "p" * 2500}
    saved = library.save_card({"name": "Provenance", "source": source,
                               "profile": {"identity": {"status": "unknown"}},
                               "semantic": {"mind_model": []}})
    record = db.get_character_record(saved["character_id"], saved["revision"])
    assert record["source"] == source
    assert isinstance(record["source"], dict)
    assert record["profile"]["identity"]["status"] == "unknown"
    assert record["semantic"] == {"mind_model": []}


def test_v3_v03_revision_indexes_all_tags_and_atomic_failure():
    first = library.save_card({"name": "Indexed", "aliases": ["Alias"], "slot_keys": {"伙伴栏": ["甲", "乙"]},
                               "facts": [{"fact_id": "f", "domain": "source", "predicate": "known", "subject_id": "s", "knowledge_holder_id": "s", "value": "x", "status": "confirmed"}]})["record"]
    conn = db.get_connection()
    assert {r[0] for r in conn.execute("SELECT tag FROM character_revision_tags WHERE character_id=? AND slot_name='伙伴栏'", (first["id"],))} == {"甲", "乙"}
    assert conn.execute("SELECT current_revision FROM characters WHERE id=?", (first["id"],)).fetchone()[0] == first["revision"]
    assert conn.execute("SELECT COUNT(*) FROM character_revisions WHERE character_id=?", (first["id"],)).fetchone()[0] == 1
    conn.execute("CREATE TRIGGER fail_rich_index BEFORE INSERT ON character_revision_aliases BEGIN SELECT RAISE(ABORT,'index failed'); END")
    conn.commit()
    conn.close()
    with pytest.raises(library.LibraryError):
        library.update_card(first["id"], {"name": "NotCommitted", "idempotency_key": "failed-index-key"})
    assert db.get_character_record(first["id"]) == first
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM character_revisions WHERE character_id=?", (first["id"],)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM character_write_requests WHERE request_key='failed-index-key'").fetchone()[0] == 0
    conn.close()


def test_v3_v03_index_migration_rebuild_and_noncharacter_sentinel():
    first = library.save_card({"name": "Migrated", "aliases": ["Keep"], "slot_keys": {"伙伴栏": ["甲", "乙"]}})["record"]
    conn = db.get_connection()
    conn.execute("CREATE TABLE saved_history_sentinel (payload TEXT)")
    conn.execute("INSERT INTO saved_history_sentinel VALUES ('untouched')")
    conn.execute("DELETE FROM character_schema_metadata WHERE component='revision_indexes'")
    conn.execute("DELETE FROM character_revision_tags")
    conn.commit()
    conn.close()
    db.ensure_database()
    db.ensure_database()
    conn = db.get_connection()
    assert conn.execute("SELECT payload FROM saved_history_sentinel").fetchone()[0] == "untouched"
    assert conn.execute("SELECT COUNT(*) FROM character_revision_tags WHERE character_id=? AND slot_name='伙伴栏'", (first["id"],)).fetchone()[0] == 2
    assert conn.execute("SELECT version FROM character_schema_metadata WHERE component='revision_indexes'").fetchone()[0] == 1
    conn.close()
    assert db.get_character_record(first["id"]) == first


def test_v3_v03_temporal_and_typed_claim_validation():
    with pytest.raises(library.LibraryError, match="时间范围倒置"):
        library.save_card({"name": "Bad", "facts": [{"valid_from": {"chapter_no": 2, "offset": 0}, "valid_until": {"chapter_no": 1, "offset": 0}}]})
    with pytest.raises(library.LibraryError, match="布尔事实"):
        library.save_card({"name": "Bad", "facts": [{"predicate": "is_alive", "status": "confirmed", "value": "yes"}]})
    with pytest.raises(library.LibraryError, match="effective_boundary"):
        library.save_card({"name": "Bad", "relationships": [{"effective_boundary": "yesterday"}]})
