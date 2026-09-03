# Novelborne 接手与发布手册

本文面向维护者，描述 v2.0.2 的运行、架构、测试与发布纪律。用户说明见根目录 `README.md`。

## 1. 环境与启动

- Python 3.10+
- Node.js 18+
- Windows 构建：PyInstaller、pywebview、pythonnet、clr-loader
- Android 构建：JDK 17+、Android SDK、Android Studio；没有完整环境时不得发布 APK

```bash
pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
python run_app.py
```

默认监听 `0.0.0.0:8000`，本机浏览器打开 `127.0.0.1:8000`。多实例必须分别指定端口和运行数据目录：

```bash
python run_app.py --port 8010 --var var/cluster/a --no-browser
```

## 2. 强化模式回合链

```text
机制上下文
→ 导演蓝图
→ 段落生成 ∥ 选项生成
→ 空级批改
→ 失败空定向重填
→ 确定性兜底
→ 代码组装
→ 全局答卷整合润色
→ 角色/任务/碎锚/宿敌/收束结算
→ 持久化
```

规则：

- 质量检测落在具体生成空，不做整篇语义门禁；
- 整回合只检查 JSON、代码围栏、系统标记、隐藏日志和选项块等格式污染；
- 全局润色采用事实锁定、接缝整合、文学润色、输出整理四步，不设质量门；
- 润色失败、空返回或格式污染时保留初步组装稿；
- API 并发硬上限 10，限流自动退避，达不到目标并发时排队；
- 普通模式继续使用 legacy 路径。

## 3. 目录与依赖方向

```text
server/app/services → engine → assets
```

- `core/server.py`：HTTP、LAN/QR、会话、静态托管；
- `core/app.py`：开局与回合事务状态机；
- `core/services/`：中台编排门面；
- `core/engine/`：纯机制和校验；
- `assets/`：公开规则、提示词、数据和剧情丰度模板；
- `var/`：运行数据，绝不进入仓库/Release；
- `tests/`：单元与集成回归；
- `tools/`：试玩和审计工具。

除已声明惰性例外外，engine 不得反向 import app/server。

## 4. 发布测试门禁

```bash
python -m unittest discover -s . -p "test_*.py"
cd frontend
npm run build
cd ..
python tools/run_strengthened_playtest.py
set FATE_PLAYTEST_LEGACY=1
python tools/run_strengthened_playtest.py
```

重点测试文件：

- `tests/test_lan.py`：LAN 地址、二维码、session 原样接续；
- `tests/test_papers.py`：六档剧情丰度和 frozen 资产路径；
- `tests/test_turn_pipeline.py`：蓝图、并发、重填、格式回退、4+2 选项；
- `tests/test_answer_polish_service.py`：全局润色安全回退；
- `tests/test_character*.py`：角色证据 patch；
- `tests/test_directives*.py`：三愿/永久增补铁律账本；
- `tests/test_opening*.py`：并行开局蒸馏和角色质量门。

真实模型测试必须：

- Key 只通过环境变量传入；
- 原著只从用户本地路径读取；
- 临时作品库、角色库、锚点和报告全部放系统临时目录；
- 输出只保留耗时、字数、重填率、润色率等脱敏指标；
- 完成后删除脚本、报告、缓存和所有版权文本副本。

## 5. LAN 与二维码验收

源码和两个 Windows 包都必须验证：

1. 监听 `0.0.0.0`；
2. `/api/health` 返回当前版本；
3. `/api/lan-info?session_id=<hex>` 保留原始 session ID；
4. `urls` 包含候选网卡；
5. `/api/lan-qrcode.png?session_id=...&address=...` 返回 PNG；
6. 通过实际私网 IP 请求 `/api/health` 成功；
7. 扫码后的 `/api/sessions/{id}/state` 能恢复同一会话。

多网卡时前端允许切换地址。若同一 Wi-Fi 仍不可达，检查 Windows 防火墙。

## 6. Windows 构建和实机冒烟

```bat
build\build_windows.bat
build\build_windows_windowed.bat
```

构建脚本必须检查 PyInstaller 退出码和最终 EXE 是否存在。发布整个目录：

- `dist/FateEngine/`
- `dist/FateEngineWindowed/`

每个产物都要独立启动并验证 health、bootstrap、首页、LAN info、二维码和 session；窗口版还需确认 pywebview 主窗口和 Web 内容实际创建。

## 7. 发布清理

不得包含：

- `var/`、数据库、日志、会话、上传原著；
- `frontend/node_modules/`、`frontend/dist/`；
- `dist/`、`build/pkg*`（源码仓库）；
- `.env`、API Key、GitHub/Gitee Token；
- 真人测试脚本和报告；
- 私有恢复包、IP vault；
- 第三方版权原著或摘录。

公开 LICENSE 必须是 BSD 3-Clause，版权持有人不得包含个人邮箱等隐私。

## 8. GitHub + Gitee 发布

1. 源码测试和前端构建通过；
2. 两个 Windows 包重新构建并独立验证；
3. 生成源码 ZIP、两个 Windows ZIP 和 SHA256SUMS；
4. 初始化/更新 Git 仓库并提交公开源码；
5. 创建、签出并推送 `v2.0.1` tag；
6. GitHub/Gitee 创建同名 Release，上传同一组已验证资产；
7. 上传后检查附件大小与 SHA256；
8. 发布后轮换或吊销个人访问令牌。

仓库简介、topics 和 Release 摘要见 `docs/REPOSITORY_METADATA_v2.0.1.md`。
