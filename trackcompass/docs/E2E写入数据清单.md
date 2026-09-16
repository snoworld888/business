# E2E 冒烟脚本写入数据清单

> 核查日期 2026-09-16　｜　全部数据来自实测，非推断
> 脚本：`scripts/e2e_smoke.py`（30 项检查，连真实服务 `127.0.0.1:8020`）
>
> ✅ **本文描述的污染问题已解决（2026-09-16）** —— 已按下方「方案 A」落地：
> E2E 改用专用租户 `e2e@test.local`，运行结束自动清理，**反复运行不再累积**。
> 实现说明见 `docs/公共数据层与E2E租户隔离.md`，本文保留为「问题取证」存档。

---

## 0. 解决方案（已实施，2026-09-16）

| 项 | 内容 |
| --- | --- |
| 隔离方式 | E2E 专用租户 `__e2e__ 自动化测试` / 用户 `e2e@test.local` |
| 前置条件 | **信号须为公共数据**（`tenant_id IS NULL`）—— 否则 E2E 租户读不到信号、链路断路 |
| 清理时机 | `main()` 的 `finally`：成功、失败、超时都清理 |
| 清理范围 | 该租户 8 张表（含 `audit_logs`），严格按外键顺序；租户/用户保留供下轮复用 |
| 实测结果 | 登录租户 = `a07b2054`；运行后七张业务表**均为 0**；演示租户数据未变 |
| 实测清理量 | 33 行（趋势 8 / 机会 1 / 预警 5 / 实验 1 / 报告 1） |

---

## 1. 结论速览

E2E 走的是**真实服务 + 真实数据库**，脚本内含 **20+ 写操作**，
其中**只有 1 项有清理**（测试公司画像），其余**全部永久留在库里**。

| 表 | 当前行数 | 归属 | 说明 |
| --- | --- | --- | --- |
| `experiments` | 12 | **11 条 E2E** + 1 条 seed | 每次运行 +1 |
| `reports` | 10 | **10 条全部 E2E** | 每次运行 +1 |
| `alerts` | 54 | **54 条全部 E2E** | 每次运行 +N（分析机会时生成） |
| `opportunities` | 3 | **3 条全部 E2E** | 由 `to-pool` 创建（幂等复用） |
| `trends` | 5 | 5 条（已清理过） | 反复 `scan` 累积，曾达 17 条 |
| `audit_logs` | 571 | **绝大多数是 E2E** | 每次运行 +15~20 条 |
| `company_profiles` | 2 | 0 条 E2E | ✅ **唯一有清理的项** |
| `signals` | 48 | 0（采集器写入） | E2E 不写信号 |

---

## 2. 逐步写操作清单

对照 `e2e_smoke.py` 的 17 个步骤：

| 步 | 调用 | 写入表 | 每次产生 | 清理 |
| --- | --- | --- | --- | --- |
| 4 | `POST /company/profile`（智造未来科技） | `company_profiles` | upsert 更新 | — |
| 4b | `POST /company/profile`（`__e2e__ 烟雾测试公司`） | `company_profiles` | +1 | ✅ 直连 SQLite `DELETE` |
| 4b | `POST /company/profile`（还原演示画像为当前） | `company_profiles` | upsert | — |
| 6 | `POST /trends/scan` | **`trends`** | **+3~4** | ❌ |
| 7 | `POST /trends/{id}/to-pool` | **`opportunities`** | +1（幂等，第 2 次起复用） | ❌ |
| 8 | `POST /opportunities/{id}/analyze` | `opportunities`（更新）+ **`alerts`** | 更新 + **预警若干** | ❌ |
| 9 | `POST /score/preview` | 不落库（纯计算） | 0 | — |
| 10 | `POST /demand/probe` | 不落库（实时计算） | 0 | — |
| 11 | `POST /experiments` | **`experiments`** | **+1** | ❌ |
| 11 | `PATCH /experiments/{id}` ×2 | `experiments`（更新 metrics / status / conclusion） | 更新 | — |
| 11 | `POST /experiments/{id}/writeback` | `experiments` + `opportunities`（状态流转） | 更新 | — |
| 12 | `POST /reports` | **`reports`** | **+1** | ❌ |
| 12 | `POST /reports/{id}/export` | `reports`（更新 format） | 更新 | — |
| 13 | `POST /alerts/{id}/action` | `alerts`（status → handled） | 更新 1 条 | ❌ |
| 14 | `POST /alerts/replay` | `scorecard_versions` + 复盘记录 | +1 版本 | ❌ |
| 14 | `GET /scorecards/calibration/preview` | 只读 | 0 | — |
| 15 | `GET /graph?rebuild=true` | 内存图（不落库） | 0 | — |
| 16 | RBAC 检查（含 product 账号登录） | `audit_logs` | +2 | — |
| 17 | `GET /rag/search` | 只读 | 0 | — |

**每跑一次 `e2e_smoke.py` 的净增**：
```
experiments +1 · reports +1 · alerts +N(N 视机会分析结果) · trends +3~4
opportunities +1(仅首次) · audit_logs +15~20
```

---

## 3. 各表残留明细（实测）

### 3.1 `experiments` 12 条 → 11 条属 E2E

识别特征：**`hypothesis` 写死为「制造业客户愿意为 AI 质检付费」**，
`title` 为「落地页测试」，`conclusion` 固定为「注册率超目标，需求成立」。

```
[E2E] 2026-09-14 03:45  落地页测试 · AI 质检   running     ← 首次运行（未走完）
[seed]2026-09-14 03:45  客户访谈 · 精度溢价意愿   completed  10 家受访企业中 7 家愿意支付 20% 溢价
[E2E] 2026-09-14 08:14  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-14 09:52  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-14 10:12  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-14 10:18  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-15 05:51  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-15 06:18  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-15 06:58  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-15 07:15  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-15 07:16  落地页测试            completed  注册率超目标，需求成立
[E2E] 2026-09-15 07:22  落地页测试            completed  注册率超目标，需求成立
```

### 3.2 `reports` 10 条 → 全部属 E2E

识别特征：**标题 = 「<当时的机会卡名> 决策报告」**，`format` 为 `html`。

```
2026-09-14 08:14  AI 质检商业化 决策报告
2026-09-14 09:52  AI 质检商业化 决策报告
2026-09-14 10:12  工业视觉与智能质检规模化落地 决策报告
2026-09-14 10:18  工业视觉与智能质检规模化落地 决策报告
2026-09-15 05:51  工业视觉与智能质检规模化落地 决策报告
2026-09-15 06:18  工业视觉与智能质检规模化落地 决策报告
2026-09-15 06:58  工业视觉与智能质检规模化落地 决策报告
2026-09-15 07:15  AI算力硬件高精度光学检测 决策报告
2026-09-15 07:16  AI算力硬件高精度光学检测 决策报告
2026-09-15 07:22  AI算力硬件高精度光学检测 决策报告
```

### 3.3 `alerts` 54 条 → 全部属 E2E

由 `POST /opportunities/{id}/analyze` 触发的监控 Agent 生成。

| status | signal_type | 条数 |
| --- | --- | --- |
| pending | （空） | 40 |
| **handled** | （空） | 9 ← E2E 第 13 步处理过的 |
| handled | bidding | 1 |
| pending | funding / paper / policy / social | 各 1 |

**时间分布与 E2E 运行时刻完全吻合**（seed 时间是 09-14 03:45，此处最早是 08:00）：

```
09-14 08:00  5 条      09-15 05:00  5 条
09-14 09:00  2 条      09-15 06:00  9 条
09-14 10:00 13 条      09-15 07:00 20 条   ← 我反复跑的那几轮
```

### 3.4 `audit_logs` 571 条 → 绝大多数属 E2E

| action | 条数 | 每次 E2E |
| --- | --- | --- |
| `auth.login` | 164 | 每次 +1（+1 RBAC 用 product 账号） |
| `profile.upsert` | 87 | +2 |
| `experiment.update` | 62 | +2 |
| `demand.probe` | 40 | +1 |
| `experiment.writeback` | 31 | +1 |
| `experiment.create` | 31 | +1 |
| `report.generate` | 30 | +1 |
| `alert.replay` | 30 | +1 |
| `alert.handle` | 30 | +1 |
| `trend.to_pool` | 26 | +1 |
| `profile.create` | 26 | +1 |
| `signal.ingest` | 10 | 0（采集器产生） |
| `signal.create` | 3 | 0（手工） |
| `signal.collect` | 1 | 0（采集器产生） |

⚠️ **`report.generate` / `alert.replay` / `alert.handle` 各 30 次** ⇒
历史上 E2E 至少跑过 **约 30 轮**（当前残留的 experiments/reports 只有 11/10 条，
是因为早期数据在之前几轮清理中被连带删除过）。

### 3.5 `trends` — 曾累积到 17 条

根因见 `docs/趋势累积语义分析.md`：upsert 键是 `(tenant, name)` 精确匹配，
而 LLM 每次生成的名字都不同 ⇒ 异名必新增。实测 6 次扫描累积产生 17 条
（同主题近义重复），已清理为 5 条。

---

## 4. 为什么这是个问题

| # | 问题 | 具体表现 |
| --- | --- | --- |
| ① | **数据污染** | 用户在界面上看到的实验/报告/预警里混着测试垃圾（11 条"落地页测试"、10 条重复报告） |
| ② | **掩盖真实缺陷** | 断言若用"库里现成的数据"判定就会**恒为真**。「数据源覆盖 ≥5」原先读 `sources.connected`（库里有几类数据），种子时代含 9 类信号 ⇒ 永远通过；删掉种子后只剩 3 类才暴露 |
| ③ | **无法区分业务数据与测试痕迹** | 没有 `is_test` 标记，只能靠时间/标题等启发式特征回溯 |
| ④ | **累积不可逆** | 只有 `company_profiles` 有清理；其余表只增不减 |

---

## 5. 建议方案（供裁决）

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| **A. 独立租户（推荐）** | 注册一个 `e2e@test.local` 租户，全部操作在其名下 | 改动最小（只换登录凭据），业务数据与测试数据天然隔离，可随时 `DELETE ... WHERE tenant_id = ?` 整体清理 |
| B. 独立临时库 | 启动一个指向临时 SQLite 的实例，E2E 连它 | 隔离最彻底，但要额外起进程、跑 bootstrap，CI 成本高 |
| C. 加清理步骤 | 在 E2E 末尾 teardown 删除本次创建的数据 | 需为每张表写反删逻辑，且要处理外键顺序（参考前次踩坑：`alerts.track_id` 外键） |
| D. 标记 `is_test` | 加字段标记测试数据 | 需要 DDL；且要在所有查询里过滤，改动面大 |

**推荐 A**：与「多租户」这一既有设计对齐（`signals`/`trends`/`opportunities` 等表本就带 `tenant_id`），
不新增字段、不改查询逻辑，一处改动解决全部四个问题。

---

## 6. 附：当前残留的建议清理范围

若决定清理，按依赖顺序（**先子表后主表**，注意外键）：

```sql
-- 1) 预警（引用 opportunities / tracks）
DELETE FROM alerts WHERE tenant_id = '<tid>' AND id IN (...);
-- 2) 报告 / 实验（引用 opportunities）
DELETE FROM reports WHERE opportunity_id IN (...);
DELETE FROM experiments WHERE opportunity_id IN (...);
-- 3) 机会卡
DELETE FROM opportunities WHERE id IN (...);
-- 4) 趋势
DELETE FROM trends WHERE id IN (...);
-- 5) 审计日志（量最大，按 action+时间窗）
DELETE FROM audit_logs WHERE action = 'auth.login' AND created_at < '...';
```

**识别特征汇总**（用于回捞）：

| 表 | 特征 |
| --- | --- |
| experiments | `hypothesis = '制造业客户愿意为 AI 质检付费'` |
| reports | `title LIKE '% 决策报告'` 且 `format='html'` |
| alerts | 无 `is_test`，需按 `created_at` 时间窗 + `opportunity_id` 反查 |
| opportunities | `source_trend_id IS NOT NULL`（由 `to-pool` 创建） |
| audit_logs | `action IN ('auth.login','profile.upsert','experiment.*','report.generate','alert.*','trend.to_pool')` |

⚠️ 清理前请先备份（`db_backup/<日期>_<用途>/`）。
