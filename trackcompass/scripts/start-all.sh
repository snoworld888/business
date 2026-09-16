#!/usr/bin/env bash
# ============================================================
# TrackCompass 一键启动（Git Bash / WSL）—— 单端口模式
# 前端构建产物由后端同端口托管，只需一个进程：
#     UI   http://127.0.0.1:8020/
#     API  http://127.0.0.1:8020/docs
# 停止：Ctrl+C
#
# 若需要前端热更新（改前端代码时），改用：
#     scripts/start-backend.cmd   +   scripts/start-frontend.cmd
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="/c/Users/PC/.workbuddy/binaries/python/envs/trackcompass/Scripts/python.exe"
PORT="${PORT:-8020}"

# ---- 0. 前置检查 ----
if [ ! -x "$PY" ]; then
  echo "[ERROR] 未找到虚拟环境：$PY"
  echo "        请先创建："
  echo "        \"C:/Users/PC/.workbuddy/binaries/python/versions/3.13.12/python.exe\" -m venv \"C:/Users/PC/.workbuddy/binaries/python/envs/trackcompass\""
  echo "        \"$PY\" -m pip install -r \"$ROOT/backend/requirements.txt\" -i https://pypi.tuna.tsinghua.edu.cn/simple"
  exit 1
fi

if [ -z "${SKIP_BUILD:-}" ]; then
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    echo "[1/4] 安装前端依赖（首次约 5-10 分钟）..."
    (cd "$ROOT/frontend" && npm install --registry=https://registry.npmmirror.com)
  else
    echo "[1/4] 前端依赖已存在，跳过安装"
  fi

  echo "[2/4] 构建前端产物..."
  (cd "$ROOT/frontend" && npm run build)
else
  echo "[1/4][2/4] SKIP_BUILD=1，跳过前端依赖与构建"
fi

echo "[3/4] 初始化数据库与种子数据..."
(cd "$ROOT/backend" && "$PY" -c "from app.main import bootstrap; print(bootstrap(seed=True))")

echo "[4/4] 启动服务 http://127.0.0.1:${PORT}"
echo ""
echo "==============================================="
echo "  赛道罗盘 TrackCompass"
echo "  UI    http://127.0.0.1:${PORT}/"
echo "  API   http://127.0.0.1:${PORT}/docs"
echo "  登录  admin@demo.com / TrackCompass@2026"
echo "  停止  Ctrl+C"
echo "==============================================="
cd "$ROOT/backend"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
