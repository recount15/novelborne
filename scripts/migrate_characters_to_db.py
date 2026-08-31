#!/usr/bin/env python3
"""数据迁移脚本：将角色数据从JSON文件迁移到SQLite数据库。

使用方法：
    python scripts/migrate_characters_to_db.py [--backup] [--dry-run] [--stats]

选项：
    --backup    迁移前备份数据库
    --dry-run   仅显示将要迁移的数据，不实际执行
    --stats     显示迁移后的统计信息
"""
from __future__ import annotations

import sys
import shutil
from pathlib import Path

# 添加项目根目录到Python路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.engine.character_db import (
    migrate_from_json, get_character_stats, DATABASE_PATH, 
    init_database, DatabaseError
)


def backup_database() -> Path | None:
    """备份数据库文件"""
    if not DATABASE_PATH.exists():
        print("数据库文件不存在，无需备份")
        return None
    
    backup_path = DATABASE_PATH.with_suffix(".db.bak")
    try:
        shutil.copy2(DATABASE_PATH, backup_path)
        print(f"数据库已备份到: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"备份失败: {e}")
        return None


def show_stats() -> None:
    """显示数据库统计信息"""
    try:
        stats = get_character_stats()
        print("\n=== 数据库统计信息 ===")
        print(f"总角色数: {stats['total']}")
        print(f"\n按角色类型:")
        for role, count in stats['by_role'].items():
            print(f"  {role}: {count}")
        print(f"\n按性别:")
        for gender, count in stats['by_gender'].items():
            print(f"  {gender}: {count}")
        print(f"\n按来源:")
        for source, count in stats['by_source'].items():
            print(f"  {source}: {count}")
    except Exception as e:
        print(f"获取统计信息失败: {e}")


def dry_run_migration() -> None:
    """预览将要迁移的数据"""
    print("=== 预览迁移数据 ===")
    
    # 检查JSON文件
    json_files = {
        "character_pools.json": PROJECT_ROOT / "data" / "character_pools.json",
        "builtin目录": PROJECT_ROOT / "data" / "characters" / "builtin",
        "user目录": PROJECT_ROOT / "data" / "characters" / "user",
    }
    
    for name, path in json_files.items():
        if path.exists():
            if path.is_file():
                print(f"✓ {name}: 文件存在")
            elif path.is_dir():
                json_count = len(list(path.glob("**/*.json")))
                print(f"✓ {name}: 目录存在，包含 {json_count} 个JSON文件")
        else:
            print(f"✗ {name}: 不存在")
    
    # 检查数据库
    if DATABASE_PATH.exists():
        print(f"✓ 数据库: {DATABASE_PATH} 存在")
    else:
        print(f"✗ 数据库: {DATABASE_PATH} 不存在")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="将角色数据从JSON文件迁移到SQLite数据库")
    parser.add_argument("--backup", action="store_true", help="迁移前备份数据库")
    parser.add_argument("--dry-run", action="store_true", help="仅显示将要迁移的数据，不实际执行")
    parser.add_argument("--stats", action="store_true", help="显示迁移后的统计信息")
    
    args = parser.parse_args()
    
    print("=== 角色数据迁移工具 ===")
    print(f"数据库路径: {DATABASE_PATH}")
    
    # 预览模式
    if args.dry_run:
        dry_run_migration()
        return
    
    # 备份数据库
    if args.backup:
        backup_database()
    
    # 初始化数据库
    print("\n正在初始化数据库...")
    try:
        init_database()
        print("✓ 数据库初始化完成")
    except DatabaseError as e:
        print(f"✗ 数据库初始化失败: {e}")
        return 1
    
    # 执行迁移
    print("\n开始迁移数据...")
    try:
        results = migrate_from_json()
        
        print(f"\n=== 迁移结果 ===")
        print(f"✓ 内置角色池: {results['builtin_pool']} 个角色")
        print(f"✓ 内置文件角色: {results['builtin_files']} 个角色")
        print(f"✓ 用户自定义角色: {results['user_cards']} 个角色")
        print(f"✓ 替换内置角色: {results['override_cards']} 个角色")
        
        if results['errors']:
            print(f"\n⚠️  迁移过程中遇到 {len(results['errors'])} 个错误:")
            for error in results['errors'][:5]:  # 只显示前5个错误
                print(f"  - {error}")
            if len(results['errors']) > 5:
                print(f"  ... 还有 {len(results['errors']) - 5} 个错误")
        
        total_migrated = (results['builtin_pool'] + results['builtin_files'] + 
                         results['user_cards'] + results['override_cards'])
        print(f"\n总计迁移: {total_migrated} 个角色")
        
    except Exception as e:
        print(f"✗ 迁移失败: {e}")
        return 1
    
    # 显示统计信息
    if args.stats:
        show_stats()
    
    print("\n✅ 迁移完成！")
    print("\n下一步操作：")
    print("1. 验证迁移结果：python scripts/migrate_characters_to_db.py --stats")
    print("2. 测试应用功能：python app.py")
    print("3. 运行单元测试：python -m pytest tests/test_character_db.py")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())