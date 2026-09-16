#!/usr/bin/env python
"""信号采集 CLI —— 手工触发一轮采集。

对齐《技术文档》§3.1：数据源 → 采集器 → 清洗 → 实体抽取 → 写库。

用法：

    # 列出全部数据源及其可用状态
    python scripts/collect_signals.py --list

    # 干跑：只采集与清洗，不写库（首次接入/调试用，强烈建议先跑这个）
    python scripts/collect_signals.py --keywords 机器视觉 工业质检 --limit 5 --dry-run

    # 正式采集：写入 signal 库并重建索引
    python scripts/collect_signals.py --keywords 机器视觉 --limit 10

    # 只跑指定源
    python scripts/collect_signals.py --sources openalex gov_policy

    # 不传 --keywords 则自动取「当前公司画像」的技术栈作为检索词
    python scripts/collect_signals.py --limit 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.collector import describe_adapters, run_collection  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.models import CompanyProfile  # noqa: E402


def _current_tenant(db) -> str:
    profile = db.query(CompanyProfile).order_by(CompanyProfile.updated_at.desc()).first()
    if profile is None:
        print("✗ 未找到公司画像，无法确定租户。请先在「能力画像」页创建公司。")
        sys.exit(1)
    return profile.tenant_id


def cmd_list() -> int:
    print(f"{'状态':<4} {'key':<20} {'数据源':<26} {'类型':<9} {'频率':<7} 说明")
    print("-" * 108)
    for row in describe_adapters():
        flag = "✓" if row["available"] else "✗"
        note = row["unavailable_reason"] or ("默认启用" if row["enabled_by_default"] else "")
        key_env = f"（{row['key_env']}）" if row["key_env"] and not row["available"] else ""
        print(f"{flag:<4} {row['key']:<20} {row['name']:<26} "
              f"{row['signal_type']:<9} {row['frequency']:<7} {note}{key_env}")
    total = len(describe_adapters())
    avail = sum(1 for r in describe_adapters() if r["available"])
    print("-" * 108)
    print(f"共 {total} 个适配器，当前可用 {avail} 个"
          f"{'（满足《设计文档》§7.2「覆盖至少 5 个数据源」）' if avail >= 5 else ''}")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    # --frequency 按《技术文档》§3.1 的采集频率筛选数据源（调度侧使用）
    sources = args.sources or None
    if args.frequency:
        from app.collector.registry import keys_by_frequency

        picked = keys_by_frequency(args.frequency)
        if not picked:
            print(f"✗ 频率 {args.frequency} 下没有默认启用的数据源。"
                  f"可用频率：{', '.join(_all_frequencies())}")
            return 1
        sources = picked
        print(f"[频率={args.frequency}] 启用数据源：{', '.join(picked)}")

    db = SessionLocal()
    try:
        tid = _current_tenant(db)
        report = run_collection(
            db, tid,
            adapter_keys=sources,
            keywords=args.keywords or None,
            limit_per_source=args.limit,
            dry_run=args.dry_run,
            reindex=not args.no_reindex,
        )
    finally:
        db.close()

    data = report.as_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    mode = "【干跑·不写库】" if args.dry_run else "【正式采集】"
    print(f"\n{mode} 检索词 = {'、'.join(data['keywords'])}"
          f" | 行业 = {data['industry'] or '自动'}")
    print(f"{'源':<24} {'状态':<8} {'抓取':>5} {'清洗':>5} {'丢弃':>5} {'新增':>5} {'重复':>5} {'耗时':>7}")
    print("-" * 92)
    for s in data["sources"]:
        print(f"{s['name'][:22]:<24} {s['status']:<8} {s['fetched']:>5} "
              f"{s['normalized']:>5} {s['dropped']:>5} {s['inserted']:>5} "
              f"{s['duplicated']:>5} {s['elapsed_ms']:>5}ms"
              + (f"  {s['error'] or s['note'] or ''}"[:50] if s["status"] != "ok" else ""))
    print("-" * 92)
    print(f"合计：抓取 {data['total_fetched']} / 新增 {data['total_inserted']} "
          f"/ 重复 {data['total_duplicated']} / 丢弃 {data['total_dropped']} "
          f"| 成功源 {data['ok_sources']} 失败 {data['failed_sources']} 跳过 {data['skipped_sources']}")
    if not args.dry_run:
        print(f"索引重建：{data['indexed_documents']} 篇文档"
              f" | 图谱：{data['graph'] or '未刷新'}")
    print(f"总耗时 {data['elapsed_ms']} ms")
    return 0


def _all_frequencies() -> list[str]:
    from app.collector.registry import frequencies

    return frequencies()


def main() -> int:
    p = argparse.ArgumentParser(description="TrackCompass 信号采集器")
    p.add_argument("--list", action="store_true", help="列出数据源与可用状态")
    p.add_argument("--sources", nargs="*", help="只跑指定适配器 key")
    p.add_argument("--frequency",
                   help="按《技术文档》§3.1 的频率筛选数据源（hourly / daily / weekly）")
    p.add_argument("--keywords", nargs="*", help="检索词（缺省取当前公司画像技术栈）")
    p.add_argument("--limit", type=int, default=10, help="每个源最多采集条数")
    p.add_argument("--dry-run", action="store_true", help="只采集清洗，不写库")
    p.add_argument("--no-reindex", action="store_true", help="写库后不重建索引")
    p.add_argument("--json", action="store_true", help="以 JSON 输出完整报告")
    args = p.parse_args()

    if args.list:
        return cmd_list()
    return cmd_collect(args)


if __name__ == "__main__":
    sys.exit(main())
