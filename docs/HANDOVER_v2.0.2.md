# Novelborne（书中织梦）v2.0.2 接手包

日期：2026-09-01　|　仓库：`C:\Novelborne\Novelborne-2.0.0-clean-source`（非 git，版本管理靠 release 目录快照）

> 本文档是交接快照：汇总项目现状、用户报告的全部 bug 及已查明根因、待执行的修复计划、未实装功能与发布风险。接手者从「三、修复计划」开始干即可。

## 一、项目概况

- 产品：大模型驱动的互动小说世界模拟器。玩家以穿越者身份进入原著世界；程序负责世界规则、锚点、涟漪、任务、收束力和持久化。README 定位：「不是聊天壳，而是结构化出卷 → 并行填空 → 空级批改 → 错题重填 → 代码组装 → 全局润色 → 机制结算」的叙事运行时。分强化模式（上传完整 TXT）与普通模式，6 档剧情丰度。
- 技术栈：FastAPI + uvicorn（`core/`，分层 api 路由 / services 业务 / engine 引擎 / prompts）；Vue 3 + TS + Vite + Tailwind 4（`frontend/`，lucide 图标 + 霞鹜文楷字体，capacitor 支持 Android）；SQLite（`var/db/fate_engine.db`，WAL 模式）。
- 入口：`run_app.py`（web 版，默认 8000，数据目录仓库 `var/`，`--var/--port/--no-browser` 可调）；`run_windowed.py`（pywebview 无边框窗口版，含 DWM 圆角/图标/首帧重绘）；打包用 `build/*.spec`（PyInstaller）。
- 运行现状：8000 端口服务运行中（`python -X utf8 run_app.py`，PID 3816），使用仓库 `var/`（db-wal 活跃）。
- 测试：`tests/` 28 个文件、341 个用例可正常收集（`pytest tests/ --collect-only -q` 0.66s）；**没有存读档端点/persistence 的任何测试**——补测试是修复计划一部分。
- 发布状态：`C:\Novelborne\release-v2.0.1\` 完整（源码 + web 版 + 窗口版 zip/7z + SHA256SUMS）；**`release-v2.0.2\` 为空目录，尚未产出**。`dist/` 下两个 exe 为 09-01 12:19/12:24 构建。

## 二、用户报告的 bug（第 1、2 点）及已查明根因

### 阅读器（`frontend/src/components/OriginalReaderModal.vue`）

**Bug 1 无法到达下文**：邻章 fetch 失败被 `.catch(() => null)` 静默吞掉（`selectChapter` L169-170）→ `nextChapter` 为 null，或正文剥标题后为空 → 预览区 `v-if="nextChapter && nextPreviewText"` 不渲染 → `nextPreviewRef` 缺失 → `onScroll` 的 `enteredNext` 永假 → 滚动永远无法触发翻章（footer/悬浮按钮仍可用，无任何报错提示）。

**Bug 2 跳章（跳到上一章的上一章）**：`onScroll`（L223-280）在抑制/加载/移动锁检查**之前**就更新 `lastScrollTop`（L229）→ 350/500ms 抑制窗口放开后，残余惯性滚动的首个事件方向被误判 → 误触发 `enteredPrev` 连跳。次级因素：向上切章锚点失败回退 `'end'` 定位到新当前章末尾，视口顶部紧贴上章预览，轻微上滚立刻再触发 `enteredPrev`。

**Bug 3 书签跳转无效**：实测单击一次书签卡片 `jumpBookmark` 被调用 **3 次**（`chapterRequest` 序号连增、前两次响应到达时被 `request !== chapterRequest.value` 守卫丢弃 → 页面不动）。已在 `jumpBookmark` 入口加 `chapterLoading` 守卫（L215，逻辑验证通过：3 连击只放行 1 次，见 `C:\Novelborne\var\verify_bookmark_fix.py`）。源码事件链干净（App.vue:3323 唯一渲染点、无 keep-alive/Transition、emit 无冒泡叠加）——3 次触发疑似 Vite HMR 热更新残留旧实例监听器，需硬刷新（Ctrl+F5）实测确认。

### 系统

**Bug 4 快速蒸馏角色无法入库**：role 映射链路本身正常——`opening_distill._normalize_opening_card` → `work_distiller.normalize_character` 的 `_POSITION_TO_ROLE` 恒产合法 role（主角/女主/配角/反派 → 主角/single_heroine/伙伴/反派，全部 ∈ catalog.ROLES）；历史 6 张用户卡均按此映射入库成功。真正嫌疑：**`save_card` 的 SQLite 双写静默失败**——`assets/data/characters/user/` 有 6 张 JSON 卡（source="用户上传蒸馏"），但 `var/db/fate_engine.db` 的 characters 表 **0 行**；而角色库加载（`merged_pool`）是数据库优先，DB 空表但不报错时 JSON 卡"看不见"。`save_card` 内 `except Exception: warnings.warn(...)` 吞掉异常，根因不可见（先查 `var/logs/` 的 warnings 与 `insert_character` 的 schema）。次级问题：`_default_save_characters`（opening_distill.py L617-629）单卡失败 `except: continue` 完全静默，`report["characters"]` 里只有 `saved=False` 没有原因。

**Bug 5 开局等待无状态提示**：进度链路三环齐备——`core/services/opening_service.py` 的 `_PROGRESS_REGISTRY`（L169）→ `core/server.py` `/api/sessions/{id}/state`（L2161-2180）合并 → `frontend/src/App.vue` 右栏「锚点蒸馏」区块渲染。缺口：(a) `/state` 合并键（`distill_key`/`chapter_index.book_id`，server.py L2170-2175）未就绪时 `take_progress` 返回 None → 前端整段时间空白；(b) `chapters_done/chapters_total` 只在结束后的 `_apply_to_state` 才写（opening_service.py L77-81），进行中阶段无章节计数。

**Bug 6 存读档后无法交互**：读档端点（`core/server.py` L2237-2258 会话内 `/load`、L2215-2234 自由 `/api/saves/load`）恢复后**只强制 `game_ready=True`，不镜像 `gf_confirmed`/`opening_confirmed` 顶层键、不兜底 `options`**。两类死局：
1. 确认前档（开局 `st0`，`app.py` L1792-1795：`gf_confirmed=False, opening_confirmed=False`）读入后：后端 `on_send` 被 `app.py` L2061-2085 强化门禁拦截推不动剧情；前端 `openingStep`（App.vue L416-423，只读顶层键）若判 null，输入框 `:disabled="openingInputDisabled"` 恒禁用、选项区又为空（options 未生成）→ 界面无任何交互入口。
2. 回合中间档：`app.py` L2575 回合中段 `save_state` 发生在 `_finalize_options` **之前**，与 L2651 回合末同名自动档覆盖——进程在两次落盘间中断则磁盘上是"无 options 中间档"，读档后无按钮可点 + 输入框禁用 → 死界面。

**Bug 7（第 3 点）窗口版打不开**：`dist/FateEngineWindowed/FateEngineWindowed.exe` 实测启动 12 秒内退出（退出码 -1）。且 dist exe（12:24 构建）**不含 09-01 13:20 之后的所有源码修复**（`opening_distill.py` 13:20、`server.py` 13:30、`opening_service.py` 13:31、`App.vue` 13:46、`OriginalReaderModal.vue` 15:56 均晚于打包，mtime 倒挂）——用户在打包版上复现的任何 bug 都可能与源码已修状态不一致。本轮未修。

## 三、修复计划（用户已明确要求，待执行）

1. **阅读器**（OriginalReaderModal.vue）：
   - 预览区渲染条件 `v-if="nextChapter && nextPreviewText"` 放宽为 `v-if="nextChapter"`（文本为空时渲染占位提示），保证 `nextPreviewRef` 始终存在、滚动翻章链路不断；
   - 邻章 fetch 的 `.catch(() => null)` 加一次快速重试；
   - `onScroll` 中 `lastScrollTop = scrollTop` 移到抑制/加载/移动锁检查**之后**——抑制期间不更新方向基准，抑制放开后首个滚动事件与抑制前位置比较，惯性回弹不再误判方向；
   - 向上切章 `enteredPrev` 阈值 0.4→0.25；锚点失败回退后的稳定抑制 500ms→800ms。
2. **角色入库**（character_library.py、opening_distill.py）：
   - `save_card` 双写失败从静默 `warnings.warn` 改为捕获具体异常信息写入返回结构；
   - 实际运行一次入库调用定位 DB 写入失败原因（schema 不匹配/表未初始化）并修复；
   - `merged_pool`：DB characters 空表时回退/合并 JSON 用户卡，保证已落盘卡可见；
   - 不放宽 `_validate_role`/catalog.ROLES（全系统契约，波及 save/update/import/scan/merged_pool 全链）。
3. **开局进度**（opening_service.py、server.py、App.vue）：
   - `/state`：开局流进行中但合并键未就绪时返回通用 running 进度（"正在准备书籍…"）消除首帧空窗；
   - `_write_progress` 进行中阶段同步写 `chapters_done/total`；
   - 前端：`openingRunning` 为真但无 `opening_distill` 数据时显示默认"正在准备开局蒸馏…"文案。
4. **存读档**（server.py、app.py、App.vue）：
   - 读档端点恢复后对 pending 态推导兜底：顶层 `gf_confirmed`/`opening_confirmed` 从 `opening_state` 镜像补齐；若 options 为空且尚未过开局确认，正确反映 pending 步骤而非强行 `game_ready=True`；
   - `app.py` 回合中段 `save_state`（L2575）移到 `_finalize_options` 之后（与 L2651 合并），消除"无 options 中间档"竞态；
   - 前端 `openingStep` 读顶层键缺失时回退读 `state.opening_state` 嵌套键；`openingStep` 为 null 且 options 为空时放开输入框禁用（永远保留交互通道）；
   - 补回归测试：确认前档、回合中间档读入后 state 必须可交互（options 非空或 pending 明确）。
5. **验证标准（用户硬性要求）**：`npm run build` 通过 + 硬刷新浏览器实测滚动跨章/书签/翻页按钮/短章边界；`pytest tests/` 全量通过；入库端到端验证（蒸馏卡 → DB+JSON 双写 → 角色库可见）；存读档端到端验证（确认前档、正常档读入后可交互）。**不实测不汇报。**

## 四、未实装功能（用户要求但未完成）

- **阅读器"查看本章锚点"**：`frontend/src/components/reader/ReaderAnchorsPanel.vue` 已创建（当前实现显示本章书签位置），主组件 import 已加（OriginalReaderModal.vue L8）、Drawer 类型已扩展为含 `'anchors'`（L11），但**模板按钮/抽屉分支未接、未构建验证**。注意：更好的数据源是锚点文件 `var/books/{book_id}/anchors/NNNN.json` 的九字段（chapter/title/summary/events/characters/world/foreshadowing/quotes/ripple），可加只读 API `GET /api/books/{id}/chapters/{n}/anchors`。
- **阅读器"查看本章活跃人物"**：未实装。**数据源已存在**——锚点蒸馏产物 `anchors/NNNN.json` 的 `characters` 字段（实测每章 4-8 人，"名字：身份/行为描述"富文本），且经 `core/app.py` 的 `_anchor_text` → `fe.pacing_hint`（pacing_hint.md 的 `【已知锚点】`）已注入剧情生成 prompt，**无需新蒸馏**。需新增只读 API + 前端面板（样式参考 ReaderAnchorsPanel.vue）。已知隐患：`pacing_hint` 只取锚点文本前 **1200 字符**，characters 是九字段第 5 键，多锚点拼接时可能被截掉——若用户反馈"活跃人物没计入剧情"，先查这里。

## 五、约束与红线（历史会话遗留，必须遵守）

- 许可证 **BSD-3**；源码和 release 都要验证后再上传；上传前清除所有隐私、版权、真人测试信息。
- 智谱 API（仅 glm-5.2）、GitHub、Gitee 的 token **只能走环境变量**，绝不写入源码/日志/仓库。
- `D:\机娘纪元：我的机娘都是世界级.txt` 为本地测试用版权文件，**绝不复制进项目/归档/仓库**。
- 用户核心工作方式要求：所有提过的功能必须真实模拟实验、确保有效后再汇报成果。

## 六、其他待办与风险

- `release-v2.0.2\` 为空，修复完成后需重新产出（源码 + web + 窗口版 zip/7z，含 SHA256SUMS），并按红线做隐私清理后上传 GitHub + Gitee。
- 窗口版 exe 启动失败待排查（`build/build_windows_windowed.bat` / `FateEngineWindowed.spec`；先看 `--restore-private` 参数路径、`_ensure_stdio` 与 pywebview 初始化）。
- 版本号不一致：`frontend/package.json` 仍为 2.0.1，需与 v2.0.2 对齐。
- `publish-v2.0.1\Novelborne-v2.0.1\run_windowed.py`（10,796 B）落后于仓库根版本（11,375 B，09-01 02:09 修改）。
- 仓库根 0 字节 `conftest_dummy`（09-01 16:12）疑似调试遗留，可清理。
- `var/uploads` 下 44 个测试目录（`progress-test-1`、`test123`、`verify-open-progress` 等）；`C:\Novelborne\var\` 下 4 个调试脚本（`verify_bookmark_fix.py`、`parallel_diagnostics.py`、`test_bookmark_jump.html`、`bookmark_fix_verification.md`）——发布前需清理，且 `C:\Novelborne\var` 不是运行时数据目录（真正数据在仓库 `var/`）。

## 附：关键文件速查

| 文件 | 作用 |
|---|---|
| `frontend/src/components/OriginalReaderModal.vue` | 阅读器主组件（三章滑动窗口、滚动切章、书签，Bug 1-3 全在此） |
| `frontend/src/components/reader/ReaderBookmarksPanel.vue` | 书签面板（已美化，事件链干净） |
| `frontend/src/components/reader/ReaderAnchorsPanel.vue` | 本章锚点面板（已建未接线） |
| `frontend/src/composables/useReaderState.ts` | 书签/进度 localStorage（key：`fate-engine-reader-*-v2/v1`）；注意 `restoreScrollRatio` 是死 import 且口径与 `currentRatio` 不一致 |
| `frontend/src/App.vue` | 主工作台（openingStep L416-423、loadSave L1755-1778、开局进度右栏 UI） |
| `core/engine/opening_distill.py` | 开局蒸馏流水线（波次并行、角色卡 `_default_save_characters` L617） |
| `core/engine/character_library.py` | 角色库（`save_card` L329、`merged_pool`、`build_record` L82 校验） |
| `core/engine/work_distiller.py` | `normalize_character` + `_POSITION_TO_ROLE` 映射 + quick_distill |
| `core/services/opening_service.py` | 开局蒸馏门面 + `_PROGRESS_REGISTRY` + `_STAGE_LABELS` |
| `core/server.py` | 全部路由（/state 进度合并 L2161、存读档 L2183-2258、/api/books L1476+） |
| `core/app.py` | 游戏引擎门面（强化门禁 L2061、回合落盘 L2575/L2651、`_anchor_text` 注入 prompt） |
| `core/engine/anchor_distiller.py` | 锚点九字段蒸馏（ANCHOR_FIELDS 含 characters） |
