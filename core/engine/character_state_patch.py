# -*- coding: utf-8 -*-
"""角色状态 patch 严格机制：结构化校验 → 关系行 upsert → 记忆 patch 装配。

docs/REFACTOR_PLAN.md §0 原则 6「角色状态 patch（补关系写路径）」的机制层
实现，与记忆提取器（extractor：正则宽松抽取）相对——本模块走**模型结构化
答卷 + 代码严格校验**的路径：名字精确 ∈ active_names、evidence 必须为正文
**精确子串**且不含换行/控制字符、重复名整包拒收。原则：fail-closed，
任何校验不过的条目一律拒收并附中文原因，绝不静默放行。

与 turn_grader.grade_character_patch 的分工：那边是「逐条分拣」（剔坏条
留好条）；本模块在分拣基础上加了 envelope 结构校验（FieldSpec）、重复名
检测、控制字符检查与 upsert/to_memory_patch 的写路径机制。
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from core.engine.structured import FieldSpec, extract_json, validate
from core.engine.turn_grader import (
    RELATIONSHIP_DELTAS,
    RELATIONSHIP_SUMMARY_MAX,
    grade_character_patch,
)

# 角色名：精确匹配（不 trim 白字以外的归一化，防止同名异写绕过名册校验）
NAME_MAX = 30
EVIDENCE_MIN = 4
EVIDENCE_MAX = 160
SUMMARY_MAX = RELATIONSHIP_SUMMARY_MAX
SOURCE = "character_patch"

# evidence 禁止的换行与控制字符（\t 也算控制字符；全角空格不算）
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")

# 顶层 envelope 规格：{patches: [...]}
# 注意 kind="any"：structured.validate 的 list 语义是**字符串数组**（逐项要求非空
# 字符串），而 patches 是**对象数组**，用 list 会把每个合法条目判成「必须是非空
# 字符串」。envelope 的数组形状与项数在 _validate_envelope 里手工校验。
PATCH_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(name="patches", kind="any", required=True,
              hint="数组，每位在场角色至多一条角色状态补丁"),
)

# 条目规格（name/evidence/relationship_delta/summary）
ENTRY_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(name="name", kind="str", required=True, max_len=NAME_MAX,
              hint="角色名，必须精确属于本回合在场角色名册"),
    FieldSpec(name="evidence", kind="str", required=True,
              min_len=EVIDENCE_MIN, max_len=EVIDENCE_MAX,
              hint="证据句，必须逐字摘自本回合正文（精确子串，不含换行）"),
    FieldSpec(name="relationship_delta", kind="str", required=True,
              enum=RELATIONSHIP_DELTAS, hint="关系走向"),
    FieldSpec(name="summary", kind="str", required=True, max_len=SUMMARY_MAX,
              hint="关系变化一句话摘要"),
)

_ENTRY_KNOWN = frozenset(spec.name for spec in ENTRY_SPECS)


def _clean_name(value: Any) -> str:
    return str(value or "").strip()


def parse_patch_payload(data: Any, active_names: Sequence[str],
                        narrative: str) -> tuple[list[dict[str, Any]], list[tuple[Any, str]]]:
    """严格校验角色 patch 卷输出，返回 ``(valid, rejected)``。

    校验阶梯（fail-closed，任何一步不过即拒收对应条目）：
    1. 顶层 envelope：必须是含 ``patches`` 非空数组的 JSON 对象（FieldSpec）；
    2. 条目结构：四个字段齐全、类型正确、长度/枚举合规（FieldSpec）；
       未知字段拒收（与 structured.validate 的「未知字段不算错」不同，
       这里是严格模式：patch 条目白名单外字段一律拒收）；
    3. 名字：精确 ∈ active_names（去首尾空白后全等比较）；
    4. evidence：为 narrative 的**精确子串**，且不含换行/控制字符；
    5. 重复名：同一名字出现多次 → **整包拒收**（rejected 一条带原因，
       valid 为空——半套关系更新比不更新更危险）。

    ``rejected`` 每项为 ``(原始条目, 中文原因)``。
    """
    roster = {_clean_name(name) for name in (active_names or ()) if _clean_name(name)}
    story = str(narrative or "")

    # 0) 模型原文入口：字符串按 JSON 提取（兼容 ```json 围栏与前后散文）
    payload = data
    if isinstance(payload, str):
        try:
            payload = extract_json(payload)
        except ValueError as exc:
            return [], [(data, str(exc))]

    # 1) 顶层 envelope：必填字段 + 数组形状（kind="any" 只保证键存在）
    envelope_errors = validate(PATCH_SPECS, payload)
    if envelope_errors:
        return [], [(data, "；".join(envelope_errors))]
    raw_patches = payload.get("patches")
    if not isinstance(raw_patches, list):
        return [], [(data, "字段 patches 必须是数组")]
    valid: list[dict[str, Any]] = []
    rejected: list[tuple[Any, str]] = []
    seen_names: dict[str, int] = {}
    duplicated: dict[str, int] = {}

    for position, item in enumerate(raw_patches, 1):
        if not isinstance(item, Mapping):
            rejected.append((item, f"第{position}条必须是包含 name/evidence/relationship_delta/summary 的对象"))
            continue
        # 2) 条目结构（FieldSpec）+ 未知字段（严格模式）
        errors = list(validate(ENTRY_SPECS, item))
        unknown = [key for key in item if key not in _ENTRY_KNOWN]
        if unknown:
            errors.append(f"第{position}条含未知字段：{'、'.join(sorted(unknown))}"
                          f"（只允许 {'、'.join(sorted(_ENTRY_KNOWN))}）")

        name = _clean_name(item.get("name"))
        evidence = str(item.get("evidence") or "").strip()
        # 3) 名字 ∈ 名册（精确）
        if name and name not in roster:
            errors.append(f"名字「{name}」不在本回合在场名册中")
        # 4) evidence 精确子串 + 换行/控制字符
        if evidence and len(evidence) >= EVIDENCE_MIN:
            if _CONTROL_RE.search(evidence):
                errors.append(f"证据句不得包含换行或控制字符（当前含 "
                              f"{_describe_control(evidence)}）")
            elif evidence not in story:
                excerpt = evidence[:20] + ("…" if len(evidence) > 20 else "")
                errors.append(f"证据句并非正文精确子串：「{excerpt}」")

        if errors:
            rejected.append((item, "；".join(errors)))
            continue
        # 5) 重复名：先登记位置，循环后**整包拒收**（半套关系更新比不更新更危险）
        if name in seen_names:
            duplicated[name] = seen_names[name]
            continue
        seen_names[name] = position
        valid.append({
            "name": name,
            "evidence": evidence,
            "relationship_delta": str(item.get("relationship_delta") or "").strip(),
            "summary": str(item.get("summary") or "").strip(),
        })

    if duplicated:
        names = "、".join(f"「{name}」" for name in sorted(duplicated))
        return [], [(data, f"角色名重复出现：{names}（每位角色至多一条，"
                           f"重复即整包拒收以免半套关系更新）")]
    return valid, rejected


def _describe_control(text: str) -> str:
    """描述 evidence 中的首个换行/控制字符（中文错误信息用）。"""
    match = _CONTROL_RE.search(text)
    if not match:
        return "控制字符"
    char = match.group(0)
    labels = {"\n": "换行符", "\r": "回车符", "\t": "制表符"}
    return labels.get(char, f"控制字符 U+{ord(char):04X}")


def build_relationship_rows(valid_patches: Sequence[Mapping[str, Any]],
                            existing_rows: Sequence[Mapping[str, Any]],
                            round_no: int) -> list[dict[str, Any]]:
    """按 name 精确 upsert 关系行，返回**基于现有完整列表构造的新列表**。

    - 既有行：未知字段全部保留、位置顺序不变（仅命中 name 的行被更新）；
    - 新角色：追加在列表尾部；
    - 更新的字段：relationship_delta / summary / last_evidence / last_round /
      source（固定 ``"character_patch"``）。

    注意：apply_turn 是分类级替换，所以这里的返回值必须携带既有全部行
    （不只是本回合命中的行），否则未命中行会在合并时丢失。
    """
    patches_by_name: dict[str, Mapping[str, Any]] = {}
    for patch in valid_patches or ():
        if isinstance(patch, Mapping):
            name = _clean_name(patch.get("name"))
            if name:
                patches_by_name[name] = patch

    rows: list[dict[str, Any]] = []
    updated_names: set[str] = set()
    for raw in existing_rows or ():
        if not isinstance(raw, Mapping):
            rows.append(raw)  # 原样保留非对象行（防御：不因坏数据丢行）
            continue
        row = dict(raw)  # 浅拷贝：未知字段全量保留
        name = _clean_name(row.get("name"))
        patch = patches_by_name.get(name)
        if patch is not None and name and name not in updated_names:
            row["name"] = name
            row["relationship_delta"] = str(patch.get("relationship_delta") or "").strip()
            row["summary"] = str(patch.get("summary") or "").strip()
            row["last_evidence"] = str(patch.get("evidence") or "").strip()
            row["last_round"] = int(round_no)
            row["source"] = SOURCE
            updated_names.add(name)
        rows.append(row)

    for name, patch in patches_by_name.items():
        if name in updated_names:
            continue
        rows.append({
            "name": name,
            "relationship_delta": str(patch.get("relationship_delta") or "").strip(),
            "summary": str(patch.get("summary") or "").strip(),
            "last_evidence": str(patch.get("evidence") or "").strip(),
            "last_round": int(round_no),
            "source": SOURCE,
        })
    return rows


def to_memory_patch(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """把关系行装配为 ``apply_turn(source="character_patch")`` 可合并的 patch。

    形状：``{"relationships": {"characters": rows}}``。apply_turn 的合并是
    分类级替换（``_merge`` 按 key 整值覆盖），所以 ``rows`` 必须是基于现有
    完整列表构造的新列表（由 :func:`build_relationship_rows` 保证），
    只传本回合命中行会抹掉未命中角色。
    """
    return {"relationships": {"characters": list(rows or ())}}


def build_patch_prompt(active_members: Sequence[Any], narrative: str) -> str:
    """装配角色 patch 卷提示词（assets/prompts/character_patch.md）。

    ``active_members`` 兼容两种形态：名字字符串序列、或含 ``name`` 键的
    对象序列（app 层的 ``active_members`` 是 dict 列表）。
    """
    from core.prompts import render

    names: list[str] = []
    for member in active_members or ():
        if isinstance(member, Mapping):
            name = _clean_name(member.get("name"))
        else:
            name = _clean_name(member)
        if name and name not in names:
            names.append(name)
    roster_text = "、".join(names) if names else "（本回合无在场角色）"
    return render("character_patch.md", active_names=roster_text,
                  narrative=str(narrative or "").strip())


__all__ = [
    "PATCH_SPECS", "ENTRY_SPECS", "SOURCE",
    "NAME_MAX", "EVIDENCE_MIN", "EVIDENCE_MAX", "SUMMARY_MAX",
    "parse_patch_payload", "build_relationship_rows", "to_memory_patch",
    "build_patch_prompt",
]
