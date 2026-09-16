@echo off
REM ============================================================
REM 构建前端产物（由后端同端口托管）
REM 用法：scripts\build-frontend.cmd
REM ============================================================
setlocal
cd /d "%~dp0..\frontend"

if not exist node_modules (
  echo [1/3] 安装依赖（首次约 5-10 分钟）...
  call npm install --registry=https://registry.npmmirror.com
) else (
  echo [1/3] 依赖已存在，跳过安装
)

echo [2/3] 类型检查...
call npx tsc --noEmit
if errorlevel 1 (
  echo [ERROR] 类型检查未通过，已中止构建
  pause
  exit /b 1
)

echo [3/3] 构建...
call npm run build
if errorlevel 1 (
  echo [ERROR] 构建失败
  pause
  exit /b 1
)

echo.
echo 构建完成：%~dp0..\frontend\dist
echo 重启后端后访问 http://127.0.0.1:8020/
endlocal
