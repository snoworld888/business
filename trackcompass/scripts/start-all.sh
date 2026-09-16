#!/usr/bin/env bash
# ============================================================
# TrackCompass 一键启动（Git Bash / WSL）—— 单端口模式
# 前端构建产物由后端同端口托管，只需一个进程：
#     UI   本机 http://127.0.0.1:8020/
#     UI   局域网 http://<本机IP>:8020/
#     API  http://<本机IP>:8020/docs
# 停止：Ctrl+C
#
# 绑定地址可用 HOST 覆盖（默认 0.0.0.0 = 局域网可访问）：
#     HOST=127.0.0.1 bash scripts/start-all.sh   # 仅本机可访问
#
# 若需要前端热更新（改前端代码时），改用：
#     scripts/start-backend.cmd   +   scripts/start-frontend.cmd
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="/c/Users/PC/.workbuddy/binaries/python/envs/trackcompass/Scripts/python.exe"
PORT="${PORT:-8020}"
HOST="${HOST:-0.0.0.0}"

# 探测本机对外 IP：UDP connect 不实际发包，不需要外网可达。
# ⚠️ 必须把脚本路径转成 Windows 风格：`$ROOT` 是 MSYS 路径（/d/...），
#    直接传给 python.exe 会被解析成 `C:\d\...` 而报「文件不存在」（实测踩坑）。
LAN_SCRIPT="$ROOT/scripts/lan_ip.py"
if command -v cygpath >/dev/null 2>&1; then
  LAN_SCRIPT="$(cygpath -w "$LAN_SCRIPT")"
fi
LAN_IP="$("$PY" "$LAN_SCRIPT" 2>/dev/null || true)"

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

echo "[4/4] 启动服务（绑定 ${HOST}:${PORT}）"
echo ""
echo "==============================================="
echo "  赛道罗盘 TrackCompass"
echo "  UI    本机     http://127.0.0.1:${PORT}/"
if [ -n "$LAN_IP" ]; then
  echo "  UI    局域网   http://${LAN_IP}:${PORT}/"
  echo "  API   局域网   http://${LAN_IP}:${PORT}/docs"
fi
echo "  登录  admin@demo.com / TrackCompass@2026"
echo "  停止  Ctrl+C"
echo "==============================================="
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  echo "  ⚠ 已开放局域网访问：同网段机器可直接打开上面的局域网地址。"
  echo "    若本机防火墙拦截，需放行入站 TCP ${PORT}（管理员执行一次）:"
  echo "      netsh advfirewall firewall add rule name=\"TrackCompass ${PORT}\" ^"
  echo "        dir=in action=allow protocol=TCP localport=${PORT}"
  echo "    仅本机使用请改：HOST=127.0.0.1 bash scripts/start-all.sh"
  echo "==============================================="
fi
cd "$ROOT/backend"
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
