"""统一角色数据库访问层。

提供角色数据的CRUD操作，支持从JSON文件迁移数据到SQLite数据库。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.engine.catalog import ROLES, CharacterCard

# 数据库路径：FATE_VAR_DIR 优先（多实例并发时每实例独立库），
# 否则项目根 var/db。集群实例首次启动时若库缺失会从主库拷贝种子。
def _database_path() -> Path:
    override = os.environ.get("FATE_VAR_DIR", "").strip()
    if override:
        return Path(override) / "db" / "fate_engine.db"
    return Path(__file__).resolve().parents[2] / "var" / "db" / "fate_engine.db"


def _seed_cluster_database(path: Path) -> None:
    """集群实例（FATE_VAR_DIR）首次启动：若库缺失，从主库拷贝角色资产种子。"""
    if path.exists():
        return
    main_db = Path(__file__).resolve().parents[2] / "var" / "db" / "fate_engine.db"
    if main_db.exists() and main_db.resolve() != path.resolve():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(main_db, path)
            # WAL/SHM 侧文件不拷贝：新库从干净状态开始。
            for suffix in ("-wal", "-shm"):
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists():
                    sidecar.unlink()
        except OSError:
            pass  # 拷贝失败则空库冷启动（bootstrap 仍可用，仅无历史卡）


_DATABASE_PATH = _database_path()
if os.environ.get("FATE_VAR_DIR", "").strip():
    _seed_cluster_database(_DATABASE_PATH)
DATABASE_PATH = _DATABASE_PATH
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
    init_database()


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
        conn = get_connection()
        try:
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
            
            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_characters_role ON characters(role)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_characters_gender ON characters(gender)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_characters_source_type ON characters(source_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_characters_is_active ON characters(is_active)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_lists_character_id ON character_lists(character_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_relationships_character_id ON character_relationships(character_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_slots_character_id ON character_slots(character_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_character_slots_slot_name ON character_slots(slot_name)")

            # 栏位名迁移：历史"主线栏"统一改写为"伴侣栏"
            cursor.execute(
                "UPDATE character_slots SET slot_name = '伴侣栏' WHERE slot_name = '主线栏'"
            )

            # 四维兜底：每个活跃角色四个栏位各至少一条标签，缺失补"通用"。
            cursor.execute(
                "SELECT DISTINCT character_id FROM character_slots"
            )
            all_ids = [row[0] for row in cursor.fetchall()]
            for cid in all_ids:
                cursor.execute(
                    "SELECT DISTINCT slot_name FROM character_slots WHERE character_id = ?",
                    (cid,),
                )
                present = {row[0] for row in cursor.fetchall()}
                for slot in ("主角栏", "伴侣栏", "伙伴栏", "宿敌栏"):
                    if slot not in present:
                        cursor.execute(
                            "INSERT OR IGNORE INTO character_slots (character_id, slot_name, slot_type)"
                            " VALUES (?, ?, '通用')",
                            (cid, slot),
                        )

            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(f"数据库初始化失败: {e}") from e
        finally:
            conn.close()


def insert_character(card: CharacterCard, source_type: str = "builtin") -> None:
    """插入单个角色到数据库"""
    with _LOCK:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            # 插入主表
            cursor.execute("""
                INSERT OR REPLACE INTO characters (
                    id, role, name, work, archetype, desire, fear, voice, background,
                    source, gender, original_position, source_medium, source_region,
                    distill_level, source_type, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                card.id, card.role, card.name, card.work, card.archetype,
                card.desire, card.fear, card.voice, card.background,
                card.source, card.gender, card.original_position,
                card.source_medium, card.source_region, card.distill_level,
                source_type
            ))
            
            # 删除旧数据（如果存在）
            cursor.execute("DELETE FROM character_lists WHERE character_id = ?", (card.id,))
            cursor.execute("DELETE FROM character_relationships WHERE character_id = ?", (card.id,))
            cursor.execute("DELETE FROM character_slots WHERE character_id = ?", (card.id,))
            
            # 插入列表字段
            list_fields = [
                ("abilities", card.abilities),
                ("knowledge_scope", card.knowledge_scope),
                ("unacceptable_actions", card.unacceptable_actions),
                ("skill_ids", card.skill_ids),
            ]
            
            for field_name, items in list_fields:
                for idx, item in enumerate(items):
                    cursor.execute("""
                        INSERT INTO character_lists (character_id, field_name, item_order, item_value)
                        VALUES (?, ?, ?, ?)
                    """, (card.id, field_name, idx, item))
            
            # 插入关系数据
            for target, rel_type in card.relationship_vector:
                cursor.execute("""
                    INSERT OR REPLACE INTO character_relationships (character_id, target_entity, relationship_type)
                    VALUES (?, ?, ?)
                """, (card.id, target, rel_type))
            
            # 插入栏位数据；历史名"主线栏"统一写为"伴侣栏"
            for slot_name, slot_types in card.slot_keys.items():
                normalized_slot = "伴侣栏" if slot_name == "主线栏" else slot_name
                for slot_type in slot_types:
                    cursor.execute("""
                        INSERT OR REPLACE INTO character_slots (character_id, slot_name, slot_type)
                        VALUES (?, ?, ?)
                    """, (card.id, normalized_slot, slot_type))

            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(f"插入角色失败: {e}") from e
        finally:
            conn.close()


def insert_characters_batch(cards: Iterable[CharacterCard], source_type: str = "builtin") -> int:
    """批量插入角色到数据库"""
    count = 0
    with _LOCK:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            for card in cards:
                # 插入主表
                cursor.execute("""
                    INSERT OR REPLACE INTO characters (
                        id, role, name, work, archetype, desire, fear, voice, background,
                        source, gender, original_position, source_medium, source_region,
                        distill_level, source_type, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    card.id, card.role, card.name, card.work, card.archetype,
                    card.desire, card.fear, card.voice, card.background,
                    card.source, card.gender, card.original_position,
                    card.source_medium, card.source_region, card.distill_level,
                    source_type
                ))
                
                # 删除旧数据（如果存在）
                cursor.execute("DELETE FROM character_lists WHERE character_id = ?", (card.id,))
                cursor.execute("DELETE FROM character_relationships WHERE character_id = ?", (card.id,))
                cursor.execute("DELETE FROM character_slots WHERE character_id = ?", (card.id,))
                
                # 插入列表字段
                list_fields = [
                    ("abilities", card.abilities),
                    ("knowledge_scope", card.knowledge_scope),
                    ("unacceptable_actions", card.unacceptable_actions),
                    ("skill_ids", card.skill_ids),
                ]
                
                for field_name, items in list_fields:
                    for idx, item in enumerate(items):
                        cursor.execute("""
                            INSERT INTO character_lists (character_id, field_name, item_order, item_value)
                            VALUES (?, ?, ?, ?)
                        """, (card.id, field_name, idx, item))
                
                # 插入关系数据
                for target, rel_type in card.relationship_vector:
                    cursor.execute("""
                        INSERT OR REPLACE INTO character_relationships (character_id, target_entity, relationship_type)
                        VALUES (?, ?, ?)
                    """, (card.id, target, rel_type))
                
                # 插入栏位数据；历史名"主线栏"统一写为"伴侣栏"
                for slot_name, slot_types in card.slot_keys.items():
                    normalized_slot = "伴侣栏" if slot_name == "主线栏" else slot_name
                    for slot_type in slot_types:
                        cursor.execute("""
                            INSERT OR REPLACE INTO character_slots (character_id, slot_name, slot_type)
                            VALUES (?, ?, ?)
                        """, (card.id, normalized_slot, slot_type))

                count += 1
            
            conn.commit()
            return count
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(f"批量插入角色失败: {e}") from e
        finally:
            conn.close()


def get_character_by_id(character_id: str) -> CharacterCard | None:
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
    """更新角色信息"""
    with _LOCK:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            # 检查角色是否存在
            cursor.execute("SELECT id FROM characters WHERE id = ?", (character_id,))
            if not cursor.fetchone():
                return False
            
            # 构建更新语句
            allowed_fields = [
                "role", "name", "work", "archetype", "desire", "fear", "voice",
                "background", "source", "gender", "original_position", "source_medium",
                "source_region", "distill_level"
            ]
            
            set_clauses = []
            values = []
            
            for field in allowed_fields:
                if field in updates:
                    set_clauses.append(f"{field} = ?")
                    values.append(updates[field])
            
            if set_clauses:
                set_clauses.append("updated_at = CURRENT_TIMESTAMP")
                values.append(character_id)
                
                sql = f"UPDATE characters SET {', '.join(set_clauses)} WHERE id = ?"
                cursor.execute(sql, values)
            
            # 更新列表字段
            if "abilities" in updates:
                cursor.execute("DELETE FROM character_lists WHERE character_id = ? AND field_name = 'abilities'", (character_id,))
                for idx, item in enumerate(updates["abilities"]):
                    cursor.execute("""
                        INSERT INTO character_lists (character_id, field_name, item_order, item_value)
                        VALUES (?, 'abilities', ?, ?)
                    """, (character_id, idx, item))
            
            # 更新关系数据
            if "relationship_vector" in updates:
                cursor.execute("DELETE FROM character_relationships WHERE character_id = ?", (character_id,))
                for target, rel_type in updates["relationship_vector"]:
                    cursor.execute("""
                        INSERT INTO character_relationships (character_id, target_entity, relationship_type)
                        VALUES (?, ?, ?)
                    """, (character_id, target, rel_type))
            
            # 更新栏位数据
            if "slot_keys" in updates:
                cursor.execute("DELETE FROM character_slots WHERE character_id = ?", (character_id,))
                for slot_name, slot_types in updates["slot_keys"].items():
                    for slot_type in slot_types:
                        cursor.execute("""
                            INSERT INTO character_slots (character_id, slot_name, slot_type)
                            VALUES (?, ?, ?)
                        """, (character_id, slot_name, slot_type))
            
            conn.commit()
            return True
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseError(f"更新角色失败: {e}") from e
        finally:
            conn.close()


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
            return cursor.rowcount > 0
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
    """从JSON文件迁移数据到数据库"""
    results = {
        "builtin_pool": 0,
        "builtin_files": 0,
        "user_cards": 0,
        "override_cards": 0,
        "errors": []
    }
    
    try:
        # 1. 迁移character_pools.json
        from core.engine.catalog import load_character_pool
        builtin_cards = load_character_pool()
        insert_characters_batch(builtin_cards, source_type="builtin")
        results["builtin_pool"] = len(builtin_cards)
        
        # 2. 迁移data/characters/builtin/目录
        builtin_dir = PROJECT_DATA_DIR / "characters" / "builtin"
        if builtin_dir.is_dir():
            for json_file in builtin_dir.glob("**/*.json"):
                try:
                    with open(json_file, encoding="utf-8") as f:
                        data = json.load(f)
                    
                    if isinstance(data, dict) and "characters" in data:
                        # 处理包装格式
                        for char_data in data["characters"]:
                            card = CharacterCard.from_record(char_data)
                            insert_character(card, source_type="builtin")
                            results["builtin_files"] += 1
                    elif isinstance(data, dict) and "role" in data:
                        # 单个角色格式
                        card = CharacterCard.from_record(data)
                        insert_character(card, source_type="builtin")
                        results["builtin_files"] += 1
                except Exception as e:
                    results["errors"].append(f"处理文件 {json_file} 失败: {str(e)}")
        
        # 3. 迁移用户卡目录
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
        
        # 4. 迁移替换卡目录
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


# 初始化数据库
def ensure_database() -> None:
    """确保数据库已初始化"""
    try:
        init_database()
    except DatabaseError:
        # 如果初始化失败，尝试创建数据库文件
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        init_database()


# 模块加载时自动初始化
ensure_database()