"""同名监测与世界观测名（规格 spec_character_db_expansion.md §7）。

- 判定：裸名相同**且出处等价**（work strip 后相同或互为包含）才冲突；
  同名不同出处=不同角色不冲突；同一张卡被选两次也算冲突。
- 处理：冲突组保留第一顺位（主角 > 主线 > 伙伴 > 宿敌），其余按
  ``source_region + source_medium + 开局题材`` 风格自动改名，2-3 字为主，
  不与当前局任何已用名、著名角色撞名。
- 全部为纯函数，方便 unittest 直接覆盖。
"""
from __future__ import annotations

import hashlib

from core.engine.catalog import CharacterCard

# 栏位优先级：输入顺序 主角 > 主线 > 伙伴 > 宿敌（先出现者保留原名）。
SLOT_ORDER = ("主角", "主线", "伙伴", "宿敌")
_SLOT_RANK = {name: rank for rank, name in enumerate(SLOT_ORDER)}

# 党和国家领导人回避名表（历任与现任最高层）：新建角色（含自动改名产出）
# 一律回避。历史人物与虚构角色名由用户自定，不在此列。
POLITICAL_FIGURES = frozenset({
    "毛泽东", "周恩来", "刘少奇", "朱德", "邓小平", "陈云", "林彪", "江青",
    "华国锋", "胡耀邦", "赵紫阳", "江泽民", "李鹏", "朱镕基", "胡锦涛",
    "温家宝", "习近平", "李克强", "李强", "赵乐际", "王沪宁", "蔡奇",
    "丁薛祥", "李希", "韩正",
})

# 兼容旧引用名（语义已收窄为政治人物回避）。
FAMOUS_NAMES = POLITICAL_FIGURES


def is_political_figure(name: str) -> bool:
    """裸名命中党和国家领导人名表（strip 后精确匹配）。"""
    return str(name or "").strip() in POLITICAL_FIGURES

# —— 候选名池：按 (region, 风格, gender) 组织，2-3 字为主 ——
_CN_CLASSICAL_MALE = ("沈砚舟", "顾长风", "陆云铮", "裴远之", "谢照临", "宋濯缨",
                      "陆惊鸿", "沈慕言", "顾清让", "裴云深", "谢行舟", "宋既明",
                      "陆沉舟", "沈知节", "顾北辰")
_CN_CLASSICAL_FEMALE = ("沈青梧", "顾云裳", "谢流苏", "裴月凝", "宋绾绾", "陆芷宁",
                        "沈疏影", "顾念安", "谢婉音", "裴清欢", "宋知薇", "陆雁回",
                        "沈玉茗", "顾香凝", "谢蕴宁")
_CN_MODERN_MALE = ("林一舟", "陈屿", "周砚", "徐司白", "江野", "许知远", "程亦安",
                   "沈聿", "韩东来", "宋则川", "陆知行", "方屿深", "顾北洲", "秦朗",
                   "任平生")
_CN_MODERN_FEMALE = ("林晚棠", "苏念", "沈栀", "许清宁", "江疏影", "温言", "程橘",
                     "宋知意", "陆安夏", "顾星野", "叶栖迟", "乔一", "简遇", "苏杳",
                     "黎照")
_CN_WEBNOVEL_MALE = ("楚惊蛰", "叶尘", "陆长生", "秦无衣", "萧决", "江白圭",
                     "顾北歌", "沈青崖", "李摹云", "宋拂晓", "赵拔山", "周天阙",
                     "洛九霄", "温酒", "燕回声")
_CN_WEBNOVEL_FEMALE = ("洛清欢", "叶青鸾", "苏挽月", "云缨", "姬瑶光", "楚菱歌",
                       "姜芜", "凤栖梧", "白琉夏", "沈流萤", "月见笙", "花折枝",
                       "宁霜华", "温青梧", "阮烟罗")
_JP_MALE = ("黑川律", "柊木凑", "绫濑川彻", "白石湊", "高濑航", "宫本律", "藤堂司",
            "雪代千鹤", "如月透", "神谷澪", "一条寺凛", "樱庭飒", "桐生汐", "橘夏树",
            "结城遼")
_JP_FEMALE = ("雪村千鹤", "樱井澪", "藤宫汐里", "绫小路薰", "白石绘麻", "桐岛葵",
              "一之濑千夏", "柊真昼", "神乐坂雫", "宫濑未咲", "月见里堇", "花泽香澄",
              "冰室菫", "结城野乃", "望月诗织")
_WEST_MALE = ("艾伦", "凯尔文", "罗兰", "维克多", "赛德里克", "卢卡", "奥利弗",
              "亚瑟", "诺亚", "西奥多", "加百列", "伊万", "奥斯卡", "德里克", "莱昂")
_WEST_FEMALE = ("塞琳娜", "艾达", "薇拉", "伊莲娜", "露西亚", "诺拉", "夏洛特",
                "奥菲莉亚", "玛戈", "赛琳", "莉迪亚", "艾斯特", "萝丝", "温蒂", "妮娜")

# 候选名池索引：(region, style_bucket, gender) -> tuple
_POOLS = {
    ("cn", "古典", "male"): _CN_CLASSICAL_MALE,
    ("cn", "古典", "female"): _CN_CLASSICAL_FEMALE,
    ("cn", "现代", "male"): _CN_MODERN_MALE,
    ("cn", "现代", "female"): _CN_MODERN_FEMALE,
    ("cn", "网文", "male"): _CN_WEBNOVEL_MALE,
    ("cn", "网文", "female"): _CN_WEBNOVEL_FEMALE,
    ("jp", "日式", "male"): _JP_MALE,
    ("jp", "日式", "female"): _JP_FEMALE,
    ("west", "音译", "male"): _WEST_MALE,
    ("west", "音译", "female"): _WEST_FEMALE,
    ("other", "现代", "male"): _CN_MODERN_MALE,
    ("other", "现代", "female"): _CN_MODERN_FEMALE,
}

# cn 风格分桶：神话/历史/戏剧/传说/古典小说 → 古典；网文 → 网文；其余 → 现代。
_CN_CLASSICAL_MEDIA = frozenset({"神话", "历史", "戏剧", "传说", "小说"})
_THEME_CLASSICAL_TOKENS = ("武侠", "仙侠", "古风", "古代", "修仙", "玄幻", "架空王朝", "宫廷")
_THEME_MODERN_TOKENS = ("都市", "现代", "科幻", "末世", "校园")


def _normalized_work(card: CharacterCard) -> str:
    return str(getattr(card, "work", "") or "").strip()


def _same_source(work_a: str, work_b: str) -> bool:
    """出处等价：strip 后相同，或互为包含（如 "BLEACH" ⊂ "死神/BLEACH"）。

    一方为空、另一方非空视为不同出处；双方均空视为同出处（同一局内
    未标出处的同名卡按同出处处理）。
    """
    if work_a == work_b:
        return True
    if not work_a or not work_b:
        return False
    return work_a in work_b or work_b in work_a


def _source_groups(cards: list[CharacterCard], indices: list[int]) -> list[list[int]]:
    """把同名卡的索引按出处等价聚类（包含等价不保证传递性，用并查集）。"""
    parent = {i: i for i in indices}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for pos, i in enumerate(indices):
        for j in indices[pos + 1:]:
            if _same_source(_normalized_work(cards[i]), _normalized_work(cards[j])):
                root_i, root_j = find(i), find(j)
                if root_i != root_j:
                    parent[root_i] = root_j
    clusters: dict[int, list[int]] = {}
    for i in indices:
        clusters.setdefault(find(i), []).append(i)
    return sorted(clusters.values(), key=lambda group: group[0])


def detect_collisions(cards: list[CharacterCard]) -> list[list[int]]:
    """按「name + 出处等价」分组返回冲突组索引；组内多于 1 个即冲突。

    - 裸名相同且出处等价（strip 后相同或互为包含）→ 冲突；
    - 同名不同出处 = 不同角色，不冲突；
    - 同一张卡被选两次也算冲突（出处必然等价）。
    返回的组按组内首个索引升序排列，组内索引保持输入顺序。
    """
    by_name: dict[str, list[int]] = {}
    for index, card in enumerate(cards or []):
        name = str(getattr(card, "name", "") or "").strip()
        if not name:
            continue
        by_name.setdefault(name, []).append(index)
    result: list[list[int]] = []
    for indices in by_name.values():
        if len(indices) > 1:
            result.extend(group for group in _source_groups(cards or [], indices)
                          if len(group) > 1)
    result.sort(key=lambda group: group[0])
    return result


def _style_bucket(card: CharacterCard, theme: str) -> str:
    """cn 卡按 source_medium + 开局题材分桶：古典 / 网文 / 现代。"""
    medium = str(getattr(card, "source_medium", "") or "").strip()
    if medium == "网文":
        return "网文"
    if medium in _CN_CLASSICAL_MEDIA:
        return "古典"
    theme_text = str(theme or "")
    if any(token in theme_text for token in _THEME_CLASSICAL_TOKENS):
        return "古典"
    if any(token in theme_text for token in _THEME_MODERN_TOKENS):
        return "现代"
    return "现代"


def _region_of(card: CharacterCard) -> str:
    region = str(getattr(card, "source_region", "") or "").strip().lower()
    return region if region in ("cn", "jp", "west", "other") else "cn"


def worldview_rename(card: CharacterCard, context: dict | None = None) -> str:
    """为冲突卡生成一个依世界观风格的新名（纯函数、确定性）。

    context 可含：
    - ``theme``: 开局题材/世界观关键字（决定 cn 卡的古典/网文/现代分桶）；
    - ``used_names``: 当前局已用名集合（新名不得与之重复）；
    - ``slot``: 栏位名（仅记录用途，不参与风格决策）。

    风格由 source_region（cn 古典/现代/网文、jp 日式音译、west 音译）+ gender
    决定；候选循环扫描，起点由卡 id+原名的稳定散列决定，保证同名卡改名互异。
    """
    context = context or {}
    region = _region_of(card)
    style = "日式" if region == "jp" else ("音译" if region == "west" else _style_bucket(card, context.get("theme", "")))
    gender = str(getattr(card, "gender", "") or "")
    pool = _POOLS.get((region, style, gender)) or _POOLS.get((region, style, "male")) or _CN_MODERN_MALE
    forbidden = set(context.get("used_names") or [])
    forbidden |= POLITICAL_FIGURES
    forbidden.add(str(getattr(card, "name", "") or "").strip())
    start = int(hashlib.md5(f"{getattr(card, 'id', '')}|{getattr(card, 'name', '')}".encode("utf-8")).hexdigest(), 16)
    size = len(pool)
    for offset in range(size):
        candidate = pool[(start + offset) % size]
        if candidate not in forbidden:
            return candidate
    # 候选池全被占用（极端情况）：追加序号保证唯一，仍保持 2-3 字习惯。
    base = pool[start % size]
    suffix = 2
    while f"{base}{suffix}" in forbidden:
        suffix += 1
    return f"{base}{suffix}"


def card_persona(card: CharacterCard) -> str:
    """把角色卡提炼为可注入生成的 persona 文本（宿敌卡 persona 覆盖用）。"""
    parts = [
        str(getattr(card, "voice", "") or ""),
        str(getattr(card, "background", "") or ""),
        f"角色原型：{card.archetype}" if getattr(card, "archetype", "") else "",
        f"核心欲望：{card.desire}" if getattr(card, "desire", "") else "",
        f"核心恐惧：{card.fear}" if getattr(card, "fear", "") else "",
    ]
    return "\n".join(part for part in parts if part)


def plan_renames(selected: list[tuple[str, CharacterCard]], theme: str = "",
                 used_names: tuple[str, ...] | list[str] = ()) -> dict:
    """四栏选中卡的撞名处理主入口（规格 §7）。

    ``selected``：``[(slot, card)]``，slot 取 主角/主线/伙伴/宿敌；同一卡可重复选。
    内部按 主角 > 主线 > 伙伴 > 宿敌 排出顺位，冲突组保留第一顺位，其余改名。

    返回 ``{"renames": [{"from", "to", "slot"}], "cards": [运行时记录]}``：
    ``cards`` 保持 selected 输入顺序，每条含
    ``{slot, id, name, renamed_from, gender, work, source_medium, persona}``；
    被改名的卡 ``renamed_from`` 保留原名。
    """
    ordered = sorted(
        enumerate(selected or []),
        key=lambda pair: (_SLOT_RANK.get(str(pair[1][0]), len(_SLOT_RANK)), pair[0]),
    )
    ordered_cards = [card for _, (_slot, card) in ordered]
    ordered_slots = [str(slot) for slot, _card in ordered]
    groups = detect_collisions(ordered_cards)
    used = set(used_names) | {str(getattr(card, "name", "") or "") for card in ordered_cards}
    rename_by_position: dict[int, str] = {}
    for group in groups:
        for ord_index in group[1:]:
            orig_index = ordered[ord_index][0]
            card = ordered_cards[ord_index]
            new_name = worldview_rename(card, {
                "theme": theme,
                "used_names": used,
                "slot": ordered_slots[ord_index],
            })
            used.add(new_name)
            rename_by_position[orig_index] = new_name

    records: list[dict] = []
    renames: list[dict] = []
    for position, (slot, card) in enumerate(selected or []):
        new_name = rename_by_position.get(position, "")
        record = {
            "slot": str(slot),
            "id": card.id,
            "name": new_name or card.name,
            "renamed_from": card.name if new_name else "",
            "gender": card.gender,
            "work": card.work,
            "source_medium": card.source_medium,
            "original_position": getattr(card, "original_position", ""),
            "persona": card_persona(card),
        }
        records.append(record)
        if new_name:
            renames.append({"from": card.name, "to": new_name, "slot": str(slot)})
    return {"renames": renames, "cards": records}


__all__ = ["SLOT_ORDER", "FAMOUS_NAMES", "POLITICAL_FIGURES", "is_political_figure",
           "detect_collisions", "worldview_rename", "card_persona", "plan_renames"]
