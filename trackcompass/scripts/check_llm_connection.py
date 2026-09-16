"""大模型 / 智能体接入状态诊断。

回答三个问题，全部基于实测而非配置推断：
  1. 当前系统走的是真实大模型还是离线规则引擎？
  2. 智能体层是否在真实运行？
  3. 本机有哪些模型服务可用，切过去需要改什么？

用法：
    # 诊断当前配置
    python scripts/check_llm_connection.py

    # 临时用某个 provider 实测一次真实推理（不改动 .env）
    python scripts/check_llm_connection.py --provider ollama --model qwen2.5:7b
    python scripts/check_llm_connection.py --provider openai --base-url https://api.openai.com/v1 --api-key sk-xxx --model gpt-4o-mini
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

CANDIDATES = [
    ("Ollama (本机 WSL)", "http://localhost:11434", "/api/tags"),
    ("vLLM (本机 WSL)", "http://localhost:18081", "/v1/models"),
    ("OpenAI 官方", "https://api.openai.com/v1", "/models"),
]

# 外部「会话执行型」智能体服务：仅做 TCP 连通性探测，
# 绝不做登录 / 申请 ticket / 建会话 / 提交 prompt 等有副作用的操作。
EXTERNAL_AGENT_SERVICES = [
    ("Hermes 智能体服务 (hermes-native-serve)", "192.168.2.192", 9219),
]


def probe_tcp(host: str, port: int, timeout: float = 5.0) -> dict:
    """只读 TCP 连通性探测。"""
    import socket

    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__,
                "latency_ms": round((time.perf_counter() - t0) * 1000)}


def probe_local_services() -> list[dict]:
    """只读探测本机与公网的模型服务，不做任何启动/停止/重启操作。"""
    out = []
    with httpx.Client(trust_env=False, timeout=6.0) as c:
        for name, base, path in CANDIDATES:
            t0 = time.perf_counter()
            try:
                r = c.get(f"{base}{path}")
                ok = r.status_code == 200
                models = []
                if ok:
                    data = r.json()
                    if "models" in data:  # ollama /api/tags
                        models = [m["name"] for m in data["models"]]
                    elif "data" in data:  # openai 兼容 /v1/models
                        models = [m["id"] for m in data["data"]]
                out.append({
                    "name": name, "base": base, "ok": ok,
                    "status": r.status_code, "models": models,
                    "latency_ms": round((time.perf_counter() - t0) * 1000),
                })
            except Exception as exc:
                out.append({"name": name, "base": base, "ok": False,
                            "status": 0, "models": [], "error": type(exc).__name__,
                            "latency_ms": round((time.perf_counter() - t0) * 1000)})
    return out


def report_config() -> dict:
    from app.core.config import settings

    return {
        "provider": settings.LLM_PROVIDER,
        "base_url": settings.LLM_BASE_URL,
        "api_key_set": bool(settings.LLM_API_KEY),
        "models": {
            "simple": settings.LLM_MODEL_SIMPLE,
            "complex": settings.LLM_MODEL_COMPLEX,
            "private": settings.LLM_MODEL_PRIVATE,
        },
        "embedding_backend": settings.EMBEDDING_BACKEND,
        "embedding_model": settings.EMBEDDING_MODEL,
        "env_file": (BACKEND / ".env").exists(),
    }


def real_inference_test() -> dict:
    """走 LLMService 真实发一次请求，验证链路是否打通。"""
    from app.services.llm import llm

    t0 = time.perf_counter()
    out = llm.complete(
        "用一句话说明什么是赛道罗盘。",
        system="你是简洁的助手，只输出一句中文。",
        complexity="complex",
        rule_engine=lambda: {"rule_engine_output": "（规则引擎兜底，未调用大模型）"},
    )
    cost = round((time.perf_counter() - t0) * 1000)
    meta = out.get("_meta", {})
    return {
        "meta": meta,
        "total_latency_ms": cost,
        "used_real_llm": meta.get("provider") not in ("rule", None)
                         and not str(meta.get("provider", "")).endswith("-fallback"),
        "output_preview": str(out)[:400],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", help="临时覆盖 LLM_PROVIDER (rule|openai|ollama)")
    ap.add_argument("--base-url", help="临时覆盖 LLM_BASE_URL")
    ap.add_argument("--api-key", help="临时覆盖 LLM_API_KEY")
    ap.add_argument("--model", help="临时覆盖 LLM_MODEL_COMPLEX")
    ap.add_argument("--skip-inference", action="store_true", help="只探测服务，不做推理")
    args = ap.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.base_url:
        os.environ["LLM_BASE_URL"] = args.base_url
    if args.api_key:
        os.environ["LLM_API_KEY"] = args.api_key
    if args.model:
        os.environ["LLM_MODEL_COMPLEX"] = args.model

    overridden = any([args.provider, args.base_url, args.api_key, args.model])

    print("=" * 68)
    print(" 赛道罗盘 —— 大模型 / 智能体 接入状态诊断")
    print("=" * 68)

    cfg = report_config()
    print(f"\n[1] 当前配置{'（含命令行临时覆盖）' if overridden else ''}")
    print(f"    LLM_PROVIDER      : {cfg['provider']}")
    print(f"    LLM_BASE_URL      : {cfg['base_url']}")
    print(f"    LLM_API_KEY       : {'已设置' if cfg['api_key_set'] else '未设置'}")
    print(f"    模型路由          : 简单={cfg['models']['simple']} / "
          f"复杂={cfg['models']['complex']} / 私有={cfg['models']['private']}")
    print(f"    Embedding         : {cfg['embedding_backend']} / {cfg['embedding_model']}")
    print(f"    backend/.env      : {'存在' if cfg['env_file'] else '不存在（使用 config.py 默认值）'}")

    print("\n[2] 智能体层")
    from app.agents import AGENT_CATALOG, Orchestrator, default_workflow

    wf = default_workflow()
    print(f"    注册 Agent        : {len(AGENT_CATALOG)} 个")
    print(f"    编排层数          : {len(wf.layers)} 层，"
          f"并行层={sum(1 for x in wf.layers if len(x) > 1)}")
    print(f"    最大反馈循环      : {wf.max_loops} 次")
    print(f"    执行器类型        : {Orchestrator.__name__}（线程池并行 + 有界回灌）")

    print("\n[3] 本机 / 公网模型服务探测（只读，不做运维操作）")
    services = probe_local_services()
    for s in services:
        flag = "✅ 可用" if s["ok"] else "❌ 不可达"
        print(f"    {flag}  {s['name']:18} {s['base']:34} "
              f"{s.get('status', 0)}  {s['latency_ms']}ms")
        if s["models"]:
            head = ", ".join(s["models"][:8])
            more = f" …共 {len(s['models'])} 个" if len(s["models"]) > 8 else ""
            print(f"          模型: {head}{more}")

    print("\n[3b] 外部会话执行型智能体服务（只读 TCP 探测，不做登录/建会话）")
    for name, host, port in EXTERNAL_AGENT_SERVICES:
        r = probe_tcp(host, port)
        flag = "✅ 在线" if r["ok"] else "❌ 不可达"
        print(f"    {flag}  {name}  {host}:{port}  {r['latency_ms']}ms"
              + ("" if r["ok"] else f"  ({r.get('error')})"))
    print("    说明：Hermes 属「会话 + 工作目录 + 文件 IO + Skill」执行型运行时，")
    print("          与本项目「结构化分析型 Agent」能力定位不同；当前代码零引用。")

    print("\n[4] 真实推理测试（走 LLMService 完整链路）")
    if not args.skip_inference:
        result = real_inference_test()
        meta = result["meta"]
        print(f"    实际 provider     : {meta.get('provider')}")
        print(f"    实际 model        : {meta.get('model')}")
        print(f"    LLM 用时          : {meta.get('latency_ms')}ms "
              f"（含 HTTP 往返；总耗时 {result['total_latency_ms']}ms）")
        print(f"    Prompt 字符数     : {meta.get('prompt_chars')}")
        print(f"    是否调用了大模型  : {'是 ✅' if result['used_real_llm'] else '否 ❌（走了规则引擎）'}")
        print(f"    输出片段          : {result['output_preview'][:220]}")

    print("\n[5] 结论")
    if cfg["provider"] == "rule":
        print("    ❗ 当前未接入大模型 —— 七个智能体运行在【确定性规则引擎】上。")
        print("       智能体编排本身是真实运行的，但「大脑」是算法而非大模型。")
        reachable = [s for s in services if s["ok"]]
        if reachable:
            best = reachable[0]
            print(f"       ✅ 检测到可用模型服务：{best['name']} @ {best['base']}")
            if best["models"]:
                print(f"          可直接使用：{', '.join(best['models'][:5])}")
            print("          切换到真实大模型（一行配置，无需改代码）：")
            print("            cd backend && copy .env.example .env")
            print("            # 编辑 .env：")
            print("            LLM_PROVIDER=ollama")
            print("            LLM_BASE_URL=http://localhost:11434")
            print("            LLM_MODEL_COMPLEX=qwen3.8:27b")
            print("            LLM_MODEL_SIMPLE=qwen2.5:7b")
            print("            EMBEDDING_BACKEND=ollama")
            print("            EMBEDDING_MODEL=bge-m3")
            print("            # 重启后端后再次运行本脚本验证")
        else:
            print("       ❌ 未检测到任何可用模型服务，需先准备一个 LLM 端点。")
    else:
        ok = None
        if not args.skip_inference:
            ok = real_inference_test()["used_real_llm"]
        if ok:
            print("    ✅ 已接入真实大模型，Agent 的 rule() 实现自动降级为校验基线。")
        else:
            print("    ⚠️ 配置为远端 provider 但推理失败，已自动回退规则引擎。")
            print("       请检查 BASE_URL / API_KEY / 模型名，或该服务是否可达。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
