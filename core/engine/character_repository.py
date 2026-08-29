"""统一角色数据仓库接口。

提供所有角色数据访问的统一入口，确保所有代码都通过这个接口访问角色数据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from core.engine.catalog import ROLES, CharacterCard
from core.engine.character_db import (
    get_character_by_id, get_characters_by_role, get_characters_by_slot,
    get_all_characters, search_characters, get_character_stats,
    insert_character, update_character, delete_character
)
from core.engine.character_library import (
    merged_pool, save_card, delete_card, load_user_cards
)


class CharacterRepository:
    """统一角色数据仓库"""
    
    @staticmethod
    def get_character(character_id: str) -> CharacterCard | None:
        """获取单个角色"""
        return get_character_by_id(character_id)
    
    @staticmethod
    def get_characters_by_role(role: str, gender: str = "") -> list[CharacterCard]:
        """根据角色类型获取角色列表"""
        return get_characters_by_role(role, gender)
    
    @staticmethod
    def get_characters_by_slot(slot_name: str, gender: str = "") -> list[CharacterCard]:
        """根据栏位获取角色列表"""
        return get_characters_by_slot(slot_name, gender)
    
    @staticmethod
    def get_all_characters() -> list[CharacterCard]:
        """获取所有活跃角色"""
        return get_all_characters()
    
    @staticmethod
    def search_characters(query: str, role: str = "", gender: str = "") -> list[CharacterCard]:
        """搜索角色"""
        return search_characters(query, role, gender)
    
    @staticmethod
    def get_character_stats() -> dict[str, Any]:
        """获取角色统计信息"""
        return get_character_stats()
    
    @staticmethod
    def get_merged_pool() -> tuple[tuple[CharacterCard, ...], set[str]]:
        """获取合并后的角色池"""
        return merged_pool()
    
    @staticmethod
    def save_character(payload: Mapping[str, Any], *, replace_built_in: bool = False,
                      allow_name_change: bool = True) -> dict[str, Any]:
        """保存角色（同时保存到JSON文件和数据库）"""
        return save_card(payload, replace_built_in=replace_built_in,
                        allow_name_change=allow_name_change)
    
    @staticmethod
    def update_character(character_id: str, updates: Mapping[str, Any]) -> bool:
        """更新角色信息"""
        return update_character(character_id, updates)
    
    @staticmethod
    def delete_character(character_id: str, hard_delete: bool = False) -> dict[str, Any]:
        """删除角色"""
        if hard_delete:
            # 硬删除：同时删除JSON文件和数据库记录
            return delete_card(character_id)
        else:
            # 软删除：只标记数据库记录为不活跃
            success = delete_character(character_id, soft_delete=True)
            return {"removed": "soft", "id": character_id, "success": success}
    
    @staticmethod
    def get_user_cards() -> tuple[list[CharacterCard], list[CharacterCard]]:
        """获取用户自定义角色"""
        return load_user_cards()
    
    @staticmethod
    def migrate_from_json_files() -> dict[str, Any]:
        """从JSON文件迁移数据到数据库"""
        from core.engine.character_db import migrate_from_json
        return migrate_from_json()


# 全局仓库实例
character_repository = CharacterRepository()


# 为了向后兼容，保留原有的模块级函数
def load_character_pool_from_db() -> tuple[CharacterCard, ...]:
    """从数据库加载角色池"""
    return tuple(character_repository.get_all_characters())


def load_character_pool_merged() -> tuple[tuple[CharacterCard, ...], set[str]]:
    """获取合并后的角色池"""
    return character_repository.get_merged_pool()