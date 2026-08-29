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

REM core.engine 等子包用 __getattr__ 惰性导入，静态分析追踪不到，
REM 必须 collect-submodules 全量收编，否则运行到对应机制才报缺模块。
pyinstaller --noconfirm --onedir --name FateEngine ^
  --collect-all gradio --collect-all gradio_client ^
  --collect-all openai --collect-all fastapi --collect-all uvicorn --collect-all multipart ^
  --collect-all httpx --collect-all pydantic ^
  --collect-all safehttpx --collect-all groovy ^
  --collect-submodules core.engine --collect-submodules core.api ^
  --collect-submodules core.ui --collect-submodules core.memory ^
  --collect-submodules core.lore --collect-submodules core.prompts ^
  --add-data "assets;assets" ^
  --add-data "frontend/dist;frontend/dist" ^
  --hidden-import core --hidden-import core.server --hidden-import core.app --hidden-import core.fate_engine ^
  --workpath build\pkg --distpath dist ^
  run_app.py

echo.
echo 构建完成：dist\FateEngine\FateEngine.exe
echo 将整个 dist\FateEngine 文件夹拷贝给使用者即可（内含规则、角色模型、提示词与世界书）。
