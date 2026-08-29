@echo off
REM ============================================================
REM  书中行·命运引擎 — Windows 可执行版本构建脚本
REM  用法：先 pip install -r requirements.txt 与 pyinstaller，再于项目根运行本脚本
REM  产物：dist\FateEngine\FateEngine.exe（双击即用，自动打开浏览器）
REM  静态打包：assets/（规则、角色、提示词、世界书、数据）与 Vue 生产前端；
REM  运行数据由程序在 exe 同级 var/ 下自动创建
REM ============================================================
cd /d "%~dp0\.."

call npm --prefix frontend install
if errorlevel 1 exit /b 1
call npm --prefix frontend run build
if errorlevel 1 exit /b 1

REM 先手动清理旧产物（避免 --clean 在某些环境下触发删改问题）
if exist build\pkg rmdir /s /q build\pkg
if exist dist rmdir /s /q dist

pyinstaller --noconfirm --onedir --name FateEngine ^
  --collect-all gradio --collect-all gradio_client ^
  --collect-all openai --collect-all fastapi --collect-all uvicorn --collect-all multipart ^
  --collect-all httpx --collect-all pydantic ^
  --collect-all safehttpx --collect-all groovy ^
  --add-data "assets;assets" ^
  --add-data "frontend/dist;frontend/dist" ^
  --hidden-import core --hidden-import core.server --hidden-import core.app --hidden-import core.fate_engine ^
  --hidden-import core.engine --hidden-import core.api --hidden-import core.ui --hidden-import core.memory --hidden-import core.lore --hidden-import core.prompts ^
  --hidden-import core.engine.chapter_tools --hidden-import core.engine.plot_summary --hidden-import core.engine.anchor_distiller ^
  --hidden-import core.engine.ledger --hidden-import core.engine.persistence --hidden-import core.engine.runtime_mechanics ^
  --workpath build\pkg --distpath dist ^
  run_app.py

echo.
echo 构建完成：dist\FateEngine\FateEngine.exe
echo 将整个 dist\FateEngine 文件夹拷贝给使用者即可（内含规则、角色模型、提示词与世界书）。
