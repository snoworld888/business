"""信号层采集器 —— 对齐《技术文档》§3.1 数据源接入 / §3.2 数据管道。

对外只暴露四个概念：

    run_collection()   执行一轮采集（编排全流程）
    build_adapters()   构建数据源适配器
    describe_adapters() 适配器清单（前端「数据源覆盖」用）
    CollectReport      采集报告（逐源结果 + 汇总）

典型用法：

    from app.core.db import SessionLocal
    from app.collector import run_collection

    db = SessionLocal()
    report = run_collection(db, tenant_id, keywords=["机器视觉"], limit_per_source=10)
    print(report.as_dict())

设计原则见 `base.py` 顶部注释：零新增依赖、礼貌采集、失败不致命。
"""

from app.collector.base import (
    CollectContext, CollectorError, HttpClient, RawSignal, SourceAdapter,
    parse_datetime, strip_html,
)
from app.collector.normalize import (
    content_hash, detect_language, infer_industry, normalize,
)
from app.collector.registry import (
    ADAPTERS, DEFAULT_ENABLED, build_adapters, describe_adapters,
)

# ⚠️ `runner` 必须**惰性导入**（PEP 562），不能写在模块顶部。
# 依赖链是：runner → services.signal_ingest → collector.normalize。
# 若在此处直接 `from app.collector.runner import ...`，那么任何
# `from app.collector.normalize import content_hash`（signal_ingest 就会这么写）
# 都会先执行本文件 → 触发 runner → 回头 import 尚未完成初始化的
# signal_ingest，形成循环导入。惰性属性让 `from app.collector import
# run_collection` 依然可用，但不产生副作用。
_LAZY = {
    "run_collection", "resolve_keywords", "CollectReport", "SourceResult",
}


def __getattr__(name: str):  # noqa: ANN202
    if name in _LAZY:
        from app.collector import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # 运行（惰性）
    "run_collection", "resolve_keywords", "CollectReport", "SourceResult",
    # 注册
    "ADAPTERS", "DEFAULT_ENABLED", "build_adapters", "describe_adapters",
    # 基座
    "SourceAdapter", "RawSignal", "CollectContext", "HttpClient", "CollectorError",
    "strip_html", "parse_datetime",
    # 清洗
    "normalize", "content_hash", "detect_language", "infer_industry",
]
