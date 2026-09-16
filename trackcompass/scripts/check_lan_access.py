"""局域网可访问性自检：验证服务从「本机回环」与「局域网地址」两个来源都可用。

用法：
    python scripts/check_lan_access.py [端口]      # 默认 8020

为什么要单独校验「两个来源」：`127.0.0.1:8020` 与 `192.168.x.x:8020` 在浏览器里是
**两个不同的站点来源**（origin），登录态分别存储，静态资源也各走一遍。
只测本机地址就宣称「局域网可用」是不成立的。

检查项（本机 / 局域网 各跑一遍）：

| # | 检查 | 说明 |
| --- | --- | --- |
| 1 | 监听地址 | 必须绑在 `0.0.0.0`（绑 `127.0.0.1` 时局域网必然连不上） |
| 2 | `/api/v1/health` | 服务存活 |
| 3 | `/` | 前端入口 HTML，且能取到 `index-*.js` 引用 |
| 4 | 首页引用的静态资源 | 真正能加载界面（不是只有 HTML 壳） |
| 5 | `/api/v1/auth/login` | 登录可用 |
| 6 | `/api/v1/auth/me` | 带 token 的鉴权接口可用 |

⚠️ 本脚本在本机运行，**访问自己的局域网 IP 可能绕开入站防火墙**，
因此「局域网」一列全绿只能证明服务绑对了、路由是通的。
要证明别的机器能连，请在另一台机器（或 WSL）上执行：

    curl -s -o /dev/null -w '%{http_code}\\n' http://<本机IP>:8020/api/v1/health
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lan_ip import lan_ip  # noqa: E402

PORT = sys.argv[1] if len(sys.argv) > 1 else "8020"
EMAIL = "admin@demo.com"
PASSWORD = "TrackCompass@2026"

PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    icon = "✅" if condition else "❌"
    print(f"{icon} {name}{(' | ' + detail) if detail else ''}")


def skip(name: str, detail: str = "") -> None:
    SKIP.append(name)
    print(f"⚪ {name}{(' | ' + detail) if detail else ''}")


def listening_addrs(port: str) -> list[str]:
    """从 netstat 里解析该端口的监听地址（Windows / Linux 通用）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, errors="replace", timeout=20,
        ).stdout
    except Exception:
        return []
    addrs: list[str] = []
    for line in out.splitlines():
        if "LISTEN" not in line.upper():
            continue
        m = re.search(rf"([0-9a-fA-F:.\[\]]+):{re.escape(port)}\s", line)
        if m:
            addrs.append(m.group(1).strip("[]"))
    return sorted(set(addrs))


def probe(origin: str, label: str) -> None:
    """在一个 origin 上跑完整链路。"""
    print(f"\n--- {label}：{origin} ---")
    client = httpx.Client(
        base_url=origin, timeout=60.0, trust_env=False, follow_redirects=True,
    )
    try:
        # 2. 健康检查
        try:
            r = client.get("/api/v1/health")
            ok = r.status_code == 200
            detail = f"HTTP {r.status_code}"
            if ok:
                d = r.json().get("data", {})
                detail += f" | env={d.get('env')} llm={d.get('llm', {}).get('provider')}"
            check(f"{label} · 健康检查可达", ok, detail)
        except Exception as e:
            check(f"{label} · 健康检查可达", False, f"{type(e).__name__}: {e}")
            return

        # 3. 前端入口
        try:
            r = client.get("/")
            html = r.text
            assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
            check(
                f"{label} · 前端入口可达",
                r.status_code == 200 and "赛道罗盘" in html,
                f"HTTP {r.status_code} · 引用 {len(assets)} 个静态资源",
            )
        except Exception as e:
            check(f"{label} · 前端入口可达", False, f"{type(e).__name__}: {e}")
            return

        # 4. 静态资源（真正能渲染界面）
        bad: list[str] = []
        for a in assets[:6]:
            try:
                ra = client.get(a)
                if ra.status_code != 200 or not ra.content:
                    bad.append(f"{a}={ra.status_code}")
            except Exception as e:
                bad.append(f"{a}={type(e).__name__}")
        check(
            f"{label} · 静态资源可加载",
            bool(assets) and not bad,
            f"抽检 {min(len(assets), 6)} 个" + (f" · 异常 {bad}" if bad else "全部 200"),
        )

        # 5. 登录
        token = None
        try:
            r = client.post(
                "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD},
            )
            ok = r.status_code == 200
            if ok:
                token = r.json()["data"]["access_token"]
            check(f"{label} · 登录接口可用", ok, f"HTTP {r.status_code}")
        except Exception as e:
            check(f"{label} · 登录接口可用", False, f"{type(e).__name__}: {e}")

        # 6. 鉴权接口
        if token:
            try:
                r = client.get(
                    "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"},
                )
                who = r.json().get("data", {}).get("email") if r.status_code == 200 else ""
                check(
                    f"{label} · 鉴权接口可用（带 token）",
                    r.status_code == 200,
                    f"HTTP {r.status_code} · {who}",
                )
            except Exception as e:
                check(f"{label} · 鉴权接口可用（带 token）", False, f"{type(e).__name__}: {e}")
    finally:
        client.close()


def main() -> int:
    ip = lan_ip()
    print(f"端口：{PORT}")
    print(f"本机局域网 IP：{ip or '（探测失败）'}")

    # 1. 监听地址
    addrs = listening_addrs(PORT)
    if not addrs:
        check("监听地址（netstat 可解析）", False, f"未发现 {PORT} 端口的监听记录")
    else:
        wildcard = any(a in ("0.0.0.0", "::") for a in addrs)
        check(
            "绑定 0.0.0.0（局域网可访问）",
            wildcard,
            f"实际监听 {', '.join(addrs)}"
            + ("" if wildcard else " ⇒ 需去掉 --host 127.0.0.1 的限制"),
        )
        if not wildcard:
            skip("局域网来源校验", "服务仅绑回环，局域网必然连不上")

    probe(f"http://127.0.0.1:{PORT}", "本机回环")
    if ip and (not addrs or any(a in ("0.0.0.0", "::") for a in addrs)):
        probe(f"http://{ip}:{PORT}", "局域网地址")
    print(
        "\n⚠️ 本机访问自己的局域网 IP 可能绕开入站防火墙。"
        "若同网段机器仍打不开，放行入站端口（管理员执行一次）：\n"
        f'   netsh advfirewall firewall add rule name="TrackCompass {PORT}" '
        f"dir=in action=allow protocol=TCP localport={PORT}"
    )

    total = len(PASS) + len(FAIL)
    print(f"\n{'=' * 60}")
    print(f"通过 {len(PASS)}/{total}" + (f" · 跳过 {len(SKIP)}" if SKIP else ""))
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    print("=" * 60)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
