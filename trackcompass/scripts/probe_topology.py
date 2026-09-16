"""网络定位脚本：判断 Hermes / Ollama / vLLM 分别跑在哪台机器上。

用途：回答「把模型调用指向远端能否真正释放本地显存」这一决策问题。
纯只读探测（TCP connect + HTTP GET），不做任何认证/会话/写操作。

用法：python scripts/probe_topology.py [remote_host]
"""

from __future__ import annotations

import socket
import sys
import time

import httpx

REMOTE = sys.argv[1] if len(sys.argv) > 1 else "192.168.2.192"

# 待探测端口：本地与远端都测，用于判断「谁在哪」
PORTS = [
    (11434, "Ollama", "/api/tags"),
    (18081, "vLLM(voice-interop)", "/v1/models"),
    (8001, "ab-dashboard", "/"),
    (9219, "Hermes 智能体", "/"),
    (8000, "常见 FastAPI", "/"),
    (3080, "dsh web", "/"),
    (2222, "SSH", None),
]


def local_ip_for(host: str, port: int = 9219) -> str:
    """取得访问该目标时本机使用的源 IP —— 据此判断目标是否为本机。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, port))
        return s.getsockname()[0]
    except Exception:
        return "n/a"
    finally:
        s.close()


def tcp_probe(host: str, port: int, timeout: float = 3.0) -> tuple[bool, float]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.perf_counter() - t0) * 1000, 1)
    except Exception:
        return False, round((time.perf_counter() - t0) * 1000, 1)


def http_get(url: str, timeout: float = 6.0) -> tuple[int, str]:
    try:
        with httpx.Client(trust_env=False, timeout=timeout, follow_redirects=False) as c:
            r = c.get(url)
            body = r.text[:160].replace("\n", " ")
            return r.status_code, body
    except Exception as exc:
        return 0, type(exc).__name__


def ollama_identity(host: str) -> str:
    """Ollama 版本 + 模型清单指纹，用于判断两个端点是同一实例还是不同实例。"""
    try:
        with httpx.Client(trust_env=False, timeout=6.0) as c:
            ver = c.get(f"http://{host}:11434/api/version").json().get("version", "?")
            tags = c.get(f"http://{host}:11434/api/tags").json().get("models", [])
            names = sorted(m["name"] for m in tags)
            return f"version={ver} models={len(names)} [{', '.join(names[:4])}…]"
    except Exception as exc:
        return f"不可用 ({type(exc).__name__})"


def main() -> int:
    print("=" * 74)
    print(" 网络定位：模型服务与智能体分别跑在哪台机器")
    print("=" * 74)

    src = local_ip_for(REMOTE)
    print(f"\n本机访问 {REMOTE} 时使用的源 IP : {src}")
    print(f"目标主机                        : {REMOTE}")

    same_host = src == REMOTE
    if same_host:
        print("  ⇒ 源 IP 与目标相同，目标即本机（WSL/本机网卡）")
    else:
        print("  ⇒ 源 IP 与目标不同 ⇒ 目标在【另一台机器】上")

    print("\n---- 端口可达性对照（本机 vs 远端）----")
    print(f"{'端口':<7}{'服务':<22}{'本机(127.0.0.1)':<22}{f'远端({REMOTE})':<22}")
    print("-" * 74)
    for port, name, path in PORTS:
        ok_l, ms_l = tcp_probe("127.0.0.1", port)
        ok_r, ms_r = tcp_probe(REMOTE, port)
        ls = f"{'✅' if ok_l else '❌'} {ms_l}ms"
        rs = f"{'✅' if ok_r else '❌'} {ms_r}ms"
        print(f"{port:<7}{name:<22}{ls:<22}{rs:<22}")

    print("\n---- Ollama 实例指纹（判断是否同一实例）----")
    local_fp = ollama_identity("127.0.0.1")
    print(f"  127.0.0.1 : {local_fp}")
    if tcp_probe(REMOTE, 11434)[0]:
        remote_fp = ollama_identity(REMOTE)
        print(f"  {REMOTE} : {remote_fp}")
        print(f"  ⇒ 两个端点是{'【同一实例】' if local_fp == remote_fp else '【不同实例】'}")
    else:
        print(f"  {REMOTE} : 11434 未开放（该机没有对外提供 Ollama）")

    print("\n---- 结论 ----")
    if same_host:
        print("  ⚠️ Hermes 与本地 Ollama 在【同一台机器】。")
        print("     把模型调用改走 Hermes 只会把负载从 Ollama 挪到另一个进程，")
        print("     推理仍在同一块 GPU 上 ⇒ 【显存零收益】。")
    else:
        print(f"  ✅ {REMOTE} 是【独立于本机的另一台机器】。")
        print("     若该机提供模型推理能力，把 LLM_BASE_URL 指向它即可")
        print("     把推理搬离本机 GPU ⇒ 【本地显存可释放】。")
        print("     注意：优先找它的 OpenAI 兼容端口（如 /v1/models），")
        print("     而不是走 Hermes 的会话协议（登录/ticket/WebSocket 开销大且非高并发友好）。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
