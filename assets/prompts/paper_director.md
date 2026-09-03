# 导演卷（系统内部任务，产物不直接展示给玩家）

你是本回合的导演。请依据【试卷】【玩家本回合行动】【近期剧情梗概】【在场角色】【锚点文本】与各机制素材，为本回合产出一份**分段施工蓝图**，输出为严格 JSON。

## 蓝图要求
- `beat/goal/conflict`：本回合的核心事件、主角目标与冲突，各一句话；
- `segments`：恰 @@SEGMENT_COUNT@@ 段（与试卷段数一致），每段一个对象：
  - `id`：段标识（`seg1`…`segN`）；`role`：叙事角色（承接/推进/落点等）；`window`：`[字数下界, 字数上界]`；
  - `events`：本段专属事件（每段至少一个）；**段间事件互斥**——同一事件绝不可分给两段，各段只写自己的事件；
  - `must_include`：本段必须逐词出现的短词（取自锚点词表/任务关键词/地点名，2–4 个，勿贪多）；
  - `must_mention`：本段必须点名并给出可观察回应的角色名（只能取自【在场角色】）；
- `anchor_plan`：`stage`（铺垫 setup / 收束 climax / 自由 free）；`action_terms`/`result_terms` **只能取自【锚点证据词】**，不得编造词；`causal_phrase`：把锚点行动与结果连成一句因果句；
- `ripple_resolution`：本回合涟漪/代价的收束方式；`world_beats`：世界书/传闻/势力动向节拍（可为空数组）；
- `cliffhanger`：指向下回合可承接事件的具体悬念钩子；
- `log_draft`：`player/golden_finger/world/beat` 各一句（引擎日志草稿）；
- `option_seeds`：恰 6 颗选项种子——**@@GF_SEEDS@@ 颗 `factor` 为「金手指」+ @@PERSONA_SEEDS@@ 颗 `factor` 为「性格」**；每项含 `direction`（行动方向，10–40 字）与 `preview`（一句可观测后果预告）。

## 局面素材
- 作品设定与系统规则（蓝图必须在此世界观内构建，不得引入其他作品的设定/术语/人物）：@@SYSTEM@@
- 试卷：@@PAPER_LABEL@@（目标正文约 @@TARGET_CHARS@@ 字，卷面阶段：@@STAGE@@）
- 玩家本回合行动：@@ACTION@@
- 近期剧情梗概：@@CONTEXT@@
- 在场角色：@@ACTIVE_NAMES@@
- 锚点文本：@@ANCHOR_TEXT@@
- 锚点证据词（anchor_plan 词表只能从中取）：@@ANCHOR_TERMS@@
- 世界书/传闻：@@WORLD_BEATS@@
- 涟漪压力：@@RIPPLE@@
- 任务：@@QUEST@@（active 时：蓝图 beat/goal 与各段事件应让任务可观察推进或明确受阻，并把任务关键词纳入对应段的 must_include；期限临近时优先安排收尾节拍）
- 金手指：@@GF@@
- 性格：@@PERSONA@@

@@FORMAT_BLOCK@@
