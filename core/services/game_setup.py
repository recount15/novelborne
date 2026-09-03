# -*- coding: utf-8 -*-
"""开局装配服务（Phase 3c 自 core/app.py 的 on_start 抽出）。

on_start 巨型生成器中的「装配类职责块」收拢为纯函数：

- ``resolve_work_source``  作品来源解析（TXT 强校验/切章/摘录提取/普通模式片段）
- ``resolve_nemesis``      宿敌人格解析（上传 MD ＞ 角色模型 ＞ 自定义文本）
- ``assemble_roster``      名册装配（原伙伴/伴侣两段孪生代码合一）
- ``compute_faction``      阵营势差与宿敌难度计算

约定：不发模型调用、不读写 state；输入输出均为普通值。唯一既有磁盘
行为是 ``resolve_work_source`` 内的切章（``_split_uploaded_book`` 落
章节文件到 WRITABLE_DIR/books），这是该职责块原有的行为，照原样保留。

错误契约：``resolve_work_source`` 失败时抛 ``WorkSourceError``
（``ValueError`` 子类），``str(exc)`` 为面向用户的中文文案（逐字保留），
``exc.state`` 为原错误 yield 分支携带的部分 state，on_start 捕获后原样 yield。
"""
import os

from core import engine
from core import fate_engine as fe
from core.ui import common as ui_common
from core.ui.roster_form import normalize_count, skill_value

_split_uploaded_book = ui_common._split_uploaded_book

# 实力档位表（与 app.POWER_CHOICES 同源同值；member_pack 按 label→level 解析）。
POWER_CHOICES = [("按设定推断", -1), ("未评估/凡人 0", 0), ("偏弱 1", 1),
                 ("相当 2", 2), ("偏强 3", 3), ("远强 4", 4)]


class WorkSourceError(ValueError):
    """作品来源解析失败。message 为用户可见中文文案；state 为错误分支的部分 state。"""

    def __init__(self, message, state):
        super().__init__(message)
        self.state = state


def resolve_work_source(mode, work, novel_file, fragment, novel_display_name,
                        *, gf_confirmed=True):
    """作品来源解析（原 on_start C 块）。

    强化模式严格要求完整 TXT；作品库档案只服务基础模式。
    返回 ``(chapter_index, novel_excerpt, novel_name, work_label)``；
    失败抛 :class:`WorkSourceError`（文案与错误分支 state 逐字保留）。
    ``gf_confirmed`` 为当前金手指就绪态，仅用于错误 state 的该键。
    """
    enhanced = bool(mode and str(mode).startswith("强化"))
    novel_name, novel_excerpt, work_label = None, None, None
    chapter_index = None
    uploaded_path = fe._to_path(novel_file) if novel_file else None
    if enhanced and (not uploaded_path or not str(uploaded_path).lower().endswith(".txt")):
        raise WorkSourceError(
            "⚠️ 强化模式必须上传 TXT 原著，不能使用作品库；当前未检测到有效上传文件。",
            {"system": "", "history": [], "plot_ready": False,
             "gf_stage": "pending", "gf_confirmed": bool(gf_confirmed),
             "chapter_index": None})
    if novel_file:
        chapter_index = _split_uploaded_book(novel_file)
        if enhanced and (not chapter_index or not chapter_index.get("chapters")):
            raise WorkSourceError(
                "⚠️ 强化模式 TXT 切章失败，无法完成剧情准备，请检查章节标题和文本编码。",
                {"system": "", "history": [], "plot_ready": False,
                 "gf_stage": "pending", "gf_confirmed": bool(gf_confirmed),
                 "chapter_index": chapter_index})
        excerpt = fe.read_upload_text(novel_file, fe.MAX_NOVEL_EXCERPT)
        if excerpt:
            novel_excerpt = excerpt
            novel_name = (novel_display_name or "").strip() or os.path.splitext(
                os.path.basename(uploaded_path or novel_file))[0]
            if chapter_index and chapter_index.get("chapters"):
                first = chapter_index["chapters"][0]
                novel_excerpt = excerpt[: min(len(excerpt), max(2000, first.get("chars", 2000)))]
    if not novel_name:
        work_label = work
        if enhanced:
            raise WorkSourceError(
                "⚠️ 强化模式必须上传 TXT 原著，不能使用作品库；上传内容为空或不可读取。",
                {"system": "", "history": [], "plot_ready": False,
                 "gf_stage": "pending", "gf_confirmed": bool(gf_confirmed),
                 "chapter_index": chapter_index})
        if not work_label:
            raise WorkSourceError("⚠️ 请选择作品库作品，或上传 TXT 原著。",
                                  {"system": "", "history": []})
    if not mode.startswith("强化") and fragment:
        novel_excerpt = (novel_excerpt or "") + "\n\n# 普通模式指定片段\n" + str(fragment).strip()
    return chapter_index, novel_excerpt, novel_name, work_label


def resolve_nemesis(enable_nemesis, mode, nemesis_file, nemesis_select,
                    nemesis_display_name, char_path):
    """宿敌人格解析（原 on_start E 块）：上传 MD ＞ 角色模型 ＞ 自定义文本。

    仅强化模式生效；未启用时返回 ``(None, None)``。
    ``char_path`` 为 {显示标签: 文件路径} 的角色模型映射（app.CHAR_PATH）。
    """
    nemesis_label, nemesis_persona = None, None
    if not bool(enable_nemesis and mode.startswith("强化")):
        return nemesis_label, nemesis_persona
    if nemesis_file:
        nemesis_persona = fe.read_upload_text(nemesis_file)
        display = (nemesis_display_name or "").strip() or os.path.splitext(
            os.path.basename(nemesis_file))[0]
        nemesis_label = display + "（上传宿敌）"
    elif nemesis_select in char_path:
        nemesis_persona = fe.read_character_model(char_path[nemesis_select])
        nemesis_label = nemesis_select
    else:
        nemesis_persona = (nemesis_select or "").strip()
        nemesis_label = "自定义宿敌"
    return nemesis_label, nemesis_persona


def assemble_roster(rows, count, slot_label, default_prefix):
    """装配一段名册（原 on_start F 块：伙伴/伴侣孪生段合一）。

    无名行保留名额：卡和性格都只是「魂」，名字开局由模型分配——占位名
    ``default_prefix+序号`` 标记 ``name_pending``，穿越落定后写回真名。
    ``slot_label`` 为栏位名（伙伴/主线），供调用方自文档；``default_prefix``
    为占位名前缀（伙伴/伴侣）。
    """
    rows = list(rows or [])
    members = []
    limit = normalize_count(count, None)
    for row in rows[:limit or len(rows)]:
        row = row if isinstance(row, dict) else {}
        # 无名行保留名额：卡和性格都只是「魂」，名字开局由模型分配（占位名穿越落定后写回）。
        _row_name = str(row.get("name") or "").strip()
        _name_pending = not _row_name
        if _name_pending:
            _row_name = f"{default_prefix}{len(members) + 1}"
        skill_input = row.get("skill_source") if isinstance(row.get("skill_source"), dict) else row.get("skill")
        packed = member_pack(_row_name, skill_input, row.get("background"), row.get("power", -1), custom_skill=row.get("custom_skill", ""), participation=row.get("participation", 1), slot_id=row.get("slot_id", ""), skill_upload=row.get("skill_upload", ""))
        if packed:
            packed["roster_index"] = len(members) + 1
            packed["roster_total"] = limit or len(rows)
            packed["character_model"] = row.get("character_model", "")
            packed["character_model_source"] = row.get("character_model_source", "")
            packed["character_card"] = dict(row.get("character_card") or {})
            packed["persona_preset"] = str(row.get("persona_preset") or "").strip()
            if _name_pending:
                packed["name_pending"] = True
            members.append(packed)
    return members


def nemesis_card_power(nemesis_card: dict) -> int:
    """按宿敌卡的 original_position 推断其原型强度（与前端实时估算一致）。

    反派=4（远强），主角/男主/女主=3（准远强），配角=2（相当），其余=2。
    """
    position = str((nemesis_card or {}).get("original_position") or "").strip()
    if position == "反派":
        return 4
    if position in ("主角", "男主", "女主"):
        return 3
    return 2


def member_pack(name, skill, background, power=-1, custom_skill="",
                participation=1, slot_id="", skill_upload=""):
    """把名册行打包成成员 dict（原 app._member_pack，技能来源解析）。

    skill 支持预设下拉、上传文件、skill_source 映射和自定义文本，
    最终只写一行字段。
    """
    if not name:
        return None
    upload_path = str(skill_upload or "").strip()
    if isinstance(skill, dict):
        source = str(skill.get("source", skill.get("type", "preset"))).lower()
        value = skill.get("value", "")
        if source == "upload":
            if isinstance(value, str):
                upload_path = upload_path or value
            skill = ""
        elif source == "custom":
            custom_skill = str(value or "")
            skill = ""
        else:
            skill = str(value or "")
    if upload_path and os.path.isfile(upload_path):
        skill_text = skill_value(fe.read_upload_text(upload_path), custom_skill)
    elif isinstance(skill, str) and not os.path.isfile(skill):
        skill_text = skill_value(skill, custom_skill)
    else:
        skill_text = fe.read_upload_text(skill) if skill else ""
        skill_text = skill_value(skill_text, custom_skill)
    item = {"name": str(name).strip(), "skill": skill_text,
            "background": background or "", "participation": max(1, min(9, int(participation or 1)))}
    if slot_id:
        item["slot_id"] = str(slot_id)
    labels = {label: value for label, value in POWER_CHOICES}
    if power in labels:
        level = labels[power]
    else:
        try:
            level = int(power)
        except (TypeError, ValueError):
            level = -1
    if level >= 0:
        item["power"] = level
    return item


def compute_faction(nemesis_card, nemesis_label, nemesis_persona, companions,
                    heroines, difficulty, work_label, novel_name):
    """阵营势差与宿敌难度（原 on_start H 块）。

    传入的 companions/heroines 应已完成金手指阻断（engine.apply_block）。
    返回 ``(faction_gap, nemesis_difficulty)``。
    """
    faction_members = companions + heroines
    # 宿敌方成员：宿敌卡（original_position 反映其原型强度）+ 自定义宿敌文本。
    opposing_members = []
    if nemesis_card:
        opposing_members.append({
            "name": nemesis_card.get("name") or "宿敌",
            "power": nemesis_card_power(nemesis_card),
            "skill": nemesis_card.get("persona") or "",
            "background": nemesis_card.get("work") or "",
        })
    elif nemesis_persona:
        opposing_members.append({"name": nemesis_label or "宿敌", "skill": nemesis_persona})
    faction_gap = engine.runtime_mechanics.assess_faction_gap(
        faction_members, protagonist_power=2, genre=work_label or novel_name or "",
        opposing_members=opposing_members)
    nemesis_difficulty = engine.runtime_mechanics.nemesis_difficulty(
        difficulty, faction_members, protagonist_power=2, genre=work_label or novel_name or "",
        opposing_members=opposing_members)
    return faction_gap, nemesis_difficulty
