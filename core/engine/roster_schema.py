"""角色名册配置 schema。

该模块只处理名册配置的规范化、校验和序列化，不依赖 UI 或模型。
角色槽位没有数量上限；调用方可以在 ``slots`` 中传入任意数量角色。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

PARTICIPATION_MIN = 1
PARTICIPATION_MAX = 9
HEROINE_MODES = ("单女主", "多女主")
ROLE_TYPES = ("伙伴", "女主", "宿敌")
_ROLE_ALIASES = {
    "companion": "伙伴", "partner": "伙伴", "伙伴": "伙伴",
    "heroine": "女主", "主女": "女主", "女主": "女主",
    "nemesis": "宿敌", "enemy": "宿敌", "宿敌": "宿敌",
}
_MODE_ALIASES = {
    "single": "单女主", "single_heroine": "单女主", "单女主": "单女主",
    "multi": "多女主", "multiple": "多女主", "multi_heroine": "多女主", "多女主": "多女主",
}
_SKILL_TYPES = {"preset", "custom", "upload"}
_SKILL_ALIASES = {
    "预设": "preset", "preset": "preset", "default": "preset",
    "自定义": "custom", "custom": "custom", "manual": "custom",
    "上传": "upload", "upload": "upload", "file": "upload", "md": "upload",
}
_CARD_FIELDS = (
    "goal", "fear", "abilities", "relationship_vector", "knowledge_scope",
    "speech_style", "unacceptable_behaviors",
)


class RosterValidationError(ValueError):
    """名册配置不符合 schema 时抛出。"""


def _text(value: Any, default: str = "") -> str:
    return default if value is None else str(value).strip()


def normalize_role_type(value: Any) -> str:
    role = _text(value)
    try:
        return _ROLE_ALIASES[role.lower()]
    except KeyError:
        raise RosterValidationError(f"未知角色类型: {role or '<空>'}") from None


def normalize_heroine_mode(value: Any = "单女主") -> str:
    mode = _text(value, "单女主")
    try:
        return _MODE_ALIASES[mode.lower()]
    except KeyError:
        raise RosterValidationError(f"女主模式必须是单女主或多女主: {mode}") from None


def normalize_participation(value: Any = 1) -> int:
    """把参与度规范到 1--9；空值使用最低参与度。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = PARTICIPATION_MIN
    return max(PARTICIPATION_MIN, min(PARTICIPATION_MAX, number))


def _skill_payload(value: Any) -> tuple[str, Any]:
    if isinstance(value, Mapping):
        source = value.get("source", value.get("type", value.get("kind", value.get("来源", "preset"))))
        source = _SKILL_ALIASES.get(_text(source).lower(), _text(source).lower())
        if source not in _SKILL_TYPES:
            raise RosterValidationError(f"技能来源必须是 preset/custom/upload: {source or '<空>'}")
        if source == "upload":
            payload = value.get("value", value.get("content", value.get("path", value.get("file", ""))))
            if isinstance(payload, Mapping):
                payload = dict(payload)
        else:
            payload = value.get("value", value.get("skill", value.get("name", "")))
        return source, payload
    return "preset", _text(value)


def normalize_skill_source(value: Any = "", custom: Any = "", upload: Any = None) -> dict[str, Any]:
    """统一预设、自定义、上传三种 skill 来源。

    ``value`` 可为纯字符串或带 ``source/type/value`` 的映射；显式的
    ``upload``、``custom`` 参数优先于 value 中的同名内容。
    """
    if upload is not None and upload != "":
        source, payload = "upload", upload
    elif _text(custom):
        source, payload = "custom", _text(custom)
    else:
        source, payload = _skill_payload(value)
    result: dict[str, Any] = {"source": source, "value": payload}
    return result


def _stable_slot_id(role: str, name: str, index: int) -> str:
    """为无 ID 的槽位生成确定性 ID；重复空名仍用序号保证唯一。"""
    label = _text(name)
    if label:
        digest = hashlib.sha1(f"{role}\x00{label}".encode("utf-8")).hexdigest()[:12]
        return f"slot-{digest}"
    return f"slot-{index + 1}"


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _card_from_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    given = data.get("character_card", data.get("card", {}))
    card = dict(given) if isinstance(given, Mapping) else {}
    for key in _CARD_FIELDS:
        if key in data and key not in card:
            card[key] = data[key]
    return {key: _json_value(card.get(key, "")) for key in _CARD_FIELDS}


@dataclass
class CharacterCard:
    goal: Any = ""
    fear: Any = ""
    abilities: Any = field(default_factory=list)
    relationship_vector: Any = field(default_factory=dict)
    knowledge_scope: Any = ""
    speech_style: Any = ""
    unacceptable_behaviors: Any = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "CharacterCard":
        data = dict(value or {})
        return cls(**{key: data.get(key, getattr(cls, key, "")) for key in _CARD_FIELDS})

    def to_dict(self) -> dict[str, Any]:
        return {key: _json_value(getattr(self, key)) for key in _CARD_FIELDS}


@dataclass
class RosterSlot:
    slot_id: str
    order: int
    role_type: str
    name: str = ""
    skill: str = ""
    skill_source: dict[str, Any] = field(default_factory=lambda: normalize_skill_source())
    participation: int = PARTICIPATION_MIN
    background: str = ""
    character_card: CharacterCard = field(default_factory=CharacterCard)
    extra: dict[str, Any] = field(default_factory=dict)
    skill_upload_id: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], index: int = 0) -> "RosterSlot":
        data = dict(value)
        role = normalize_role_type(data.get("role_type", data.get("role", "伙伴")))
        name = _text(data.get("name", ""))
        raw_id = _text(data.get("slot_id", data.get("id", "")))
        slot_id = raw_id or _stable_slot_id(role, name, index)
        try:
            order = int(data.get("order", data.get("slot_order", index)))
        except (TypeError, ValueError):
            order = index
        source = normalize_skill_source(
            data.get("skill_source", data.get("skill", "")),
            custom=data.get("skill_custom", ""),
            upload=data.get("skill_upload"),
        )
        skill = _text(data.get("skill", "")) if not isinstance(data.get("skill"), Mapping) else _text(source.get("value", ""))
        if source["source"] == "custom":
            skill = _text(source.get("value", ""))
        elif source["source"] == "upload":
            # 上传 skill 的路径是来源元数据；界面单行标签优先使用显式名称。
            skill = _text(data.get("skill_label", "")) or (
                _text(source.get("value", "")) if not isinstance(source.get("value"), Mapping) else "上传技能")
        known = {"slot_id", "id", "order", "slot_order", "role_type", "role", "name", "skill", "skill_source", "skill_custom", "skill_upload", "skill_upload_id", "skill_label", "participation", "background", "character_card", "card", *_CARD_FIELDS}
        extra = {key: _json_value(item) for key, item in data.items() if key not in known}
        slot = cls(slot_id, order, role, name, skill, source, normalize_participation(data.get("participation", 1)), _text(data.get("background", "")), CharacterCard.from_mapping(_card_from_mapping(data)), extra)
        slot.skill_upload_id = _text(data.get("skill_upload_id", ""))
        return slot

    def to_dict(self) -> dict[str, Any]:
        data = {
            "slot_id": self.slot_id,
            "order": self.order,
            "role_type": self.role_type,
            "name": self.name,
            "skill": self.skill,
            "skill_source": _json_value(self.skill_source),
            "participation": self.participation,
            "background": self.background,
            "character_card": self.character_card.to_dict(),
        }
        if self.skill_upload_id:
            data["skill_upload_id"] = self.skill_upload_id
        data.update(_json_value(self.extra))
        return data

    @property
    def role(self) -> str:
        return self.role_type

    @property
    def card(self) -> dict[str, Any]:
        return self.character_card.to_dict()


@dataclass
class RosterConfig:
    slots: list[RosterSlot] = field(default_factory=list)
    heroine_mode: str = "单女主"

    def __post_init__(self) -> None:
        self.heroine_mode = normalize_heroine_mode(self.heroine_mode)
        self.slots = sorted(list(self.slots), key=lambda item: item.order)
        self.validate()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> "RosterConfig":
        if isinstance(value, Mapping):
            mode = value.get("heroine_mode", value.get("heroineMode", value.get("女主模式", "单女主")))
            rows = value.get("slots", value.get("roster", value.get("characters", [])))
        else:
            mode, rows = "单女主", value or []
        if isinstance(rows, Mapping):
            rows = list(rows.values())
        slots = [row if isinstance(row, RosterSlot) else RosterSlot.from_mapping(row, i) for i, row in enumerate(rows or ())]
        return cls(slots=slots, heroine_mode=mode)

    def validate(self) -> bool:
        ids: set[str] = set()
        heroines = 0
        for slot in self.slots:
            if not slot.slot_id:
                raise RosterValidationError("slot_id 不能为空")
            if slot.slot_id in ids:
                raise RosterValidationError(f"slot_id 重复: {slot.slot_id}")
            ids.add(slot.slot_id)
            if slot.role_type not in ROLE_TYPES:
                raise RosterValidationError(f"未知角色类型: {slot.role_type}")
            if not PARTICIPATION_MIN <= slot.participation <= PARTICIPATION_MAX:
                raise RosterValidationError(f"参与度必须在 1-9: {slot.slot_id}")
            if slot.role_type == "女主":
                heroines += 1
        if self.heroine_mode == "单女主" and heroines > 1:
            raise RosterValidationError("单女主模式最多配置 1 位女主")
        return True

    def ordered_slots(self) -> list[RosterSlot]:
        return list(self.slots)

    def to_dict(self) -> dict[str, Any]:
        return {"heroine_mode": self.heroine_mode, "slots": [slot.to_dict() for slot in self.ordered_slots()]}

    def to_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, value: str | bytes) -> "RosterConfig":
        return cls.from_mapping(json.loads(value))


def normalize_slots(slots: Iterable[Mapping[str, Any] | RosterSlot] | None, heroine_mode: str = "单女主") -> list[dict[str, Any]]:
    """规范化并按槽位顺序返回角色字典列表。"""
    return RosterConfig.from_mapping({"heroine_mode": heroine_mode, "slots": list(slots or ())}).to_dict()["slots"]


def normalize_roster(value: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None, heroine_mode: str = "单女主") -> dict[str, Any]:
    """规范化名册并返回可 JSON 序列化字典。"""
    if isinstance(value, Mapping) and any(key in value for key in ("slots", "roster", "characters", "heroine_mode", "heroineMode", "女主模式")):
        return RosterConfig.from_mapping(value).to_dict()
    return RosterConfig.from_mapping({"heroine_mode": heroine_mode, "slots": value or ()}).to_dict()


def validate_roster(value: Mapping[str, Any] | Sequence[Mapping[str, Any]] | RosterConfig, heroine_mode: str = "单女主") -> bool:
    config = value if isinstance(value, RosterConfig) else RosterConfig.from_mapping(value if isinstance(value, Mapping) else {"heroine_mode": heroine_mode, "slots": value})
    return config.validate()


def roster_to_dict(value: RosterConfig | Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return value.to_dict() if isinstance(value, RosterConfig) else normalize_roster(value)


def roster_from_dict(value: Mapping[str, Any]) -> RosterConfig:
    return RosterConfig.from_mapping(value)


# 兼容可能的调用方命名。
RoleSlot = RosterSlot
RoleCard = CharacterCard
RosterSchema = RosterConfig
normalize_skill = normalize_skill_source
check_roster = validate_roster

__all__ = [
    "PARTICIPATION_MIN", "PARTICIPATION_MAX", "HEROINE_MODES", "ROLE_TYPES",
    "RosterValidationError", "CharacterCard", "RoleCard", "RosterSlot", "RoleSlot",
    "RosterConfig", "RosterSchema", "normalize_role_type", "normalize_heroine_mode",
    "normalize_participation", "normalize_skill_source", "normalize_skill",
    "normalize_slots", "normalize_roster", "validate_roster", "check_roster",
    "roster_to_dict", "roster_from_dict",
]
