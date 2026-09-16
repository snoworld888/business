# RAGFlow 检索参数 `keyword`（大模型关键词抽取）开关评估

> 结论先行 | 证据链 | 实测日期 2026-09-14
> 相关脚本：`scripts/compare_ragflow_keyword.py`（本次新增，可复现）

---

## 1. 结论

**`RAGFLOW_KEYWORD_EXTRACTION` 已由 `true` 回退为 `false`（默认值）。**

对本项目的检索效果**无正向影响，且部分指标为负**：

| 维度 | 实测结果 | 判断 |
| --- | --- | --- |
| 召回数量 | 命中合计 33 → 34（8 场景） | 基本无变化（+1，且来自噪声场景） |
| 召回范围 | 命中集合平均 Jaccard **0.909** | 高度重合，几乎不改变召回集 |
| 排序质量 | Top1 仅 **1/8** 场景变化，且新 Top1 并非更相关 | 无改善 |
| 相似度打分 | 短查询 `固态电池` 最高相似度 **0.709 → 0.623**（↓12%），词项分 0.729 → 0.609（↓16%） | **负向**：拼接导致词项分被稀释 |
| 延迟 | 中位数 **100 ms → 1945 ms（×19.4）** | 成本极高 |
| 运行时验证 | `/rag/search` **1.56–1.78 s → 0.077–0.165 s** | 与离线结论一致 |

**保留项**：RAGFlow 租户默认对话模型 `qwen3.8:27b@Ollama` 的登记**保留不回退** —— 登记不等于加载（仅在调用时才占用显存），回退零收益，保留可避免日后重复踩 `Tenant Model ... chat not found`。

---

## 2. 参数的真实语义（源码证据）

该参数**不是**「开启/关闭 BM25」，**也不是**相似度公式的开关 —— 它只改**检索用的 query 文本**。

`D:\01code\ragflow\api\apps\restful_apis\chunk_api.py:307-309`：

```python
if req.get("keyword", False):
    chat_model_config = get_tenant_default_model_by_type(kb.tenant_id, LLMType.CHAT)
    question += await keyword_extraction(LLMBundle(kb.tenant_id, chat_model_config), question)

ranks = await settings.retriever.retrieval(question, embd_mdl, tenant_ids, kb_ids, ...)
```

抽取逻辑 `D:\01code\ragflow\rag\prompts\generator.py:214-226`：

```python
async def keyword_extraction(chat_mdl, content, topn=3):
    template = PROMPT_JINJA_ENV.from_string(KEYWORD_PROMPT_TEMPLATE)
    rendered_prompt = template.render(content=content, topn=topn)
    msg = [{"role": "system", "content": rendered_prompt}, {"role": "user", "content": "Output: "}]
    _, msg = message_fit_in(msg, chat_mdl.max_length)
    kwd = await chat_mdl.async_chat(rendered_prompt, msg[1:], {"temperature": 0.2})
    ...
    return kwd
```

提示词 `D:\01code\ragflow\rag\prompts\keyword_prompt.md`：抽取 **topn=3** 个关键词，英文逗号分隔，语言与原文一致，**只输出关键词**。

三条由源码直接推出的事实：

1. **它只做「追加」**：`question += kwd`，**无分隔符**直接拼接 → query 变长，原有关键词的相对权重被稀释。
2. **它依赖 LLM**：走租户默认 chat 模型 → 每次检索都是一次 LLM 生成（这正是 ×19.4 延迟的来源）。
3. **它不参与打分公式**：`term_similarity` / `vector_similarity` 由 `retriever.retrieval()` 统一计算，与 `keyword` 无关。**关闭后词项（BM25）检索依然在**，公式为 `sim = 0.3 × vector + 0.7 × term`（本项目 `RAGFLOW_VECTOR_WEIGHT=0.3`）。

本项目侧的注入点：`backend/app/services/rag.py:238` → `backend/app/services/ragflow.py:296`（`"keyword": bool(self.keyword_extraction)`）。
默认值本来就是 `False`（`backend/app/core/config.py:80`），本次为**回退到默认**。

---

## 3. 实测方法

- 脚本：`scripts/compare_ragflow_keyword.py`（8 个场景 × 2 档，直接打 RAGFlow `/api/v1/retrieval`）
- 语料：dataset `trackcompass-signals`，**40 篇文档 / 40 chunk**，嵌入模型 `bge-m3:latest@Ollama`
- 参数与线上完全一致：`similarity_threshold=0.2`、`vector_similarity_weight=0.3`、`top_k=20`
- 场景构成：4 条**项目真实检索串**（按赛道聚合信号关键词，由 `agents/base.py::build_retrieval_query` 的退化分支产出）+ 3 条边界样本（短查询 1 词 / 2 词、自然语言问句）+ 1 条分类标签原样

> ⚠️ 语料仅 40 篇，属**小语料**。结论对「本项目当前数据规模」成立；语料上量后应向真实语料回归复测。

---

## 4. 对照结果

| # | 场景 | 检索串 | 命中数 off/on | 最高相似度 off→on | 最高 term off→on | 最高 vec off→on | 耗时 ms off→on | 命中一致率 |
| - | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 真实拼接串·AI+制造 | `计算机视觉 智能质检 工业互联网 预测性维护…` | 6 / 6 | 0.282 → 0.268 | 0.128 → 0.104 | 0.639 → 0.651 | 112 → 3189 | 6/6 = 1.0 |
| 2 | 真实拼接串·AI+医疗 | `医疗器械 深度学习 计算机视觉` | 3 / 2 | 0.264 → 0.270 | 0.138 → 0.123 | 0.590 → 0.612 | 459 → 1351 | 2/3 = 0.667 |
| 3 | 真实拼接串·新能源科技 | `储能 固态电池 新材料` | 5 / 5 | 0.497 → 0.476 | 0.396 → 0.375 | 0.730 → 0.712 | 95 → 1365 | 5/5 = 1.0 |
| 4 | 真实拼接串·具身智能 | `具身智能 机器人 传感器 机器学习 深度学习` | 7 / 7 | 0.275 → 0.283 | 0.096 → 0.115 | 0.693 → 0.676 | 109 → 2411 | 7/7 = 1.0 |
| 5 | 短查询·2 词 | `缺陷检测 人形机器人` | 6 / 7 | 0.302 → 0.285 | 0.161 → 0.137 | 0.633 → 0.642 | 84 → 2469 | 6/7 = 0.857 |
| 6 | 短查询·1 词 | `固态电池` | 3 / 3 | **0.709 → 0.623** | **0.729 → 0.609** | 0.663 → 0.653 | 105 → 1755 | 3/3 = 1.0 |
| 7 | 自然语言问句 | `工业质检领域有哪些新政策和融资动向` | 3 / 4 | 0.261 → 0.265 | 0.116 → 0.113 | 0.601 → 0.619 | 77 → 1741 | 3/4 = 0.75 |
| 8 | 分类标签原样 | `AI 制造` | 0 / 0 | 0.000 → 0.000 | 0.000 → 0.000 | 0.000 → 0.000 | 79 → 2136 | 1.0 |

**汇总**：耗时中位数 **100 ms → 1945 ms（×19.4）**；命中合计 33 → 34；Top1 变化 1/8；平均 Jaccard **0.909**。

> 说明：两次独立运行中，`off` 列亦有 ±0.02 级别漂移（如场景 3 的 0.497/0.476）。因此漂移**不宜归因于 LLM 抽取**，更可能来自检索侧（ANN 近似检索 / ES 段合并）。本表取第二次（含上表全部场景）数据。

---

## 5. 关键词抽取实况 —— 为什么"没效果"甚至"负效果"

本地复现（同一提示词、同一模型 `qwen3.8:27b`，`think=false` 仅为提速）：

| 原始检索串 | LLM 抽取结果（topn=3） | RAGFlow 实际拿去检索的串（`question += kwd`） |
| --- | --- | --- |
| `固态电池` | `固态电池` | `固态电池固态电池` |
| `工业质检领域有哪些新政策和融资动向` | `工业质检, 新政策, 融资动向` | `工业质检领域有哪些新政策和融资动向工业质检, 新政策, 融资动向` |

两个直接导致"无收益"的机制：

1. **同义重复**：短查询下 LLM 抽出的词与原文**完全相同**，拼接后变成刻意的重复词（`固态电池固态电池`）。它没有引入任何新语义信息，却改变了 query 的词项构成 → 场景 6 的 term 分从 0.729 跌到 0.609。**这是可测量的负作用**。
2. **拼接无分隔符 + 逗号残留**：`question += kwd` 不插空格；且提示词要求"英文逗号分隔"，模型会把逗号一起输出（`工业质检, 新政策`）。逗号在 RAGFlow 的 ES `query_string` 中属语法字符 —— 与项目此前踩过的 `+` 坑（`build_retrieval_query` 注释：`"AI+制造"` 被解析成"必须同时含 AI 与 制造"导致 0 命中）**同源风险**。本项目侧只对**原始 query** 做了元字符清洗（`ragflow.py::_sanitize_query`），**拼接进去的部分不归我们控制**。

---

## 6. 运行时验证（区分代码层 / 运行时层）

关键教训：`.env` 改动**不会**自动生效 —— 引擎在 `_build_rag()` 实例化时读取该配置（`rag.py:238`），且旧进程**并非 `--reload` 模式**（`touch app/main.py` 不触发重载，实测 PID 不变）。因此必须硬重启进程。

同一接口 `GET /api/v1/rag/search?q=固态电池&top_k=5` 前后对比：

| 时点 | 耗时 | 最高相似度 | 最高词项分 | 命中 |
| --- | --- | --- | --- | --- |
| 改前（keyword=true） | 1.777 s / 1.772 s / 1.564 s | 0.6225 | 0.6093 | 3 |
| 改后（keyword=false） | 0.165 s / 0.077 s / 0.093 s | **0.7092** | **0.7288** | 3 |

改后数值与第 4 节离线实验 `off` 列（0.709 / 0.729）**逐位吻合** —— 双向印证配置已在运行时生效。

进程与端口：`127.0.0.1:8020`，启动命令 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8020`（日志 `backend/uvicorn.out`）。

---

## 7. 影响面盘点

**关闭后不受影响的**：

- 词项（BM25）检索 —— 依然生效，权重仍为 0.7
- 向量检索、RRF 融合、`similarity_threshold` 过滤
- 证据链（`citation_ratio`）与下钻 —— `document_keyword` ↔ `signal_id` 映射不变

**关闭后改善的**：

- 检索延迟降一个数量级（~1.8 s → ~0.1 s）
- **消除一个隐式 GPU 排队点**：此前每次检索都要调 27b，而 7 个 Agent 也在调同一个 27b 实例 → 检索会与分析任务抢占同一 runner。日志中 `POST /api/v1/trends/scan 58159.7 ms` 这类长任务期间，检索侧再插入 LLM 调用会显著放大排队。现在检索只依赖 bge-m3 嵌入（RAGFlow 内部调用），不再与主推理争抢。
- 消除「LLM 抽取词污染 query 语法」的隐患（需满足前置：租户默认 chat 模型已登记）

**唯一理论收益（本项目未观测到）**：当 query 极短且原词与文档用词**不同构**时，LLM 扩展词本可提升词项召回。本项目查询串由信号关键词聚合而成，与文档用词天然同构 → 该收益不存在，反而出现同义重复。

---

## 8. 复现方式

```bash
"C:/Users/PC/.workbuddy/binaries/python/envs/trackcompass/Scripts/python.exe" \
    scripts/compare_ragflow_keyword.py
```

输出即为第 4、5 节的 Markdown 表。改配置后需**硬重启**后端（非 `--reload`）：
`backend/.env` → `RAGFLOW_KEYWORD_EXTRACTION=false`（已改）。

---

## 9. 待复测条件

若出现以下情况，建议重开本评估：

1. 语料量级从 40 篇提升到**数百篇以上**（当前召回集太小，Top1 无区分度）
2. 引入**外部真实采集器**后，query 与文档用词不再同构（例如用户直接输入口语化问句）
3. RAGFlow 升级后 `keyword` 语义变更（本评估基于 v0.25.x 源码）
