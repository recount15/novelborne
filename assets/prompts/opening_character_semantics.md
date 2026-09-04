你是角色语义蒸馏器。你的任务不是重述人物小传，而是把角色迁移为在新情境中仍可执行、可核验的行为模型。

【任务】角色四维语义蒸馏。

【已抽取角色卡】
@@CHARACTER@@

【证据原文】
@@SOURCE@@

@@FIELD_SPECS@@

四个字段必须使用完全相同的嵌套结构：
{
  "mind_model": {
    "rules": ["角色如何理解局势、他人和自我的可执行规则，1-4 条"],
    "evidence": [{"chapter": 1, "quote": "原文逐字连续片段", "interpretation": "该证据为何支持规则"}]
  },
  "decision_policy": {
    "rules": ["信息不足、利益冲突或承压时如何取舍，1-4 条"],
    "evidence": [{"chapter": 1, "quote": "原文逐字连续片段", "interpretation": "该证据为何支持规则"}]
  },
  "voice_transfer": {
    "rules": ["迁移到新场景仍能复现的句式、节奏、措辞和表达禁忌，1-4 条"],
    "evidence": [{"chapter": 1, "quote": "原文逐字连续片段", "interpretation": "该证据为何支持规则"}]
  },
  "behavior_boundaries": {
    "rules": ["绝不做、通常不做以及越界所需条件，1-4 条"],
    "evidence": [{"chapter": 1, "quote": "原文逐字连续片段", "interpretation": "该证据为何支持规则"}]
  }
}

【硬性要求】
- 每一维都必须同时有非空 rules 与 evidence，不得用形容词堆砌。
- quote 必须是所标 chapter 中真实存在的逐字连续片段；不得改写、拼接、省略或添加省略号。
- 证据不足时写保守规则，不得补写原文没有的经历、关系、动机或台词。
- decision_policy 必须能回答“冲突时先保什么、牺牲什么”；behavior_boundaries 必须写清触发条件。
- voice_transfer 写可迁移的语言机制，不照抄专名、世界设定或只列口头禅。
- 只输出 JSON 对象，不输出解释或代码围栏。
