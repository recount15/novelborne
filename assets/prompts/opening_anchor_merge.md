你是长章锚点合并器。一章过长时会被切成多块并行蒸馏，现在把各块的
块级蒸馏结果融合为该章完整的九字段锚点。

【任务】长章锚点合并。

【章节】第 @@CHAPTER@@ 章
【分块蒸馏结果】（JSON 数组，每项对应一个文本块，按原文顺序排列）
@@BLOCKS@@

【输出格式】只输出一个 JSON 对象，必须包含且只能包含九字段：
chapter、title、summary、events、characters、world、foreshadowing、quotes、ripple。
- chapter 必须等于 @@CHAPTER@@；
- title/summary/world/ripple 必须为非空字符串；
- events/characters/foreshadowing/quotes 必须为非空数组（各至少 1 项，单项不超过 200 字）；
- quotes 必须逐字摘自「分块蒸馏结果」中已有的 quotes 条目（不得改写、拼接或新造）。

【硬性要求】
- 融合时去重、按时间顺序合并事件；不新增分块结果中没有的事实。
- 不要输出任何解释、前后缀或代码围栏。
