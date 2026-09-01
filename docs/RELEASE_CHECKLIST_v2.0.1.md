# 发布清单（v2.0.1）

## 构建前

- [ ] 版本号统一为 `2.0.1`
- [ ] `var/`、`node_modules/`、`dist/`、本地日志、数据库和 API Key 不进入源码包
- [ ] `python -m unittest discover -s . -p "test_*.py"` 全绿
- [ ] `npm --prefix frontend run build` 通过

## Windows Web 版

- [ ] 执行 `build\build_windows.bat`
- [ ] `dist\FateEngine\FateEngine.exe` 存在
- [ ] 独立临时目录启动成功
- [ ] `/api/health` 返回 `version: 2.0.1`
- [ ] 首页静态文件可访问
- [ ] 关闭进程后 `var/` 仅出现在发布目录内部

## Windows 窗口版

- [ ] 执行 `build\build_windows_windowed.bat`
- [ ] `dist\FateEngineWindowed\FateEngineWindowed.exe` 存在
- [ ] 窗口版后端健康接口可用
- [ ] WebView2/pywebview 依赖已被收编

## Release 文件

- [ ] `Novelborne-v2.0.1-source.zip`
- [ ] `Novelborne-v2.0.1-windows-web-x64.zip`
- [ ] `Novelborne-v2.0.1-windows-windowed-x64.zip`
- [ ] SHA256 校验文件 `SHA256SUMS.txt`
- [ ] `RELEASE_NOTES_v2.0.1.md`

## 上传

- [ ] GitHub 仓库推送源码与 `v2.0.1` tag
- [ ] GitHub Release 创建并上传构建产物
- [ ] Gitee 仓库推送源码与 `v2.0.1` tag
- [ ] Gitee Release 创建并上传构建产物
