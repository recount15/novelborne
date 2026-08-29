"""角色库迁移 dry-run 校验器。

正式底数为 data/characters/builtin/**/*.json；data/character_pools.json 仅作为旧库
审计输入，不与正式库合并。校验不会修改任何源文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RULE_VERSION = "character-library-migrate-1.0"
SLOTS = ("主角栏", "主线栏", "伙伴栏", "宿敌栏")
GENDERS = {"male", "female", "unknown"}
POSITIONS = {"主角", "女主", "男主", "配角", "反派"}
MEDIUMS = {"小说", "网文", "轻小说", "影视", "电影", "电视剧", "动漫", "漫画", "游戏", "神话", "历史", "戏剧", "传说", "其他"}
REGIONS = {"cn", "jp", "west", "other"}
ROLES = {"伙伴", "single_heroine", "multi_heroine", "主角", "反派"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path, issues: list[dict[str, Any]]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues.append({"level": "ERROR", "path": str(path), "message": f"JSON读取/解析失败: {exc}"})
        return None


def rows_from(raw: Any, path: Path, issues: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(raw, dict) and isinstance(raw.get("characters"), list):
        rows = raw["characters"]
    elif isinstance(raw, dict) and isinstance(raw.get("cards"), list):
        rows = raw["cards"]
    elif isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = [raw]
    else:
        issues.append({"level": "ERROR", "path": str(path), "message": "角色数据必须是对象、数组或 wrapper"})
        return []
    result = []
    for i, row in enumerate(rows):
        where = f"{path}#{i}" if len(rows) > 1 else str(path)
        if not isinstance(row, dict):
            issues.append({"level": "ERROR", "path": where, "message": "角色记录必须是对象"})
        else:
            result.append((where, row))
    return result


def check_card(where: str, row: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    for field in ("id", "name", "gender", "original_position", "source_medium", "source_region", "slot_keys"):
        if field not in row or row[field] in (None, ""):
            issues.append({"level": "ERROR", "path": where, "message": f"缺少固定字段 {field}"})
    if "id" in row and (not isinstance(row["id"], str) or not row["id"].strip()):
        issues.append({"level": "ERROR", "path": where, "message": "id 必须为非空字符串"})
    if "name" in row and (not isinstance(row["name"], str) or not row["name"].strip()):
        issues.append({"level": "ERROR", "path": where, "message": "name 必须为非空字符串"})
    for field, allowed in (("gender", GENDERS), ("original_position", POSITIONS), ("source_medium", MEDIUMS), ("source_region", REGIONS)):
        if field in row and row[field] not in allowed:
            issues.append({"level": "ERROR", "path": where, "message": f"{field} 非法: {row[field]!r}"})
    slots = row.get("slot_keys")
    if not isinstance(slots, dict):
        issues.append({"level": "ERROR", "path": where, "message": "slot_keys 必须是对象"})
    else:
        for key, value in slots.items():
            if key not in SLOTS:
                issues.append({"level": "ERROR", "path": where, "message": f"slot_keys 键非法: {key!r}"})
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                issues.append({"level": "ERROR", "path": where, "message": f"slot_keys[{key!r}] 必须是字符串数组"})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="角色库迁移 dry-run 校验器")
    ap.add_argument("--root", default=None, help="项目根目录，默认按脚本位置推断")
    ap.add_argument("--strict", action="store_true", help="将 WARN 升级为 ERROR")
    ap.add_argument("--dry-run", action="store_true", help="仅校验并生成报告（默认行为）")
    ap.add_argument("--json-report", default=None, help="机器报告路径")
    ap.add_argument("--text-report", default=None, help="人读报告路径")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    builtin = root / "data" / "characters" / "builtin"
    legacy = root / "data" / "character_pools.json"
    issues: list[dict[str, Any]] = []
    files = sorted(builtin.rglob("*.json")) if builtin.is_dir() else []
    if not files:
        issues.append({"level": "ERROR", "path": str(builtin), "message": "正式角色库为空或不存在"})
    cards: list[tuple[str, dict[str, Any]]] = []
    file_hashes = {}
    for path in files:
        file_hashes[str(path.relative_to(root))] = sha256(path)
        cards.extend(rows_from(load_json(path, issues), path, issues))
    id_seen: dict[str, str] = {}
    id_groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    name_work: dict[tuple[str, str], str] = {}
    for where, row in cards:
        check_card(where, row, issues)
        ident, name, work = row.get("id"), row.get("name"), row.get("work", "")
        if isinstance(ident, str) and ident:
            id_groups[ident].append((where, row))
            if ident in id_seen:
                issues.append({"level": "ERROR", "path": where, "message": f"重复 id: {ident}（冲突组见 report.conflict_groups）"})
            else:
                id_seen[ident] = where
        if isinstance(name, str) and name:
            key = (name.strip(), str(work).strip())
            if key in name_work:
                issues.append({"level": "ERROR", "path": where, "message": f"同名同出处: {name} / {work}，首次出现于 {name_work[key]}"})
            else:
                name_work[key] = where
    conflict_groups = []
    for ident, members in sorted(id_groups.items()):
        if len(members) < 2:
            continue
        canonical_where, canonical = members[0]
        canonical_candidates = []
        member_rows = []
        for where, row in members:
            path = Path(where.split("#", 1)[0])
            relative_where = str(path.relative_to(root)) + (where[len(str(path)):] if where != str(path) else "")
            row_aliases = row.get("aliases", [])
            if isinstance(row_aliases, str):
                row_aliases = [row_aliases]
            if not isinstance(row_aliases, list):
                row_aliases = []
            member_rows.append({"path": relative_where, "name": row.get("name"), "work": row.get("work"), "source": row.get("source"), "source_medium": row.get("source_medium"), "slot_keys": row.get("slot_keys"), "role": row.get("role"), "category": path.parent.name, "aliases": row_aliases})
            canonical_candidates.append({"path": relative_where, "name": row.get("name"), "work": row.get("work"), "role": row.get("role"), "category": path.parent.name, "selected": where == canonical_where})
        all_fields = sorted(set().union(*(row.keys() for _, row in members)))
        differences = {}
        for field in sorted(set(all_fields) | {"work", "source", "slot_keys"}):
            values = [row.get(field) for _, row in members]
            if field in {"work", "source", "slot_keys"} or any(value != values[0] for value in values[1:]):
                differences[field] = [{"path": candidate["path"], "value": row.get(field)} for candidate, (_, row) in zip(canonical_candidates, members)]
        aliases = set()
        for _, row in members:
            if row.get("name"):
                aliases.add(str(row["name"]))
            row_aliases = row.get("aliases", [])
            if isinstance(row_aliases, str):
                aliases.add(row_aliases)
            elif isinstance(row_aliases, list):
                aliases.update(str(alias) for alias in row_aliases if alias)
        conflict_groups.append({"id": ident, "canonical": canonical_candidates[0]["path"], "canonical_candidates": canonical_candidates, "merged_from": [candidate["path"] for candidate in canonical_candidates[1:]], "aliases": sorted(aliases), "members": member_rows, "field_differences": differences})
    legacy_raw = load_json(legacy, issues) if legacy.exists() else None
    legacy_rows = rows_from(legacy_raw, legacy, issues) if legacy_raw is not None else []
    if len(legacy_rows) != 92:
        issues.append({"level": "WARN", "path": str(legacy), "message": f"旧库记录数为 {len(legacy_rows)}，预期 92"})
    if args.strict:
        for issue in issues:
            if issue["level"] == "WARN": issue["level"] = "ERROR"
    counts = Counter(i["level"] for i in issues)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report = {"rule_version": RULE_VERSION, "generated_at": now, "root": str(root), "formal": {"path": str(builtin.relative_to(root)), "files": len(files), "cards": len(cards), "unique_ids": len(id_seen), "file_sha256": file_hashes}, "legacy": {"path": str(legacy.relative_to(root)), "cards": len(legacy_rows), "merged": False}, "summary": {"ERROR": counts["ERROR"], "WARN": counts["WARN"], "status": "FAIL" if counts["ERROR"] else "PASS"}, "conflict_groups": conflict_groups, "issues": issues}
    json_path = Path(args.json_report) if args.json_report else root / "outputs" / "character_library_migrate_report.json"
    text_path = Path(args.text_report) if args.text_report else root / "outputs" / "character_library_migrate_report.txt"
    json_path.parent.mkdir(parents=True, exist_ok=True); text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"角色库迁移 dry-run 报告", f"规则版本: {RULE_VERSION}", f"生成时间: {now}", f"正式库: {len(files)} 文件 / {len(cards)} 张 / {len(id_seen)} 唯一 id", f"旧库: {len(legacy_rows)} 张（未合并）", f"结果: {report['summary']['status']}，ERROR {counts['ERROR']}，WARN {counts['WARN']}", "", "问题明细:"]
    lines += [f"[{i['level']}] {i['path']}: {i['message']}" for i in issues] or ["无"]
    lines += ["", "重复 ID 冲突组:"]
    for group in conflict_groups:
        lines.append(f"- {group['id']} | canonical: {group['canonical']} | merged_from: {', '.join(group['merged_from'])}")
        lines.append("  canonical candidates: " + "; ".join(f"{candidate['path']} (selected={candidate['selected']})" for candidate in group["canonical_candidates"]))
        lines.append(f"  aliases: {', '.join(group['aliases'])}")
        for member in group["members"]:
            lines.append(f"  member: {member['path']} | name={member['name']} | work={member['work']} | role={member['role']} | category={member['category']}")
        for field, values in group["field_differences"].items():
            lines.append(f"  field difference {field}: " + " ; ".join(f"{v['path']}={v['value']!r}" for v in values))
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json_path); print(text_path); print(f"正式库 {len(cards)} 张，旧库 {len(legacy_rows)} 张，ERROR {counts['ERROR']}，WARN {counts['WARN']}")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
