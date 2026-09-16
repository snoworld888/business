#!/usr/bin/env python
"""趋势名语义相似度分析 —— 回答「趋势累积到底是近义重复还是真差异」。

背景：趋势落库的 upsert 键是 `(tenant, name)` **精确匹配**，而 LLM 每次
生成的趋势名都不同 ⇒ 同名才更新、异名必新增，累积出一堆「看起来像重复」的趋势。
但「看起来像」是主观判断 —— 本脚本用**向量相似度**给出客观依据。

做法：
1. 从备份库取历史趋势名（默认取 `trend-reset` 备份，即同一批真实信号
   被反复扫描 6 次所产生的全部趋势名 —— 最干净的「同源多次扫描」样本）。
2. 用 `bge-m3`（Ollama）算句子向量 —— **必须用真语义模型**：
   项目默认的 `LocalHashEmbedding` 是确定性哈希，不携带语义，算出的
   相似度没有意义（这也是当初切 `bge-m3` 的原因）。
3. 输出两两余弦相似度矩阵，并按相似度排序。
4. 与「人类判定的近义组」对照，得出可操作的阈值。

用法：
    python scripts/analyze_trend_similarity.py
    python scripts/analyze_trend_similarity.py --backup 2026-09-15_remove-seed
    python scripts/analyze_trend_similarity.py --live        # 用当前库
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from itertools import combinations
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "backend" / "db_backup"
LIVE_DB = ROOT / "backend" / "trackcompass.db"

OLLAMA = "http://localhost:11434"
EMBED_MODEL = "bge-m3"

# 人工标注的「同主题」分组（按语义归类，用于和向量结果对照）
KNOWN_GROUPS: dict[str, list[str]] = {
    "光学/视觉检测": ["光学检测", "精密光学", "良率优化", "半导体质检", "视觉智能监控",
                     "视觉数据增强", "光学卷积", "光互连", "光子计算"],
    "具身智能": ["具身智能", "机器人", "特种作业"],
    "碳管理": ["碳管理", "绿色制造"],
    "算力/边缘": ["算力硬件", "边缘AIoT", "本地算力", "智能终端"],
}


# ------------------------------------------------------------------ 取数
def load_names(source: str) -> list[str]:
    """返回趋势名列表（去重保序）。"""
    if source == "live":
        db = LIVE_DB
    else:
        db = BACKUP_DIR / source / "trackcompass.db"
    if not db.exists():
        print(f"✗ 找不到数据库：{db}")
        sys.exit(1)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("select name from trends order by created_at").fetchall()
    con.close()
    seen: list[str] = []
    for (name,) in rows:
        if name and name not in seen:
            seen.append(name)
    return seen


# ------------------------------------------------------------------ 向量
def embed(texts: list[str]) -> list[list[float]]:
    """用 bge-m3 批量取向量（走 Ollama 原生 /api/embed）。"""
    with httpx.Client(timeout=120.0, trust_env=False) as client:
        resp = client.post(f"{OLLAMA}/api/embed",
                           json={"model": EMBED_MODEL, "input": texts})
        resp.raise_for_status()
        data = resp.json()
    vecs = data.get("embeddings")
    if not vecs:
        # 旧端点兜底
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            vecs = [client.post(f"{OLLAMA}/api/embeddings",
                                json={"model": EMBED_MODEL, "prompt": t})
                    .json()["embedding"] for t in texts]
    return vecs


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def group_of(name: str) -> str:
    for g, kws in KNOWN_GROUPS.items():
        if any(k in name for k in kws):
            return g
    return "其他"


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="趋势名语义相似度分析")
    ap.add_argument("--backup", default="2026-09-15_trend-reset",
                    help="备份目录名（默认 trend-reset，即同源多次扫描的样本）")
    ap.add_argument("--live", action="store_true", help="改用当前运行库")
    ap.add_argument("--top", type=int, default=15, help="展示最相似的 N 对")
    args = ap.parse_args()

    source = "live" if args.live else args.backup
    names = load_names(source)
    if len(names) < 2:
        print("样本不足")
        return 1

    print(f"样本来源：{source}　共 {len(names)} 条趋势名\n")
    for i, n in enumerate(names, 1):
        print(f"  {i:>2}. [{group_of(n)}] {n}")
    print()

    print(f"→ 调用 {EMBED_MODEL} 计算向量 …")
    try:
        vecs = embed(names)
    except Exception as exc:                       # noqa: BLE001
        print(f"✗ 取向量失败（Ollama 未启动？）：{exc}")
        return 1
    print(f"  向量维度 {len(vecs[0])}\n")

    # ---- 两两相似度
    pairs = []
    for i, j in combinations(range(len(names)), 2):
        sim = cosine(vecs[i], vecs[j])
        same = group_of(names[i]) == group_of(names[j]) and group_of(names[i]) != "其他"
        pairs.append((sim, names[i], names[j], same))

    same_sims = [s for s, _, _, same in pairs if same]
    diff_sims = [s for s, _, _, same in pairs if not same]

    print(f"{'相似度':>7}  {'判定':<6} 趋势 A  ⟷  趋势 B")
    print("-" * 108)
    for sim, a, b, same in sorted(pairs, reverse=True)[: args.top]:
        mark = "同主题" if same else "跨主题"
        print(f"{sim:>7.4f}  {mark:<6} {a[:40]:<42} ⟷ {b[:40]}")
    print()

    def stat(label: str, xs: list[float]) -> None:
        if not xs:
            print(f"  {label}: 无样本")
            return
        xs = sorted(xs)
        n = len(xs)
        print(f"  {label:<12} n={n:<4} min={xs[0]:.4f}  "
              f"中位={xs[n // 2]:.4f}  max={xs[-1]:.4f}  "
              f"均值={sum(xs) / n:.4f}")

    print("相似度分布：")
    stat("同主题组内", same_sims)
    stat("跨主题", diff_sims)
    print()

    # ---- 阈值建议
    if same_sims and diff_sims:
        lo, hi = min(same_sims), max(diff_sims)
        print("阈值参考：")
        print(f"  · 跨主题最高相似度 = {hi:.4f}　（超过它基本可判为同主题）")
        print(f"  · 同主题最低相似度 = {lo:.4f}　（低于它基本可判为不同主题）")
        if lo > hi:
            mid = (lo + hi) / 2
            print(f"  · ✅ 两组**可线性分开**，建议阈值 ≈ {mid:.4f}")
        else:
            print("  · ⚠️ 两组**重叠**，单一阈值无法分开 —— 需结合关键词/聚类")
    print()

    # ---- 差异词分析（回答「表面像，实质差在哪」）
    print("=" * 108)
    print("差异词分析：趋势名大量共用模板词（「AI驱动的」「具身智能」「在…场景的」），")
    print("真正决定语义的是**差异词**。下表去掉公共词后单独比差异片段。")
    print("=" * 108)
    print(f"{'整体':>7} {'差异词':>8} 公共词                 差异片段 A ⟷ 差异片段 B")
    print("-" * 108)
    for sim, a, b, _same in sorted(pairs, reverse=True)[:8]:
        common, da, db = diff_parts(a, b)
        if not da and not db:
            print(f"{sim:>7.4f} {'—':>8} （完全相同）")
            continue
        dv = embed([da, db]) if (da and db) else None
        dsim = cosine(dv[0], dv[1]) if dv else float('nan')
        print(f"{sim:>7.4f} {dsim:>8.4f} {common[:20]:<22} {da[:26]:<28} ⟷ {db[:26]}")
    return 0


def diff_parts(a: str, b: str) -> tuple[str, str, str]:
    """返回 (公共词, A 的差异词, B 的差异词)。用 jieba 分词后取集合差。"""
    try:
        import jieba

        wa = [w.strip() for w in jieba.cut(a) if w.strip()]
        wb = [w.strip() for w in jieba.cut(b) if w.strip()]
    except ImportError:
        # 退化：按 2-gram 切
        wa = [a[i:i + 2] for i in range(len(a) - 1)]
        wb = [b[i:i + 2] for i in range(len(b) - 1)]
    sa, sb = set(wa), set(wb)
    common = "".join(w for w in wa if w in sa & sb)
    da = "".join(w for w in wa if w not in sb)
    db = "".join(w for w in wb if w not in sa)
    return common[:24], da[:40], db[:40]


if __name__ == "__main__":
    sys.exit(main())
