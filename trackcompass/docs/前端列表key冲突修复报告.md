# 前端列表 key 冲突修复报告

> 执行日期 2026-09-14 | 用户反馈：实验页报 `Each child in a list should have a unique "key" prop`

---

## 一、现象

`Experiments` 页控制台报错（栈指向 antd `Table` 的 `Body`）：

```
Warning: Each child in a list should have a unique "key" prop.
Check the render method of `Body`.
    at Body (antd.js)
    at Table (antd.js)
    at Experiments (Experiments.tsx:62:20)
```

---

## 二、根因 1：`rowKey` 指向了数据源里不存在的字段

```tsx
// Experiments.tsx:283（"实验方法模板"表格）
<Table rowKey="key" dataSource={METHOD_OPTIONS} />
```

而数据源根本没有 `key` 字段：

```ts
const METHOD_OPTIONS = [
  { value: 'landing_page', label: '落地页测试' },   // ← 只有 value / label
  ...
]
```

⇒ 每行的 rowKey 都是 `undefined` ⇒ 所有行 key 相同 ⇒ 警告出现在 `Table.Body`。

**修复**：`rowKey="value"`（`value` 是唯一键）。

---

## 三、全项目 `rowKey` 审计（12 处）

顺着这个缺陷把全项目扫了一遍：

| 文件:行 | rowKey | 数据源 | 结论 |
| --- | --- | --- | --- |
| `Experiments.tsx:283` | ~~`key`~~ → `value` | `METHOD_OPTIONS` `{value,label}` | ❌ **无 key 字段** → 已修 |
| `Scorecard.tsx:368` | ~~`agent`~~ → `seq` | `trace`（含反馈循环重跑） | ❌ **agent 重复** → 已修 |
| `Monitor.tsx:413` | ~~`dimension`~~ → `key` | `suggestions.map(s => ({...s, key: s.dimension}))` | ⚠️ 已 map 出 `key`，改用它更一致 |
| `Graph.tsx:261` | `name` | 中心性 `{name,key,label,...}` | ✅ |
| `Graph.tsx:307` | `name` | 空白点 `{name,degree}` | ✅ |
| `Monitor.tsx:487` | `key` | `dimensions.map(d => ({key: d.key, ...}))` | ✅ |
| `Scorecard.tsx:222` | `key` | `ScoreDimension` 类型**含 key** | ✅ |
| `Settings.tsx:94` | `id` | 用户列表含 `id` | ✅ |
| `Settings.tsx:126` | `role` | 角色列表含 `role` | ✅ |
| `Settings.tsx:196` | `key` | `/signals/sources` 返回 `{key,name,...}` | ✅ |
| `Settings.tsx:232` | `task` | `/admin/model-config` 的 `routes` 含 `task` | ✅ |
| `Settings.tsx:308` | `id` | 任务列表含 `id` | ✅ |

> `Graph.tsx:307` 的 `name` 在本轮之前的图谱修复中已改为**可读名**（`title or key`），
> 因此这里不存在"UUID 重复"问题；但需注意：若两条不同节点显示名相同，仍会冲突 —— 当前数据规模下未出现。

---

## 四、根因 2：`trace` 的 `agent` 字段本质会重复

`Scorecard.tsx:368` 的 Agent 执行轨迹表用 `rowKey="agent"`，但 `agent` 是 **agent key**
（`backend/app/agents/base.py::write_state`）：

```python
state.trace.append({"agent": self.key, ...})
```

而 **编排器的反馈循环会重跑整条流水线**（`Orchestrator.run`：verdict 为
`verify`/`watch` 且有冲突点时，把冲突写进 query 后重跑）。实测：

```
trace 14 条
  agent 重复: {'trend':2,'demand':2,'market':2,'tech':2,'competition':2,'decision':2,'monitor':2}
```

**7 个 Agent 每个都出现 2 次** —— 即反馈循环重跑的是**全部 Agent**，不只是 trend/demand。

⇒ `rowKey="agent"` 在触发反馈循环时**必然**冲突。

### 修复

后端给每条 trace 加唯一序号：

```python
state.trace.append({
    "seq": len(state.trace) + 1,      # ← 新增
    "agent": self.key,
    ...
})
```

前端 `type AgentTraceItem` 加 `seq?: number`，表格改用：

```tsx
rowKey={(r: AgentTraceItem, i?: number) => String(r.seq ?? `${r.agent}-${i}`)}
```

（`?? ` 兜底：兼容可能存在的无 `seq` 历史数据，避免再次出现 undefined key）

### 验证

```
trace 14 条
  seq 值: [1,2,3,...,14]
  seq 唯一: True | 连续 1..N: True
  agent 重复: {'trend':2,'demand':2,'market':2,'tech':2,'competition':2,'decision':2,'monitor':2}
  → 前端 rowKey 用 seq 后不再冲突
```

---

## 五、验证矩阵

| 项目 | 结果 |
| --- | --- |
| 前端 `npm run build`（含 tsc typecheck） | ✅ 通过（7.28s） |
| pytest 全量 | **267 / 267** ✅ |
| API 契约校验 | **56 / 56** ✅ |
| E2E 冒烟 | **30 / 30** ✅ |
| trace seq 唯一性 | 唯一且 1..N 连续 ✅ |
| 5180 / 8020 | 200 / 200 ✅ |

---

## 六、改动文件清单

| 文件 | 改动 |
| --- | --- |
| `frontend/src/pages/Experiments.tsx` | `rowKey="key"` → `rowKey="value"`（数据源无 key） |
| `frontend/src/pages/Scorecard.tsx` | Agent 轨迹表 `rowKey="agent"` → 基于 `seq` 的函数 |
| `frontend/src/pages/Monitor.tsx` | 校准建议表 `rowKey="dimension"` → `rowKey="key"`（数据源已 map 出 key） |
| `frontend/src/api/types.ts` | `AgentTraceItem` 增加 `seq?: number` |
| `backend/app/agents/base.py` | `write_state` 给 trace 元素加唯一 `seq` |
| `frontend/dist/` | 重新构建 |

---

## 七、流程发现

1. **`rowKey` 是"约定字段"，编译器管不了**：TS 不会校验 `rowKey` 的值是否真的存在于 dataSource 里，
   `dataSource` 又是 `any[]`，所以这类错误**只在运行时以 warning 形式出现**，很容易被忽略。
   → 建议：凡是 `rowKey` 用字符串字面量的地方，都在类型上确认该字段存在。
2. **"唯一键"必须选**语义上保证唯一**的字段**：`agent` 看起来像唯一标识，
   但反馈循环让同一 agent 出现多次 —— **业务上"会重复"的字段不能当 key**。
   遇到无法保证唯一时，用后端生成的序号（`seq`）或 `(业务键 + 序号)` 组合。
3. **一次报错值得全量审计**：用户只报了 Experiments 一处，
   但同类模式在项目里有 12 处，其中 2 处是真缺陷、1 处是不一致写法。
   （这与上一轮"图谱 UUID 显示名"在 `to_cytoscape` 与 `centrality` 两处同源的情形一致。）
