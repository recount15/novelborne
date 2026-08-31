# -*- coding: utf-8 -*-
"""试卷库机制：双族六档试卷资产的加载、结构校验与档位门禁（纯数据 + 规则）。

试卷是声明式静态资产（``assets/papers/*.json``，双族六档 × 三阶段共 18 份，
规格见 docs/REFACTOR_PLAN.md §2）：1–3 档小卷族槽位集合完全相同，档间差异
只在槽内丰度（段窗口与必含词密度）；4–6 档大卷族在小卷全集之上追加
gf_deep / ripple_echo / world_reaction / subplot 四个扩展槽。本模块只做
数据加载与确定性规则计算：不调模型、不读写用户配置；数据目录定位沿用
``engine.tropes.data_dir`` 的 frozen 感知写法（PyInstaller 打包后取捆绑目录）。

关键约定：
- 档位门禁（§0 第 4/9 条）：普通模式只允许 1–2 档；强化模式全 6 档；
  6 档（史诗卷）必须开启类 agent 批改重填（``validate_selection`` 硬校验）。
- 阶段三态：setup 铺垫卷 / climax 收束卷（预算尾声，最后一段必含收束槽
  anchor_climax）/ free 自由卷（全局碎锚或 relay 激活，锚点槽降级为
  anchor_free 且 notes 注明「仅供参考，不强制收束」）。
- 加载即严检：任何一份试卷结构不合法立即抛 ``ValueError``（中文信息、带
  文件名），并要求 18 份齐备（缺一即抛），杜绝半库运行。校验项包括：
  字段齐全、tier/family/stage 与档位表一致、slots ⊆ SLOT_TYPES 且等于
  该族该阶段的槽位全集、段数符合档位、段窗口求和 ∈ target×(1±tolerance+0.05)、
  options.count == 6、factor_split 合计 == 6、文件名与试卷键一致。
- ``load_papers`` 经 ``lru_cache`` 进程级缓存；测试或热替换数据后可调
  ``load_papers.cache_clear()`` 强制重读。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from core.engine.participation import RICHNESS_DEFAULT

# —— 档位 / 族 / 阶段常量（docs/REFACTOR_PLAN.md §2 试卷规格表）——
STAGES: tuple[str, ...] = ("setup", "climax", "free")
STAGE_LABELS: dict[str, str] = {"setup": "铺垫卷", "climax": "收束卷", "free": "自由卷"}
FAMILIES: tuple[str, ...] = ("small", "large")
FAMILY_LABELS: dict[str, str] = {"small": "小卷族", "large": "大卷族"}
TIER_LABELS: dict[int, str] = {1: "轻盈", 2: "简明", 3: "标准", 4: "丰厚", 5: "鸿篇", 6: "史诗"}
TIER_TARGET_CHARS: dict[int, int] = {1: 400, 2: 650, 3: 950, 4: 1350, 5: 1850, 6: 2400}
EXPECTED_SEGMENT_COUNTS: dict[int, int] = {1: 1, 2: 2, 3: 3, 4: 3, 5: 4, 6: 5}
BASIC_MODE_MAX_TIER = 2        # 普通模式可用的最高档（basic_mode 只在 1–2 档为 true）
AGENT_REQUIRED_TIER = 6        # 必须开启类 agent 批改重填的档位
AGENT_RECOMMENDED_TIER = 5     # 建议开启类 agent 的档位
WINDOW_SUM_SLACK = 0.05        # 段窗口求和校验在 tolerance 之外的额外松弛（覆盖取整误差）

# 小卷族固定全集：任何小卷档位都必须包含全部小卷槽位（无机制命中时运行时
# 缺省，但卷面模板必须存在）；大卷族 = 小卷全集 + 四个扩展槽。
SMALL_SLOTS: tuple[str, ...] = (
    "anchor_setup", "character_interaction", "golden_finger", "ripple_cost",
    "quest_progress", "break_anchor_stage", "directive_landing", "cliffhanger",
)
LARGE_EXTRA_SLOTS: tuple[str, ...] = ("gf_deep", "ripple_echo", "world_reaction", "subplot")

# 槽位类型注册表：槽名 -> 中文说明 + 所属族（family 为 small 的槽两族共用，
# large 为大卷族扩展槽）。词表改动必须与试卷 JSON 及批改逻辑同步。
SLOT_TYPES: dict[str, dict[str, str]] = {
    "anchor_setup": {
        "desc": "铺垫期锚点槽：锚点以铺垫/推进形式点名（pending/mentioned/partial 均可）",
        "family": "small",
    },
    "anchor_climax": {
        "desc": "收束期锚点槽：本章预算尾声，锚点必须 fulfilled 落地（事件/结果/因果齐全）",
        "family": "small",
    },
    "anchor_free": {
        "desc": "自由期锚点槽：全局碎锚/relay 激活后降级为参考（仅供参考，不强制收束）",
        "family": "small",
    },
    "character_interaction": {
        "desc": "在场角色互动槽：点名在场角色并给出可观测回应与关系变化",
        "family": "small",
    },
    "golden_finger": {
        "desc": "金手指紧凑槽：按冷却与代价一次性紧凑落地",
        "family": "small",
    },
    "ripple_cost": {
        "desc": "涟漪代价槽：玩家行动的影响分级与即时代价",
        "family": "small",
    },
    "quest_progress": {
        "desc": "任务推进槽：active 任务 requirement 关键词的可验证进展",
        "family": "small",
    },
    "break_anchor_stage": {
        "desc": "碎锚阶段槽：active 碎锚任务当前阶段 requirement 的可验证推进",
        "family": "small",
    },
    "directive_landing": {
        "desc": "铁律落地槽：三愿/永久通路以具体行为兑现（非口头宣称）",
        "family": "small",
    },
    "cliffhanger": {
        "desc": "悬念钩子槽：指向下回合可承接事件的具体钩子",
        "family": "small",
    },
    "gf_deep": {
        "desc": "金手指深描槽：机制细节/代价结构/边界的展开描写",
        "family": "large",
    },
    "ripple_echo": {
        "desc": "涟漪回响扩槽：二次影响与长线回响",
        "family": "large",
    },
    "world_reaction": {
        "desc": "世界反应槽：势力/环境/第三方对事件的可观测反馈",
        "family": "large",
    },
    "subplot": {
        "desc": "支线槽：支线或多线并进（不得喧宾夺主）",
        "family": "large",
    },
}

# 试卷 JSON 必填字段（键序即资产文件的固定键序）。
_PAPER_FIELDS: tuple[str, ...] = (
    "tier", "family", "stage", "label", "target_chars", "tolerance",
    "basic_mode", "agent_required", "agent_recommended", "slots", "segments", "options",
)
_SEGMENT_FIELDS: tuple[str, ...] = ("role", "window", "slots", "notes")

# 旧故事丰富度（participation.RICHNESS_*，300–1000）→ 双族六档的映射断点。
LEGACY_TIER_BOUNDS: tuple[tuple[float, int], ...] = ((500.0, 1), (675.0, 2))


def family_for_tier(tier: int) -> str:
    """档位 → 试卷族：1–3 档小卷族，4–6 档大卷族。"""
    return "small" if int(tier) <= 3 else "large"


def anchor_slot_for(stage: str) -> str:
    """阶段 → 锚点槽三态名：setup/climax/free → anchor_setup/anchor_climax/anchor_free。"""
    return f"anchor_{stage}"


def expected_slots(family: str, stage: str) -> tuple[str, ...]:
    """该族该阶段的槽位全集（保持注册表声明顺序）。"""
    core = tuple(
        anchor_slot_for(stage) if name == "anchor_setup" else name
        for name in SMALL_SLOTS
    )
    return core + LARGE_EXTRA_SLOTS if family == "large" else core


# 完整试卷库键集：双族六档 × 三阶段 = 18 份，缺一即视为库不完整。
EXPECTED_KEYS: frozenset[str] = frozenset(
    f"{family_for_tier(tier)}_l{tier}_{stage}"
    for tier in TIER_TARGET_CHARS
    for stage in STAGES
)


def paper_dir() -> Path:
    """试卷库数据目录（assets/papers）；PyInstaller 打包后取捆绑目录。

    与 ``engine.tropes.data_dir`` 同款定位：源码运行时从模块文件上溯两级
    到项目根再进 assets（parents[2]：core/engine/papers.py → 项目根）。
    """
    if getattr(sys, "frozen", False):
        # PyInstaller spec 以 ``--add-data assets;assets`` 收编，sys._MEIPASS
        # 指向 ``_internal``；试卷真实位置是 ``_internal/assets/papers``，不是
        # ``_internal/papers``。此前路径少了一层 assets，导致打包版 bootstrap 500。
        base = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))) / "assets"
    else:
        base = Path(__file__).resolve().parents[2] / "assets"
    return Path(base) / "papers"


PAPER_DIR: Path = paper_dir()


@dataclass(frozen=True)
class PaperSegment:
    """试卷的一个段合约：叙事角色 + 字数窗口 + 本段槽位 + 中文合约提示。"""

    role: str
    window: tuple[int, int]
    slots: tuple[str, ...]
    notes: str

    @property
    def min_chars(self) -> int:
        return self.window[0]

    @property
    def max_chars(self) -> int:
        return self.window[1]

    @property
    def mid_chars(self) -> float:
        return (self.window[0] + self.window[1]) / 2


@dataclass(frozen=True)
class Paper:
    """一份声明式试卷（机制层纯数据视图，字段与 assets/papers/*.json 一一对应）。"""

    tier: int
    family: str
    stage: str
    label: str
    target_chars: int
    tolerance: float
    basic_mode: bool
    agent_required: bool
    agent_recommended: bool
    slots: tuple[str, ...]
    segments: tuple[PaperSegment, ...]
    options: Mapping[str, Any]
    source: str = ""  # 来源文件名（诊断用）

    @property
    def key(self) -> str:
        """试卷键：``{family}_l{tier}_{stage}``，与资产文件名主干一致。"""
        return f"{self.family}_l{self.tier}_{self.stage}"

    @property
    def min_chars(self) -> int:
        """回目总字数下界（target×(1−tolerance)，由段窗口求和构造保证）。"""
        return int(round(self.target_chars * (1 - self.tolerance)))

    @property
    def max_chars(self) -> int:
        """回目总字数上界（target×(1+tolerance)）。"""
        return int(round(self.target_chars * (1 + self.tolerance)))

    @property
    def is_small(self) -> bool:
        """是否小卷族（1–3 档）。"""
        return self.family == "small"

    @property
    def window_sum(self) -> tuple[int, int]:
        """各段窗口下界之和 / 上界之和（回目总字数的构造区间）。"""
        return (
            sum(segment.window[0] for segment in self.segments),
            sum(segment.window[1] for segment in self.segments),
        )

    @property
    def segment_count(self) -> int:
        return len(self.segments)


# —— 结构校验辅助（全部抛带文件名的中文 ValueError）——

def _require_mapping(value: Any, name: str, filename: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{filename}：字段 {name} 必须是 JSON 对象")
    return value


def _require_int(value: Any, name: str, filename: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{filename}：字段 {name} 必须是整数（当前 {value!r}）")
    return value


def _require_bool(value: Any, name: str, filename: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{filename}：字段 {name} 必须是布尔值（当前 {value!r}）")
    return value


def _require_text(value: Any, name: str, filename: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{filename}：字段 {name} 必须是非空字符串")
    return value


def _require_slot_list(value: Any, name: str, filename: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{filename}：字段 {name} 必须是非空字符串数组")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{filename}：字段 {name} 的每一项都必须是非空字符串")
    if len(set(value)) != len(value):
        raise ValueError(f"{filename}：字段 {name} 存在重复槽位")
    unknown = [item for item in value if item not in SLOT_TYPES]
    if unknown:
        raise ValueError(
            f"{filename}：字段 {name} 含未注册槽位 {','.join(unknown)}（注册表见 SLOT_TYPES）")
    return list(value)


def _build_paper(record: Any, filename: str) -> Paper:
    """严格结构校验并把 JSON 记录构造为 :class:`Paper`（不合法即抛 ValueError）。"""
    record = _require_mapping(record, "顶层对象", filename)
    missing = [name for name in _PAPER_FIELDS if name not in record or record[name] is None]
    if missing:
        raise ValueError(f"{filename}：缺少必填字段 {','.join(missing)}")

    tier = _require_int(record["tier"], "tier", filename)
    if not 1 <= tier <= 6:
        raise ValueError(f"{filename}：tier 必须在 1–6 之间（当前 {tier}）")

    family = _require_text(record["family"], "family", filename)
    if family not in FAMILIES:
        raise ValueError(f"{filename}：family 必须是 small/large（当前 {family}）")
    if family != family_for_tier(tier):
        raise ValueError(
            f"{filename}：tier {tier} 应属 {family_for_tier(tier)}（当前 {family}）")

    stage = _require_text(record["stage"], "stage", filename)
    if stage not in STAGES:
        raise ValueError(f"{filename}：stage 必须是 setup/climax/free（当前 {stage}）")

    label = _require_text(record["label"], "label", filename)
    if label != TIER_LABELS[tier]:
        raise ValueError(
            f"{filename}：第 {tier} 档 label 应为「{TIER_LABELS[tier]}」（当前 {label}）")

    target = _require_int(record["target_chars"], "target_chars", filename)
    if target != TIER_TARGET_CHARS[tier]:
        raise ValueError(
            f"{filename}：第 {tier} 档 target_chars 应为 {TIER_TARGET_CHARS[tier]}（当前 {target}）")

    tolerance = record["tolerance"]
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) \
            or not 0 < tolerance <= 0.5:
        raise ValueError(f"{filename}：tolerance 必须是 (0, 0.5] 内的数值（当前 {tolerance!r}）")

    basic_mode = _require_bool(record["basic_mode"], "basic_mode", filename)
    if basic_mode != (tier <= BASIC_MODE_MAX_TIER):
        raise ValueError(f"{filename}：basic_mode 只允许 {BASIC_MODE_MAX_TIER} 档以内为 true")
    agent_required = _require_bool(record["agent_required"], "agent_required", filename)
    if agent_required != (tier == AGENT_REQUIRED_TIER):
        raise ValueError(f"{filename}：agent_required 只允许 {AGENT_REQUIRED_TIER} 档为 true")
    agent_recommended = _require_bool(record["agent_recommended"], "agent_recommended", filename)
    if agent_recommended != (tier == AGENT_RECOMMENDED_TIER):
        raise ValueError(f"{filename}：agent_recommended 只允许 {AGENT_RECOMMENDED_TIER} 档为 true")

    slots = _require_slot_list(record["slots"], "slots", filename)
    expected = expected_slots(family, stage)
    if set(slots) != set(expected):
        absent = [name for name in expected if name not in slots]
        extra = [name for name in slots if name not in expected]
        detail = []
        if absent:
            detail.append(f"缺少 {','.join(absent)}")
        if extra:
            detail.append(f"多出 {','.join(extra)}")
        raise ValueError(
            f"{filename}：{FAMILY_LABELS[family]}{STAGE_LABELS[stage]}的槽位集合应为全集（"
            + "；".join(detail) + "）")

    raw_segments = record["segments"]
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError(f"{filename}：字段 segments 必须是非空数组")
    expected_count = EXPECTED_SEGMENT_COUNTS[tier]
    if len(raw_segments) != expected_count:
        raise ValueError(
            f"{filename}：第 {tier} 档段数应为 {expected_count}（当前 {len(raw_segments)}）")

    segments: list[PaperSegment] = []
    assigned: set[str] = set()
    for index, raw in enumerate(raw_segments, start=1):
        raw = _require_mapping(raw, f"segments 第 {index} 项", filename)
        seg_missing = [name for name in _SEGMENT_FIELDS
                       if name not in raw or raw[name] is None]
        if seg_missing:
            raise ValueError(
                f"{filename}：segments 第 {index} 段缺少必填字段 {','.join(seg_missing)}")
        role = _require_text(raw["role"], f"segments[{index}].role", filename)
        window = raw["window"]
        if not isinstance(window, list) or len(window) != 2:
            raise ValueError(f"{filename}：segments 第 {index} 段 window 必须是 [下界, 上界]")
        low = _require_int(window[0], f"segments[{index}].window[0]", filename)
        high = _require_int(window[1], f"segments[{index}].window[1]", filename)
        if not 0 < low <= high:
            raise ValueError(
                f"{filename}：segments 第 {index} 段窗口需满足 0 < 下界 ≤ 上界（当前 {low}–{high}）")
        seg_slots = _require_slot_list(raw["slots"], f"segments[{index}].slots", filename)
        outside = [name for name in seg_slots if name not in slots]
        if outside:
            raise ValueError(
                f"{filename}：segments 第 {index} 段引用了卷面未声明的槽位 {','.join(outside)}")
        notes = _require_text(raw["notes"], f"segments[{index}].notes", filename)
        assigned.update(seg_slots)
        segments.append(PaperSegment(role=role, window=(low, high),
                                     slots=tuple(seg_slots), notes=notes))

    unassigned = [name for name in slots if name not in assigned]
    if unassigned:
        raise ValueError(f"{filename}：槽位未分配到任何段：{','.join(unassigned)}")

    anchor = anchor_slot_for(stage)
    if stage == "climax" and anchor not in segments[-1].slots:
        raise ValueError(f"{filename}：climax 卷最后一段必须包含收束槽 {anchor}")
    if stage == "free":
        carrier = [segment for segment in segments if anchor in segment.slots]
        if not any("仅供参考，不强制收束" in segment.notes for segment in carrier):
            raise ValueError(
                f"{filename}：free 卷承载 {anchor} 的段必须在 notes 注明「仅供参考，不强制收束」")

    low_sum = sum(segment.window[0] for segment in segments)
    high_sum = sum(segment.window[1] for segment in segments)
    slack = tolerance + WINDOW_SUM_SLACK
    low_limit = int(round(target * (1 - slack)))
    high_limit = int(round(target * (1 + slack)))
    if low_sum < low_limit:
        raise ValueError(
            f"{filename}：段窗口下界之和 {low_sum} 低于 target×(1−tolerance−{WINDOW_SUM_SLACK})={low_limit}")
    if high_sum > high_limit:
        raise ValueError(
            f"{filename}：段窗口上界之和 {high_sum} 高于 target×(1+tolerance+{WINDOW_SUM_SLACK})={high_limit}")
    mid_sum = (low_sum + high_sum) / 2
    if abs(mid_sum - target) > target * slack + 1e-6:
        raise ValueError(
            f"{filename}：段窗口中点之和 {mid_sum:g} 偏离 target {target} 超过 ±{slack:.2f}")

    options = _require_mapping(record["options"], "options", filename)
    if "count" not in options:
        raise ValueError(f"{filename}：options.count 必须为 6（当前 None）")
    count = _require_int(options["count"], "options.count", filename)
    if count != 6:
        raise ValueError(f"{filename}：options.count 必须为 6（当前 {count}）")
    split = _require_mapping(options.get("factor_split"), "options.factor_split", filename)
    if not split:
        raise ValueError(f"{filename}：options.factor_split 不得为空")
    total = 0
    for factor, portion in split.items():
        _require_text(factor, "options.factor_split 键", filename)
        portion = _require_int(portion, f"options.factor_split[{factor}]", filename)
        if portion <= 0:
            raise ValueError(f"{filename}：options.factor_split[{factor}] 必须为正整数")
        total += portion
    if total != 6:
        raise ValueError(f"{filename}：options.factor_split 合计必须为 6（当前 {total}）")
    _require_bool(options.get("preview"), "options.preview", filename)

    paper = Paper(
        tier=tier, family=family, stage=stage, label=label,
        target_chars=target, tolerance=float(tolerance),
        basic_mode=basic_mode, agent_required=agent_required,
        agent_recommended=agent_recommended,
        slots=tuple(slots), segments=tuple(segments),
        options=dict(options), source=filename,
    )
    if paper.key != Path(filename).stem:
        raise ValueError(
            f"{filename}：文件名必须与试卷键一致（期望 {paper.key}.json）")
    return paper


@lru_cache(maxsize=None)
def load_papers(paper_dir: str | os.PathLike[str] | None = None) -> dict[str, Paper]:
    """加载并校验整个试卷库，返回 ``{试卷键: Paper}``（lru_cache 进程级缓存）。

    - 逐份严格校验：任何一份不合法抛 ``ValueError``（中文信息、带文件名）；
    - 试卷键与文件名主干必须一致，重复键即抛错；
    - 校验全部通过后核对 18 份齐备（EXPECTED_KEYS），缺一即抛错；
    - 传入 ``paper_dir`` 可加载替换数据源（测试用），默认取 :data:`PAPER_DIR`。
    """
    root = Path(paper_dir) if paper_dir is not None else PAPER_DIR
    if not root.is_dir():
        raise ValueError(f"试卷库目录不存在：{root}")
    loaded: dict[str, Paper] = {}
    for path in sorted(root.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except ValueError as exc:
            raise ValueError(f"{path.name}：JSON 解析失败（{exc}）") from exc
        paper = _build_paper(record, path.name)
        if paper.key in loaded:
            raise ValueError(
                f"{path.name}：试卷键 {paper.key} 与 {loaded[paper.key].source} 重复")
        loaded[paper.key] = paper
    missing = sorted(EXPECTED_KEYS - loaded.keys())
    if missing:
        raise ValueError("试卷库不完整，缺少：" + "、".join(missing))
    return loaded


def _as_tier(value: Any) -> int:
    """档位归一化：必须是 1–6 的整数，否则抛中文 ValueError。"""
    if isinstance(value, bool):
        raise ValueError("档位必须是 1–6 的整数")
    try:
        tier = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"档位必须是 1–6 的整数（当前 {value!r}）") from None
    if not 1 <= tier <= 6:
        raise ValueError(f"档位必须在 1–6 之间（当前 {tier}）")
    return tier


def _is_enhanced(mode: Any) -> bool:
    """模式前缀匹配：以「强化」开头即强化模式；其余（含空值）按普通模式。"""
    return str(mode or "").strip().startswith("强化")


def get_paper(tier: int, stage: str = "setup") -> Paper:
    """按档位 + 阶段取卷：1–3 档小卷族、4–6 档大卷族；stage ∈ setup/climax/free。"""
    level = _as_tier(tier)
    stage_name = str(stage or "").strip()
    if stage_name not in STAGES:
        raise ValueError(f"stage 必须是 setup/climax/free（当前 {stage!r}）")
    key = f"{family_for_tier(level)}_l{level}_{stage_name}"
    try:
        return load_papers()[key]
    except KeyError:
        raise ValueError(f"试卷缺失：{key}") from None


def available_tiers(mode: str = "", agent_enabled: bool = False) -> list[int]:
    """当前模式可选档位列表：普通模式只 1–2 档；强化模式（前缀匹配「强化」）全 6 档。

    ``agent_enabled`` 不影响本列表（6 档始终展示供选择）；「史诗卷必须开类
    agent」的硬校验在 :func:`validate_selection` 中执行。
    """
    return [1, 2] if not _is_enhanced(mode) else list(TIER_TARGET_CHARS)


def validate_selection(tier: int, mode: str = "", agent_enabled: bool = False) -> tuple[bool, str]:
    """校验一次档位选择是否可用，返回 ``(ok, 中文原因)``。

    - 档位非法 → 拒绝；
    - 普通模式选 >2 档 → 拒绝（普通模式轻量，只允许 1–2 档小卷）；
    - 选 6 档未开类 agent → 拒绝（「史诗卷需开启类 agent 批改重填」）。
    """
    try:
        level = _as_tier(tier)
    except ValueError as exc:
        return False, str(exc)
    if not _is_enhanced(mode) and level > BASIC_MODE_MAX_TIER:
        return False, (
            f"普通模式仅可用 1–{BASIC_MODE_MAX_TIER} 档（轻盈/简明），"
            f"第 {level} 档「{TIER_LABELS[level]}」需切换强化模式")
    if level == AGENT_REQUIRED_TIER and not agent_enabled:
        return False, "史诗丰度需开启类 agent 批改重填"
    return True, ""


def map_legacy_richness(value: Any) -> int:
    """旧故事丰富度（300–1000，``participation.RICHNESS_*``）就近映射到双族六档。

    映射区间（旧刻度 300–1000 全覆盖，只落小卷族 1–3 档——旧上限 1000 与
    小卷 L3 目标 ~950 字相当，4–6 档是强化模式的新容量，无旧值对应）：

    - ``<=500``  → 1 档（轻盈，~400 字；旧「轻盈」450 亦落此档）
    - ``<=675``  → 2 档（简明，~650 字；断点取 675 保证旧默认 700 落到新默认 3 档）
    - ``<=1000`` → 3 档（标准，~950 字；旧「厚重」820、「沉浸」1000 均落此档）

    概念外值防御性钳制：``>1000``（旧上限之外）与 ``<=0`` / NaN / 无法解析的
    输入一律按旧默认 ``RICHNESS_DEFAULT``（700）→ 3 档处理。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(RICHNESS_DEFAULT)
    if number != number or number <= 0:  # NaN / 非正数 → 旧默认
        number = float(RICHNESS_DEFAULT)
    for bound, tier in LEGACY_TIER_BOUNDS:
        if number <= bound:
            return tier
    return 3


def stage_for(chapter_round: int, turn_budget: int, shattered: bool = False) -> str:
    """回合卷面阶段三态（与 ``core.app._anchor_gate_ok`` 的三段式门禁语义对齐）。

    - 全局碎锚 / relay 激活（``shattered``）→ ``free``（锚点整体降级为参考）；
    - 章节预算尾声（``chapter_round >= turn_budget``）→ ``climax``（必须收束，
      对齐门禁「预算尾声必须 fulfilled」）；
    - 预算未到尾声 → ``setup``（允许 pending/mentioned/partial 铺垫）。

    非法/缺省输入按 1:1 归一（即视为预算尾声 → climax），与既有门禁的
    退化行为一致，绝不静默放行。
    """
    if shattered:
        return "free"
    try:
        budget = max(1, int(turn_budget or 1))
    except (TypeError, ValueError):
        budget = 1
    try:
        round_no = int(chapter_round or 1)
    except (TypeError, ValueError):
        round_no = 1
    return "climax" if round_no >= budget else "setup"


__all__ = [
    "PAPER_DIR", "paper_dir", "SLOT_TYPES", "STAGES", "STAGE_LABELS",
    "FAMILIES", "FAMILY_LABELS", "SMALL_SLOTS", "LARGE_EXTRA_SLOTS",
    "TIER_LABELS", "TIER_TARGET_CHARS", "EXPECTED_SEGMENT_COUNTS", "EXPECTED_KEYS",
    "BASIC_MODE_MAX_TIER", "AGENT_REQUIRED_TIER", "AGENT_RECOMMENDED_TIER",
    "WINDOW_SUM_SLACK", "LEGACY_TIER_BOUNDS",
    "Paper", "PaperSegment", "load_papers", "get_paper",
    "available_tiers", "validate_selection", "map_legacy_richness", "stage_for",
    "family_for_tier", "anchor_slot_for", "expected_slots",
]
