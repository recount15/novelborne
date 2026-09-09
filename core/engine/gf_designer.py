"""金手指设计器：确定性规格拼接、质量门与本地库。

纯计算模块，不调用模型 SDK。模型润色只在 api_server 集成层发生；
本模块仅提供 ``polish_prompt`` 字符串，以及把模型原文解析回规格的
``apply_polish``（失败由调用方回退 compose 结果）。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.engine.golden_finger import GoldenFingerSpec, _difficulty_num

# 用户自定义金手指规格目录：assets 布局（与源码/PyInstaller 捆绑一致）。
# 旧路径 core/data 不存在，保存的规格对金手指库（bootstrap golden_finger_library）不可见。
# fate_engine 为模型接入层：此处仅取 BASE_DIR 常量，按 standards/01-architecture.md
# 以函数内惰性导入方式声明本例外（模块顶层 import 会形成 engine→fate_engine 的
# 启动期循环依赖风险）。
def _project_data_dir() -> Path:
    from core import fate_engine as _fe

    return Path(_fe.BASE_DIR) / "assets" / "data"


PROJECT_DATA_DIR = _project_data_dir()
USER_SPECS_DIR = PROJECT_DATA_DIR / "golden_fingers" / "user"

_LOCK = threading.Lock()
_USER_PREFIX = "gf-user-"
MAX_NAME = 18
MAX_EFFECT = 80
MAX_FIELD = 200
MAX_ID = 64
MAX_FUELS = 3

# 向导选项：构成 / 代价 / 冷却。限制三条锁定，compose 时强制写入。
COMPOSITIONS: dict[str, dict[str, str]] = {
    "信息": {
        "name": "观察回响",
        "effect": "读取刚发生事件的可验证因果链",
        "scope": "当前场景",
        "fit": "适合谨慎、探索型主角",
    },
    "存储": {
        "name": "物资暗格",
        "effect": "随身存取有限体积的既得物资",
        "scope": "随身物品",
        "fit": "适合务实、成长型主角",
    },
    "预警": {
        "name": "气机预警",
        "effect": "对指向自身的杀意与陷阱提前示警",
        "scope": "自身安危",
        "fit": "适合苟稳、行动型主角",
    },
    "映射": {
        "name": "技能映射",
        "effect": "将已掌握知识映射为当前世界可用技巧",
        "scope": "已有知识",
        "fit": "适合成长、行动型主角",
    },
    "契约": {
        "name": "契约账本",
        "effect": "把承诺、债务与交换条件结构化追踪",
        "scope": "关系与交易",
        "fit": "适合谋略、义守型主角",
    },
    "重演": {
        "name": "有限重演",
        "effect": "付出代价后重试最近一次关键选择",
        "scope": "单次行动",
        "fit": "适合规则、苟稳型主角",
    },
    "资源转化": {
        "name": "资源转化",
        "effect": "把已得世界资源转化为当前场景可验证的等价物",
        "scope": "已得资源",
        "fit": "适合务实、经营型主角",
    },
}

COSTS: dict[str, str] = {
    "精神负荷": "精神负荷累积、信息噪声干扰",
    "寿命": "折损寿元或精气作为发动代价",
    "暴露行踪": "发动时暴露关注方向与所在方位",
    "资源消耗": "消耗既得物资、灵力或等价资源",
    "因果债": "必须承担对等的因果债务与违约后果",
}

COOLDOWNS: tuple[str, ...] = ("每场景一次", "每日一次", "每章一次")

LOCKED_LIMITS: tuple[str, ...] = (
    "不得抹除既成事实",
    "不得越过世界上限",
    "必须可验证",
)
LOCKED_LIMITS_TEXT = "、".join(LOCKED_LIMITS)

# 效果必须可观察：至少命中一个可验证动词或机制词。
_OBSERVABLE_MARKERS: tuple[str, ...] = (
    "读", "查", "证", "示", "存", "映", "追", "警", "转", "显", "标",
    "记", "提", "还", "重试", "映射", "预警", "存取", "追踪", "转化",
    "线索", "因果", "账本", "路径", "提示", "取出", "放入", "见证",
    "结构化", "等价",
)

_VAGUE_EFFECTS: tuple[str, ...] = (
    "无敌", "变强", "开挂", "秒杀", "全知", "永生", "无限", "随便",
)


class DesignerError(ValueError):
    """金手指设计器操作错误；信息面向最终用户。"""


def _safe_id(text: str) -> str:
    """清洗规格 id：只保留安全字符，禁止路径分隔符（防穿越读写用户库外文件）。"""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5_\-]", "", str(text or "").strip())
    return cleaned


def _slug(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", str(text)).strip("-")
    return slug or "spec"


def spec_label(spec: GoldenFingerSpec | Mapping[str, Any]) -> str:
    """给开局下拉与 ``golden_finger.resolve`` 用的 ``name｜effect`` 标签。"""
    if isinstance(spec, GoldenFingerSpec):
        return spec.label()
    name = str(spec.get("name") or "").strip()
    effect = str(spec.get("effect") or "").strip()
    if name and effect:
        return f"{name}｜{effect}"
    return name or effect


def ensure_dirs(directory: Path | None = None) -> Path:
    target = directory or USER_SPECS_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def wizard_options() -> dict[str, Any]:
    """前端向导的确定性选项清单。"""
    return {
        "compositions": [
            {"id": key, "name": meta["name"], "effect": meta["effect"], "scope": meta["scope"]}
            for key, meta in COMPOSITIONS.items()
        ],
        "costs": [{"id": key, "text": text} for key, text in COSTS.items()],
        "cooldowns": list(COOLDOWNS),
        "locked_limits": list(LOCKED_LIMITS),
        "max_fuels": MAX_FUELS,
        "max_name": MAX_NAME,
    }


def _normalize_fuels(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[,，、;；\n]+", value) if part.strip()]
        value = parts
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return rows
    for item in value:
        if isinstance(item, Mapping):
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                continue
            rows.append({
                "id": str(item.get("id") or _slug(name)),
                "kind": str(item.get("kind") or ""),
                "name": name[:40],
                "summary": str(item.get("summary") or "")[:80],
            })
        else:
            name = str(item or "").strip()
            if not name:
                continue
            rows.append({"id": _slug(name), "kind": "", "name": name[:40], "summary": ""})
        if len(rows) >= MAX_FUELS:
            break
    return rows


def _expand_cost(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return COSTS.get(text, text)[:MAX_FIELD]


def _normalize_cooldown(raw: Any, difficulty: Any = "") -> str:
    text = str(raw or "").strip()
    level = _difficulty_num(difficulty)
    if text == "每场景一次" and level >= 7:
        return "每日一次"
    return text[:MAX_FIELD]


def _effect_from(composition: str, fuels: list[dict[str, str]], template: Mapping[str, str]) -> str:
    base = str(template.get("effect") or "").strip()
    if not fuels:
        return base
    names = "、".join(item["name"] for item in fuels)
    if composition == "资源转化":
        return f"把已得资源「{names}」转化为当前场景可验证的等价物"
    if composition == "存储":
        return f"随身存取有限体积的既得物资（燃料：{names}）"
    if composition == "映射":
        return f"将「{names}」映射为当前世界可用且可验证的技巧"
    if composition == "信息":
        return f"围绕「{names}」读取刚发生事件的可验证因果链"
    if composition == "预警":
        return f"对指向自身且与「{names}」相关的杀意与陷阱提前示警"
    if composition == "契约":
        return f"把涉及「{names}」的承诺、债务与交换条件结构化追踪"
    if composition == "重演":
        return f"付出代价后重试最近一次与「{names}」相关的关键选择"
    return f"{base}（燃料：{names}）"


def compose_spec(draft: Mapping[str, Any] | None) -> GoldenFingerSpec:
    """把向导草稿拼接为可被 ``GoldenFingerSpec`` 消费的规格。

    三条硬限制始终强制写入。代价/冷却缺省时仍产出规格，由
    ``quality_gate`` 拒绝。高难度（D7+）把「每场景一次」提升为「每日一次」。
    """
    data = dict(draft or {})
    composition = str(data.get("composition") or data.get("kind") or "").strip()
    if composition not in COMPOSITIONS:
        raise DesignerError(
            f"构成必须是：{' / '.join(COMPOSITIONS)}（收到：{composition or '空'}）")
    template = COMPOSITIONS[composition]
    fuels = _normalize_fuels(data.get("fuels") or data.get("resources") or data.get("fuel"))
    if composition == "资源转化" and not fuels:
        # 允许手填燃料：若完全空，质量门仍可通过构成本身的可观察效果；
        # 这里不抛错，只不写入燃料短语。
        pass

    difficulty = data.get("difficulty") or data.get("difficulty_label") or ""
    cost = _expand_cost(data.get("cost"))
    cooldown = _normalize_cooldown(data.get("cooldown"), difficulty)

    name = str(data.get("name") or "").strip()
    if not name:
        name = f"{composition}·{fuels[0]['name']}" if fuels else str(template["name"])
    name = name[:MAX_NAME]

    effect = str(data.get("effect") or "").strip() or _effect_from(composition, fuels, template)
    effect = effect[:MAX_EFFECT]
    scope = str(data.get("scope") or "").strip() or str(template["scope"])
    scope = scope[:MAX_FIELD]
    fit = str(data.get("fit") or "").strip() or str(template["fit"])
    if fuels and "燃料" not in fit:
        fit = f"{fit}；燃料：{'、'.join(item['name'] for item in fuels)}"
    fit = fit[:MAX_FIELD]

    raw_id = str(data.get("id") or "").strip()
    spec_id = raw_id if raw_id.startswith(_USER_PREFIX) else f"{_USER_PREFIX}{_slug(name)}"
    spec_id = spec_id[:MAX_ID]

    source = str(data.get("source") or "designed").strip() or "designed"
    level = _difficulty_num(difficulty)
    if level:
        source = f"{source}:D{level}" if ":D" not in source else source

    return GoldenFingerSpec(
        id=spec_id,
        name=name,
        effect=effect,
        scope=scope,
        cost=cost,
        cooldown=cooldown,
        limits=LOCKED_LIMITS_TEXT,
        fit=fit,
        source=source,
    )


def _as_mapping(spec: GoldenFingerSpec | Mapping[str, Any] | None) -> dict[str, Any]:
    if spec is None:
        return {}
    if isinstance(spec, GoldenFingerSpec):
        return spec.to_dict()
    return dict(spec)


def _difficulty_from_spec(spec: Mapping[str, Any], explicit: Any = "") -> int:
    if explicit:
        return _difficulty_num(explicit)
    source = str(spec.get("source") or "")
    match = re.search(r"D([1-9])", source)
    if match:
        return int(match.group(1))
    fit = str(spec.get("fit") or "")
    match = re.search(r"D([1-9])", fit)
    return int(match.group(1)) if match else 0


def quality_gate(spec: GoldenFingerSpec | Mapping[str, Any] | None,
                 difficulty: Any = "") -> dict[str, Any]:
    """缺代价/冷却/限制则拒；name ≤ 18 字；effect 必须可观察；D7+ 禁「每场景一次」。"""
    data = _as_mapping(spec)
    issues: list[str] = []
    name = str(data.get("name") or "").strip()
    effect = str(data.get("effect") or "").strip()
    cost = str(data.get("cost") or "").strip()
    cooldown = str(data.get("cooldown") or "").strip()
    limits = str(data.get("limits") or "").strip()
    scope = str(data.get("scope") or "").strip()

    if not name:
        issues.append("缺少名称")
    elif len(name) > MAX_NAME:
        issues.append(f"名称超过 {MAX_NAME} 字")

    if not cost or cost in {"无", "无代价"}:
        issues.append("缺少代价")
    if not cooldown or cooldown in {"无", "无冷却"}:
        issues.append("缺少冷却")
    if not limits:
        issues.append("缺少限制")
    else:
        missing = [item for item in LOCKED_LIMITS if item not in limits]
        if missing:
            issues.append("限制必须包含：" + "、".join(missing))

    if not effect:
        issues.append("缺少效果")
    else:
        if any(word in effect for word in _VAGUE_EFFECTS):
            issues.append("效果不可观察：禁止无敌/变强等空泛描述")
        elif not any(marker in effect for marker in _OBSERVABLE_MARKERS):
            issues.append("效果必须可观察（含读取、示警、存取、映射等可验证动作）")

    if not scope:
        issues.append("缺少作用范围")

    level = _difficulty_from_spec(data, difficulty)
    if level >= 7 and cooldown == "每场景一次":
        issues.append("高难度（D7+）冷却不得是「每场景一次」")

    return {"ok": not issues, "issues": issues}


def polish_prompt(spec: GoldenFingerSpec | Mapping[str, Any], world: str = "") -> str:
    """只返回提示词字符串，不调用模型。"""
    data = _as_mapping(spec)
    world_text = str(world or "").strip() or "（未提供世界观摘录）"
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    return (
        "你是金手指规格润色助手。在不改变机制边界的前提下，让名称与效果更贴合世界、更可观察。\n"
        "硬性约束：\n"
        f"1. 代价必须保留原意，不得删成「无」。当前代价：{data.get('cost') or '（空）'}\n"
        f"2. 冷却必须保留同一档或更长，禁止改成「每场景一次」以外的更短冷却。"
        f"当前冷却：{data.get('cooldown') or '（空）'}\n"
        f"3. 限制必须完整保留这三句：{LOCKED_LIMITS_TEXT}\n"
        "4. 名称不超过 18 字；效果必须是可验证的观察/存储/预警/映射/契约/重演/转化。\n"
        "5. 只输出一个 JSON 对象，不要 Markdown，不要解释。字段："
        "id,name,effect,scope,cost,cooldown,limits,fit,source\n"
        f"世界观摘录：{world_text[:800]}\n"
        f"当前规格：\n{payload}\n"
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidate = fence.group(1) if fence else raw
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def apply_polish(original: GoldenFingerSpec | Mapping[str, Any],
                 model_text: str) -> GoldenFingerSpec:
    """把模型润色原文合入原规格；解析失败或越界则仍返回原规格。"""
    base = GoldenFingerSpec(**{key: value for key, value in _as_mapping(original).items()
                               if key in GoldenFingerSpec.__dataclass_fields__})
    parsed = _extract_json_object(model_text)
    if not parsed:
        return base
    name = str(parsed.get("name") or base.name).strip()[:MAX_NAME] or base.name
    effect = str(parsed.get("effect") or base.effect).strip()[:MAX_EFFECT] or base.effect
    scope = str(parsed.get("scope") or base.scope).strip()[:MAX_FIELD] or base.scope
    fit = str(parsed.get("fit") or base.fit).strip()[:MAX_FIELD] or base.fit
    # 代价/冷却允许同义润色，但不能被删空；限制强制三句。
    cost = str(parsed.get("cost") or base.cost).strip()[:MAX_FIELD] or base.cost
    cooldown = str(parsed.get("cooldown") or base.cooldown).strip()[:MAX_FIELD] or base.cooldown
    if cooldown == "每场景一次" and "每场景一次" not in str(base.cooldown):
        cooldown = base.cooldown
    polished = GoldenFingerSpec(
        id=base.id,
        name=name,
        effect=effect,
        scope=scope,
        cost=cost,
        cooldown=cooldown,
        limits=LOCKED_LIMITS_TEXT,
        fit=fit,
        source=str(parsed.get("source") or base.source) or base.source,
    )
    gate = quality_gate(polished, difficulty=base.source)
    return polished if gate["ok"] else base


def as_spec(raw: Mapping[str, Any] | GoldenFingerSpec, fallback_id: str = "") -> GoldenFingerSpec:
    """把映射或既有规格收成 GoldenFingerSpec；限制始终补齐三条。"""
    if isinstance(raw, GoldenFingerSpec):
        if raw.limits == LOCKED_LIMITS_TEXT:
            return raw
        return GoldenFingerSpec(
            id=raw.id, name=raw.name, effect=raw.effect, scope=raw.scope,
            cost=raw.cost, cooldown=raw.cooldown, limits=LOCKED_LIMITS_TEXT,
            fit=raw.fit, source=raw.source,
        )
    payload = raw.get("spec") if isinstance(raw.get("spec"), Mapping) else raw
    data = dict(payload)
    spec_id = _safe_id(str(data.get("id") or fallback_id or ""))
    if not spec_id:
        spec_id = f"{_USER_PREFIX}{_slug(str(data.get('name') or 'spec'))}"
    return GoldenFingerSpec(
        id=spec_id[:MAX_ID],
        name=str(data.get("name") or "")[:MAX_NAME],
        effect=str(data.get("effect") or "")[:MAX_EFFECT],
        scope=str(data.get("scope") or "")[:MAX_FIELD],
        cost=str(data.get("cost") or "")[:MAX_FIELD],
        cooldown=str(data.get("cooldown") or "")[:MAX_FIELD],
        limits=LOCKED_LIMITS_TEXT,
        fit=str(data.get("fit") or "")[:MAX_FIELD],
        source=str(data.get("source") or "designed")[:MAX_FIELD],
    )


def _spec_from_record(raw: Mapping[str, Any], fallback_id: str = "") -> dict[str, Any]:
    spec = as_spec(raw, fallback_id=fallback_id)
    out = spec.to_dict()
    out["label"] = spec.label()
    return out


def save_spec(spec: GoldenFingerSpec | Mapping[str, Any],
              directory: Path | None = None) -> dict[str, Any]:
    """把规格写入 ``data/golden_fingers/user/``；返回含 path 的记录。"""
    data = _as_mapping(spec)
    if not data.get("name") or not data.get("effect"):
        raise DesignerError("规格缺少 name 或 effect，无法保存")
    record = _spec_from_record(data)
    gate = quality_gate(record)
    if not gate["ok"]:
        raise DesignerError("规格未通过质量门：" + "；".join(gate["issues"]))
    with _LOCK:
        target = ensure_dirs(directory)
        spec_id = _safe_id(str(record["id"]))
        if not spec_id:
            raise DesignerError("规格 id 非法，无法保存")
        path = target / f"{spec_id}.json"
        payload = {
            "schema_version": "1.0",
            "asset": "golden_finger_spec",
            "scope": "user_designed",
            "spec": {key: record[key] for key in
                     ("id", "name", "effect", "scope", "cost", "cooldown", "limits", "fit", "source")},
        }
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise DesignerError(f"金手指规格写入失败：{exc}") from exc
    record["path"] = str(path.name)
    return record


def list_specs(directory: Path | None = None) -> list[dict[str, Any]]:
    """列出用户库规格摘要：至少含 id 与 label。"""
    target = directory or USER_SPECS_DIR
    if not target.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(target.glob("*.json"), key=lambda item: item.stem):
        try:
            with path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, Mapping):
                continue
            record = _spec_from_record(raw, fallback_id=path.stem)
            rows.append({
                "id": record["id"],
                "label": record["label"],
                "name": record["name"],
                "effect": record["effect"],
                "cost": record["cost"],
                "cooldown": record["cooldown"],
                "source": record["source"],
            })
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return rows


def load_spec(spec_id: str, directory: Path | None = None) -> dict[str, Any]:
    """读取单条规格；找不到则抛 DesignerError。"""
    clean_id = _safe_id(str(spec_id or ""))
    if not clean_id:
        raise DesignerError("缺少规格 id")
    target = directory or USER_SPECS_DIR
    path = target / f"{clean_id}.json"
    if not path.is_file():
        raise DesignerError(f"找不到金手指规格：{spec_id}")
    try:
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, Mapping):
            raise DesignerError(f"金手指规格损坏：{spec_id}")
        return _spec_from_record(raw, fallback_id=spec_id)
    except (OSError, json.JSONDecodeError) as exc:
        raise DesignerError(f"金手指规格读取失败：{exc}") from exc


__all__ = [
    "COMPOSITIONS", "COSTS", "COOLDOWNS", "LOCKED_LIMITS", "LOCKED_LIMITS_TEXT",
    "MAX_NAME", "USER_SPECS_DIR", "DesignerError",
    "compose_spec", "quality_gate", "polish_prompt", "apply_polish",
    "save_spec", "list_specs", "load_spec", "spec_label", "wizard_options",
    "ensure_dirs", "as_spec",
]
