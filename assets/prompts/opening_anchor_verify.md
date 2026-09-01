你是首章锚点核验器。开局第一问的锚点草稿已生成，请对照章节原文
逐字段核对九字段并修正——这是两遍法的第二遍。

【任务】首章锚点核验（草稿 + 原文 → 修正后的九字段锚点）。

【章节】第 @@CHAPTER@@ 章
【锚点草稿】（JSON）
@@DRAFT@@

【章节原文】
@@ORIGINAL@@

【输出格式】只输出一个 JSON 对象，必须包含且只能包含九字段：
chapter、title、summary、events、characters、world、foreshadowing、quotes、ripple。
- chapter 必须等于 @@CHAPTER@@；
- title/summary/world/ripple 必须为非空字符串；
- events/characters/foreshadowing/quotes 必须为非空数组（各至少 1 项，单项不超过 200 字）；
- quotes 必须是上方章节原文的逐字连续片段（不得改写、拼接或新造）。

【硬性要求】
- 草稿与原文冲突时以原文为准；摘要、事件、人物、世界观一律以原文可考内容修正。
- 不要输出任何解释、前后缀或代码围栏。
