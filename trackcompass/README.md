# 赛道罗盘 TrackCompass

> AI 驱动的科技公司商机发现与赛道选择决策系统。
> 把「凭感觉选方向」变成 **信号扫描 → 机会生成 → 需求验证 → 技术匹配 → 决策报告 → 持续监控** 的可追溯流水线。

本仓库是依据 `需求文档.txt` / `设计文档.txt` / `技术文档.txt` / `原型设计.txt` 四份文档落地的 **可运行实现**。

---

## 1. 快速开始

### 1.1 后端（Python 3.13 + FastAPI）

```bash
# ① 创建独立虚拟环境（只需一次）
"C:/Users/PC/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m venv "C:/Users/PC/.workbuddy/binaries/python/envs/trackcompass"

# ② 安装依赖
cd D:/01code/business/trackcompass/backend
"C:/Users/PC/.workbuddy/binaries/python/envs/trackcompass/Scripts/python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# ③ 初始化数据库 + 写入种子数据（40 条信号）
"C:/Users/PC/.workbuddy/binaries/python/envs/trackcompass/Scripts/python.exe" -c "from app.main import bootstrap; print(bootstrap(seed=True, reset=True))"

# ④ 构建前端（只需一次，构建产物由后端同端口托管）
cd D:/01code/business/trackcompass/frontend
npm install --registry=https://registry.npmmirror.com
npm run build

# ⑤ 启动服务（单端口同时提供 UI 与 API；0.0.0.0 = 局域网可访问）
cd D:/01code/business/trackcompass/backend
"C:/Users/PC/.workbuddy/binaries/python/envs/trackcompass/Scripts/python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8020
```

启动后**同一个端口**即可访问全部内容（`<本机IP>` 见 `python scripts/lan_ip.py`）：

| 地址 | 说明 |
| --- | --- |
| <http://127.0.0.1:8020/> | 前端界面（本机；登录 `admin@demo.com` / `TrackCompass@2026`） |
| <http://本机IP:8020/> | 前端界面（局域网，同网段同事直接打开） |
| <http://127.0.0.1:8020/docs> | Swagger UI（OpenAPI 契约） |
| <http://127.0.0.1:8020/redoc> | ReDoc |
| <http://127.0.0.1:8020/api/v1/health> | 健康检查 |
| <http://127.0.0.1:8020/api/v1/agents> | 七 Agent 目录 |
| <http://127.0.0.1:8020/api/v1/workflow> | 编排图结构 |

> 前端使用 HashRouter，因此静态托管无需服务端做 history fallback。
>
> **局域网访问**：默认监听 `0.0.0.0`，一键脚本会打印可用的局域网地址。
> 仅本机使用请加 `HOST=127.0.0.1`（详见 `docs/局域网访问配置说明.md`）。
> ⚠️ 开放局域网后同网段任何人都能登录，演示账号是公开的 —— 仅限受信任内网使用。

### 1.2 前端开发模式（可选）

改前端代码需要热更新时才用；只跑通流程不需要。

```bash
cd D:/01code/business/trackcompass/frontend
npm run dev          # http://127.0.0.1:5180（局域网亦可），已代理 /api → 8020
```

> 端口说明：本机 8010 已被其他服务占用，因此统一使用 **8020**。
> 如需变更，同时修改 `frontend/vite.config.ts` 的 proxy target 与启动命令。
> 若要让后端不再托管前端，设置 `SERVE_FRONTEND=false`。
> ⚠️ Vite dev server **首次**访问会触发依赖预构建，可能耗时数秒（局域网首次打开尤其明显），之后就恢复正常。

### 1.3 演示账号

| 邮箱 | 角色 | 密码 |
| --- | --- | --- |
| admin@demo.com | 管理员 | TrackCompass@2026 |
| strategy@demo.com | 战略部 | TrackCompass@2026 |
| product@demo.com | 产品部 | TrackCompass@2026 |
| invest@demo.com | 投资部 | TrackCompass@2026 |
| expert@demo.com | 领域专家 | TrackCompass@2026 |

### 1.4 验证脚本

```bash
cd D:/01code/business/trackcompass
PY="C:/Users/PC/.workbuddy/binaries/python/envs/trackcompass/Scripts/python.exe"

# 单元 / 集成测试
cd backend && "$PY" -m pytest && cd ..

# 端到端 HTTP 冒烟（需服务已启动）
"$PY" scripts/e2e_smoke.py

# 前后端契约一致性（防 404）
"$PY" scripts/check_api_contract.py

# 局域网可访问性自检（本机 + 局域网 IP 两个来源各跑一遍）
"$PY" scripts/check_lan_access.py

# UI 冒烟（真实 Chromium，零依赖；可指定任意来源，含局域网地址）
node scripts/ui_smoke_persist.mjs
TC_BASE=http://192.168.2.192:8020 node scripts/ui_smoke_persist.mjs
```

---

## 2. 目录结构

```
trackcompass/
├── backend/
│   ├── app/
│   │   ├── core/           配置 / 数据库 / JWT+RBAC / 统一错误码 / 结构化日志
│   │   ├── models/         14 张领域表（技术文档 §3.3）
│   │   ├── schemas/        Pydantic 请求响应契约
│   │   ├── agents/         七个 AI Agent + 编排器（技术文档 §5）
│   │   ├── services/       认知层：embedding / 向量库 / 图谱 / RAG / 评分 / 报告
│   │   ├── api/v1/         12 个路由模块（技术文档 §6）
│   │   ├── seed/           种子数据（AI+制造 / AI+医疗 / 新能源科技 / 具身智能）
│   │   └── main.py         应用入口 + bootstrap()
│   ├── tests/              246 个测试用例
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api/            类型定义 + 统一请求封装
│       ├── components/     证据抽屉 / 雷达图 / Markdown 渲染 / 业务组件
│       ├── layout/         侧边导航 + 顶栏
│       ├── pages/          11 个页面（P01–P11）
│       └── styles/tokens.css  设计 Token（原型文档 §4.1）
├── docs/                   研发计划 / 测试报告 / 使用手册 / 验收标准
└── scripts/                启动脚本
```

---

## 3. 架构落位

### 3.1 四层架构

| 层 | 落地位置 |
| --- | --- |
| 信号层 | `app/api/v1/signals.py`、`app/models/Signal`、11 类数据源状态 |
| 认知层 | `app/services/{embeddings,vector_store,graph,rag}.py` |
| 决策层 | `app/agents/*`、`app/services/{scoring,analysis,report}.py` |
| 行动层 | `app/api/v1/{alerts,experiments}.py`、复盘库与模型校准 |

### 3.2 七个 AI Agent（`app/agents/`）

编排为 LangGraph 语义的有向图，支持并行、条件分支与有界反馈循环：

```
[趋势侦察] ┐
           ├─→ [市场测算] ┐
[需求挖掘] ┘  [竞争情报] ├─→ [决策综合] → [监控预警] ─┐
              [技术评估] ┘                            │
     ↑                                               │
     └────────────── 反馈循环（携带冲突点重跑）────────┘
```

| Agent | 文件 | 核心算法 |
| --- | --- | --- |
| 趋势侦察 | `agents/trend.py` | `0.3·signal + 0.3·growth + 0.2·policy + 0.2·capital`（技术文档 §4.2 原文公式） |
| 需求挖掘 | `agents/demand.py` | `0.3·freq + 0.3·负面情感 + 0.2·付费意愿 + 0.2·紧迫度`（§4.4） |
| 市场测算 | `agents/market.py` | 自下而上 × 自上而下，几何均值融合 |
| 竞争情报 | `agents/competition.py` | 大厂威胁评分 + 空白点识别 |
| 技术评估 | `agents/tech.py` | TRL/CRL/BRL/IPRL/TMRL/FRL 六维（1–9 量纲） |
| 决策综合 | `agents/decision.py` | 七维加权评分 + 冲突点检测 + 可解释贡献度 |
| 监控预警 | `agents/monitor.py` | 6 条预警规则 + 权重校准建议 |

> **关键设计**：每个 Agent 同时提供 `rule()`（确定性规则实现，离线可跑、可断言、可回归）
> 与 `build_prompt()`（Prompt 模板）。`LLM_PROVIDER=rule` 时完全离线，
> 切换为 `openai` / `ollama` 后启用真实 LLM，规则实现自动降级为校验基线 —— **换模型不改业务**。

### 3.3 重型组件以「开关 + 适配器」接入

| 组件 | 默认（零依赖） | 生产 |
| --- | --- | --- |
| 关系库 | SQLite | `DATABASE_URL=postgresql+psycopg://…` |
| 向量库 | 内存 + 余弦 | `VECTOR_BACKEND=milvus`（IVF_FLAT + COSINE） |
| 知识图谱 | NetworkX | `GRAPH_BACKEND=neo4j`（Cypher） |
| Embedding | 确定性哈希（1536 维） | `EMBEDDING_BACKEND=openai\|ollama`（BGE-M3） |
| LLM | 规则引擎 | `LLM_PROVIDER=openai\|ollama` |
| 报告导出 | Markdown / HTML / JSON | 安装 `python-pptx` / `python-docx` 后自动启用 PPTX / DOCX |

未配置任何中间件即可跑通全链路；配置后业务代码零改动。

---

## 4. 核心业务链路

```
能力画像 ──→ 搜索边界 ──→ 趋势扫描 ──→ 机会池 ──→ 七 Agent 分析
   │                                                      │
   └──────────── 赛道评分（可下钻证据） ←──────────────────┘
                          │
        验证实验 ←────────┤────────→ 决策报告（9 段结构）
                          │
                    监控预警 ──→ 复盘 ──→ 模型校准（版本化）
```

API 层完整覆盖技术文档 §6.2 的 8 个核心接口，并扩展了信号、图谱、评分卡版本、实验模板、复盘、审计等接口，共 **60+ 端点**。

---

## 5. 文档

| 文档 | 内容 |
| --- | --- |
| `docs/研发计划.md` | 分阶段研发计划与当前进度 |
| `docs/大模型与智能体接入说明.md` | **当前接入状态、判定证据、模型清单与切换步骤** |
| `docs/Hermes接入评估.md` | **是否对接 Hermes 的核查证据、能力错配分析与接入建议** |
| `docs/模型调用路径与显存评估.md` | **模型调用点枚举、推理落点定位、「走 Hermes 省显存」方案证伪与替代路径** |
| `docs/显存优化与云模型评估.md` | **实测显存明细、KV 量化/单模型驻留等零代价优化杠杆、云模型限制与数据出境风险；含 D1/D2 执行与验证记录** |
| `docs/趋势雷达工作原理与诊断.md` | **趋势雷达四步原理、为何扫不出大模型/智能体（四层归因 + 注入实证）、修复优先级** |
| `docs/数据来源与真实度说明.md` | **每个数字从哪来：你填写的 / 系统默认 / 计算派生 / 网络抓取；RAG 在链路中的真实位置（含对照实验）；6 个「看起来像 A 其实是 B」的认知陷阱** |
| `docs/RAGFlow接入评估.md` | **RAGFlow 全量替换的实测证据、激活结果与踩坑记录（`keyword` 参数导致检索失败的根因）** |
| `docs/测试报告.md` | 测试用例清单与实测结果 |
| `docs/产品使用手册.md` | 面向最终用户的操作说明 |
| `docs/局域网访问配置说明.md` | **绑定地址改造、开关方式、防火墙放行、安全提示与实测证据** |
| `docs/验收标准.md` | 逐条对照四份文档的验收矩阵 |

---

## 6. 大模型与智能体：当前状态速查

| 项 | 状态 |
| --- | --- |
| **智能体** | ✅ **已接入并真实运行** —— 7 个 Agent，4 层编排（2 层并行）+ 有界反馈循环 |
| **大模型** | ⚠️ **当前未接入** —— `LLM_PROVIDER=rule`，Agent 大脑是确定性规则引擎，零网络调用 |
| 接入条件 | ✅ 本机 Ollama 在线（`http://localhost:11434`，9 个模型），已实测跑通真实推理（149ms） |
| 切换成本 | 改 `backend/.env` 4 行 + 重启，**业务代码零改动** |

一键自查：

```bash
python scripts/check_llm_connection.py
```

## 🔎 RAG 后端：已全量替换为 RAGFlow

| 项 | 状态 |
| --- | --- |
| 适配器 | `backend/app/services/ragflow.py` —— 与 `RAGEngine` 同契约，**7 Agent / API / 前端零改动** |
| 默认后端 | `RAG_BACKEND=ragflow`；**未配置 API Key 或服务不可达时自动降级 `memory` 并告警** |
| 证据链映射 | RAGFlow 文档名 = `signal_id`，检索结果可反查回来源/类型/标题/URL |
| **真机激活** | ✅ **已完成**（2026-09-14 16:09）：40 篇文档解析成功、向量分 0.57–0.72、检索 ≈162 ms |
| 关键词抽取 | ✅ **已开启**（R6）：RAGFlow 租户默认对话模型改为 `qwen3.8:27b@Ollama`（原先指向未注册的 `qwen3.5:35b`）。⚠️ 实测**无命中提升但单次检索 +2.5 s**，可一键回退 |
| LLM | ✅ **已切真实大模型**（R7）：`LLM_PROVIDER=ollama` + `qwen3.8:27b`，与 Hermes **共用同一 runner**（模型默认 `num_ctx=131072`，三方一致 ⇒ 无重载抖动） |

**当前实测**

| 项 | 值 |
| --- | --- |
| dataset | `trackcompass-signals`（`19a68252b01311f18c92bfc493440fd4`） |
| 索引规模 | 40 篇 / 40 chunks，`run=3` 全部解析完成、0 失败 |
| 嵌入模型 | `bge-m3:latest@Ollama`（1024 维，真语义） |
| 启动日志 | `INFO app.services.rag: RAG 后端 = ragflow @ http://localhost:9380` |

> ⚠️ **开 `keyword` 参数的前置条件（踩过的坑）**：RAGFlow 在 `keyword=true` 时会让
> **租户默认对话模型**抽取关键词；若该模型未登记在 `tenant_llm`，会抛
> `LookupError: Tenant Model ... type chat not found`，**整条检索直接失败**。
> 本机原配置 `tenant.llm_id=qwen3.5:35b@Ollama` 就是这种未登记状态，
> 已改为 `qwen3.8:27b@Ollama` 并登记到 `tenant_llm`（备份在 `docs/backup/`）。
> 详见 `docs/RAGFlow接入评估.md` §0.2 / §0.3。

```bash
# 自查与激活（API Key 在 RAGFlow UI http://localhost:8060 → 右上角头像 → API）
python scripts/check_ragflow.py --api-key <KEY> --list-datasets
python scripts/check_ragflow.py --api-key <KEY> --ensure-dataset trackcompass-signals
python scripts/check_ragflow.py --api-key <KEY> --dataset-id <id> --smoke
```

> ⚠️ 切换会改变检索口径：自建哈希嵌入 1536 维 vs RAGFlow bge-m3 1024 维，向量不可比；
> 切回 `memory` 需 `POST /api/v1/signals/reindex` 重建索引。详见 `docs/RAGFlow接入评估.md`。

> 设计上，每个 Agent 同时提供 `rule()`（确定性算法，离线可跑、可断言）
> 与 `build_prompt()`（真实大模型）。切换 provider 后 `rule()` 降级为校验基线而非被删除，
> 既保证零配置可演示，又保证 246 个单测始终有效。详见
> `docs/大模型与智能体接入说明.md`。
