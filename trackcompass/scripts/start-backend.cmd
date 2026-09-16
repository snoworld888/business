@echo off
REM ============================================================
REM TrackCompass 后端启动（Windows CMD）
REM 用法：双击本文件，或在项目根目录执行 scripts\start-backend.cmd
REM ============================================================
setlocal
set PY=C:\Users\PC\.workbuddy\binaries\python\envs\trackcompass\Scripts\python.exe
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

echo [2/2] 启动服务
echo       UI    http://127.0.0.1:8020/
echo       API   http://127.0.0.1:8020/docs
echo       登录  admin@demo.com / TrackCompass@2026
echo.
echo       注意：若未执行过 frontend\build，UI 不可访问。
echo             可执行 scripts\build-frontend.cmd 构建，或设置 SERVE_FRONTEND=false
echo             后改用 scripts\start-frontend.cmd 以开发模式运行前端。
echo.
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8020 --reload
endlocal
