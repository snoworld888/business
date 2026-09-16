@echo off
REM ============================================================
REM TrackCompass 后端启动（Windows CMD）
REM 用法：双击本文件，或在项目根目录执行 scripts\start-backend.cmd
REM
REM 绑定地址：默认 0.0.0.0（局域网可访问）。
REM 仅本机使用可先执行 set HOST=127.0.0.1 再运行本脚本。
REM ============================================================
setlocal
set PY=C:\Users\PC\.workbuddy\binaries\python\envs\trackcompass\Scripts\python.exe
if not defined HOST set HOST=0.0.0.0
if not defined PORT set PORT=8020
cd /d "%~dp0..\backend"

if not exist "%PY%" (
  echo [ERROR] 未找到虚拟环境 %PY%
  echo         请先执行：
  echo         "C:\Users\PC\.workbuddy\binaries\python\versions\3.13.12\python.exe" -m venv "C:\Users\PC\.workbuddy\binaries\python\envs\trackcompass"
  echo         "%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  pause
  exit /b 1
)

echo [1/2] 初始化数据库与种子数据...
"%PY%" -c "from app.main import bootstrap; print(bootstrap(seed=True))"

set LAN_IP=
for /f "usebackq delims=" %%i in (`"%PY%" "%~dp0lan_ip.py"`) do set LAN_IP=%%i

echo [2/2] 启动服务（绑定 %HOST%:%PORT%）
echo       UI    本机     http://127.0.0.1:%PORT%/
if defined LAN_IP echo       UI    局域网   http://%LAN_IP%:%PORT%/
if defined LAN_IP echo       API   局域网   http://%LAN_IP%:%PORT%/docs
echo       登录  admin@demo.com / TrackCompass@2026
echo.
echo       注意：若未执行过 frontend\build，UI 不可访问。
echo             可执行 scripts\build-frontend.cmd 构建，或设置 SERVE_FRONTEND=false
echo             后改用 scripts\start-frontend.cmd 以开发模式运行前端。
echo.
if not "%HOST%"=="127.0.0.1" (
  echo       [!] 已开放局域网访问。若同网段机器打不开，需放行入站 TCP %PORT%（管理员执行一次）：
  echo           netsh advfirewall firewall add rule name="TrackCompass %PORT%" dir=in action=allow protocol=TCP localport=%PORT%
  echo.
)
"%PY%" -m uvicorn app.main:app --host %HOST% --port %PORT% --reload
endlocal
