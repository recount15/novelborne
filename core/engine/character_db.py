"""统一角色数据库访问层。

提供角色数据的CRUD操作，支持从JSON文件迁移数据到SQLite数据库。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict
from uuid import uuid4
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.engine.catalog import ROLES, CharacterCard

# 数据库路径：FATE_VAR_DIR 优先（多实例并发时每实例独立库），
# 否则项目根 var/db。每个实例独立空库启动，禁止隐式拷贝种子。
def _database_path() -> Path:
    override = os.environ.get("FATE_VAR_DIR", "").strip()
    if override:
        return Path(override) / "db" / "fate_engine.db"
    return Path(__file__).resolve().parents[2] / "var" / "db" / "fate_engine.db"


DATABASE_PATH = _database_path()
PROJECT_DATA_DIR = Path(__file__).resolve().parents[2] / "assets" / "data"

# 线程锁
_LOCK = threading.Lock()


class DatabaseError(Exception):
    """数据库操作错误"""


def set_database_path(new_path: str | Path) -> None:
    """重定向数据库文件路径（测试隔离用）。

    切换后立即对新库执行初始化，并清空只读缓存，
    确保后续所有连接都指向新库。
    """
    global DATABASE_PATH
    with _LOCK:
        DATABASE_PATH = Path(new_path)
    ensure_database()
    _invalidate_cache()


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except sqlite3.Error as e:
        raise DatabaseError(f"数据库连接失败: {e}") from e


def init_database() -> None:
    """初始化数据库表结构"""
    with _LOCK:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            
            # 创建角色主表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS characters (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    name TEXT NOT NULL,
                    work TEXT DEFAULT '',
                    archetype TEXT DEFAULT '',
                    desire TEXT DEFAULT '',
                    fear TEXT DEFAULT '',
                    voice TEXT DEFAULT '',
                    background TEXT DEFAULT '',
                    source TEXT DEFAULT '',
                    gender TEXT DEFAULT 'unknown',
                    original_position TEXT DEFAULT '',
                    source_medium TEXT DEFAULT '',
                    source_region TEXT DEFAULT '',
                    distill_level TEXT DEFAULT 'normal',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source_type TEXT NOT NULL DEFAULT 'builtin',
                    is_active INTEGER DEFAULT 1
                )
            """)
            
            # 创建角色列表字段表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS character_lists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    item_order INTEGER NOT NULL,
                    item_value TEXT NOT NULL,
                    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
                    UNIQUE(character_id, field_name, item_order)
                )
            """)
            
            # 创建角色关系表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS character_relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id TEXT NOT NULL,
                    target_entity TEXT NOT NULL,
                    relationship_type TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
                    UNIQUE(character_id, target_entity)
                )
            """)
            
            # 创建角色栏位归属表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS character_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id TEXT NOT NULL,
                    slot_name TEXT NOT NULL,
                    slot_type TEXT NOT NULL,
                    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
                    UNIQUE(character_id, slot_name)
                )
            """)
            
            # 创建四维语义字段扩展表（向后兼容，存储 JSON）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS character_semantic_fields (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    field_data TEXT NOT NULL,
                    FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE,
                    UNIQUE(character_id, field_name)
                )
            """)
            
            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_characters_role ON characters(role)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_characters_gender ON characters(gender)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_characters_source_type ON characters(source_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_characters_is_active ON characters(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_lists_character_id ON character_lists(character_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_relationships_character_id ON character_relationships(character_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_slots_character_id ON character_slots(character_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_slots_slot_name ON character_slots(slot_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_semantic_fields_character_id ON character_semantic_fields(character_id)")

            # Additive schema only: no rewriting existing role qualifications.
            cursor.execute("""CREATE TABLE IF NOT EXISTS character_record_snapshots (
                character_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (character_id, revision),
                FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
            )""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS character_write_requests (
                request_key TEXT PRIMARY KEY, request_json TEXT NOT NULL,
                character_id TEXT NOT NULL, revision INTEGER NOT NULL
            )""")
            _migrate_revision_indexes(conn)
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(f"数据库初始化失败: {e}") from e
        finally:
            conn.close()


def _invalidate_cache() -> None:
    from core.engine.character_library import refresh_game_cache
    refresh_game_cache()


def _index_revision(conn, record):
    """Rebuildable indexes only; snapshot JSON remains the sole record authority."""
    cid, revision = record["id"], record["revision"]
    for field in ("aliases", "evidence", "facts", "relationships", "tags"):
        conn.execute(f"DELETE FROM character_revision_{field} WHERE character_id=? AND revision=?", (cid, revision))
    for i, alias in enumerate(record.get("aliases", [])):
        conn.execute("INSERT INTO character_revision_aliases VALUES (?,?,?,?)", (cid,revision,i,str(alias)))
    for item in record.get("evidence", []):
        conn.execute("INSERT INTO character_revision_evidence VALUES (?,?,?,?,?,?,?,?,?,?)", (cid,revision,item["evidence_id"],item.get("book_id"),item.get("source_hash"),item.get("block_id"),item.get("chapter_no"),item.get("start"),item.get("end"),json.dumps(item,ensure_ascii=False)))
    for i, item in enumerate(record.get("facts", [])):
        conn.execute("INSERT INTO character_revision_facts VALUES (?,?,?,?,?,?,?,?,?,?)", (cid,revision,str(item.get("fact_id",i)),item.get("domain"),item.get("predicate"),item.get("subject_id"),item.get("knowledge_holder_id"),json.dumps(item.get("valid_from")),json.dumps(item.get("valid_until")),json.dumps(item,ensure_ascii=False)))
    for i, item in enumerate(record.get("relationships", [])):
        conn.execute("INSERT INTO character_revision_relationships VALUES (?,?,?,?,?,?,?,?)", (cid,revision,str(item.get("relation_id",i)),item.get("source_character_id"),item.get("target_character_id"),item.get("type"),json.dumps(item.get("effective_boundary")),json.dumps(item,ensure_ascii=False)))
    for slot, tags in record.get("slot_keys", {}).items():
        for tag in dict.fromkeys(tags):
            conn.execute("INSERT INTO character_revision_tags VALUES (?,?,?,?)",(cid,revision,slot,tag))


def _migrate_revision_indexes(conn):
    """v3 schema compatibility: canonical view maps directly to existing snapshots.

    No second revision authority or legacy/non-character data rewrite is introduced.
    All indexes, migration marker and current pointers commit with the schema upgrade.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS character_schema_metadata (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(characters)")}
    if "current_revision" not in columns:
        conn.execute("ALTER TABLE characters ADD COLUMN current_revision INTEGER")
    conn.execute("CREATE VIEW IF NOT EXISTS character_revisions AS SELECT character_id,revision,record_json,created_at FROM character_record_snapshots")
    conn.execute("CREATE TABLE IF NOT EXISTS character_revision_aliases (character_id TEXT NOT NULL, revision INTEGER NOT NULL, item_order INTEGER NOT NULL, alias TEXT NOT NULL, PRIMARY KEY(character_id,revision,item_order), FOREIGN KEY(character_id,revision) REFERENCES character_record_snapshots(character_id,revision) ON DELETE CASCADE)")
    conn.execute("CREATE TABLE IF NOT EXISTS character_revision_evidence (character_id TEXT NOT NULL, revision INTEGER NOT NULL, evidence_id TEXT NOT NULL, book_id TEXT, source_hash TEXT, block_id TEXT, chapter_no INTEGER, start INTEGER, end INTEGER, record_json TEXT NOT NULL, PRIMARY KEY(character_id,revision,evidence_id), FOREIGN KEY(character_id,revision) REFERENCES character_record_snapshots(character_id,revision) ON DELETE CASCADE)")
    conn.execute("CREATE TABLE IF NOT EXISTS character_revision_facts (character_id TEXT NOT NULL, revision INTEGER NOT NULL, fact_id TEXT NOT NULL, domain TEXT, predicate TEXT, subject_id TEXT, knowledge_holder_id TEXT, valid_from TEXT, valid_until TEXT, record_json TEXT NOT NULL, PRIMARY KEY(character_id,revision,fact_id), FOREIGN KEY(character_id,revision) REFERENCES character_record_snapshots(character_id,revision) ON DELETE CASCADE)")
    conn.execute("CREATE TABLE IF NOT EXISTS character_revision_relationships (character_id TEXT NOT NULL, revision INTEGER NOT NULL, relation_id TEXT NOT NULL, source_character_id TEXT, target_character_id TEXT, type TEXT, effective_boundary TEXT, record_json TEXT NOT NULL, PRIMARY KEY(character_id,revision,relation_id), FOREIGN KEY(character_id,revision) REFERENCES character_record_snapshots(character_id,revision) ON DELETE CASCADE)")
    conn.execute("CREATE TABLE IF NOT EXISTS character_revision_tags (character_id TEXT NOT NULL, revision INTEGER NOT NULL, slot_name TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(character_id,revision,slot_name,tag), FOREIGN KEY(character_id,revision) REFERENCES character_record_snapshots(character_id,revision) ON DELETE CASCADE)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_character_revision_alias ON character_revision_aliases (alias)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_character_revision_book ON character_revision_evidence (book_id,source_hash,chapter_no,start,end)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_character_revision_knowledge ON character_revision_facts (knowledge_holder_id,domain,predicate,valid_from,valid_until)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_character_revision_relation ON character_revision_relationships (target_character_id,type,effective_boundary)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_character_revision_tag ON character_revision_tags (slot_name,tag)")
    row = conn.execute("SELECT version FROM character_schema_metadata WHERE component='revision_indexes'").fetchone()
    if row is None or row[0] < 1:
        for snapshot in conn.execute("SELECT character_id,revision,record_json FROM character_record_snapshots").fetchall():
            record = json.loads(snapshot["record_json"])
            record.update(id=snapshot["character_id"],revision=snapshot["revision"])
            _index_revision(conn,record)
        conn.execute("UPDATE characters SET current_revision=(SELECT MAX(revision) FROM character_record_snapshots WHERE character_id=characters.id)")
        conn.execute("INSERT OR REPLACE INTO character_schema_metadata VALUES ('revision_indexes',1)")


def _write_record(conn, record, source_type):
    raw = dict(record)
    raw["id"] = str(raw.get("id") or raw.get("character_id") or f"user-{uuid4().hex}")
    card = CharacterCard.from_record(raw)
    old = conn.execute("SELECT MAX(revision) FROM character_record_snapshots WHERE character_id=?", (card.id,)).fetchone()[0]
    expected = raw.pop("base_revision", None)
    if expected is not None and expected != old:
        raise DatabaseError("角色修订冲突")
    raw["revision"] = 0 if old is None else old + 1
    encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
    fields = ("role", "name", "work", "archetype", "desire", "fear", "voice", "background", "source", "gender", "original_position", "source_medium", "source_region", "distill_level")
    conn.execute("INSERT INTO characters (id," + ",".join(fields) + ",source_type) VALUES (" + ",".join("?" for _ in range(len(fields)+2)) + ") ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in fields) + ",source_type=excluded.source_type,is_active=1,updated_at=CURRENT_TIMESTAMP", [card.id, *(getattr(card,k) for k in fields), source_type])
    for table in ("character_lists", "character_relationships", "character_slots", "character_semantic_fields"):
        conn.execute(f"DELETE FROM {table} WHERE character_id=?", (card.id,))
    for field_name in ("abilities", "knowledge_scope", "unacceptable_actions", "skill_ids"):
        for i, item in enumerate(getattr(card,field_name)):
            conn.execute("INSERT INTO character_lists (character_id,field_name,item_order,item_value) VALUES (?,?,?,?)", (card.id,field_name,i,item))
    for target, relation in card.relationship_vector:
        conn.execute("INSERT OR REPLACE INTO character_relationships (character_id,target_entity,relationship_type) VALUES (?,?,?)", (card.id,target,relation))
    for slot, types in card.slot_keys.items():
        if types:
            # Legacy unique slot projection; snapshot retains ALL tags.
            conn.execute("INSERT INTO character_slots (character_id,slot_name,slot_type) VALUES (?,?,?)", (card.id,slot,types[0]))
    for field_name in ("mind_model", "decision_policy", "voice_transfer", "behavior_boundaries"):
        conn.execute("INSERT INTO character_semantic_fields (character_id,field_name,field_data) VALUES (?,?,?)", (card.id,field_name,json.dumps(raw.get(field_name,getattr(card,field_name)),ensure_ascii=False)))
    conn.execute("INSERT INTO character_record_snapshots (character_id,revision,record_json) VALUES (?,?,?)", (card.id,raw["revision"],encoded))
    _index_revision(conn,raw)
    conn.execute("UPDATE characters SET current_revision=? WHERE id=?", (raw["revision"],card.id))
    return raw


def save_character_record(record: Mapping[str, Any], source_type: str = "user") -> dict[str, Any]:
    with _LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            request_key = record.get("idempotency_key")
            request_json = json.dumps(dict(record), ensure_ascii=False, sort_keys=True, allow_nan=False)
            replay = conn.execute("SELECT request_json,character_id,revision FROM character_write_requests WHERE request_key=?", (request_key,)).fetchone() if request_key else None
            if replay:
                if replay["request_json"] != request_json:
                    raise DatabaseError("幂等键冲突")
                row = conn.execute("SELECT record_json FROM character_record_snapshots WHERE character_id=? AND revision=?", (replay["character_id"], replay["revision"])).fetchone()
                if row is None:
                    raise DatabaseError("幂等记录已删除，不能重新创建")
                result = json.loads(row[0])
            else:
                result = _write_record(conn,record,source_type)
                if request_key:
                    conn.execute("INSERT INTO character_write_requests VALUES (?,?,?,?)", (request_key,request_json,result["id"],result["revision"]))
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise DatabaseError(f"保存角色失败: {exc}") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    _invalidate_cache()
    return result


def insert_character(card: CharacterCard, source_type: str = "builtin") -> None:
    save_character_record(asdict(card),source_type)


def insert_characters_batch(cards: Iterable[CharacterCard], source_type: str = "builtin") -> int:
    count = 0
    with _LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for card in cards:
                _write_record(conn,asdict(card),source_type)
                count += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    _invalidate_cache()
    return count


def get_character_record(character_id: str, revision: int | None = None) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        sql = "SELECT s.record_json FROM character_record_snapshots s JOIN characters c ON c.id=s.character_id WHERE c.id=?"
        params = [character_id]
        if revision is None:
            sql += " AND c.is_active=1 ORDER BY s.revision DESC LIMIT 1"
        else:
            sql += " AND s.revision=?"
            params.append(revision)
        row = conn.execute(sql,params).fetchone()
        if row:
            return json.loads(row[0])
    finally:
        conn.close()
    if revision is not None:
        return None
    card = _get_legacy_character_by_id(character_id)
    return asdict(card) if card else None


def get_character_source_type(character_id: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT source_type FROM characters WHERE id=? AND is_active=1",(character_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_character_by_id(character_id: str) -> CharacterCard | None:
    record = get_character_record(character_id)
    return CharacterCard.from_record(record) if record is not None else None


def _get_legacy_character_by_id(character_id: str) -> CharacterCard | None:
    """根据ID获取角色"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # 获取主表数据
        cursor.execute("SELECT * FROM characters WHERE id = ? AND is_active = 1", (character_id,))
        row = cursor.fetchone()
        if not row:
            return None
        
        # 构建角色卡
        card_data = dict(row)
        
        # 获取列表字段
        list_fields = ["abilities", "knowledge_scope", "unacceptable_actions", "skill_ids"]
        for field_name in list_fields:
            cursor.execute("""
                SELECT item_value FROM character_lists 
                WHERE character_id = ? AND field_name = ?
                ORDER BY item_order
            """, (character_id, field_name))
            card_data[field_name] = tuple(item["item_value"] for item in cursor.fetchall())
        
        # 获取关系数据
        cursor.execute("""
            SELECT target_entity, relationship_type FROM character_relationships
            WHERE character_id = ?
        """, (character_id,))
        card_data["relationship_vector"] = tuple(
            (item["target_entity"], item["relationship_type"]) for item in cursor.fetchall()
        )
        
        # 获取栏位数据；历史数据里的"主线栏"统一读作"伴侣栏"
        cursor.execute("""
            SELECT slot_name, slot_type FROM character_slots
            WHERE character_id = ?
        """, (character_id,))
        slot_keys: dict[str, tuple[str, ...]] = {}
        for item in cursor.fetchall():
            slot_name = item["slot_name"]
            if slot_name == "主线栏":
                slot_name = "伴侣栏"
            slot_type = item["slot_type"]
            if slot_name not in slot_keys:
                slot_keys[slot_name] = ()
            slot_keys[slot_name] = slot_keys[slot_name] + (slot_type,)
        card_data["slot_keys"] = slot_keys
        
        for item in cursor.execute("SELECT field_name,field_data FROM character_semantic_fields WHERE character_id=?",(character_id,)):
            card_data[item["field_name"]] = json.loads(item["field_data"])
        return CharacterCard.from_record(card_data)
    finally:
        conn.close()


def get_characters_by_role(role: str, gender: str = "") -> list[CharacterCard]:
    """根据角色类型和性别获取角色列表"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        if gender:
            cursor.execute("""
                SELECT id FROM characters 
                WHERE role = ? AND gender = ? AND is_active = 1
                ORDER BY name
            """, (role, gender))
        else:
            cursor.execute("""
                SELECT id FROM characters 
                WHERE role = ? AND is_active = 1
                ORDER BY name
            """, (role,))
        
        characters = []
        for row in cursor.fetchall():
            card = get_character_by_id(row["id"])
            if card:
                characters.append(card)
        
        return characters
    finally:
        conn.close()


def get_characters_by_slot(slot_name: str, gender: str = "") -> list[CharacterCard]:
    """根据栏位和性别获取角色列表"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        if gender:
            cursor.execute("""
                SELECT DISTINCT c.id FROM characters c
                JOIN character_slots cs ON c.id = cs.character_id
                WHERE cs.slot_name = ? AND c.gender = ? AND c.is_active = 1
                ORDER BY c.name
            """, (slot_name, gender))
        else:
            cursor.execute("""
                SELECT DISTINCT c.id FROM characters c
                JOIN character_slots cs ON c.id = cs.character_id
                WHERE cs.slot_name = ? AND c.is_active = 1
                ORDER BY c.name
            """, (slot_name,))
        
        characters = []
        for row in cursor.fetchall():
            card = get_character_by_id(row["id"])
            if card:
                characters.append(card)
        
        return characters
    finally:
        conn.close()


def get_all_characters() -> list[CharacterCard]:
    """获取所有活跃角色"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM characters WHERE is_active = 1 ORDER BY role, name")
        
        characters = []
        for row in cursor.fetchall():
            card = get_character_by_id(row["id"])
            if card:
                characters.append(card)
        
        return characters
    finally:
        conn.close()


def update_character(character_id: str, updates: Mapping[str, Any]) -> bool:
    record = get_character_record(character_id)
    if record is None:
        return False
    expected = record.get("revision")
    record.update(updates)
    record["id"] = character_id
    record.setdefault("base_revision", expected)
    save_character_record(record, get_character_source_type(character_id) or "user")
    return True


def delete_character(character_id: str, soft_delete: bool = True) -> bool:
    """删除角色（支持软删除和硬删除）"""
    with _LOCK:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            if soft_delete:
                # 软删除：标记为不活跃
                cursor.execute("""
                    UPDATE characters SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (character_id,))
            else:
                # 硬删除：物理删除
                cursor.execute("DELETE FROM character_lists WHERE character_id = ?", (character_id,))
                cursor.execute("DELETE FROM character_relationships WHERE character_id = ?", (character_id,))
                cursor.execute("DELETE FROM character_slots WHERE character_id = ?", (character_id,))
                cursor.execute("DELETE FROM characters WHERE id = ?", (character_id,))
            
            conn.commit()
            removed = cursor.rowcount > 0
            _invalidate_cache()
            return removed
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(f"删除角色失败: {e}") from e
        finally:
            conn.close()


def search_characters(query: str, role: str = "", gender: str = "") -> list[CharacterCard]:
    """搜索角色"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # 构建查询
        conditions = ["is_active = 1"]
        params = []
        
        if query:
            conditions.append("(name LIKE ? OR work LIKE ? OR archetype LIKE ? OR background LIKE ?)")
            search_pattern = f"%{query}%"
            params.extend([search_pattern] * 4)
        
        if role:
            conditions.append("role = ?")
            params.append(role)
        
        if gender:
            conditions.append("gender = ?")
            params.append(gender)
        
        where_clause = " AND ".join(conditions)
        sql = f"SELECT id FROM characters WHERE {where_clause} ORDER BY name"
        
        cursor.execute(sql, params)
        
        characters = []
        for row in cursor.fetchall():
            card = get_character_by_id(row["id"])
            if card:
                characters.append(card)
        
        return characters
    finally:
        conn.close()


def get_character_stats() -> dict[str, Any]:
    """获取角色统计信息"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # 总数统计
        cursor.execute("SELECT COUNT(*) as total FROM characters WHERE is_active = 1")
        total = cursor.fetchone()["total"]
        
        # 按角色类型统计
        cursor.execute("""
            SELECT role, COUNT(*) as count 
            FROM characters 
            WHERE is_active = 1 
            GROUP BY role
        """)
        by_role = {row["role"]: row["count"] for row in cursor.fetchall()}
        
        # 按性别统计
        cursor.execute("""
            SELECT gender, COUNT(*) as count 
            FROM characters 
            WHERE is_active = 1 
            GROUP BY gender
        """)
        by_gender = {row["gender"]: row["count"] for row in cursor.fetchall()}
        
        # 按来源统计
        cursor.execute("""
            SELECT source_type, COUNT(*) as count 
            FROM characters 
            WHERE is_active = 1 
            GROUP BY source_type
        """)
        by_source = {row["source_type"]: row["count"] for row in cursor.fetchall()}
        
        return {
            "total": total,
            "by_role": by_role,
            "by_gender": by_gender,
            "by_source": by_source
        }
    finally:
        conn.close()


def migrate_from_json() -> dict[str, Any]:
    """从JSON文件迁移用户自有数据到数据库。

    v3.0.0 预置退役后仅迁移用户显式放置的卡（characters/user 与 overrides）；
    不再扫描任何内置目录，手动调用也不可能带回预置内容。
    """
    results = {
        "user_cards": 0,
        "override_cards": 0,
        "errors": []
    }

    try:
        # 1. 迁移用户卡目录
        user_dir = PROJECT_DATA_DIR / "characters" / "user"
        if user_dir.is_dir():
            for json_file in user_dir.glob("*.json"):
                try:
                    with open(json_file, encoding="utf-8") as f:
                        data = json.load(f)

                    if isinstance(data, dict) and "role" in data:
                        card = CharacterCard.from_record(data)
                        insert_character(card, source_type="user")
                        results["user_cards"] += 1
                except Exception as e:
                    results["errors"].append(f"处理用户卡 {json_file} 失败: {str(e)}")
        
        # 2. 迁移替换卡目录
        overrides_dir = user_dir / "overrides"
        if overrides_dir.is_dir():
            for json_file in overrides_dir.glob("*.json"):
                try:
                    with open(json_file, encoding="utf-8") as f:
                        data = json.load(f)
                    
                    if isinstance(data, dict) and "role" in data:
                        card = CharacterCard.from_record(data)
                        insert_character(card, source_type="override")
                        results["override_cards"] += 1
                except Exception as e:
                    results["errors"].append(f"处理替换卡 {json_file} 失败: {str(e)}")
        
        return results
    except Exception as e:
        results["errors"].append(f"迁移过程出错: {str(e)}")
        return results


def ensure_database() -> None:
    """Initialize schema only; an empty database must remain empty."""
    init_database()


ensure_database()
