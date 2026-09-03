"""角色卡 JSON 校验工具（spec_character_db_expansion §1/§2/§5/§6 口径）。

用法：
    python tools/validate_characters.py <路径...> [--strict]

路径可以是单个 JSON 文件、一个目录（递归扫描 *.json）。
文件格式支持单卡对象或 wrapper ``{"characters": [...]}``（与
character_library._scan_cards 同口径）。

校验规则：
- 必填：name（≤40 字）、role ∈ {伙伴, single_heroine, multi_heroine}
- 新字段枚举：gender / original_position / source_medium / source_region /
  distill_level；缺失=警告（存量兼容），存在但非法=错误
- slot_keys：键 ∈ {主角栏,伙伴栏,主线栏,宿敌栏}，值为字符串数组
- 文本字段超 character_library 上限（MAX_FIELD_TEXT=2000 / 列表≤12 项 /
  MAX_NAME=40）= 错误
- 同一批扫描内 name+work+role 重复=错误；同角色不同 role 变体（如伙伴版/女主版）合法；
  裸 name 重复=警告

退出码：存在 ERROR 时为 1（--strict 下存在 WARN 也为 1），否则 0。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 与 engine/character_library.py 保持一致的上限口径
MAX_FIELD_TEXT = 2000
MAX_LIST_ITEMS = 12
MAX_NAME = 40

ROLES = {"伙伴", "single_heroine", "multi_heroine", "主角", "反派"}
GENDERS = {"male", "female", "unknown"}
ORIGINAL_POSITIONS = {"主角", "女主", "男主", "配角", "反派"}
SOURCE_MEDIUMS = {
    "小说", "网文", "轻小说", "影视", "电影", "电视剧", "动漫", "漫画",
    "游戏", "神话", "历史", "戏剧", "传说", "其他",
}
SOURCE_REGIONS = {"cn", "jp", "west", "other"}
DISTILL_LEVELS = {"normal", "enhanced"}
SLOT_KEYS = {"主角栏", "伙伴栏", "主线栏", "宿敌栏"}

# §1 新增字段：缺失视为警告（存量兼容）
NEW_FIELDS = ("gender", "original_position", "source_medium",
              "source_region", "slot_keys", "distill_level")

# 单文本字段（值必须是字符串，≤ MAX_FIELD_TEXT）
TEXT_FIELDS = ("work", "archetype", "desire", "fear", "knowledge_scope",
               "voice", "background", "source")
# 列表字段（字符串或字符串数组均可，≤ MAX_LIST_ITEMS 项，单项 ≤ MAX_FIELD_TEXT）
LIST_FIELDS = ("abilities", "unacceptable_actions", "skill_ids")


class Issue:
    __slots__ = ("level", "path", "message")

    def __init__(self, level: str, path: Path | str, message: str) -> None:
        self.level = level
        self.path = str(path)
        self.message = message

    def render(self) -> str:
        return f"[{self.level}] {self.path}: {self.message}"


def _iter_card_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        # 跳过评价文件（*.review.json）——它们是分数记录，不是角色卡
        return sorted(p for p in target.rglob("*.json") if not p.name.endswith(".review.json"))
    return []


def _load_cards(path: Path) -> tuple[list[tuple[str, dict[str, Any]]], list[Issue]]:
    """读取一个 JSON 文件，返回 (卡记录列表, 问题列表)。每条记录带定位标签。"""
    issues: list[Issue] = []
    try:
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, UnicodeDecodeError) as exc:
        return [], [Issue("ERROR", path, f"文件读取失败：{exc}")]
    except json.JSONDecodeError as exc:
        return [], [Issue("ERROR", path, f"JSON 解析失败：{exc}")]

    if isinstance(raw, list):
        rows = raw
        where = lambda i: f"{path}#{i}"  # noqa: E731
    elif isinstance(raw, dict) and isinstance(raw.get("characters"), list):
        rows = raw["characters"]
        where = lambda i: f"{path}#characters[{i}]"  # noqa: E731
    elif isinstance(raw, dict) and isinstance(raw.get("cards"), list):
        rows = raw["cards"]  # final_roster.json 顶层包装
        where = lambda i: f"{path}#cards[{i}]"  # noqa: E731
    elif isinstance(raw, dict) and (raw.get("role") or raw.get("name")):
        rows = [raw]
        where = lambda i: str(path)  # noqa: E731
    else:
        return [], [Issue("ERROR", path, "既不是单卡对象也不是 {\"characters\": [...]} wrapper")]

    cards: list[tuple[str, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(Issue("ERROR", where(index), "角色卡记录必须是 JSON 对象"))
            continue
        cards.append((where(index), row))
    return cards, issues


def _check_text_field(label: str, value: Any, issues: list[Issue], where: str) -> None:
    if not isinstance(value, str):
        issues.append(Issue("ERROR", where, f"{label} 必须是字符串"))
        return
    if len(value) > MAX_FIELD_TEXT:
        issues.append(Issue(
            "ERROR", where,
            f"{label} 文本 {len(value)} 字，超过 {MAX_FIELD_TEXT} 字上限"))


def _check_list_field(label: str, value: Any, issues: list[Issue], where: str) -> None:
    if isinstance(value, str):
        items: list[Any] = [value]
    elif isinstance(value, list):
        items = value
    else:
        issues.append(Issue("ERROR", where, f"{label} 必须是字符串或字符串数组"))
        return
    if len(items) > MAX_LIST_ITEMS:
        issues.append(Issue(
            "ERROR", where,
            f"{label} 有 {len(items)} 项，超过 {MAX_LIST_ITEMS} 项上限"))
    for i, item in enumerate(items):
        if not isinstance(item, str):
            issues.append(Issue("ERROR", where, f"{label}[{i}] 必须是字符串"))
        elif len(item) > MAX_FIELD_TEXT:
            issues.append(Issue(
                "ERROR", where,
                f"{label}[{i}] 文本 {len(item)} 字，超过 {MAX_FIELD_TEXT} 字上限"))


def check_card(row: dict[str, Any], where: str) -> list[Issue]:
    issues: list[Issue] = []

    # ---- 必填字段 ----
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append(Issue("ERROR", where, "缺少必填字段 name"))
        name = ""
    elif len(name.strip()) > MAX_NAME:
        issues.append(Issue(
            "ERROR", where,
            f"name {len(name.strip())} 字，超过 {MAX_NAME} 字上限"))

    role = row.get("role")
    if not isinstance(role, str) or not role.strip():
        issues.append(Issue("ERROR", where, "缺少必填字段 role"))
    elif role not in ROLES:
        issues.append(Issue(
            "ERROR", where,
            f"role 非法：{role!r}（允许 {', '.join(sorted(ROLES))}）"))

    # ---- 文本 / 列表字段上限（character_library 口径）----
    for field in TEXT_FIELDS:
        if field in row and row[field] is not None:
            _check_text_field(field, row[field], issues, where)
    for field in LIST_FIELDS:
        if field in row and row[field] is not None:
            _check_list_field(field, row[field], issues, where)

    relation = row.get("relationship_vector")
    if isinstance(relation, dict):
        # 字典形态：对象→关系描述（character_library.build_record 同口径）
        if len(relation) > MAX_LIST_ITEMS:
            issues.append(Issue(
                "ERROR", where,
                f"relationship_vector 有 {len(relation)} 条，超过 {MAX_LIST_ITEMS} 条上限"))
        for key, value in relation.items():
            if len(str(key)) > MAX_NAME:
                issues.append(Issue(
                    "ERROR", where,
                    f"relationship_vector 键 {key!r} 超过 {MAX_NAME} 字上限"))
            _check_text_field(f"relationship_vector[{key}]", value, issues, where)
    elif isinstance(relation, (str, list)):
        _check_list_field("relationship_vector", relation, issues, where)
    elif relation is not None:
        issues.append(Issue(
            "ERROR", where,
            "relationship_vector 必须是字符串、字符串数组或 {对象: 描述} 字典"))

    # ---- 新字段枚举校验（缺失=警告，非法=错误）----
    enum_checks = (
        ("gender", GENDERS),
        ("original_position", ORIGINAL_POSITIONS),
        ("source_medium", SOURCE_MEDIUMS),
        ("source_region", SOURCE_REGIONS),
        ("distill_level", DISTILL_LEVELS),
    )
    for field, allowed in enum_checks:
        if field not in row or row[field] is None:
            issues.append(Issue("WARN", where, f"缺少新字段 {field}（存量兼容，建议补标）"))
        elif row[field] not in allowed:
            issues.append(Issue(
                "ERROR", where,
                f"{field} 非法：{row[field]!r}（允许 {', '.join(sorted(allowed))}）"))

    if "slot_keys" not in row or row["slot_keys"] is None:
        issues.append(Issue("WARN", where, "缺少新字段 slot_keys（存量兼容，建议补标）"))
    else:
        slot_keys = row["slot_keys"]
        if not isinstance(slot_keys, dict):
            issues.append(Issue("ERROR", where, "slot_keys 必须是对象"))
        else:
            for key, value in slot_keys.items():
                if key not in SLOT_KEYS:
                    issues.append(Issue(
                        "ERROR", where,
                        f"slot_keys 键非法：{key!r}（允许 {', '.join(sorted(SLOT_KEYS))}）"))
                if not isinstance(value, list) or not all(
                        isinstance(item, str) for item in value):
                    issues.append(Issue(
                        "ERROR", where,
                        f"slot_keys[{key!r}] 必须是字符串数组"))

    return issues


def collect_paths(inputs: list[str]) -> tuple[list[Path], list[Issue]]:
    all_files: list[Path] = []
    issues: list[Issue] = []
    for raw_input in inputs:
        target = Path(raw_input)
        if not target.exists():
            issues.append(Issue("ERROR", target, "路径不存在"))
            continue
        files = _iter_card_files(target)
        if not files:
            issues.append(Issue("WARN", target, "未找到任何 .json 文件"))
        all_files.extend(files)
    return all_files, issues


def validate(inputs: list[str], strict: bool = False) -> tuple[list[Issue], int]:
    issues: list[Issue] = []
    files, load_issues = collect_paths(inputs)
    issues.extend(load_issues)

    seen_name_work: dict[tuple[str, str, str], str] = {}
    seen_name: dict[str, str] = {}
    card_count = 0

    for path in files:
        cards, file_issues = _load_cards(path)
        issues.extend(file_issues)
        for where, row in cards:
            card_count += 1
            issues.extend(check_card(row, where))

            name = str(row.get("name") or "").strip()
            work = str(row.get("work") or "").strip()
            role = str(row.get("role") or "").strip()
            if name:
                key = (name, work, role)
                if key in seen_name_work:
                    issues.append(Issue(
                        "ERROR", where,
                        f"name+work+role 重复：{name}（{work or '无作品'}，role={role or '缺省'}），首次出现在 {seen_name_work[key]}"))
                else:
                    seen_name_work[key] = where
                # 裸名重复不再告警：同名不同出处=不同角色（用户口径 2026-08-28）

    if strict:
        for issue in issues:
            if issue.level == "WARN":
                issue.level = "ERROR"
    return issues, card_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验角色卡 JSON（fate-engine 角色库口径）")
    parser.add_argument("paths", nargs="+", help="JSON 文件或目录（递归扫描）")
    parser.add_argument("--strict", action="store_true", help="把警告升级为错误")
    args = parser.parse_args(argv)

    issues, card_count = validate(args.paths, strict=args.strict)
    for issue in issues:
        print(issue.render())

    errors = sum(1 for i in issues if i.level == "ERROR")
    warnings = sum(1 for i in issues if i.level == "WARN")
    print("-" * 60)
    print(f"汇总：检查 {card_count} 张角色卡；ERROR {errors} 条，WARN {warnings} 条。")
    if errors:
        print("结果：不通过（存在 ERROR）")
        return 1
    print("结果：通过" + ("（--strict 下零警告）" if warnings == 0 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
