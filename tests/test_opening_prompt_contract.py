# -*- coding: utf-8 -*-
"""C01 开局提示词 ⇄ 校验门禁契约回归（产品缺陷）。

契约：基础模式 API 开局是一次流式调用，app 门禁（core/app.py 开局段）要求
首回复即含完整 A–F 六选项后才提交正式状态。因此开局核对提示词必须要求
「设定清单 + A–F 选项并存」，不得再指示模型「不要自己列选项」——
旧提示词与门禁互相矛盾，合规回复必然被拒（开局永远失败）。
"""
from __future__ import annotations

from pathlib import Path

from core.api.save_contract import valid_options
from core.engine.options import parse_options

ROOT = Path(__file__).resolve().parents[1]
PROMPT = (ROOT / "assets" / "prompts" / "opening_check.md").read_text(encoding="utf-8")

COMPLIANT_REPLY = """【开局设定核对】
- 作品：《示例之作》；模式：基础；难度：标准。
- 金手指：示例神瞳（数值折算 E=WS×GF(D)：6）。
- 穿越角色：张三（原著主角）。
- 穿越时间点：第一章开篇。
以上设定是否确认？确认后开始第一幕。也可以直接从下面的行动中选一个立即动身：
A. 留在原地观察四周。
B. 翻看随身物品。
C. 向路人打听消息。
D. 去往市中心。
E. 回家整理思路。
F. 尝试激活金手指。
（也可以自由输入其它行动。）"""

CHECKLIST_ONLY_REPLY = """【开局设定核对】
- 作品：《示例之作》；模式：基础；难度：标准。
以上设定是否确认？确认后开始第一幕。"""


def test_prompt_requires_inline_options_instead_of_forbidding_them():
    assert "不要自己列选项" not in PROMPT, (
        "开局核对提示词禁止模型列选项，与开局门禁（首回复必须含完整 A–F）矛盾")
    assert ("A–F" in PROMPT) or ("A-F" in PROMPT), "提示词必须明确要求 A–F 六个选项"
    assert "是否确认" in PROMPT, "保留核对确认问句（引擎确认阶段标记依赖它）"


def test_prompt_compliant_reply_passes_opening_gate():
    options = parse_options(COMPLIANT_REPLY)
    assert len(options) == 6
    assert valid_options(options), "按更新后提示词作答的合规回复必须通过开局门禁"


def test_checklist_only_reply_still_rejected_by_gate():
    assert len(parse_options(CHECKLIST_ONLY_REPLY)) < 6
    assert not valid_options(parse_options(CHECKLIST_ONLY_REPLY)), (
        "只给清单不给选项仍不能提交正式状态（门禁语义保持不变）")
