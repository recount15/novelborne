# 接手手册（HANDBOOK）

> 目标读者：接手本项目的开发者。按顺序读完即可独立运行、测试、发布。

## 1. 环境与运行

- 依赖：Python 3.10+（`pip install -r requirements.txt`）、Node 18+（仅构建前端）
- 源码运行：`python run_app.py`（自动打开 http://127.0.0.1:8000）
- 前端开发热更：`cd frontend && npm install && npm run dev`（:5173 代理 /api 到 :8000）
- 多实例：`python run_app.py --port 8010 --var var/cluster/a --no-browser`
- 模型服务：内置 DeepSeek/通义/Kimi/智谱/OpenAI 及自定义 OpenAI 兼容预设，
  页面左栏填 Key 即用；**各家平等，代码不偏向任何提供商**。
- Windows 免安装版：`build\build_windows.bat` 产出 `dist\FateEngine\`（PyInstaller
  onedir，含 assets + 前端 dist；运行数据在 exe 同级 var/ 自动创建）。

## 2. 回归闸门（改代码后必须全绿才算完）

三件套脚本（在 `var/` 下，git 忽略；key 走对话传递不落盘，用前自行填入）：

| 脚本 | 覆盖 | 通过线 |
|---|---|---|
| `var/gaptest_api.py` | 47 端点中的只读面 + 静态页 + 错误优雅性 | 15/15 |
| `var/fulltest_glm.py` | 基础模式：开局→穿越表→回合→问答→存档 | 14/14 |
| `var/fulltest_enhanced.py` | 强化模式：上传→切章→四槽穿越→蒸馏→确认→回合 | 8/8 |

要点：
- NDJSON 流的 `delta` 是**增量块**，必须累加拼接（脚本已内置）
- 强化模式两步确认：聊天框发"确认金手指：X"→"确认开局"
- 机械门禁的"⚠️ 本回合未通过机械门禁"是**合法引擎行为**（回滚+重试提示），
  不是 bug；真错误只认"模型服务失败/调用失败"
- 测试会向作品库蒸馏 W02 与测试角色卡，脚本尾部自动清理；若残留，
  `git checkout assets/rules/work_library.md` + 清 `assets/data/characters/user/`
- 闸门跑前**重启 8000 实例**载入新代码

## 3. 发布流程（GitHub + Gitee）

1. 闸门全绿 + `git status` 干净 → commit
2. 重建 exe（见上）→ 冒烟：exe 起独立端口 → health → 一次基础开局
3. 打包 zip（121MB）+ 7z 极限压缩（97.9MB，Gitee 附件限 100MB）
4. GitHub：release（tag vX.Y.Z）上传双包；Gitee：同名仓库 release 传 7z
5. 仓库可见性、凭据管理：token 只用于推送/建仓，用后建议吊销
6. **版权红线**：`ip_vault/`、`var/`、恢复包 zip 绝不入库（.gitignore 已挡，
   发布前 `git status` 复核）

## 4. 已知问题与设计备忘

- ~~quick_distill 无独立超时~~ **已解决（2026-08-30）**：`engine/distill.py`
  的 `distill_model` 统一 `DEFAULT_SUBCALL_TIMEOUT=120s`，覆盖全部内部子调用
  （蒸馏/托管/任务与碎锚结算/宿敌回合/压缩）；调用方显式传 timeout 的沿用其值
- **glm-5.2 合规性**：正文超体量 ~10% 且未收锚时机械门禁会拒绝（引擎按
  设计工作）；提示词对体量/锚点的引导可继续调优
- **sessions 无淘汰**：/api/saves/load 每次新建会话，内存只增不减——
  Phase 3 服务层拆分时补 TTL 淘汰
- **流式锁语义**：生成器 finally 释放会话锁，客户端不读完流锁不释放——
  Phase 3 重构项
- **性别栏杆已破除**（2026-08-30）：穿越不受性别限制，叙事以附身角色
  生理性别为准；gender_guard.py 现为"穿越保障"，旧探测函数已下线
- **金手指设计器规格目录**：assets/data/golden_fingers/user（曾指向
  core/data 孤儿路径致规格不可见，已修——同类路径问题见 Phase 4 paths 统一）

## 5. 文档地图与规范

见《DOC_STANDARDS.md》：什么文档放哪、何时更新、怎么写。

## 6. 接手第一周建议路线

1. 跑通环境 + 三件套闸门（熟悉对外行为）
2. 读 ARCHITECTURE.md 对照代码走一遍 on_start/on_send
3. 小改动练手：跑闸门→commit 流程走一遍
4. 接续重构 3d/e（蓝图在 var/refactor/DESIGN.md，每阶段闸门纪律不变）
