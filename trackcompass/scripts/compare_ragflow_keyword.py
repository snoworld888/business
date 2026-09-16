"""RAGFlow 检索：`keyword`（大模型关键词抽取）开 / 关 对照实验。

背景
----
RAGFlow 的 `/api/v1/retrieval` 有一个 `keyword` 布尔参数，其真实语义
（源码 `api/apps/restful_apis/chunk_api.py:307-309`）：

    if req.get("keyword", False):
        question += await keyword_extraction(LLMBundle(...), question)

即：**先用租户默认对话模型抽取 3 个关键词，再把关键词字符串直接拼到 question 尾部**，
然后用拼接后的 question 走同一条「向量 + 词项」混合检索。

所以它影响的是**检索 query 本身**，不是相似度公式。本脚本量化这个影响。

跑法
----
    C:\\Users\\PC\\.workbuddy\\binaries\\python\\envs\\trackcompass\\Scripts\\python.exe ^
        scripts\\compare_ragflow_keyword.py

产出：stdout 的 Markdown 对照表（可粘贴进文档）。
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
DB_PATH = BACKEND / "trackcompass.db"

# ---------------------------------------------------------------- 配置读取
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    f = BACKEND / ".env"
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
BASE_URL = ENV.get("RAGFLOW_BASE_URL", "http://localhost:9380").rstrip("/")
API_KEY = ENV.get("RAGFLOW_API_KEY", "")
DATASET_ID = ENV.get("RAGFLOW_DATASET_ID", "")
SIM_THRESHOLD = float(ENV.get("RAGFLOW_SIMILARITY_THRESHOLD", "0.2"))
VEC_WEIGHT = float(ENV.get("RAGFLOW_VECTOR_WEIGHT", "0.3"))
TOP_K = 20

_QUERY_META = re.compile(r"[+\-&|!(){}\[\]^\"~*?:\\/]")


def sanitize(q: str) -> str:
    """与 ragflow.py `_sanitize_query` 同逻辑（ES 元字符 → 空格）。"""
    return re.sub(r"\s+", " ", _QUERY_META.sub(" ", str(q or ""))).strip()


# ---------------------------------------------------------------- query 构造
def build_queries() -> list[tuple[str, str]]:
    """(标签, query)。query 尽量贴近项目真实发出的检索串。"""
    out: list[tuple[str, str]] = []
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    for ind in ("AI+制造", "AI+医疗", "新能源科技", "具身智能"):
        kws: list[str] = []
        seen: set[str] = set()
        for r in conn.execute(
                "select keywords from signals where industry=? limit 12", (ind,)):
            try:
                ks = json.loads(r["keywords"] or "[]")
            except Exception:
                ks = []
            for k in ks:
                k = str(k).strip()
                if k and k not in seen:
                    seen.add(k)
                    kws.append(k)
            if len(kws) >= 8:
                break
        if kws:
            out.append((f"真实拼接串·{ind}", " ".join(kws[:8])))
    conn.close()

    # 边界样本：短查询 / 自然语言长查询 / 分类标签原样（已知归一化为空语义）
    out.append(("短查询·2词", "缺陷检测 人形机器人"))
    out.append(("短查询·1词", "固态电池"))
    out.append(("自然语言问句", "工业质检领域有哪些新政策和融资动向"))
    out.append(("分类标签原样", "AI+制造"))
    return out


# ---------------------------------------------------------------- 单次检索
def retrieve(question: str, keyword: bool) -> dict:
    payload = {
        "question": question,
        "dataset_ids": [DATASET_ID],
        "page": 1,
        "page_size": TOP_K,
        "top_k": TOP_K,
        "similarity_threshold": SIM_THRESHOLD,
        "vector_similarity_weight": VEC_WEIGHT,
        "keyword": keyword,
        "highlight": False,
    }
    t0 = time.perf_counter()
    with httpx.Client(base_url=f"{BASE_URL}/api/v1",
                      headers={"Authorization": f"Bearer {API_KEY}"},
                      timeout=120, trust_env=False) as c:
        r = c.post("/retrieval", json=payload)
        r.raise_for_status()
        data = r.json()
    ms = (time.perf_counter() - t0) * 1000
    if data.get("code") not in (0, None):
        return {"ms": ms, "err": f"code={data.get('code')} {data.get('message')}", "chunks": []}
    chunks = ((data.get("data") or {}).get("chunks") or [])
    return {"ms": ms, "err": "", "chunks": chunks}


def summarize(res: dict) -> dict:
    ch = res["chunks"]
    names = [str(c.get("document_keyword") or "") for c in ch]
    sims = [float(c.get("similarity") or 0) for c in ch]
    terms = [float(c.get("term_similarity") or 0) for c in ch]
    vecs = [float(c.get("vector_similarity") or 0) for c in ch]
    return {
        "ms": res["ms"],
        "err": res.get("err", ""),
        "n": len(ch),
        "max_sim": max(sims) if sims else 0.0,
        "max_term": max(terms) if terms else 0.0,
        "max_vec": max(vecs) if vecs else 0.0,
        "names": names,
        "ids": [n[:-4] if n.lower().endswith(".txt") else n for n in names],
    }


# ---------------------------------------------------------------- 抽取实况
KEYWORD_PROMPT = """## Role
You are a text analyzer.

## Task
Extract the most important keywords/phrases of a given piece of text content.

## Requirements
- Summarize the text content, and give the top __TOPN__ important keywords/phrases.
- The keywords MUST be in the same language as the given piece of text content.
- The keywords are delimited by ENGLISH COMMA.
- Output keywords ONLY.

---

## Text Content
__CONTENT__
"""

LLM_BASE = ENV.get("LLM_BASE_URL", "http://localhost:11434").rstrip("/")
LLM_MODEL = ENV.get("LLM_MODEL_COMPLEX", "qwen3.8:27b")


def reproduce_extraction(content: str, topn: int = 3) -> str:
    """本地复现 RAGFlow 的关键词抽取（同一提示词、同一模型）。

    ⚠️ 与 RAGFlow 真实调用有差异：RAGFlow 走 LiteLLM 默认参数（模型可能先输出
    思维链，再由 `re.sub(r"^.*</think>", "", kwd)` 截断），此处 `think=false` 只为
    快。用途是**展示抽取形态**，不作为逐字一致的凭证。
    """
    prompt = KEYWORD_PROMPT.replace("__TOPN__", str(topn)).replace("__CONTENT__", content)
    with httpx.Client(base_url=LLM_BASE, timeout=180, trust_env=False) as c:
        r = c.post("/api/chat", json={
            "model": LLM_MODEL,
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": "Output: "}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2, "num_predict": 128},
        })
        r.raise_for_status()
        return str((r.json().get("message") or {}).get("content") or "").strip()


def main() -> int:
    if not API_KEY or not DATASET_ID:
        print("[FATAL] .env 缺少 RAGFLOW_API_KEY / RAGFLOW_DATASET_ID")
        return 2

    # 数据规模（结论强度的前提）
    with httpx.Client(base_url=f"{BASE_URL}/api/v1",
                      headers={"Authorization": f"Bearer {API_KEY}"},
                      timeout=30, trust_env=False) as c:
        d = c.get("/datasets", params={"id": DATASET_ID, "page_size": 1}).json()
    ds = (d.get("data") or [{}])[0]
    print(f"## 语料规模\n- dataset: `{ds.get('name')}` 文档 {ds.get('document_count')} / "
          f"chunk {ds.get('chunk_count')}\n- 嵌入模型: `{ds.get('embedding_model')}`\n")

    rows = []
    for label, raw_q in build_queries():
        q = sanitize(raw_q)
        latin = 0 if not q else 1
        a = summarize(retrieve(q, False))
        b = summarize(retrieve(q, True))
        inter = len(set(a["ids"]) & set(b["ids"]))
        union = len(set(a["ids"]) | set(b["ids"]))
        jac = round(inter / union, 3) if union else 1.0
        rows.append((label, q, a, b, jac, inter, union))
        print(f"  done: {label}", file=sys.stderr)

    print("## 对照结果（keyword=false vs true）\n")
    print("| # | 场景 | 检索串（前 40 字） | 命中数 off/on | 最高相似度 off→on | "
          "最高 term off→on | 最高 vec off→on | 耗时 ms off→on | Top1 名称 off→on | 命中一致率(交集/并集) |")
    print("| - | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, (label, q, a, b, jac, inter, union) in enumerate(rows, 1):
        t1 = (a["names"][0][:28] if a["names"] else "—")
        t2 = (b["names"][0][:28] if b["names"] else "—")
        flag = "" if t1 == t2 else " ⚠️变"
        print(f"| {i} | {label} | `{q[:40]}` | {a['n']} / {b['n']} | "
              f"{a['max_sim']:.3f} → {b['max_sim']:.3f} | "
              f"{a['max_term']:.3f} → {b['max_term']:.3f} | "
              f"{a['max_vec']:.3f} → {b['max_vec']:.3f} | "
              f"{a['ms']:.0f} → {b['ms']:.0f} | {t1} → {t2}{flag} | {inter}/{union} = {jac} |")

    ms_off = [r[2]["ms"] for r in rows]
    ms_on = [r[3]["ms"] for r in rows]
    print(f"\n## 汇总\n")
    print(f"- 耗时中位数：off **{statistics.median(ms_off):.0f} ms** → "
          f"on **{statistics.median(ms_on):.0f} ms**（×{statistics.median(ms_on)/max(1,statistics.median(ms_off)):.1f}）")
    print(f"- 命中数合计：off {sum(r[2]['n'] for r in rows)} → on {sum(r[3]['n'] for r in rows)}")
    print(f"- Top1 变化场景数：{sum(1 for r in rows if (r[2]['names'][:1] != r[3]['names'][:1]))} / {len(rows)}")
    print(f"- 命中集合平均 Jaccard："
          f"{statistics.mean([r[4] for r in rows]):.3f}")
    errs = [r for r in rows if r[2]["err"] or r[3]["err"]]
    if errs:
        print("\n## 错误\n")
        for label, _, a, b, _, _, _ in errs:
            print(f"- {label}: off=`{a['err']}` on=`{b['err']}`")

    # 抽取实况：keyword=true 时 query 到底被扩成什么
    print("\n## 关键词抽取实况（本地复现）\n")
    print(f"提示词 = RAGFlow `rag/prompts/keyword_prompt.md`，模型 = `{LLM_MODEL}`，"
          f"topn=3。RAGFlow 侧是 `question += kwd`（**无分隔符直接拼接**）。\n")
    print("| 原始检索串 | LLM 抽取结果 | RAGFlow 实际拿去检索的串（拼接后）|")
    print("| --- | --- | --- |")
    for label, raw_q in (("短查询·1词", "固态电池"),
                         ("自然语言问句", "工业质检领域有哪些新政策和融资动向")):
        q = sanitize(raw_q)
        try:
            kw = " ".join(reproduce_extraction(q).split())
        except Exception as exc:  # noqa: BLE001
            kw = f"<抽取失败 {type(exc).__name__}: {exc}>"
        print(f"| `{q}` | {kw} | `{(q + kw)[:90]}` |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
