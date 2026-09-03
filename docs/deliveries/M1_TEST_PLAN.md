# M1 实测计划：阶段 A+B Kimi-K3 冒烟测试

## 测试环境准备

### 1. 配置 Kimi API Key
```bash
# Windows PowerShell
$env:MOONSHOT_API_KEY="your-kimi-api-key"

# 或在启动命令中临时设置
$env:MOONSHOT_API_KEY="your-key"; python run_app.py --port 8010
```

### 2. 启动隔离实例（8010 端口）
```bash
cd C:\Novelborne\Novelborne-2.0.0-clean-source
python run_app.py --port 8010
```

### 3. 浏览器访问
```
http://localhost:8010
```

---

## 测试用例设计

### 🎯 测试用例 1：任务全生命周期
**目标**：验证任务机制的注入、判定、奖励发放、补发

#### 步骤：
1. **创建新游戏**
   - 选择任意作品模板
   - 进入第一回合

2. **触发任务 offer（通过 `/quest offer` 或剧情触发）**
   - 任务标题：「寻找失踪的村长」
   - 任务目标：「找到村长并确认其安危」
   - 完成条件：「村长出现在正文中，且明确其安危状态」
   - 时限：3 回合
   - 奖励：
     ```json
     {
       "物资": [{"name": "银两", "quantity": 100}],
       "技能碎片": [{"name": "侦查", "level": 1}]
     }
     ```

3. **验证任务注入（回合 1-2）**
   - **断言 1**：查看开发者工具 Network，检查 `/turn` 请求的 `context_audit`
     - 必须包含任务标题「寻找失踪的村长」
     - 必须包含剩余轮数「剩余 3 回合」或「剩余 2 回合」
   - **断言 2**：生成的正文应提及任务相关内容
   - **断言 3**：选项中应出现「任务：寻找失踪的村长」因素

4. **推进并完成任务（回合 3）**
   - 选择与任务相关的行动
   - 生成的正文中**必须明确出现**：
     - 村长的名字（或「村长」一词）
     - 安危状态（如「村长安然无恙」或「村长已遇害」）

5. **验证判定与奖励发放**
   - **断言 4**：任务状态从 `active` 变为 `completed`
   - **断言 5**：查看 `state_memory.assets.items`，必须包含 `{"name": "银两", "quantity": 100}`
   - **断言 6**：查看 `state_memory.abilities.skills`，必须包含 `{"name": "侦查", "level": 1}`
   - **断言 7**：查看 `convergence` 或 `agent_meta.quest`，审计记录与实际发放一致
   - **断言 8**：下一回合的润色卷注入中包含「任务已完成(寻找失踪的村长)，奖励已由系统发放」

#### 边缘场景测试：
- **证据制拒收**：手动修改正文，删除村长相关内容，刷新判定
  - 预期：`evidence` 不符，判定被拒，任务保持 `active`
- **奖励发放失败补发**：
  - 模拟：在 `_grant_quest_reward` 中抛异常
  - 预期：任务 `reward_pending` 挂起，下回合自动补发

---

### 🎯 测试用例 2：主角状态恒定（可演进约束）
**目标**：验证状态硬事实注入、质量门检测、合理演进允许

#### 步骤：
1. **构造初始状态**
   - 手动编辑存档或通过剧情推进至：
     ```json
     {
       "state_memory": {
         "identity": {"role": "江湖游侠", "name": "李寻欢"},
         "body": {"condition": "重伤", "injuries": [{"type": "内伤"}, {"type": "骨折", "part": "左臂"}]},
         "location": {"name": "破庙"},
         "assets": {
           "items": [{"name": "小李飞刀"}, {"name": "金疮药", "quantity": 2}]
         }
       }
     }
     ```

2. **验证硬事实注入（compose_mode 管线）**
   - 进入回合，查看 Network `/turn` 请求的 prompt
   - **断言 9**：必须包含以下硬事实块：
     ```
     【主角状态硬事实】
     - 主角身份：江湖游侠、李寻欢
     - 主角当前身体状态：重伤（伤势：内伤、骨折（左臂））；可经治疗逐渐康复...
     - 主角当前位置：破庙；可合理移动，但需有走/骑/传送等铺垫...
     - 主角当前持有物品：小李飞刀、金疮药×2；可合理使用、赠送、消耗...
     ```

3. **验证 LEGACY 路径注入**
   - 修改代码强制走 LEGACY 路径（或删除 papers 触发回落）
   - **断言 10**：LEGACY 路径的 llm_msg 也包含相同硬事实块

4. **测试质量门检测：历史否定（应拦截）**
   - 手动修改生成正文为：
     ```
     李寻欢从未到过破庙，这是第一次来。他并未受伤，身体完好无损。
     ```
   - 刷新质量门评分
   - **断言 11**：`state` 维度分数 < 60.0
   - **断言 12**：`issues` 包含 `location_negation` 和 `injury_negation` 错误

5. **测试合理演进（应允许）**
   - 正文描述合理康复过程：
     ```
     李寻欢服下金疮药，运功疗伤。经过一夜调息，内伤逐渐好转，但左臂骨折仍需静养。
     ```
   - **断言 13**：`state` 维度分数 = 100.0（不扣分）
   - **断言 14**：extractor 提取 `body.condition` 从「重伤」变为「受伤」
   - **断言 15**：`injuries` 保留「骨折（左臂）」

6. **测试物品消耗（应允许）**
   - 正文：「李寻欢用掉一份金疮药」
   - **断言 16**：extractor 提取后 `items` 中金疮药数量 -1
   - **断言 17**：质量门不扣分

7. **测试物品历史否定（应拦截）**
   - 正文：「李寻欢从未拥有小李飞刀，一直赤手空拳」
   - **断言 18**：`state` 维度 < 70.0，`issues` 包含 `asset_history_negation`

---

### 🎯 测试用例 3：LEGACY 路径角色 patch
**目标**：验证 LEGACY 路径也能生成角色关系 patch

#### 步骤：
1. **强制走 LEGACY 路径**
   - 删除 `assets/papers/` 触发回落，或修改代码跳过管线

2. **构造场景：有活跃角色**
   - 正文中出现角色「林诗音」，且有互动

3. **验证角色 patch 调用**
   - **断言 19**：检查 `agent_meta.character_patch` 存在
   - **断言 20**：`state_memory.relationships.characters` 包含林诗音的关系行
   - **断言 21**：关系行包含 `source: "character_patch"`

---

## 强化断言清单

| # | 断言内容 | 验证方式 | 预期结果 |
|---|---------|---------|---------|
| 1 | 任务上下文注入到 context_audit | Network 检查 | 包含任务标题+剩余轮数 |
| 2 | 正文提及任务相关内容 | 阅读正文 | 与任务目标相关 |
| 3 | 选项因素包含任务 | UI 查看 | 「任务：XXX」因素 |
| 4 | 任务完成判定 | 查看存档 `quest.status` | `completed` |
| 5 | 奖励物资落地 | `state_memory.assets.items` | 包含银两×100 |
| 6 | 奖励技能落地 | `state_memory.abilities.skills` | 包含侦查 Lv1 |
| 7 | 审计与发放一致 | `convergence` 或 `agent_meta` | 审计记录=实际发放 |
| 8 | 下回合任务回响 | 润色卷注入 | 「任务已完成...奖励已发放」|
| 9 | compose_mode 硬事实注入 | Network prompt | 包含完整硬事实块 |
| 10 | LEGACY 硬事实注入 | Network prompt (LEGACY) | 包含完整硬事实块 |
| 11 | 历史否定被检出 | quality_gate state 分数 | < 60.0 |
| 12 | 历史否定错误记录 | `issues` | location_negation + injury_negation |
| 13 | 合理康复不扣分 | quality_gate state 分数 | = 100.0 |
| 14 | extractor 提取康复 | `body.condition` | 从「重伤」→「受伤」|
| 15 | 伤势保留 | `body.injuries` | 仍含「骨折」 |
| 16 | 物品消耗提取 | `assets.items` | 金疮药 -1 |
| 17 | 消耗不扣分 | quality_gate state 分数 | = 100.0 |
| 18 | 物品历史否定检出 | quality_gate state 分数 | < 70.0 |
| 19 | LEGACY 角色 patch 调用 | `agent_meta.character_patch` | 存在且 ok=true |
| 20 | 关系行落地 | `relationships.characters` | 包含林诗音 |
| 21 | 关系行 source | `source` 字段 | `"character_patch"` |

---

## 测试执行记录模板

```markdown
## M1 实测执行记录

**执行时间**：YYYY-MM-DD HH:MM
**测试人员**：
**Kimi 模型**：moonshot-v1-32k (或 128k)
**隔离实例**：http://localhost:8010

### 测试用例 1：任务全生命周期
- [ ] 断言 1：任务上下文注入 ✓/✗ 
- [ ] 断言 2：正文提及任务 ✓/✗
- [ ] 断言 3：选项因素包含任务 ✓/✗
- [ ] 断言 4：任务完成判定 ✓/✗
- [ ] 断言 5：奖励物资落地 ✓/✗
- [ ] 断言 6：奖励技能落地 ✓/✗
- [ ] 断言 7：审计一致 ✓/✗
- [ ] 断言 8：任务回响 ✓/✗
**问题记录**：

### 测试用例 2：主角状态恒定
- [ ] 断言 9：compose_mode 注入 ✓/✗
- [ ] 断言 10：LEGACY 注入 ✓/✗
- [ ] 断言 11：历史否定检出 ✓/✗
- [ ] 断言 12：错误记录完整 ✓/✗
- [ ] 断言 13：合理康复不扣分 ✓/✗
- [ ] 断言 14：康复状态提取 ✓/✗
- [ ] 断言 15：伤势保留 ✓/✗
- [ ] 断言 16：物品消耗提取 ✓/✗
- [ ] 断言 17：消耗不扣分 ✓/✗
- [ ] 断言 18：物品否定检出 ✓/✗
**问题记录**：

### 测试用例 3：LEGACY 角色 patch
- [ ] 断言 19：LEGACY patch 调用 ✓/✗
- [ ] 断言 20：关系行落地 ✓/✗
- [ ] 断言 21：source 正确 ✓/✗
**问题记录**：

### 总结
- **通过断言数**：__/21
- **发现问题**：
- **修复计划**：
```

---

## 注意事项

1. **不实测不汇报原则**：所有断言必须实际检查，不可臆测
2. **key 安全**：
   - 仅通过环境变量传递
   - 测试后建议轮换 key
   - 绝不写入代码或配置文件
3. **端口隔离**：使用 8010 端口，不动 8000 生产端口
4. **存档检查**：
   - 存档位置：`saves/` 目录
   - 使用 JSON 工具格式化查看
   - 关键字段：`state_memory`, `quest`, `agent_meta`, `convergence`
5. **Network 调试**：
   - 开启浏览器开发者工具
   - 过滤 `/turn` 请求
   - 查看 Request Payload 和 Response

---

## 回归检查

测试完成后，运行全量单测确保无回归：
```bash
cd C:\Novelborne\Novelborne-2.0.0-clean-source
python -m pytest tests/ -v --tb=short
```

预期：**449 passed**
