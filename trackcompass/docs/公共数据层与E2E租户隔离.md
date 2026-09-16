# 公共数据层与 E2E 租户隔离

> 回答两个问题：**① 哪些信息应该进入公共数据；② E2E 如何与业务数据隔离。**
> 结论均基于源码核查与运行实测（2026-09-16）。

---

## 一、结论先行：公共数据边界

**判据：这条数据是否属于「外部客观事实」？**

| 数据 | 归属 | 理由 |
| --- | --- | --- |
| `signals` 信号 | 🟢 **公共** | 政策/论文/融资/招标等外部客观情报，与租户无关，采集一次全租户共享 |
| `tracks` 赛道 | 🟢 全局 | 行业标准分类（模型本就没有 `tenant_id`） |
| `roles` 角色 | 🟢 全局 | 角色权限定义（同上） |
| `trends` 趋势 | 🔴 租户私有 | 携带 `subscribed` / `in_pool` 等**租户状态**，是租户的认知产物 |
| `company_profiles` 能力画像 | 🔴 租户私有 | 企业自身能力，敏感 |
| `opportunities` 机会卡 | 🔴 租户私有 | 决策资产 |
| `scorecards` / `scorecard_versions` 评分卡 | 🔴 租户私有 | 租户自定的评价体系 |
| `experiments` 实验 | 🔴 租户私有 | 执行记录 |
| `reports` 报告 | 🔴 租户私有 | 决策产物 |
| `alerts` 预警 | 🔴 租户私有 | 监控动作 |
| `audit_logs` 审计日志 | 🔴 租户私有 | 合规记录，按租户隔离 |

> ⚠️ **趋势保持私有是刻意的**，不是遗漏。趋势 = 公共信号的聚类结果 **+ 租户的订阅/入池状态**。
> 若要让趋势也跨租户共享，需要把「客观聚类」与「租户状态」拆成两张表 ——
> 属产品决策，见 §六。

---

## 二、为什么信号必须是公共数据

### 2.1 三个理由

1. **采集在物理上就不该重复** —— 采集器从 `gov.cn` / `OpenAlex` / `中国政府采购网` 抓同一批
   外部情报，不可能为每个租户各抓一遍。私有化意味着 N 个租户 = N 倍抓取量、N 份重复存储。

2. **数据层是唯一把它当私有的地方** —— 在这之前，同一批数据在系统里其实是**两套口径**：
   - RAG 索引：RAGFlow **单一 dataset**，本来就是全租户共享
   - 知识图谱：进程内**全局单例**，本来就是全租户共享
   - 只有 `signals` 表按 `tenant_id` 过滤 ⇒ **唯一不一致的一环**

3. **它是 E2E 隔离成立的前提**（最关键，见下）

### 2.2 与「E2E 独立租户」的强耦合

这两件事不是并列的两个需求，而是**同一件事的两面**：

```
若信号仍为租户私有：
   E2E 租户 → 0 条信号 → POST /trends/scan 返回 generated=0
            → 「趋势 → 机会 → 分析 → 评分 → 报告」整条链路断路
            → E2E 根本跑不起来

若信号为公共：
   E2E 租户 → 读到公共信号（48 条）→ 扫描出趋势（写自己名下）→ 链路完整
            → 测试数据全在 E2E 租户 → 一条 SQL 整体清理
```

**所以「方案 A」要以「公共数据层」为前提，单独做不了。**

---

## 三、实现：`tenant_id IS NULL` = 公共

### 3.1 表示法选型

| 方案 | 评价 |
| --- | --- |
| ✅ **`tenant_id IS NULL`** | 模型里本就是 `str \| None`，**零 DDL**；语义自洽（外部情报没有归属）；与 RAG/图谱的既有全局性对齐 |
| ❌ 哨兵租户 ID（如 `__public__`） | 需要真实租户行以满足外键；语义上是"造一个假租户" |
| ❌ 新增 `is_public` 字段 | 需要 DDL 迁移；且与 `tenant_id` 产生两种表达同一件事的字段 |

### 3.2 可见性 API（`app/core/visibility.py`）

```python
PUBLIC_TENANT_ID = None          # 显式常量，避免散落裸 None

visible_clause(model, tenant_id) # SQL 条件：public ∪ 本租户
visible(stmt, model, tenant_id)  # 链式包装
private_only(stmt, model, tenant_id)
```

### 3.3 读写点清单

| 位置 | 改动 |
| --- | --- |
| `collector/runner.py` | 采集写入 `PUBLIC_TENANT_ID`（`tenant_id` 仍用于解析检索词与审计） |
| `api/v1/signals.py::create_signal` | 新增 `SignalIn.is_public`（**默认 True**）；False 才归本租户 |
| `api/deps.py::signal_stats` | `visible_clause` |
| `api/v1/signals.py`（列表 / 数据源状态） | `visible` |
| `api/v1/demand.py`（探针 / 痛点聚合） | `visible` |
| `api/v1/alerts.py`（监控扫描） | `visible` |
| `api/v1/profile.py::list_tracks` | `visible` |
| `services/analysis.py::run_analysis` | `visible` |
| `services/analysis.py::reindex` | **只索引公共层**（RAGFlow 单 dataset，见 §六-2） |
| `services/graph.py::build_from_signals` | **只纳入公共信号**（图谱是全局单例） |
| `seed/__init__.py` | 种子信号写入 `PUBLIC_TENANT_ID` |

### 3.4 数据迁移

```
UPDATE signals SET tenant_id = NULL;   -- 48 行
```

依据：48 条信号**全部**由采集器写入（国务院文件 10 / OpenAlex 10 / Crossref 8 / 36氪 10 / IT之家 10），
无一条是租户私有材料。迁移后 `PRAGMA foreign_key_check` 为空。
备份：`backend/db_backup/2026-09-16_public-layer/trackcompass.db`。

---

## 四、E2E 租户隔离

### 4.1 机制

```
ensure_e2e_tenant(db)      幂等创建 租户 + 用户 + 默认评分卡
      ↓
E2E 全程用 e2e@test.local 的 token （所有写入落在 E2E 租户名下）
      ↓
purge_e2e_data(db, tid)    finally 中执行：清空该租户业务数据 + 重建全局图谱
```

- **为什么必须建评分卡**：`/opportunities/{id}/score` 调 `deps.get_default_scorecard`，
  缺失直接 404 —— 没有评分卡的租户跑不完链路。
- **为什么保留租户/用户**：下一轮免重建；清掉的评分卡由 `ensure_e2e_tenant` 自动补回。
- **为什么清理放在 `finally`**：即便中途异常/超时，也不留残留（已实测：一次 `ReadTimeout`
  中断后，E2E 租户七张业务表全部为 0）。

### 4.2 清理顺序（严格按外键依赖）

```
scorecard_versions  ← 该表**没有 tenant_id**，须经 scorecard_id 子查询间接删除
experiments         ← opportunities
reports             ← opportunities
alerts              ← tracks
opportunities       ← tracks
trends
company_profiles
scorecards
audit_logs
```

> ⚠️ 顺序错误会触发 `FOREIGN KEY constraint failed` —— 本项目在删脏赛道时踩过一次。

### 4.3 为什么要清理（历史实证）

改造前 E2E 直接写演示租户的真实库，**20+ 写操作只有 1 项有清理**，实测累积：

| 表 | 累积量 | 识别特征 |
| --- | --- | --- |
| `experiments` | 11 条 | hypothesis 写死「制造业客户愿意为 AI 质检付费」 |
| `reports` | 10 条 | 标题 = 当时的机会卡名 |
| `alerts` | 54 条 | 由 analyze 触发 |
| `trends` | 6 轮累积到 17 条 | 每次 `POST /trends/scan` |
| `audit_logs` | 571 条 | `report.generate` 30 次 → 至少跑过 30 轮 |

三个后果：① 界面混着测试垃圾；② **掩盖真实缺陷**（断言若读"库里现成数据"会恒为真，
「数据源覆盖 ≥5」就是这么假通过的）；③ 无法区分业务数据与测试痕迹。

---

## 五、验证矩阵

| 项目 | 结果 |
| --- | --- |
| pytest 全量 | **319/319** ✅（新增 12 项隔离测试 + 6 项级别词表测试） |
| 公共层：新租户可读公共信号 | ✅ 0 条自有信号，仍读到全部 48 条 |
| 公共层：私有信号不可跨租户 | ✅ 另一租户列表中不出现 |
| 公共层：人工上传默认公共 | ✅ `tenant_id IS NULL` |
| E2E：写入落在隔离租户 | ✅ 审计日志 `tenant_id` = `__e2e__` |
| E2E：清理后零残留 | ✅ 八张业务表均为 0；演示租户数据未变 |
| E2E：清理幂等 + 评分卡重建 | ✅ 连续调用租户 ID 不变、评分卡回补 |
| E2E 冒烟 | **30/30** ✅（清理 33 行：趋势 8 / 机会 1 / 预警 5 / 实验 1 / 报告 1） |
| API 契约 | 57 前端路径 / 62 路由全对齐 ✅ |
| 数据迁移：信号转公共 | 48/48，`foreign_key_check` 为空 ✅ |
| 数据迁移：预警级别归一 | 49 行，三档之和 = 总数 54 ✅ |

---

## 六、遗留项（未做，需裁决）

> 📋 **本表已登记至统一台账 `docs/遗留项台账.md`**。新增/解决请**同时**改两处，保持对齐。

| # | 台账编号 | 项 | 现状 | 建议 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | **L-01** | **知识图谱仍是全局单例** | `build_from_signals` 只纳入公共信号，但 `trends`/`opportunities` 仍是**全租户混合** —— 即 A 租户的趋势节点会出现在 B 租户的图谱视图里 | 把 store 改为按租户分片（`dict[tenant_id, MemoryGraphStore]`），或拆「公共图谱 + 租户叠加层」。**多租户上线前必须处理** | ⬜ 暂缓（2026-09-16） |
| 2 | **L-02** | **私有信号的向量检索未隔离** | `reindex` 只索引公共层 ⇒ 私有信号**检索不到**（宁可不检索也不泄露） | 按租户分区索引（每租户独立 dataset / 命名空间） | ⬜ 暂缓（2026-09-16） |
| 3 | **L-03** | **趋势跨租户共享** | 趋势保持私有 ⇒ 多租户会各存一份高度相似的聚类结果（重复存储 + 重复 LLM 调用） | 若要做，需把「客观聚类」与「租户订阅状态」拆表 | 🔵 待裁决 |
| 4 | **L-08** | E2E 仍只读依赖演示租户 | RBAC 校验用 `product@demo.com`（仅 2 个 GET） | 无害。若追求彻底隔离，可给 E2E 租户建一个 `product` 角色用户 | ⬜ 暂缓 |

⚠️ **L-01 / L-02 的共同前提**：当前是**单组织部署**（1 个演示租户 + 1 个 E2E 租户），
二者在该形态下**不产生实际数据泄露**，属**结构性欠债**；**触发条件 = 接入第 2 个真实组织租户之前**。


---

## 七、本轮踩坑与修正记录

1. **客户端超时**（`httpx.ReadTimeout`）—— 真实 Ollama `qwen3.8:27b` 下
   `/opportunities/{id}/analyze`（7 Agent + 反馈循环 = 14 次 LLM 调用）耗时 > 180s。
   已给 `/trends/scan` 与 `/analyze` 单独放宽到 900s。
2. **脚本裸抛栈** —— 未预期异常原样抛出，掩盖了「后面哪些步骤根本没跑」。
   已在 `main()` 捕获并以「失败」收尾，同时保证 `finally` 清理仍执行。
3. **登录凭据未生效（严重）** —— 改造中 `e2e_smoke.py` 的登录行改动丢失，
   脚本仍以 `admin@demo.com` 登录，导致一轮 E2E 把 6 条趋势 + 4 条预警写进了**演示租户**。
   已用审计日志定位（`auth.login` 的 `tenant_id` 归属），并清理该批污染数据。
   → **教训：改动后必须复核落盘内容，不能只看"编辑成功"回执。**

---

## 八、连带发现并修复：预警级别词表三套混用

验证 E2E 结果时注意到一处输出反常 —— 「监控预警生成 | 严重 0 / 警告 0 / 提示 0」
却断言通过（`total > 0`）。追下去是一处**用户可见**的缺陷。

### 现象与根因

`alerts.level` 在库里混有 **三套写法**，而接口只统计规范值：

| 库中实际取值 | 条数 | 是否被接口统计 |
| --- | --- | --- |
| `high` / `medium` / `low` | 49 | ❌ 被丢弃 |
| `High` / `Medium` / `Low` | 4 | ❌ 被丢弃 |
| `critical` / `warning` / `info` | 5 | ✅ |

```python
# alerts.py（修复前）
"severity": {lv: sum(1 for a in rows if a.level == lv)
             for lv in ("critical", "warning", "info")},
```

⇒ 54 条预警里 **49 条级别不可见**，严重度汇总几乎恒为 `0/0/0`；
且按 `level=critical` 过滤**查不到** `high`。

**权威口径**由「Agent 规则表（`monitor.py::RULES`）+ 前端 `LEVEL_META`」共同确定，
两者都是 `critical/warning/info`（严重/警告/提示）。设计文档未定义级别词表。

### 修复

| 层次 | 改动 |
| --- | --- |
| 产出方 `agents/monitor.py` | 新增 `ALERT_LEVELS` / `_LEVEL_ALIASES` / `normalize_alert_level()` / `level_variants()` / `level_rank()` |
| 写入 `api/v1/alerts.py` | 落库前 `normalize_alert_level()` —— 不再产生新脏数据 |
| 读取 `api/v1/alerts.py` | 分档计数与响应中的 `level` 均归一；`level` 过滤展开为全部历史写法 |
| 排序 `monitor.py` | 原 `{"critical":0,...}[level]` 硬查表遇非规范值会 `KeyError`，改 `level_rank()` |
| 数据迁移 | 49 行归一：`high→critical`、`medium→warning`、`low→info`（含首字母大写变体） |

**副作用检查**：`competition.py` 的 `level` 与 `scoring.py` 的 `threat_level` 也使用
`high/medium/low`，但那是**竞争威胁等级**、只存在于分析结果（不进 `alerts` 表）
⇒ **不在本次改造范围**，保持原样。

### 验证

```
迁移前: medium 22 / high 17 / low 6 / warning 2 / info 2 / High 2 / critical 1 / Medium 1 / Low 1
迁移后: warning 25 / critical 20 / info 9      ← 总数仍 54，三档之和 = 总数 ✅
```

