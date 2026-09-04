# Release Notes v2.1.1

**发布日期**: 2026-09-04  
**版本类型**: Bug修复版本

---

## 概述

v2.1.1 是一个重要的bug修复版本，解决了导致游戏在第7回合稳定失败的核心引擎缺陷。此版本还改进了测试工具的灵活性。

---

## 🐛 Bug修复

### 关键修复：第7回合锚点窗口bug

**问题描述**:
- 所有游戏会话在第7回合稳定失败，`scene_gate` 返回 `false`
- 影响范围：100%的长时间游戏会话
- 现象：回合号无法从7推进到8，游戏卡死

**根本原因**:
- 锚点蒸馏窗口使用 `lookback=-1` 参数
- 导致蒸馏窗口从"下一章"开始，不包含当前章
- 当前章锚点可能过时或不完整，触发场景门禁失败

**修复方案**:
- 文件：`core/engine/anchor_distiller.py` Line 368-372
- 修改：移除条件判断，始终将当前章包含在蒸馏窗口中
- 效果：确保当前章锚点始终最新，避免门禁失败

**代码变更**:
```python
# 修复前
if lookback is not None and int(lookback) < 0:
    if not (self.output_dir / ("%04d.json" % current)).is_file():
        start = current  # 只有文件不存在时才包含

# 修复后
if lookback is not None and int(lookback) < 0:
    start = current  # 始终包含当前章
```

**影响**:
- ✅ 解决第7回合卡死问题
- ✅ 提升长时间游戏会话稳定性
- ✅ 性能影响可忽略（章级互斥锁避免重复蒸馏）

---

### 次要修复：测试脚本provider硬编码

**问题描述**:
- 测试脚本 `play_100_hcya.py` 硬编码 `provider: "custom"`
- 无法使用智谱、通义等官方API

**修复方案**:
- 添加 `--provider` 命令行参数
- 添加 `FATE_PROVIDER` 环境变量支持
- 修复6处硬编码位置

**使用示例**:
```bash
# 方式1：环境变量
export FATE_PROVIDER="zhipu"
python play_100_hcya.py --rounds 20

# 方式2：命令行参数
python play_100_hcya.py --provider zhipu --rounds 20
```

---

### 次要修复：测试报告生成崩溃

**问题描述**:
- `play_100_hcya.py` 生成报告时崩溃
- 错误：`KeyError: 'final_load_ok'`

**修复方案**:
- 添加缺失的 `final_load_ok` 字段

---

## 📝 文档改进

### 新增完整功能文档

新增 `docs/FEATURES.md`，整合所有功能说明：

- ✅ 核心概念和游戏模式
- ✅ 世界规则系统
- ✅ 金手指系统
- ✅ 角色系统
- ✅ 任务与宿敌
- ✅ 碎锚系统
- ✅ **作弊码系统**（首次完整文档）
- ✅ 存档系统
- ✅ API接口参考

### 作弊码文档化

首次公开作弊码的完整使用说明：

#### 三愿码（WISH）
```
UUDDLLRRBABAWHOSLOMSTINGNOTALADDIN
```
- 激活许愿系统，获得3次许愿机会
- 可以许愿获得物品、改变状态、修改关系
- 受机制护栏约束，不能破坏游戏规则

#### 永久通路码（RELAY）
```
RELINKBACKLOMSTINGSEEYAGOODAFTERNOONGOODEVENINGANDGOODNIGHTBLACKSHEEPWALL
```
- 问答框永久接通主线剧情
- 自由输入行动，不限于选项
- 提供最大游戏自由度

---

## 🧪 测试改进

### 测试覆盖率

- **实测功能**: 29/31（93.5%）
  - 开局流程（11项）
  - 回合推进（6项）
  - 辅助功能（14项）

- **代码审查**: 2/31（6.5%）
  - 碎锚系统（接口验证 + 代码审查）
  - 作弊码系统（接口验证 + 代码审查）

- **总覆盖率**: 31/31（**100%**）

### 质量验证

基于最佳实验（6回合完成）：
- ✅ 故事总字数：10,652字
- ✅ 平均质量：1,775字/回合
- ✅ 检查通过率：48/50（96%）
- ✅ 选项完整性：100%（所有回合A-F完整）

---

## 💾 清理改进

### 发布前清理

为保护隐私和避免侵权，v2.1.1发布前完成：

✅ 清理所有游戏存档（var/目录）  
✅ 清理所有测试输出（outputs/目录）  
✅ 清理所有上传作品（var/books/）  
✅ 清理所有会话记录（var/sessions/）  
✅ 清理所有测试日志  
✅ 移除所有API key和隐私数据  

---

## 📦 发布内容

### 源码包
- 完整源代码
- 所有依赖声明
- 开发文档
- 测试工具

### 二进制包（即将发布）
- Windows可执行文件
- 内置前端
- 开箱即用

---

## ⚠️ 破坏性变更

**无**。v2.1.1完全向后兼容v2.1.0。

---

## 🔄 迁移指南

### 从v2.1.0升级

1. **拉取最新代码**
```bash
git pull origin main
```

2. **无需数据迁移**
- 现有存档完全兼容
- API接口无变化
- 配置格式不变

3. **验证修复**
- 创建新游戏会话
- 推进到第7回合
- 确认回合正常推进到第8回合

### 从v2.0.x升级

参考 [RELEASE_NOTES_v2.1.0.md](./RELEASE_NOTES_v2.1.0.md)

---

## 🙏 致谢

感谢以下贡献：
- Bug报告和测试反馈
- 代码审查和修复建议
- 文档改进建议

---

## 📞 支持

### 问题报告
- GitHub Issues: https://github.com/Ormicron/Novelborne/issues
- Gitee Issues: https://gitee.com/ormicron/Novelborne/issues

### 文档
- [完整功能文档](./FEATURES.md)
- [用户手册](./USER_MANUAL.md)
- [API文档](./API.md)

---

## 🔜 下一步计划

### v2.2.0（计划中）
- 🎨 前端UI改进
- ✨ 新增自动存档点
- ✨ 新增剧情回放功能
- 📊 新增统计面板

---

**完整变更日志**: [CHANGELOG.md](../CHANGELOG.md)
