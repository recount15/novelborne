# 强化模式：通用同步与收束规则

## 适用范围

本规则适用于任意已切分的文本作品。运行时事实源是当前章节原文；桥段资产只负责为玩家选择提供着色与反应模板，不得凭空增加重大人物、地点、组织或事件。

## 章节同步

1. 先依据章节索引读取当前章完整原文，再在本地按空行切分段落。
2. 从段落候选中识别三类锚点：出场人物及其动作、改变后续的关键事件、影响后续行为的情感转折。
3. 事件类优先保留，人物类保证每个关键出场者至少有一个代表锚，连续情感变化可合并。
4. 每章回合预算由字符数本地计算：`<1500→3`、`<3000→4`、`<5000→5`、`<8000→6`、`<12000→7`、`<18000→8`、其余为 `9`。
5. 每个回合至少承载一个锚点。锚点数量少于预算时，剩余回合只能做当前章内的过渡和氛围；锚点数量超预算时，按优先级合并，不能丢失关键事件。
6. 章末必须完成当前章最后一个关键锚点，再留下当前文本已经支持的未闭合钩子；跨章时先校验上一章钩子与下一章首锚点的衔接。
7. 锚点清单作为硬性 checklist。未完成的锚点不得推进章节；若生成结果漏演锚点，应回滚该回合并重演。
8. 极短章抽取不到候选时，将整章视为单锚点，按回合预算分段推进；特别章节只做视角或时间说明，不强行并入相邻主线。

## 锚点九字段校验

每条锚点必须包含：

`chap/title/vol/arc/chars/event/detail/quotes/significance`

字段要求：

- `chap`：非空章节标识。
- `title`、`vol`：非空标题与卷标识。
- `arc`：4–8 字的阶段名。
- `chars`：非空人物或实体列表。
- `event`：15–30 字的事实概括，不写模板化空话。
- `detail`：150–250 字的当前章节转述，不直接粘贴长段原文。
- `quotes`：0–2 条原文逐字摘录；空数组合法，伪造或拼接摘录不合法。若提供章节原文，必须逐字命中。
- `significance`：20–40 字，说明该锚点如何影响后续，而非泛泛评价。

解析失败、字段缺失、长度越界或摘录不命中时必须报告失败，不得静默跳过。校验器还会拒绝把 `event` 原样复述为 `quotes` 的机器污染。

## 玩家选择与收束

每回合处理顺序固定为：

1. 将玩家选择归类为九种风格之一：`强硬/隐忍/智取/示弱/反将/借势/试探/斡旋/收买`。
2. 以当前锚点的 `cat/sub/trigger` 与风格检索通用桥段；无精确命中时放宽到同类别，再退回通用收束模板。
3. 实例化 `reaction` 与 `converge`。模板只能使用运行时上下文占位符，例如 `{主角}`、`{交互角色}`、`{对手}`、`{下一锚}`、`{内因}`。
4. 收束必须具备至少一种内因：角色意志、环境阻力、已有伏笔支持的可信巧合。收束应保留玩家选择造成的可观测回响，同时与当前锚点兼容。
5. 不允许用命运、作者或系统越权改写当前事实；特殊能力可以改变路径、代价和细节，但不能让当前硬锚点凭空消失。
6. 记录选择风格、桥段 id、回响、收束文本、内因与可信度 K。K 低于 60 时换候选；候选耗尽则使用显式内因的通用收束模板。

## 桥段资产契约

`data/tropes_biz.json`、`tropes_combat.json`、`tropes_life.json`、`tropes_mystery.json`、`tropes_romance.json` 共 3409 条记录，统一字段为：

`id/cat/sub/name/summary/trigger/choice_styles/reaction/converge/K/src`

`src` 固定为 `通用桥段库`。资产只提供叙事结构和反应方式，不包含任何特定作品的书名、作者、角色、地名、组织或桥段事实。`data/tropes_manifest.json` 记录数量、字段、风格与占位符清单。

## 运行时入口

`data/trope_index.py` 提供标准库实现：

- `load_tropes()`：严格加载五个 JSON，解析失败直接抛错。
- `search_tropes(cat, sub, trigger, style, limit)`：按锚点情境和选择风格检索。
- `instantiate(template, context)`：按运行时上下文替换占位符。
- `chapter_turn_budget(char_count)`：计算本地回合预算。
- `extract_anchor_candidates(text)`、`map_anchors_to_turns(anchors, budget)`：生成候选并映射回合。
- `validate_anchor(anchor, source_text)`、`validate_anchors(anchors, source_text)`：执行九字段校验和逐字摘录检查。

推荐冒烟命令：

```text
python -c "from data.trope_index import load_tropes; print(len(load_tropes()))"
python -c "from data.trope_index import chapter_turn_budget; print([chapter_turn_budget(n) for n in (1000, 2000, 4000, 7000, 10000, 15000, 20000)])"
```
