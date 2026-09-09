# -*- coding: utf-8 -*-
"""IP 保险库一键恢复：把 ip_vault/ 中的作品库与角色数据拷回项目原位。

用法（项目根目录下）：
  python tools/ip_vault_restore.py            # 恢复缺失项（已存在的文件跳过不动）
  python tools/ip_vault_restore.py --force    # 覆盖恢复（vault 版本优先）
  python tools/ip_vault_restore.py --status   # 只查看两侧差异，不写任何文件

设计约定：
  - vault 只存「作品库 + 角色信息」（测试书/切章书/存档等运行数据不在其中）；
  - 恢复只按 manifest 列出的路径拷贝，不扫 vault 其他内容；
  - 默认不覆盖：主目录里后来新建/修改的同名文件（如用户自建卡）优先保留；
  - SQLite（fate_engine.db）同样按文件拷贝：恢复前请先停掉运行中的服务实例。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / "ip_vault"
MANIFEST = VAULT / "manifest.json"

# 源路径（项目原位） → vault 相对路径。目录整棵恢复，文件按项恢复。
ITEMS: list[tuple[str, str]] = [
    ("assets/rules/work_library.md", "assets/rules/work_library.md"),
    ("assets/data/characters", "assets/data/characters"),
    ("assets/data/roster", "assets/data/roster"),
    ("assets/data/roster_draft", "assets/data/roster_draft"),
    ("assets/data/samples", "assets/data/samples"),
    ("assets/data/character_pools.json", "assets/data/character_pools.json"),
    ("assets/data/popular_roster.json", "assets/data/popular_roster.json"),
    ("assets/personas", "assets/personas"),
    ("var/db/fate_engine.db", "var/fate_engine.db"),
    ("var/db/fate_engine.db.bak", "var/fate_engine.db.bak"),
]


def _merge_tree(src: Path, dst: Path) -> int:
    """合并式目录恢复：只补主目录缺失的文件（已存在的一律不动）。

    返回拷贝的文件数。目录级整条 skip 是缺陷：目录存在但内部缺文件时
    （例如维护中途误删几张卡），必须逐文件补齐而不是跳过整棵树。
    """
    copied = 0
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        target = dst / item.relative_to(src)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied += 1
    return copied


def _copy(src: Path, dst: Path, force: bool, label: str) -> str:
    """拷贝一个条目，返回动作描述（skip/merge/copy/overwrite/missing）。"""
    if not src.exists():
        return f"missing（vault 缺失: {label}）"
    if dst.exists() and not force:
        if dst.is_dir() and src.is_dir():
            n = _merge_tree(src, dst)
            if n:
                return f"merge（补齐 {n} 个缺失文件）-> {dst.relative_to(ROOT)}"
            return "skip（两侧一致，无需恢复）"
        return "skip（已存在，默认不覆盖；--force 可覆盖）"
    dst.parent.mkdir(parents=True, exist_ok=True)
    action = "overwrite" if dst.exists() else "copy"
    if src.is_dir():
        # dirs_exist_ok 直接覆盖（不先 rmtree：沙箱/杀软可能拦截整树删除，
        # 先删后建会在中途失败留下半恢复状态）。
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        if dst.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copy2(src, dst)
    return f"{action} -> {dst.relative_to(ROOT)}"


def _status(src: Path, dst: Path) -> str:
    if not src.exists():
        return "vault缺失"
    if not dst.exists():
        return "可恢复（主目录缺失）"
    if src.is_dir():
        src_n = sum(1 for _ in src.rglob("*") if _.is_file())
        dst_n = sum(1 for _ in dst.rglob("*") if _.is_file())
        return f"两侧均存在（vault {src_n} 文件 / 主目录 {dst_n} 文件）"
    same = src.stat().st_size == dst.stat().st_size
    return "一致" if same else "大小不同（vault 可能更新）"


def main() -> None:
    parser = argparse.ArgumentParser(description="IP 保险库一键恢复")
    parser.add_argument("--force", action="store_true", help="覆盖主目录同名文件/目录")
    parser.add_argument("--status", action="store_true", help="仅查看差异，不写文件")
    args = parser.parse_args()

    if not VAULT.is_dir():
        print(f"未找到 IP 保险库：{VAULT}")
        sys.exit(2)

    if args.status:
        for src_rel, vault_rel in ITEMS:
            print(f"[{_status(VAULT / vault_rel, ROOT / src_rel)}] {src_rel}")
        return

    print("== IP 保险库恢复（作品库 + 全部角色信息）==")
    print("提示：涉及 SQLite 恢复前请先停掉运行中的服务实例。\n")
    for src_rel, vault_rel in ITEMS:
        print(f"[{_copy(VAULT / vault_rel, ROOT / src_rel, args.force, src_rel)}] {src_rel}")
    print("\n完成。角色卡 JSON / 性格档案 md / 作品库 / 角色数据库 已按上述动作恢复。")


if __name__ == "__main__":
    main()
