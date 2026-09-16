# RAGFlow 接入评估

> 问题：能否把 TrackCompass 自建的 RAG 换成机器上已有的 RAGFlow？
> 全部结论基于 2026-09-14 的实测（Docker / MySQL / API 探测），非推断。

---

## 零、已实施（2026-09-14 用户决定：**全量替换** → ✅ **已真机激活**）

| 项 | 状态 |
| --- | --- |
| 适配器 `app/services/ragflow.py` | ✅ 已实现（`RAGFlowEngine`，与 `RAGEngine` 同契约） |
| 后端开关 `RAG_BACKEND=ragflow` | ✅ 已设为默认；未配置/不可达时**自动降级 memory 并告警** |
| 信号同步（文档名 = `signal_id`） | ✅ 已实现，幂等（已存在则跳过） |
| 异步解析等待 + 状态轮询 | ✅ 已实现（默认 90s，超时仅告警） |
| 证据链映射（元数据旁路映射表） | ✅ 已实现（`backend/ragflow_index_map.json`，重启自动加载） |
| 单元测试 | ✅ **新增 20 个**（Mock RAGFlow，覆盖就绪/上传/幂等/映射/过滤/去重/降级/关键词开关） |
| 回归 | ✅ pytest **246/246**、E2E **30/30**、契约 **55/55** |
| 一键激活与自查 | ✅ `scripts/check_ragflow.py` |
| **真机激活** | ✅ **已完成**（2026-09-14 16:09） |

### 0.1 激活结果（实测）

| 项 | 实测值 |
| --- | --- |
| dataset | `trackcompass-signals` / `19a68252b01311f18c92bfc493440fd4` |
| 索引规模 | **40 篇文档 / 40 chunks，`run=3`（解析完成）40 篇、失败 0 篇** |
| 嵌入模型 | **`bge-m3:latest@Ollama`（1024 维，真语义）** |
| 启动日志 | `INFO app.services.rag: RAG 后端 = ragflow @ http://localhost:9380`（原为 `WARNING ... 降级`） |
| 检索耗时 | ≈162 ms（自建 memory 时代亚毫秒） |
| 向量分对比 | **0.57–0.72**（哈希嵌入时代 **0.15–0.29**）⇒ 语义检索真正生效 |
| 幂等验证 | 第二次 `reindex` 只新增 37 篇（前 3 篇已存在被跳过） |

### 0.2 ⚠️ 激活中踩到的真实坑：`keyword` 参数会让整条检索失败

首次冒烟报错：

```
RAGFlow 检索返回 code=100:
LookupError('Tenant Model with name qwen3.5:35b@Ollama and type chat not found')
```

**根因**（RAGFlow 容器内 `api/apps/sdk/doc.py`）：

```python
if req.get("keyword", False):
    chat_model_config = get_tenant_default_model_by_type(kb.tenant_id, LLMType.CHAT)
    chat_mdl = LLMBundle(kb.tenant_id, chat_model_config)   # ← 这里抛 LookupError
    question += await keyword_extraction(chat_mdl, question)
```

对照 RAGFlow 库：

| 项 | 值 |
| --- | --- |
| `tenant.llm_id`（租户默认对话模型） | **`qwen3.5:35b@Ollama`** ← **未登记** |
| `tenant_llm` 实际登记 | `qwen2.5:7b`(chat)、`bge-m3:latest`(embedding) |

⇒ 原实现传 `"keyword": True`，触发 RAGFlow 去调一个**未注册**的对话模型，**整条检索直接失败**。

**处理**：新增开关 `RAGFLOW_KEYWORD_EXTRACTION`，**默认 `false`** —— 只用向量 + BM25，
不依赖任何对话模型。若日后在 RAGFlow 中把租户默认对话模型改为已注册的模型，可再开启。

> 顺带修掉一个**会掩盖故障的自查缺陷**：`retrieve()` 原先只在日志里 warning，
> 自查脚本会把"检索失败"报成"无命中"。现新增 `last_error` 字段并在脚本中区分二者。

**当前配置**（`backend/.env`，已 gitignore）：

```ini
RAG_BACKEND=ragflow
RAGFLOW_BASE_URL=http://localhost:9380
RAGFLOW_API_KEY=ragflow-nanobot-mcp-key-2026
RAGFLOW_DATASET_ID=19a68252b01311f18c92bfc493440fd4
RAGFLOW_KEYWORD_EXTRACTION=false
```

---

### 0.3 R6 / R7 已执行（2026-09-14 17:00，用户授权）

**R6 —— 开启大模型关键词抽取**

前置条件是修掉该租户指向未注册模型的配置。执行内容：

| 步骤 | 操作 | 说明 |
| --- | --- | --- |
| 1 | 备份 | `docs/backup/ragflow_tenant_before_20260914.sql`、`..._tenant_llm_before_...sql`（保留原值可回滚） |
| 2 | 登记 `qwen3.8:27b` 到 `tenant_llm` | 让 RAGFlow 认得这个模型 |
| 3 | `tenant.llm_id` → **`qwen3.8:27b@Ollama`** | 原值 `qwen3.5:35b@Ollama`（未注册） |
| 4 | 清 RAGFlow Redis 缓存 | 避免读到旧的模型配置 |
| 5 | `.env` 设 `RAGFLOW_KEYWORD_EXTRACTION=true` | 启用 |

> **为什么选 27b 而不是已登记的 `qwen2.5:7b`**：选 7b 会引入**第 3 个常驻模型**，
> 需要把 `OLLAMA_MAX_LOADED_MODELS` 从 2 提到 3；而 27b 与 Hermes、TrackCompass
> **共用同一个 runner**（关键：`qwen3.8:27b` 的模型默认 `num_ctx` 就是 **131072**，
> 三方都不显式传 `num_ctx` ⇒ 落在同一个 runner 上），常驻模型数仍是 2。

**实测成本（同一查询，keyword 开关对比）**

| 配置 | 命中 | 耗时 |
| --- | --- | --- |
| `keyword=false`（向量 + BM25） | 6 | **107–278 ms** |
| `keyword=true`（27b 抽取） | 6 | **2.6–10.5 s** |

⚠️ **诚实结论：本数据集上关键词抽取没有带来命中提升（都是 6 条），但把单次检索从 ~0.15 s 拉到 ~3 s（约 30×）。**
这是为「短查询召回」买的保险，实际收益需在**真实采集的语料**上再评估。
若要回退，只需 `.env` 设 `RAGFLOW_KEYWORD_EXTRACTION=false`（RAGFlow 侧配置无需改动）。

**R7 —— 切换到真实大模型（与 Hermes 共用 27b）**

```ini
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL_SIMPLE/COMPLEX/PRIVATE=qwen3.8:27b
```

> 三档全部指向 27b：`monitor` Agent 走 `simple` 档，若指向 `qwen2.5:7b` 会引入第 3 个模型。

**实测效果**

| 指标 | 结果 |
| --- | --- |
| 常驻模型数 | **2**（`qwen3.8:27b` 17.87 GB @131072 + `bge-m3` 0.66 GB）＝ **18.54 GB / 32 GB** |
| 七 Agent 分析总耗时 | **418 s → 47 s**（优化后 30 项 E2E 全绿） |
| 单个 Agent 耗时 | 19.5–129 s → **4.8–12.5 s** |

**性能优化的两个关键改动**

1. **关闭 thinking**（`LLM_OLLAMA_THINK=false`）：qwen3.8 默认先输出推理链再给答案。
   实测同一请求 `think=false` **0.9 s** vs 默认 **2.6 s**（快 2.9×）。
2. **强制 JSON + 限制生成上限**（`LLM_OLLAMA_JSON_FORMAT=true`、`LLM_OLLAMA_NUM_PREDICT=2048`）：
   模型自带 `num_predict=8192`，不封顶会放大最坏情况延迟。

**⚠️ 切真模型暴露并修复的 5 类契约问题**（规则引擎时代全部被掩盖）

| # | 问题 | 后果 | 修复 |
| --- | --- | --- | --- |
| 1 | **检索串 = 行业标签** | `"AI+制造"` 里的 `+` 被 ES 当必需项 ⇒ **0 命中** ⇒ 模型**凭空编造**产业事实 | `build_retrieval_query` 用信号关键词构造；适配器加 ES 元字符净化 |
| 2 | **RAG 无命中时零证据** | 模型无依据乱编 | `retrieve()` 回退本地信号（证据永不为空） |
| 3 | **`trends[].score` 缺失** | 落库 `float(None or 0)` ⇒ 趋势评分写成 **0.0**，雷达排序垫底 | 用 `confidence = 0.35 + score×0.65` 的**反函数**还原 |
| 4 | **`verdict: {}` / 上游字段类型错乱** | `/score` 的 `verdict.label` **KeyError**；`coverage` 是字符串让规则基线崩溃 | 声明 `required_keys` 兜底 + `as_dict/dicts_only` 类型护栏 |
| 5 | **`total_score` 与维度不自洽** | Σ贡献 ≠ 总分（E2E 断言失败） | 不采信模型自报总分，用最终维度 + 配置权重**重算**，verdict 同步重算 |

**回归**：pytest **246/246**、E2E **30/30**、契约 **55/55**、UI/API 全 200。

---

## 一、结论

| 问题 | 答案 |
| --- | --- |
| RAGFlow 可用吗？ | ✅ **完全可用且在真实使用中** —— 144 篇文档解析完成、7353 个 chunk、6 个知识库 |
| 技术上能接吗？ | ✅ **能**。TrackCompass 是适配器架构，加一个 `RAGFlowRetriever` 即可，**业务代码零改动** |
| 建议全量替换吗？ | 🔴 **不建议**。见 §4 —— 对 40 条**结构化短信号**，RAGFlow 反而更慢、更重 |
| 那 RAGFlow 用在哪？ | ⭐ **用在真正的短板上：长文档**（PDF/Word 公司简介、专利全文、研报）—— 当前明确不支持 |
| 顺带结论 | ⭐ **无论接不接，TrackCompass 的嵌入都应切 `bge-m3`** —— 而且 RAGFlow 已在用同一实例，**不额外占显存** |

---

## 二、RAGFlow 现状（实测证据）

| 项 | 实测值 |
| --- | --- |
| 容器 | `docker-ragflow-cpu-1`，镜像 `infiniflow/ragflow:latest`，Up 4 天 |
| 版本 | 容器 **v0.25.0-63-gfb95136f3**（本地仓库 0.25.5） |
| Web UI | `http://localhost:8060` → 200 |
| HTTP API | `http://localhost:9380`，无 key 返回 `{"code":401}` ⇒ **鉴权正常** |
| 文档引擎 | **Elasticsearch**（宿主 `:1200`） |
| 依赖栈 | MySQL `:3307` / Redis `:6379` / MinIO `:9000` / ES `:1200` |
| 已有知识库 | **6 个**，其中最大的 136 文档 / 7353 chunk |
| 解析状态 | **144 篇 run=3（完成）**，2 篇 run=4（失败） |
| 已有 API Key | 1 个 |
| GPU | **未分配**（CPU 版镜像，`DeviceRequests=null`） |

### ★ 关键发现：RAGFlow 用的就是本机 WSL 的 Ollama

```
llm_factory  model_type   llm_name        api_base
Ollama       embedding    bge-m3:latest   http://172.25.252.110:11434
Ollama       chat         qwen2.5:7b      http://172.25.252.110:11434
```

`172.25.252.110` = **本机 WSL 的 IP**。

**三个连带含义**：

1. **嵌入模型就是 `bge-m3`** —— 正是我们建议 TrackCompass 切换的目标模型
2. **RAGFlow 与 Hermes 共用同一个 Ollama + 同一块 RTX 5090** ⇒ 显存策略是同一个池子
3. **切 `bge-m3` 是「共用同一模型实例」而非「再加一个模型」** —— 显存增量几乎为 0
   （`bge-m3` 仅 0.66 GB；且已满足 `MAX_LOADED_MODELS=2` 的共存条件）

> ⚠️ `service_conf.yaml` 里写的 `http://tei:80` **只是默认模板，未启用**（无 TEI 容器）。
> 实际模型配置存 MySQL `tenant_llm` 表 —— 只看配置文件会得出错误结论。

---

## 三、接入的技术方案（若要做）

### 3.1 最小侵入实现

TrackCompass 的检索入口已经收敛为 `RAGEngine.retrieve(query, top_k, signal_type) → list[RetrievedDoc]`，
因此只需新增一个适配器，**保持同一签名**：

```
新增  services/ragflow.py         RAGFlowRetriever
改   .env                        RAG_BACKEND=memory|ragflow（新增一个开关）
改   services/rag.py             get_rag() 按开关返回实现
不动  7 个 Agent / API 层 / 前端   ← 关键：零改动
```

### 3.2 调用契约（取自 v0.25.5 官方文档）

```
POST http://localhost:9380/api/v1/retrieval
Headers: Authorization: Bearer <API_KEY>
Body: {
  "question": "...",
  "dataset_ids": ["<trackcompass 专用 dataset>"],
  "top_k": 6,
  "page_size": 6,
  "similarity_threshold": 0.2,
  "vector_similarity_weight": 0.3,
  "keyword": true,
  "highlight": false
}
```

返回：`chunks[].{ content, document_id, document_keyword, similarity, vector_similarity, term_similarity }`

### 3.3 证据链映射（关键细节）

RAGFlow **不返回 `signal_id`**，只返回 `document_id` + `document_keyword`（文档名）。

**解法**：同步信号时**用 `signal_id` 作为文档名**
⇒ 返回的 `document_keyword` 即 `signal_id`，可直接生成 `[E1] (source/type) title`，证据链完整保留。

### 3.4 落地步骤

1. 在 RAGFlow UI（`:8060`）新建 dataset，如 `trackcompass-signals`（**不动现有 6 个**）
2. 取 API Key（已有 1 个，可复用或新建）
3. 实现适配器 + 开关
4. 写同步脚本：40 条 signal → 40 篇文档（文档名 = `signal_id`），等待解析完成
5. 回归：246 单测 + 30 项 E2E 必须全绿（`RAG_BACKEND=memory` 时行为不变）

---

## 四、为什么不建议全量替换（5 个真实摩擦）

| # | 摩擦 | 影响 |
| --- | --- | --- |
| 1 | **异步解析** | RAGFlow 是「上传 → 异步 parse → 才可检索」。Signal 变更后有**不可检索窗口**；当前 `reindex()` 是**同步且即时**生效 |
| 2 | **粒度不匹配** | RAGFlow 面向长文档切块，而 TrackCompass 的 signal 平均仅几十字 ⇒ **切块会破坏语义完整性**，且它的 rerank 对短文本收益有限 |
| 3 | **延迟量级跃升** | 现状是**进程内亚毫秒**；走 RAGFlow 要经过 HTTP → ES → **Ollama 做一次 bge-m3 embedding**，量级为**几十到几百毫秒**。七 Agent 每轮多次检索 ⇒ 累加明显 |
| 4 | **隐式契约** | 证据链依赖「文档名 = signal_id」这一约定，**脆弱**（误改名即断链）；且解析失败（实测已有 2 篇失败）会静默丢证据 |
| 5 | **资源争抢** | RAGFlow 的 chat（`qwen2.5:7b`）与 embedding（`bge-m3`）都走**同一个 Ollama** ⇒ 与 Hermes / TrackCompass 的显存策略互相影响 |

**另有运维代价**：RAGFlow 依赖 4 个容器（ES / MySQL / Redis / MinIO）。自建 RAG 是**零依赖**（进程内 + 纯标准库）—— 这也是当时「208 个单测可断言」的基础。
（本项已由用户在知情后接受：2026-09-14 决定全量替换并完成激活。）

---

## 五、差异对比

| 维度 | 自建 RAG（现状） | RAGFlow |
| --- | --- | --- |
| 数据形态 | 40 条**结构化短信号** | 144 篇**长文档** / 7353 chunk |
| 检索 | 哈希向量 + BM25 + **RRF 融合** | bge-m3 向量 + 全文 + rerank + KG |
| 嵌入 | ❌ 哈希（**假语义**，向量分仅 0.15–0.29） | ✅ **bge-m3（真语义）** |
| 延迟 | ✅ 亚毫秒（进程内） | 几十~几百 ms |
| 证据链 | ✅ 直接返回 `signal_id` | ⚠️ 需靠文档名映射 |
| 多租户隔离 | ❌ **全局，无 tenant 概念** | ✅ **dataset 天然隔离** |
| 长文档解析 | ❌ 不支持 PDF/Word | ✅ 原生支持 |
| 运维 | ✅ 零依赖 | ⚠️ 4 个容器 |
| 可断言性 | ✅ 确定性、可单测 | ⚠️ 依赖外部服务，单测需 Mock |

---

## 六、推荐方案（按优先级）

> **实际决策（2026-09-14）：用户选择「全量替换」**，本节的第二步「双路并存」未被采纳 ——
> 保留于此作为权衡记录。§零 记录了已实施内容与激活步骤。

### 🥇 第一步（无论接不接，都建议做）

```bash
# backend/.env
EMBEDDING_BACKEND=ollama
EMBEDDING_OLLAMA_MODEL=bge-m3
```
**理由**：一次性解决「向量分只有 0.15–0.29、排序由 BM25 主导」的真正短板。
**增量显存 ≈ 0** —— 因为 **RAGFlow 已经在用 `bge-m3`，切过去是共用同一实例**。
（切换后需 `POST /api/v1/signals/reindex` 重建索引）

### 🥈 第二步（推荐组合：双路并存）

| 数据 | 走向 | 理由 |
| --- | --- | --- |
| **40 条结构化短信号** | 保留自建内存混合检索 | 快（亚毫秒）、可断言、RRF 已够用 |
| **长文档**（PDF/Word 公司简介、专利全文、研报） | **走 RAGFlow** | 正是 RAGFlow 强项，也是当前**明确的功能空缺**（`parse_profile` 拒绝二进制） |

这样既补上长文档能力，又不牺牲短信号的性能与可测试性。

### 🥉 第三步（仅当你更看重治理收益）

全量替换为 RAGFlow。收益：**统一到一个 RAG 平台** + **顺带修复跨租户隔离缺陷**（dataset 天然隔离）。
代价：§4 的 5 个摩擦 + 失去零依赖与亚毫秒延迟。

---

## 七、待裁决项

| 编号 | 事项 | 状态 / 建议 |
| --- | --- | --- |
| **R1** | 是否切 `EMBEDDING_BACKEND=ollama` + `bge-m3` | ⭐ 建议，收益大、成本低、显存增量≈0 —— **逻辑上已被 R2 覆盖**（检索侧已用 bge-m3） |
| **R2** | 接入方案：全量替换 / 双路并存 / 仅长文档 | ✅ **用户 2026-09-14 决定：全量替换，已完成激活**（列保留为决策记录） |
| **R3** | 提供 RAGFlow API Key | ✅ **已提供并写入 `backend/.env`**（该文件已 gitignore，不入库） |
| **R4** | 允许新建 `trackcompass-signals` dataset | ✅ **已授权并创建**；原有 6 个知识库**未被触碰**（仅新建 1 个） |
| **R5** | 是否允许我直连 RAGFlow MySQL 做只读核查 | ✅ 已做只读核查（仅 `COUNT`、库名/模型名/解析状态，**未读文档内容、未打印密钥**） |
| **R6** | ⏳ **仍在待决**：`RAGFLOW_KEYWORD_EXTRACTION` 是否要开启？ | 若要开启，需先把该租户的默认对话模型改为**已登记**的模型（如 `qwen2.5:7b`）—— 这会改动 RAGFlow 侧配置，**需另行授权** |
| **R7** | ⏳ **仍在待决**：是否把 `LLM_PROVIDER` 切到真实模型？ | 不切的话，RAG 的返回值仍然只进 prompt 而被 `rule` 引擎忽略（空转） |

---

## 八、本次核查的访问纪律

> 以下为**评估阶段**（接入前）的纪律记录；接入阶段用户已明确授权（R2/R3/R4）。

**评估阶段只做只读探测**：`docker ps / inspect / exec <只读命令>`、RAGFlow 的 `.env` 与 `service_conf.yaml`、
MySQL **仅统计**（`COUNT`、库名/模型名/解析状态，**未读取任何文档内容**）。

**评估阶段未做**：未创建 dataset、未上传文档、未调用检索接口、**未读取或打印 API Key 明文**、未修改 RAGFlow 任何配置。
