"""RAGFlow 接入自查与激活工具。

用途：把「全量替换为 RAGFlow」从配置到真机验证，压缩成三条命令。

    # 1) 列出 RAGFlow 已有知识库（确认 API Key 可用，并挑一个 dataset）
    python scripts/check_ragflow.py --list-datasets

    # 2) 建一个 TrackCompass 专用 dataset（已存在则直接返回其 id，不会重复建）
    python scripts/check_ragflow.py --ensure-dataset trackcompass-signals

    # 3) 把本地信号同步过去并做一次真实检索（打印证据链）
    python scripts/check_ragflow.py --dataset-id <上一步输出的 id> --smoke

API Key 来源优先级：`--api-key` 参数 > 环境变量 `RAGFLOW_API_KEY` > `backend/.env`。
本脚本**只读写 TrackCompass 自己的 dataset**，不会改动 RAGFlow 中已存在的其他知识库。

⚠️ 前置：RAGFlow 的嵌入模型必须与本机 Ollama 可用（本机实测为 `bge-m3:latest@Ollama`）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.ragflow import RAGFlowEngine  # noqa: E402

DEFAULT_DATASET_NAME = "trackcompass-signals"
#: 与 RAGFlow 侧实际配置一致（`<model>@<factory>`）；本机实测 RAGFlow 用的是 Ollama 的 bge-m3
DEFAULT_EMBEDDING_MODEL = "bge-m3:latest@Ollama"


def _client(base_url: str, api_key: str, timeout: int = 30) -> httpx.Client:
    # trust_env=False 避免本机 http_proxy 污染请求
    return httpx.Client(base_url=f"{base_url.rstrip('/')}/api/v1",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=timeout, trust_env=False)


def _resolve_key(args: argparse.Namespace) -> str:
    return args.api_key or os.environ.get("RAGFLOW_API_KEY") or settings.RAGFLOW_API_KEY or ""


def cmd_list_datasets(base: str, key: str) -> int:
    try:
        with _client(base, key) as c:
            r = c.get("/datasets", params={"page": 1, "page_size": 100})
            r.raise_for_status()
            body = r.json()
    except Exception as exc:
        print(f"  ❌ 调用失败：{exc}")
        print("     请确认 API Key 正确（RAGFlow UI → 右上角头像 → API）")
        return 1

    if body.get("code") not in (0, None):
        print(f"  ❌ RAGFlow 返回 code={body.get('code')} message={body.get('message')}")
        return 1

    rows = (body.get("data") or [])
    if isinstance(rows, dict):
        rows = rows.get("docs") or []
    if not rows:
        print("  （当前没有任何知识库）")
        return 0

    print(f"  RAGFlow 共 {len(rows)} 个知识库：")
    print(f"  {'name':<32}{'doc':>6}{'chunk':>8}  embedding_model")
    print("  " + "-" * 78)
    for d in rows:
        print(f"  {str(d.get('name'))[:30]:<32}{d.get('document_count', 0):>6}"
              f"{d.get('chunk_count', 0):>8}  {d.get('embedding_model', '')}")
    print("\n  ⚠️ 接入时请新建专用 dataset，不要复用上表中其他项目的知识库。")
    return 0


def cmd_ensure_dataset(base: str, key: str, name: str, model: str) -> int:
    with _client(base, key) as c:
        r = c.get("/datasets", params={"name": name, "page": 1, "page_size": 10})
        r.raise_for_status()
        rows = (r.json().get("data") or [])
        if isinstance(rows, dict):
            rows = rows.get("docs") or []
        exists = [d for d in rows if d.get("name") == name]
        if exists:
            print(f"  ✅ 已存在，直接复用：dataset_id = {exists[0]['id']}")
            print(f"     文档 {exists[0].get('document_count', 0)} 篇 / "
                  f"chunk {exists[0].get('chunk_count', 0)} 个")
            return 0

        r = c.post("/datasets", json={
            "name": name,
            "embedding_model": model,
            "chunk_method": "naive",
            "permission": "me",
        })
        r.raise_for_status()
        body = r.json()
    if body.get("code") not in (0, None):
        print(f"  ❌ 创建失败 code={body.get('code')} message={body.get('message')}")
        print(f"     提示：embedding_model 必须是 RAGFlow 已配置的模型，如 `{model}`")
        return 1
    ds_id = (body.get("data") or {}).get("id") if isinstance(body.get("data"), dict) else None
    print(f"  ✅ 已创建：dataset_id = {ds_id}")
    print(f"     嵌入模型 = {model}（与 RAGFlow 现有知识库一致）")
    return 0


def cmd_smoke(base: str, key: str, dataset_id: str, limit: int, query: str) -> int:
    from app.core.db import SessionLocal
    from app.models import Signal
    from app.services.rag import RetrievedDoc

    db = SessionLocal()
    try:
        signals = db.query(Signal).limit(limit).all()
        docs = [
            RetrievedDoc(signal_id=s.id, source=s.source, signal_type=s.signal_type,
                         title=s.title, text=s.content or s.title, url=s.url)
            for s in signals
        ]
    finally:
        db.close()
    if not docs:
        print("  ❌ 本地库没有信号，请先初始化种子数据")
        return 1

    eng = RAGFlowEngine(base, key, dataset_id,
                        map_path=str(ROOT / "backend" / "ragflow_index_map.json"),
                        timeout=60, parse_wait_seconds=120)
    print("  [1/3] 就绪判定 …")
    ok, reason = eng.ready()
    print(f"        {ok} / {reason}")
    if not ok:
        return 1

    print(f"  [2/3] 同步 {len(docs)} 条信号（上传 → 异步解析，最长等 120s）…")
    total = eng.index(docs)
    print(f"        映射表已登记 {total} 条")

    print(f"  [3/3] 真实检索：{query!r}")
    hits = eng.retrieve(query, top_k=5)
    if not hits:
        # 关键：必须区分「真的没命中」与「检索本身失败」，否则会掩盖真实故障
        err = getattr(eng, "last_error", "")
        if err:
            print(f"        ❌ 检索调用失败（并非「无命中」）：{err}")
            if "type chat not found" in err or "chat" in err.lower():
                print("        → 该 RAGFlow 租户的默认对话模型未注册。两种解法：")
                print("          a) 保持 RAGFLOW_KEYWORD_EXTRACTION=false（默认，无需对话模型）")
                print("          b) 在 RAGFlow 中把租户默认对话模型改为已注册的模型")
            return 1
        print("        ⚠️ 无命中。若刚上传，解析可能尚未完成，稍后重试即可")
        return 0
    print(f"  {'#':<4}{'类型':<10}{'来源':<14}{'标题':<26}{'融合':>8}{'向量':>8}{'关键词':>8}")
    print("  " + "-" * 78)
    for i, d in enumerate(hits, start=1):
        print(f"  {i:<4}{d.signal_type[:8]:<10}{d.source[:12]:<14}{d.title[:24]:<26}"
              f"{d.score:>8.4f}{d.vector_score:>8.4f}{d.keyword_score:>8.4f}")
    print(f"\n  证据链完整率 = {eng.citation_ratio(hits)}")
    print("  ✅ RAGFlow 检索链路可用。启用方式：backend/.env 设")
    print(f"     RAG_BACKEND=ragflow")
    print(f"     RAGFLOW_API_KEY=<你的 key>")
    print(f"     RAGFLOW_DATASET_ID={dataset_id}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGFlow 接入自查与激活")
    ap.add_argument("--base-url", default=settings.RAGFLOW_BASE_URL)
    ap.add_argument("--api-key", default="")
    ap.add_argument("--list-datasets", action="store_true", help="列出 RAGFlow 已有知识库")
    ap.add_argument("--ensure-dataset", metavar="NAME", default="",
                    help=f"创建（或复用）专用知识库，默认名 {DEFAULT_DATASET_NAME}")
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--dataset-id", default=settings.RAGFLOW_DATASET_ID)
    ap.add_argument("--smoke", action="store_true", help="同步信号并做一次真实检索")
    ap.add_argument("--limit", type=int, default=20, help="smoke 时同步的信号条数")
    ap.add_argument("--query", default="智能质检 缺陷检测")
    args = ap.parse_args()

    key = _resolve_key(args)
    print("== RAGFlow 接入自查 ==")
    print(f"  base_url = {args.base_url}")
    print(f"  api_key  = {'(已提供)' if key else '(未提供 —— 需 --api-key 或环境变量)'}")
    print()

    if not key:
        print("❌ 缺少 API Key。获取方式：RAGFlow UI http://localhost:8060")
        print("   → 右上角头像 → API → 生成/复制 API Key")
        print("   然后：python scripts/check_ragflow.py --api-key <KEY> --list-datasets")
        return 1

    if args.list_datasets:
        return cmd_list_datasets(args.base_url, key)
    if args.ensure_dataset or (not args.dataset_id and not args.smoke):
        name = args.ensure_dataset or DEFAULT_DATASET_NAME
        return cmd_ensure_dataset(args.base_url, key, name, args.embedding_model)
    if args.smoke:
        if not args.dataset_id:
            print("❌ --smoke 需要 --dataset-id（先运行 --ensure-dataset）")
            return 1
        return cmd_smoke(args.base_url, key, args.dataset_id, args.limit, args.query)

    print("ℹ️  未指定动作。可用：--list-datasets / --ensure-dataset / --smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
