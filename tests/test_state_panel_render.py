# -*- coding: utf-8 -*-
"""C02 状态面板中文展示回归。

缺陷：_text() 把 dict/list 直接 json.dumps，面板出现 `货币 []`、
`金手指 {"name": "无（凡人开局）", "status": "inactive", ...}` 之类的
机器序列化泄漏；空列表还会挡住『无/未记录』兜底。
要求：面板全中文业务表述，枚举值映射（inactive→未激活），
空容器回落到既有的中文兜底词。
"""
from __future__ import annotations

from core.memory.schema import blank_state
from core.memory.state_store import _text, render_panel


def _panel(state=None) -> str:
    return render_panel(state or blank_state())


def test_blank_panel_has_no_json_or_brackets():
    panel = _panel()
    assert "{" not in panel and "}" not in panel, f"面板泄漏 JSON 对象：{panel}"
    assert "[" not in panel and "]" not in panel, f"面板泄漏 JSON 数组：{panel}"
    assert '"' not in panel, "面板含引号包裹的机器字符串"


def test_blank_panel_empty_containers_fall_back_to_chinese():
    panel = _panel()
    assert "货币 无；装备 无；物品 无" in panel
    assert "技能 无" in panel
    assert "关系/阵营" in panel


def test_golden_finger_dict_rendered_in_business_chinese():
    state = blank_state()
    state["abilities"]["golden_finger"] = {
        "name": "透视神瞳", "status": "inactive", "cooldown": 3, "costs": ["眼球刺痛"]}
    panel = _panel(state)
    assert "透视神瞳" in panel
    assert "未激活" in panel
    assert "inactive" not in panel, "枚举原值 inactive 不应出现在面板"
    assert "{" not in panel and '"' not in panel


def test_list_and_dict_helpers():
    assert _text([]) == ""                # 空容器交还兜底词责任
    assert _text({}) == ""
    assert _text(None) == ""
    assert _text(["课本", "手机"]) == "课本、手机"
    assert _text({"name": "透视神瞳", "status": "inactive"}) == "名称：透视神瞳；状态：未激活"
    assert _text([{"name": "张三"}, {"name": "李四"}]) == "张三、李四"
    assert "未激活" in _text({"status": "inactive"})


def test_distinct_fallback_words_preserved():
    panel = _panel()
    assert "修为 未记录" in panel       # cultivation 空串 → 未记录
    assert "伤势 无" in panel           # injuries 空列表 → 无
    assert "场景 未命名" in panel
