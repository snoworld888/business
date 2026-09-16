@echo off
REM ============================================================
REM TrackCompass 前端启动（Windows CMD）
REM 用法：双击本文件，或在项目根目录执行 scripts\start-frontend.cmd
REM ============================================================
setlocal
set PY=C:\Users\PC\.workbuddy\binaries\python\envs\trackcompass\Scripts\python.exe
cd /d "%~dp0..\frontend"

if not exist node_modules (
  echo [1/2] 首次运行，安装依赖（约 5-10 分钟）...
  call npm install --registry=https://registry.npmmirror.com
) else (
  echo [1/2] 依赖已存在，跳过安装
)

echo [2/2] 启动开发服务器（局域网可访问）
echo       本机     http://127.0.0.1:5180
for /f "usebackq delims=" %%i in (`"%PY%" "%~dp0lan_ip.py" 2^>nul`) do echo       局域网   http://%%i:5180
call npm run dev
endlocal
