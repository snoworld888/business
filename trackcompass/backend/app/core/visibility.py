"""数据可见性：**公共层**（外部客观情报）与**租户私有层**的分界。

## 约定

`tenant_id IS NULL` ⇒ **公共数据**。所有租户均可读，只有采集器与管理员可写。

选这个表示法的原因：

1. `signals.tenant_id` / `trends.tenant_id` 在模型里本就是 `str | None`
   （见 `app/models/__init__.py`），**不需要任何 DDL**；
2. 语义自洽 —— 外部客观情报（政策、论文、融资、招标）不属于任何租户，
   「没有归属」正是 `NULL` 的含义，比硬造一个哨兵租户 ID 更干净；
3. 与既有实现对齐 —— RAG 索引（单一 RAGFlow dataset）与知识图谱
   （进程内全局单例）本来就是全租户共享的，数据层过去是**唯一**把它当私有的地方。

## 分界

| 数据 | 归属 | 理由 |
| --- | --- | --- |
| `signals` | **公共** | 外部客观事实，采集一次全租户共享 |
| `tracks` / `roles` | 全局 | 模型本就没有 `tenant_id`（行业分类、角色定义） |
| `trends` | 租户私有 | 携带 `subscribed` / `in_pool` 等**租户状态** |
| `company_profiles` | 租户私有 | 企业自身能力，敏感 |
| `opportunities` / `scorecards` / `experiments` / `reports` / `alerts` / `audit_logs` | 租户私有 | 决策资产与合规记录 |

⚠️ **趋势保持私有**是刻意的：它不是「信号」的原始事实，而是租户的认知产物
（订阅、入池都是租户动作）。跨租户共享趋势属于产品决策，不在本层处理。
"""

from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import or_
from sqlalchemy.sql import Select

#: 公共数据的 `tenant_id` 取值。显式常量，避免代码里散落裸 `None`。
PUBLIC_TENANT_ID: None = None

_M = TypeVar("_M")


def is_public(model: _M, tenant_id: str | None) -> bool:
    """判断某条记录的 `tenant_id` 是否代表公共数据。"""
    return tenant_id is None


def visible_clause(model: Any, tenant_id: str | None):
    """SQLAlchemy 过滤条件：**公共数据 ∪ 本租户私有数据**。

    用法::

        stmt = select(Signal).where(visible_clause(Signal, tid))
    """
    if tenant_id is None:
        # 无租户上下文（如系统任务）只能看公共层
        return model.tenant_id.is_(None)
    return or_(model.tenant_id.is_(None), model.tenant_id == tenant_id)


def visible(stmt: Select, model: Any, tenant_id: str | None) -> Select:
    """给查询语句加上可见性作用域（`visible_clause` 的链式写法）。"""
    return stmt.where(visible_clause(model, tenant_id))


def private_only(stmt: Select, model: Any, tenant_id: str) -> Select:
    """只取本租户私有数据（用于确定不该看到公共层的场景）。"""
    return stmt.where(model.tenant_id == tenant_id)


__all__ = [
    "PUBLIC_TENANT_ID",
    "is_public",
    "visible_clause",
    "visible",
    "private_only",
]
