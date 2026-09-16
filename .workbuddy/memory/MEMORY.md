# 工作区长期记忆（D:\01code\business）

> 详细过程留 `YYYY-MM-DD.md` 日志；本文件只留**仍在生效的事实、可复用命令、必须遵守的约定**。
> 最后整理：2026-09-16（本次做了压缩合并，条目编号保持不变，代码注释里有引用）

## 项目：赛道罗盘 TrackCompass

- 路径 `D:\01code\business\trackcompass`
- 定位：AI 驱动的科技公司商机发现与赛道选择决策系统（四层架构 + 七 Agent）
- 技术栈：FastAPI + SQLAlchemy + SQLite(dev) ／ React18 + TS + AntD5 + ECharts
- 状态（2026-09-16）：设计项全部落地 —— 后端 63 路由、前端 11 页、7 Agent、数据采集器、
  公共数据层与 E2E 租户隔离、前端会话态保持

### 入口与账号

| 项 | 值 |
| --- | --- |
| 入口 | **http://127.0.0.1:8020/**（单端口同时服务 UI 与 API；8010 被本机其他服务占用，勿动） |
| Swagger | http://127.0.0.1:8020/docs |
| 开发模式前端 | http://127.0.0.1:5180（`npm run dev`，仅改前端时用） |
| 登录 | admin@demo.com / TrackCompass@2026（另有 strategy/product/invest/expert 四个角色账号） |

### 环境与命令

- Python venv：`C:\Users\PC\.workbuddy\binaries\python\envs\trackcompass\Scripts\python.exe`
- 一键启动：`bash scripts/start-all.sh`（已构建过加 `SKIP_BUILD=1`）
- 种子：`python -c "from app.main import bootstrap; print(bootstrap(seed=True, reset=True))"`
- 单测：`cd backend && <venv python> -m pytest`（**328 用例**，全离线）
- E2E：`python scripts/e2e_smoke.py`（**30 项**，需服务已启动，走 `e2e@test.local` 独立租户）
- 契约：`python scripts/check_api_contract.py`（自动从 `client.ts` 提路径比 openapi.json）
- **UI 冒烟**：`node scripts/ui_smoke_persist.mjs`（13 项，零依赖 CDP，验「切菜单回来不空白」）
- 采集器：`python scripts/collect_signals.py --list|--dry-run|--frequency daily`
- 前端：`cd frontend && npm run dev` / `npx tsc --noEmit` / `npm run build`
- ⚠️ **RAGFlow 必须先于后端起**，否则 `_build_rag()` 探测失败会**静默降级 memory**；
  判据一行日志：`RAG 后端 = ragflow @ ...`
- ⚠️ 改前端产物**不需要重启后端**（`StaticFiles` 按请求读盘，`curl /` 看 hash 即可确认）

### Git / 提交纪律（2026-09-16 用户指令）

- **仓库根 = `D:\01code\business`**（不是 `trackcompass/`）—— `trackcompass/` 与 `.workbuddy/memory/` 都在同一仓库里
- ★★ **用户指令（2026-09-16）：每次修改完直接提交**，不必等总指挥签发。沿用既有纪律：
  逐路径 `git add`（白名单，`diff` ⊆ 白名单、零越界）→ `git commit -F <UTF-8 message 文件>`；
  **禁 `amend`、禁 `push`**；提交前先 `git status --short` 核验；提交后再 `git status` 确认工作区干净
- ⚠️ 该指令**只适用于本仓库**。EOS（`bq-eos-*`）是**多窗口共享 working tree**，仍由总指挥统一 commit/push
- 历史：`0e651cb` Initial commit（2026-09-16 10:39，用户提交）→ `18aa311` 会话态保持 + 探针口径放宽

### 跨页并行 / 后台任务（2026-09-16）

- **核心事实**：服务端是同步接口，HTTP 请求**不会**因组件卸载而中断（活一定干完），
  断点在**前端** —— 接结果的回调挂在页面组件上，组件一卸载响应就没人接
- `api/taskStore.ts`（应用级任务注册表，模块级 Map + useSyncExternalStore）：
  `runTask(key, meta, fn, opts)` 发起 / `useTask(key)` 认领 / `useRunningTasks()` 顶栏 /
  `useTaskVersion()` 放进 `useAsync` 依赖 ⇒ **任务完成即自动刷新列表**
- 已接入 **9 页 12 个手动操作**；任务键约定 `页面:动作`，**同一件事跨页共用键**
  （`trends:scan` 三处、`signals:collect` 两处）⇒ 跨页防并发
- 关键取舍：**不取消请求**（掐断=白干）/ 同 key 不并发 / 结果镜像 sessionStorage /
  **残留 `running` 一律改判为「已中断」**（刷新页面会杀掉在飞请求，否则顶栏永远转圈骗人）/
  完成弹全局 `notification`（唯一能跨页触达的方式）/ 任务自带 `meta.params` 留痕「按什么条件跑的」
- 「弹窗类结果」的已读语义：关闭弹窗 = `clearTask`（否则下次进来又自动弹）
- 实测耗时（写文档/判断「是否值得后台化」用）：
  需求探针 **18.2s**（首次真实推理）/ 0.3s（同 prompt 缓存命中）；
  七 Agent 分析 **47s**（真实模型，规则基线 418s）；采集 **4.9s**（6 源 40 条，1 源失败）；
  趋势扫描 0.34s；报告生成 0.016s；实验创建/回写 0.016s/0.015s
- ⚠️ **写测试要考虑 LLM 缓存**：同 prompt 命中缓存 0.3s 就返回，观察不到「进行中」⇒
  冒烟用例用**随机关键词**制造缓存未命中
- 文档：`docs/跨页并行与后台任务报告.md`

### 数据采集器（`backend/app/collector/`）


- `base`(限流/重试/UA) · `normalize`(清洗/语言/行业/质量闸门) · `registry` · `runner` · `sources/` 5 模块
- 统一入库口 `app/services/signal_ingest.py::persist_signals` —— API 与采集器共用，
  `content_hash + url` 双去重 + 事务 + 审计 + 实体抽取
- API：`POST /signals/collect`、`GET /signals/adapters`、`GET /signals/collect/preview`
- **源可用性（勿重复试错）**：arXiv ❌ `Rate exceeded` → 用 **OpenAlex + Crossref**；
  gov.cn `zhengce/zuixin.htm` ❌ JS 跳转 → 真入口 `sousuo.www.gov.cn/search-gov/data?t=zhengcelibrary_gw`；
  ccgp `search.ccgp.gov.cn` ❌ 反爬 → 用列表页 `cggg/zygg/` 解析 HTML；工信部需 UA；
  ✅ gov.cn / 发改委 / 36氪 / IT之家 / 界面 / ccgp 列表 / OpenAlex / Crossref
- 13 适配器 / **8 可用**（设计要 ≥5）；需密钥 5 类（专利/融资/招聘/社媒/供应链）逻辑就绪，
  填 `.env` 的 `*_API_KEY` 即启用（**未联网验证**，首次先 `--dry-run`）
- ⚠️ 论文源**不能按日期排序**（`sort=publication_date:desc` 会覆盖相关性）
- ⚠️ 相对链接**必须基于列表页**解析（ccgp 的 `./gkzb/...` 否则丢 `/cggg/zygg/` → 404）
- ⚠️ **行业优先级**：信号自带 > 内容推断（标题权重×3）> 公司行业兜底。
  **公司行业不能优先**，否则所有外部信号同质化、赛道差异消失
- ⚠️ 招标源 `requires_keywords=True`（无检索词会拉回全量无关公告）
- **实测信号库分布（2026-09-16）**：`news 20 / paper 18 / policy 10` = 48 条，**无需求侧五类**
  ⇒ 需求探针恒 0（见 L-15）。文档 `docs/信号采集器实现说明.md`

### 大模型 / 嵌入

- 7 Agent + 4 层编排（第 1、2 层并行）+ 有界反馈循环；`LLM_PROVIDER=ollama`，
  **三档全指 `qwen3.8:27b`**（monitor 也指 27b，指 7b 会引入第 3 个常驻模型）
  - 常驻：27b 17.87 GB @131072 + bge-m3 0.66 GB = **18.54 / 32 GB**；七 Agent 分析 **418s → 47s**
  - 三方（Hermes / TrackCompass / RAGFlow）**都不传 num_ctx** ⇒ 共用同一 runner，无重载抖动
- Ollama：`http://localhost:11434`（**必须 localhost**，`127.0.0.1` 返 000，IPv6-only）
- 切换成本 = 改 `backend/.env` + 重启，业务代码零改动；自查 `python scripts/check_llm_connection.py`
- `rule` 必须保留为零依赖可断言基线（pytest conftest 强制 rule）
- **嵌入**：`EMBEDDING_BACKEND=local`（哈希 1536 维，零依赖）。切 ollama+bge-m3 注意三点：
  ① 模型名用 `EMBEDDING_OLLAMA_MODEL=bge-m3`（非 HF 名）② 端点 `/api/embed` + `input`（旧端点兜底）
  ③ bge-m3 是 **1024 维**（`EMBEDDING_DIM` 1536 只适用 OpenAI）⇒ **切后端后向量不可比，需 reindex**

### RAG / RAGFlow

- 开关 `RAG_BACKEND`：`ragflow`（默认）｜`memory`；缺 Key/dataset/不可达时**自动降级并告警**
- 适配器 `services/ragflow.py: RAGFlowEngine`，与 `rag.py: BaseRAG` 同契约（共享后处理）
- 证据链映射：上传文件名 = `{signal_id}.txt` → `document_keyword` 反查；
  `source/signal_type/title/url` 存旁路表 `backend/ragflow_index_map.json`
- RAGFlow 写入是四步异步流水线 ⇒ **有不可检索窗口**（`RAGFLOW_PARSE_WAIT_SECONDS` 默认 90s）
- dataset `trackcompass-signals` = `19a68252b01311f18c92bfc493440fd4`
- **R6 关键词抽取 = 已关**（`RAGFLOW_KEYWORD_EXTRACTION=false`）：收益 0、耗时 ×19（100ms→1945ms）、
  短查询相似度 −12%。关闭后 `/rag/search` 1.777s → 0.077s，并消除一个隐式 GPU 排队点
- ★ **RAG 不参与趋势/评分/报告的数值产出**，只喂 prompt；`provider=rule` 时 prompt 全被忽略。
  断言「某页面数据来自 RAG」前**必须先做换桩对照**（曾实测 8 条趋势换空桩后完全一致）
- 留档：`docs/RAGFlow接入评估.md`、`docs/RAG关键词抽取开关评估.md`

### 趋势雷达（理解雷达的关键）

- **两条独立链，只在最后一步相交**：
  ① 左侧 = **外部信号** → 分桶（`signals.keywords` 当桶名：**有则出现，无则永不出现**）→
  4 因子打分 → 趋势（+客观标签 `_tags`）→ **决定"有哪些趋势"**
  ② 右侧 = **公司画像**（tech_stack / patents）→ 能力词 → **只做标注**（`matched` / 情报空白）
  ⇒ **画像不参与生成、不参与打分**；"雷达里有没有 X"只取决于外部信号里有没有 X
- **能力匹配 = 查询时计算**：趋势落库存客观标签 `factors._tags`，接口层用
  `tags ∩ 当前公司能力词` 实时算 `matched` ⇒ 切公司即时生效，无需回算
- ⚠️ **画像→Agent 只有一条通道 `analysis.build_profile_input`，口径必须与
  `deps.current_capability_terms` 一致**（曾漏传 `patents`，导致分析路径专利能力恒空）
- **专利抽词** `base.extract_patent_terms()`（2-4 字技术对象 + 26 个动作后缀）；
  此前专利标题命中 0 时退化成整句 ⇒ **专利贡献恒为 0**。
  两个约束：前缀**不得跨过上一个后缀**；**首字为词尾字则回退 2 字**
- `/analyze` 返回 `profile_used` + 前端「本次分析依据」卡片 ⇒ 「标注」这一步可核查
- 证据链：LLM prompt 要求回编号（`E1`），**必须用 Agent 自己刚检索的 docs 反查**
  （不能用 `state.evidence` —— 跨 Agent 累积 + 并行 + `_dedupe_evidence` 重排都会错位）
- 判「同一实体」**用证据集合而非 LLM 生成的名字**：实测名字相似 0.91 的三对证据 Jaccard = 1.000；
  算相似度必须用真语义模型（bge-m3），项目的 `LocalHashEmbedding` **不携带语义**
- 文档：`docs/趋势标签化重构报告.md`、`docs/趋势累积语义分析.md`、`docs/趋势雷达工作原理与诊断.md`

### 需求探针（口径 2026-09-16 放宽，用户裁决 ①+③）

- **数据源分两类**（`agents/demand.py`）：
  `DEMAND_SOURCE_TYPES` 需求侧五类（社媒/评论/论坛/招标/招聘）= **主证据**；
  `CONTEXT_SOURCE_TYPES` 补充三类（产业媒体/论文/政策）= **补充证据**；
  `SOURCE_TYPES` = 两者相加
- ★ 取数口径统一在 **`demand_pool(state)`**：需求侧优先排序 → 补充类型 → 全量兜底，
  **`rule()` 与 `build_prompt()` 必须共用同一份**（曾出现 prompt 自称「以下是 0 条信号」
  却塞满一屏证据的自相矛盾）
- ⚠️ **真实信号库只有 `news 20 / paper 18 / policy 10`（48 条）**，需求侧五类**一条都没有**
  ⇒ 只选需求侧必然 0 命中。**这不是 bug 而是数据现状**，接通数据源见 `L-04`
- 前端默认数据源 = 需求侧五类 ∪ **库内已有类型**，首屏按 `/signals` 存量自动补全；
  用户手动改过后不再覆盖（`sourcesTouched` 直接读会话态判定）；选项**按主/补证据分组**并标存量
- 结果跨页保留（`tc:ui:demand:*`），顶部标「上次分析 时间（关键词 · 数据源 N 个 · 行业）」；
  条件改了未重跑会提示；「清除结果」回空态
- 实测（默认 8 类，真实模型）：匹配 4 / 痛点 4 / 强度 **3.41**；只选补充三类同值；
  只选需求侧五类 → 0（自洽）
- 语义边界必写：需求侧有**直接的付费意愿与负面表达**，补充信号只体现**意向**
  （政策=强制/补贴要求、论文=未解痛点、产业媒体=行业诉求与扩产意图）
- 文档：`docs/前端会话态保持修复报告.md` §三、`docs/产品使用手册.md` P06

### 能力画像 / 多公司

- 一租户可建**多份**画像；**「当前公司」= 最近保存的那份**（`updated_at desc`）
- ⚠️ 同一口径有**两个调用点必须同步**：`api/deps.py:get_profile` 与
  `services/analysis.py:build_profile_input`
- `POST /company/profile` 解析顺序：显式 `id` > 同名 > 新建；越权/不存在 → 404
- 接口：`GET /company/profiles`（含 `is_current`）、`GET /company/profile?profile_id=`

### 公共数据层与 E2E 隔离

★ **判据：`tenant_id IS NULL` ⇒ 公共数据**（`core/visibility.py`）。**零 DDL**
（模型里本就是 `str | None`）。选它而非哨兵租户/`is_public` 字段：无需迁移 + 语义自洽
（外部情报无归属）+ 与 RAG（单 dataset）/ 图谱（全局单例）的既有全局性对齐

| 归属 | 数据 |
| --- | --- |
| 🟢 公共/全局 | `signals`（公共）、`tracks`、`roles`（后两者本就无 `tenant_id`） |
| 🔴 租户私有 | `trends`（含 `subscribed`/`in_pool`）、`company_profiles`、`opportunities`、`scorecards`、`experiments`、`reports`、`alerts`、`audit_logs` |

- 采集器写 `PUBLIC_TENANT_ID`；`POST /signals` 新增 `is_public`（默认 True）；
  `deps.signal_stats` / `signals.py` / `demand.py` / `alerts.py` / `profile.py` / `analysis.py` 已改 `visible()`
- ⚠️ `reindex` 与 `build_from_signals` **只纳入公共信号**（RAGFlow 单 dataset，否则跨租户泄露）
- ⚠️ **E2E 隔离与公共层是同一件事的两面**：信号若私有，E2E 租户看不到 → `generated=0` → 链路断路
- E2E：`seed.ensure_e2e_tenant()` + `purge_e2e_data()`（8 表；`scorecard_versions` 无 `tenant_id`，
  须经 `scorecard_id` 子查询删）
- 文档：`docs/公共数据层与E2E租户隔离.md`

### 前端会话态保持（2026-09-16）

- **路由切换会卸载页面组件**，`useState` 全归零。凡「手动触发 + 等待可观 + 结果不能即时重取」、
  以及**用户手工输入过的内容（无论是否保存）**，都走 `hooks/usePersistentState`
  （sessionStorage，键前缀 `tc:ui:`，纯存储层 `api/uiState.ts`）
- 已接入**四处**：**需求探针**（关键词/数据源/行业/结果/条件快照）、**趋势雷达**（筛选 + 扫描面板）、
  **赛道评分**（分析明细 + 本次分析依据 + **选中的机会卡片** + 权重草稿 + 权重预览，
  后三者**按 `opportunityId` 存**防串台）、
  **能力画像**（`profile:editing` 上次编辑对象 / `profile:drafts` **按公司分键**的未保存草稿 / `profile:step`）
- ★★★ **排查口径：先问「这个状态存在哪」** —— 组件 state / **URL query** / 父组件 / 全局 store。
  赛道评分那次选中项只存在 `?opportunity=` 里，**从菜单进来 query 就没了**，
  连带「按机会卡片存」的两个面板也因匹配不上而隐藏（存储没坏，是钥匙丢了）。
  只看组件 state 会漏掉整个原因
- ⚠️ 能力画像有**两层根因**（只修一层不够）：① 回到页面固定拉「最近保存那家」⇒ 用户选的公司会跳回去
  ⇒ 必须记住 `editing`；② 表单值只在内存 ⇒ 必须存草稿。**草稿只回填表单，
  右侧能力向量/推荐赛道仍按已保存画像渲染**（草稿没落库，拿它算向量等于展示不存在的结果）
- **登出 / 401 自动失效**都会 `clearSessionState()`（在 `api/client.ts::storage.clear()` 内）
- 约定：**凡保留的结果必须标注「条件 + 时间」**；草稿必须给「已恢复未保存的草稿 + 丢弃草稿」，
  否则用户分不清屏上内容哪部分已生效
- 「弹窗类草稿」的坑：弹窗开合必须是独立 state —— 若用 `草稿 !== null` 当 open，
  恢复草稿等于**一进页面就糊一个弹窗**；另外「切换时清空」的副作用是恢复的敌人
  （首屏 selectedId 从空到有也会触发它，把刚恢复的草稿清掉）
- 判断标准（含 1.4 修正）：**「用户手工输入过的内容不能丢」与「耗时结果不能丢」并列**；
  列表类数据会重新拉取（不空白）不必持久化。其余页面见 L-16
- 验证脚本 `scripts/ui_smoke_persist.mjs`（**27 项**，两段场景）。**写它踩的两个坑**：
  antd **Select 靠 `mousedown` 唤起下拉**（纯 `click()` 打不开）；
  **Steps 的点击挂在内层 `.ant-steps-item-container`**（派发到外层 `.ant-steps-item` 不会向下传到 handler）

### 局域网访问（2026-09-16 改造，用户需求）

- **默认绑定 `0.0.0.0`** ⇒ 局域网 `http://192.168.2.192:8020/`（同网段直接打开）
- 收回本机：`HOST=127.0.0.1 bash scripts/start-all.sh`；换端口：`PORT=8030`
- 四个启动/构建脚本都会打印局域网地址（`start-all.sh` / `start-backend.cmd` /
  `start-frontend.cmd` / `build-frontend.cmd`）
- 新脚本：`scripts/lan_ip.py`（探测对外 IP，UDP connect 不实际发包）、
  `scripts/check_lan_access.py`（**本机 + 局域网两个来源**各 6 项自检）
- dev server：`vite.config.ts` 同步 `host: '0.0.0.0'`（proxy 仍指 `127.0.0.1:8020`，同机走回环更稳）
- ★ **前端零改动**：`axios baseURL: '/api/v1'` 是相对路径 ⇒ 天然跟随页面来源，CORS 也不需要。
  缺口只有「服务监听地址」一处 —— **别被"要改 API 地址/加 CORS"的直觉带偏**
- ⚠️ 开放后**同网段任何人可登录**（演示账号写在 README 里是公开的）；
  `127.0.0.1:8020` 与 `192.168.2.192:8020` 是**两个 origin**，登录态各存各的，切换需重新登录
- 文档：`docs/局域网访问配置说明.md`

### 主机 / 网络

| 项 | 真值 |
| --- | --- |
| 本机主机名 | `DESKTOP-9HDUO7P` |
| **本机 LAN IP** | **192.168.2.192**（旧记录 `192.168.2.28` 已过期） |
| WSL 客户机 IP | 172.25.252.110，网关 172.25.240.1 |
| 判定手法 | `socket.connect((目标,P)).getsockname()[0]`，**源 IP == 目标 IP ⇒ 目标就是本机** |
| 端口 | 8000 ✅ / 8001 ✅ / 9219 ✅(Hermes) / 2222 ✅(SSH) / 11434 ✅(需 localhost) / 18081 ❌ / 3080 ❌ |

- 本机 `http_proxy` 会污染 curl（返 502/000 假象）⇒ 验证端口一律 `--noproxy '*'`；httpx 用 `trust_env=False`
- 浏览器自动化：本机**只装了 Edge**（`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`），
  **未装 playwright / agent-browser** ⇒ 用 Node 内置 `fetch` + `WebSocket` 直连 CDP 最省事

### 显存 / Ollama（配置已固化）

- 生效配置：`OLLAMA_MAX_LOADED_MODELS=2` + `OLLAMA_FLASH_ATTENTION=true` + `OLLAMA_KV_CACHE_TYPE=q8_0`
  - **值必须是 2 不是 1**：Ollama 把 embedding 一并计数，设 1 会让 bge-m3 与对话模型互相驱逐
  - 落点 `/etc/systemd/system/ollama.service.d/override.conf`（**原文件已存在，必须合并不能覆盖**）；
    留档 `scripts/ollama-override.conf`；两级备份 `.bak.20260914-143125` / `.bak.step1-max1.20260914-143506`
- ★ **共存规律**：加载 `bge-m3` 时预测仅 1.1 GiB ⇒ 对话模型已驻留就必然可共存；
  加载 `qwen3.8:27b@128K` 时预测 **42.7 GiB** > 可用 29.6 GiB ⇒ **无条件驱逐**
  ⇒ **RAG 实用做法：先预热对话模型，嵌入模型随后加载**
- 根因：Ollama 对 27B@128K 预测**高估 2.4×**（42.7 vs 实测 17.87 GB），闸门在 `sched.go:551`，
  **与 `MAX_LOADED_MODELS` 无关**，0.32.15 无开关可消
- KV 明细（读 `journalctl -u ollama`）：27b @128K f16 → 权重 15,339 + KV 8,192 ≈ 23.0 GiB；
  @64K → 4,096；@32K → 2,048（**严格线性**；曾误估「降 num_ctx 释放 15GB+」，真值 ≈6.0 GiB）
- ⚠️ Ollama 按 `n_ctx` 分组调度，不同调用方传不同 num_ctx 会建不同 runner ⇒ 模型反复重载
- 云模型：Free 并发 1 / Pro 3 / Max 10；**服务器在美欧新 ⇒ 数据出境**，与企业本地化冲突
- ⚠️ **Ollama 在 WSL 中，禁止在 Windows 侧重启或操作**

### Hermes（评估结论：核心链路不对接）

- TrackCompass 对 Hermes **零引用**；Hermes 在线 `192.168.2.192:9219`
- **能力错配**：Hermes 是「会话+cwd+文件IO+Skill」的**执行型**运行时，TrackCompass Agent 是
  **分析型**（结构化 JSON）⇒ 接核心链路净增复杂度、破坏可回归性、无业务用户隔离
- 唯二值得接的边缘场景：① 非文本文件解析（PDF/Word）② 需真跑代码的取数
- **访问纪律**：只做 TCP + HTTP GET 只读探测；登录/ticket/会话/prompt 等有副作用操作须先获许可
- 文档：`docs/Hermes接入评估.md`

---

## 约定（后续迭代必须遵守）

1. **重型基础设施一律走「开关 + 适配器」**，不写死在业务代码。基类 `BaseVectorStore`/`BaseGraphStore`/
   `BaseEmbedding`/`BaseAgent`，开关在 `backend/.env`。
2. **Agent 双实现不得只留一个**：`rule()` 是离线基线与测试依据，`build_prompt()` 是真实模型路径。
3. **评分公式改动必须同步更新单测里的文档公式断言**（`tests/test_scoring.py`）。
4. **输出契约的数值字段必须显式命名并注明量纲**。
5. **同一文件多次编辑必须串行**（并行 Edit 会静默互相覆盖）。
6. **挂在 `/` 的 catch-all `StaticFiles` mount 必须最后注册**，显式路由先声明。
7. **UI 由后端同端口托管**（`SERVE_FRONTEND=true`）；改前端必须 `npm run build`。前端用 HashRouter。
8. 🌟 **改后端 Python 代码（含 `agents/`、`services/`）必须硬重启进程** —— uvicorn **未开 `--reload`**，
   `touch` 无效。踩过的坑（2026-09-16）：改完 `agents/demand.py` 直接跑 UI，
   接口 0.3s 返回「正常结果」，实为 **LLM 缓存命中旧 prompt 的产物** —— 验证的是**旧代码**。
   判据：进程 `Started server process [pid]` 的时间戳必须晚于最后一次改动的文件 mtime；
   重启后 `cache_entries=0`，一次真实推理 18.2s，才算真验证。
   （改 `config.py` / `main.py` / `.env` 同理。）
9. **排查前端 4xx 顺序**：服务存活 → 契约校验 → 静态资源 → 浏览器默认请求（`/favicon.ico`）。每步一条 curl 证伪。
10. **写「检查类工具」必须先自证**：用已知正确样本确认工具不误报，再判断业务代码。
11. **`LLMService.complete()` 三条路径（成功/缓存/失败）都必须写 `_meta`**，`provider` 如实反映调用方。
12. **判定「是否真在用大模型」看运行时 trace 耗时**：亚毫秒 = 规则引擎；数十~数百毫秒 = 真实推理。
13. **缓存层返回可变对象必须深拷贝**（`copy.deepcopy`）。
14. **收敛资源上限前先问「有几种类型的模型要常驻」** —— 生成与嵌入是两类，设 1 会互相驱逐。
15. **判定外部服务日志状态必须匹配完整消息文本**，不能只匹配字段尾部
    （Ollama 的 `fits alongside` 与 `predicted to exceed ..., evicting` 尾部格式完全相同）。
16. **清理常驻模型用 `ollama stop <model>`**，不要用 `keep_alive:0` 请求（会「先加载再卸载」且卸载异步）。
17. **「配置值」≠「实际生效值」**，对外一律报实际值（并附 `configured` 对照）。
18. **同一能力的两条模型通道（生成/嵌入）必须分别给出切换与自查手段**，默认都应是零依赖离线实现。
19. 🌟 **同一概念的多个入口必须核对口径**。已踩四次：`_capability` 落库快照 vs 查询时计算、
    `alerts.level` 三套词表、画像→Agent 漏传 `patents`、**前端信号类型中文名两处各写一份**
    （已收敛到 `widgets.tsx::SIGNAL_TYPE_LABEL`）。
    判据：**凡读同一份数据、给同一概念赋值的函数，把签名并排看字段是否一一对应**。跨层宁加注释互指。
20. **新增「选择已有 X」类功能前，先确认数据模型是否支持多份**。
21. **替换外部组件必须走「适配器 + 开关 + 自动降级」**：抽共同基类让新旧实现共享后处理，上层零改动。
22. **对接外部系统先读官方 API 契约与真实配置**（RAGFlow 的 TEI 配置就是未启用模板）。
23. **外部系统返回字段不足以还原领域对象时必须补映射层**：优先「业务 id 当外部主键」+ 旁路元数据表，
    而非解析正文标记（会被对方解析器改写）。
24. 🌟 **批量替换数字绝不能用裸数字做 `replace_all`**，必须带上下文整串替换。
    曾用 `old_string="192"` 把 `192.168.2.192` 改成 `208.168.2.208`（8 处）、KV 值 `8,192`→`8,208`。
    **改完必须全库正则审计**。泛化：**任何「按值替换」都可能误伤含该值的其它语义（IP/端口/坐标/尺寸）**。
25. **「加载了适配器」≠「适配器已启用」**：验证要看启动日志分支 + 健康接口实际值。
26. 🌟🌟 **从「规则引擎」切到「真实大模型」必须假定下游契约会被打破**。实测一天暴露 5 类缺陷：
    字段缺失 / 空对象 / 类型错乱 / 数学不自洽 / 输入不匹配。对策：① 每 Agent 声明 `required_keys`；
    ② 边界用 `as_dict/as_list/dicts_only` 收口；③ **派生量一律重算，不采信模型自报**；
    ④ 提示词写「严格 JSON」不等于模型会遵守。
27. **「规则实现作为校验基线」必须真正接上**，落点在 `run()` 里的独立方法（如 `backfill_contract`），
    **不能塞进 `postprocess()`**（子类都重写且不调 `super()`）。
28. **Ollama 跑 Agent 类任务必开 `think=false` + `format=json` + 限 `num_predict`**
    （带 thinking 默认先出推理链，2.6s → 0.9s；不限 num_predict 单 Agent 曾达 129s）。
29. 🌟 **「只写入、不防重」是本项目高频缺陷**（已出现 4 次：`upsert_trends` 键不一致、
    `persist_scan_result` 重复写、`to-pool` 无幂等、`POST /signals` 不判重）。
    **任何「创建型」接口都必须默认考虑幂等**，去重收敛到唯一入口。
30. 🌟🌟 **「代码修好了」≠「数据是对的」**：修完必问 **「这个字段是实时算的还是落库快照？」**
    派生数据（关键词/标签/匹配/图谱）多为快照，规则一变就与代码脱节。已实测四次：
    `factors._capability`、图谱未重置（幽灵节点）、`alerts.level` 旧词表、信号关键词旧规则。
    ⇒ 凡改抽词/匹配规则，**必须列出需回算的字段并执行回算**。
31. **契约字段缺失往往三层同时漏**（prompt 没要求 / 后处理没兜 / 前端无防御）。
    新增断言要校验**语义**（「label 不能是 UUID」「experiments 必须是数组」）而非存在性 ——
    弱检查会导致「假通过」。
32. **`rowKey` 是 TS 管不到的约定字段**（`dataSource` 是 `any[]`），字段不存在只在运行时 warning；
    唯一键必须选**语义上保证唯一**的字段（`agent` 看着唯一但反馈循环会重跑 ⇒ 用后端 `seq`）。
33. **一次报错值得全量审计**：用户报 1 处 `rowKey`，全项目扫出 12 处同类模式（2 处真缺陷）。
34. 🌟 **删除数据必须同时检查「派生数据」与「外部索引」**。实测删 40 条种子信号：
    44 条趋势证据引用它们、2 条机会卡 `source_trend_id` 指向它们、**RAGFlow 侧 79 篇已删文档仍会被召回**
    （`index()` 是幂等追加、不做反向清理）。这类残留**不报错、极难察觉**
    ⇒ 清理清单 = 主表 → 引用它的派生表 → 外部索引（RAG/向量/图谱）。
    同理，任何「演示/种子数据」都要有开关。
35. 🌟 **断言描述里不要放可能越界的取值**。`check(名, 条件, 描述)` 三个参数**都会先求值**，
    描述串写 `x[0]` 时若 `x` 为空则**描述先崩** —— 把「检查失败」变成「脚本崩溃」。
    这类缺陷**在数据充足时永不触发**（属「被数据掩盖的脚本缺陷」），
    断言应表达**请求-响应自洽**而非「必须有值」。
36. **实体抽取的英文分支必须做形态白名单**：只按「len>1 且 ascii」会把 Rough/Military/https/Zhang/of 全收进
    keywords，并经 `trend_tags` 传染到趋势标签。只收：显式词表 / 含数字型号 / 全大写缩写 / 驼峰。
37. 🌟 **判断两条记录是否「同一实体」，用事实字段而非 LLM 生成的名称**。
    实测趋势名相似度 0.91 的三对，**证据信号集合 Jaccard = 1.000**；而名称相似度不可靠
    （模板词系统性抬高整体相似度，单一阈值分不开：跨主题最高 0.7132 > 同主题最低 0.4253）。
    ⚠️ 算相似度必须用真语义模型（bge-m3）；`LocalHashEmbedding` 是确定性哈希、**不携带语义**。
    ⚠️ 小集合上 Jaccard 要带**最小交集数**下限（证据常只有 1–3 条）。
38. **测试脚本必须与真实数据隔离**。`e2e_smoke.py` 连真实服务 + 真实库，20+ 写操作无清理
    ⇒ 曾留下 11 实验 / 10 报告 / 趋势累积到 17 条；用种子数据判定的断言会**恒为真**、掩盖缺陷。
    ✅ 已解决（2026-09-16）：E2E 改用 `e2e@test.local`，`finally` 清理，反复运行零累积。
39. **枚举型字段换词表后必须迁移历史数据 + 读取时归一**。`alerts.level` 库里混了三套写法，
    接口只统计 `critical/warning/info` ⇒ **49 条级别不可见，严重度汇总恒为 0/0/0**
    （总数 > 0 但三档全 0，就是这个信号）。三件套：产出方定义规范词表 + 别名映射 + `normalize()`；
    写入即归一；读取时归一后再计数（**分档之和 = 总数**）。
    ⚠️ 别顺手改同名其它概念（`competition.level`、`scoring.threat_level` 是**竞争威胁等级**，不进 `alerts`）。
    ⚠️ 归一前 `{"critical":0,...}[level]` 会 `KeyError`。
40. **改动后必须复核落盘内容，不能只看「编辑成功」回执**。对**同一文件**并行编辑会丢改动
    （实测 `e2e_smoke.py` 登录行、`seed/__init__.py` 信号归属、`signals.py` import 各丢一次），
    后果很重 —— 登录凭据没生效，脚本仍以演示账号跑，把 6 条趋势 + 4 条预警写进演示租户。
    ⇒ **同一文件的编辑一律逐个串行，并在每批改动后 grep 复核关键行**。
41. **遗留项唯一出处 = `trackcompass/docs/遗留项台账.md`**（L-01~L-16）。
    新增时**同时**在来源报告对应小节加「已登记 L-xx」；解决后**不删行**，改 ✅ 并补日期依据。
    每项必须写**触发条件**。**P1 组（L-01/L-02）触发条件统一为：接入第 2 个真实组织租户之前。**
42. 🌟 **放宽/修改口径必须同时写清「语义边界」，不能只报「多接了几个源」**（2026-09-16 需求探针）。
    做法三件套：① 代码里把强弱证据拆成**不同常量**（主证据 / 补充证据），不混成一个元组；
    ② 取数顺序体现优先级，且**所有消费方共用同一个取数函数**；
    ③ prompt / 界面 / 文档如实标注证据类型，把可信度判断交给模型与用户。
    否则下游会把两类证据当等价输入，结论看起来更丰富、实际更不可信。
43. **凡「结构性不可用」的功能，先问「是数据不够还是类型不相交」**（2026-09-16 需求探针恒 0）。
    实测手法：前端写死的候选集合 ∩ 库里实际存在的取值集合 = ∅ 时，功能**永远**不会出数据，
    且表象是「空白」，极易被当成前端渲染 bug 去修。判据一句话：**把两侧的取值集合都打出来求交集**。
44. 🌟 **「列表接口只回元信息」是高频坑**：拿列表项去渲染详情必然空，
    而空态文案往往写成「无内容」，看起来像功能缺失（实测：决策报告列表 → 每份历史报告都显示
    「该报告无正文内容」，用户以为「不能在线看」）。
    规矩：**有 detail 接口的，打开详情必须走 detail**；列表项只服务列表。
    发现手法：对比「列表接口返回字段」与「详情接口返回字段」——差一个 `content` 就是它。
45. **对外返回的文件地址不能是服务端本地路径**：导出接口给的是 `file:///D:/...`，
    浏览器打不开。必须转成对外可访问的 URL（本项目 = 后端 mount 的 `/static/reports/<文件名>`）。
    反过来也要小心：前端别直接 `window.open(res.url)`，那个 url 是给服务端看的。
46. 🌟 **给原生 Windows 程序传路径不能用 MSYS 风格**（2026-09-16 局域网改造踩坑）：
    Git Bash 里 `$ROOT` 由 `pwd` 得到是 `/d/01code/...`，直接传给 `python.exe` 会变成
    `C:\d\01code\...` 报「文件不存在」。而 `cd`（bash 内置）**接受** MSYS 路径 ——
    同一变量"有时能用有时不能用"，最难查。修法：`cygpath -w` 转换（`command -v` 兜底）。
    **且别用 `2>/dev/null` 吞掉这类错误**，否则症状退化成"某行没打印"，极易误判成逻辑失败。
47. **端口绑定自查必须分「本机 / 局域网」两个来源**：`127.0.0.1:PORT` 与 `192.168.x.x:PORT`
    是不同 origin，登录态与静态资源各走一遍；只测本机地址不能宣称局域网可用。
    另：**本机访问自己的局域网 IP 可能绕开入站防火墙**（Windows 行为），
    跨机验证必须用另一台机器（本项目用 WSL，走 Hyper-V ⇒ 真过网络栈）。
48. **Vite dev server 首次访问会触发依赖预构建**，期间请求挂住、curl 表现为 `000`；
    同端口"一个路径 000、另一个 200"时优先怀疑是否首次访问，复测一次即可区分
    （别急着归因到 `allowedHosts` / host 白名单）。

---

## 参考文档（工作区根目录，未纳入版本管理）

`需求文档.txt` / `设计文档.txt` / `技术文档.txt` / `原型设计.txt` / `AI设计.txt`
- **§权威出处**：技术文档 §3.1 数据源 · §3.2 数据管道 · §4.x 评分公式 · §6.2 API 契约；
  设计文档 §7 功能需求 · §10 API · §12 页面；原型设计 §4.1 设计 Token · §11.1 页面清单

## 交付留档（`trackcompass/docs/`）

研发计划 · 测试报告 · 产品使用手册 · 验收标准 · 大模型与智能体接入说明 · Hermes接入评估 ·
模型调用路径与显存评估 · 显存优化与云模型评估 · 趋势雷达工作原理与诊断 · 数据来源与真实度说明 ·
RAGFlow接入评估 · RAG关键词抽取开关评估 · 趋势雷达·能力画像与证据链诊断 ·
趋势雷达证据链修复与数据清理报告 · 需求探针与图谱与能力匹配修复报告 · 机会池重复卡片修复报告 ·
前端列表key冲突修复报告 · 趋势雷达Bug复查报告 · 趋势标签化重构报告 · 信号采集器实现说明 ·
公共数据层与E2E租户隔离 · E2E写入数据清单 · 趋势能力画像链路修复报告 ·
**前端会话态保持修复报告** · **跨页并行与后台任务报告** · **决策报告在线查看修复** ·
**遗留项台账（唯一出处）**

可复用脚本（`trackcompass/scripts/`）：`check_api_contract.py` · `e2e_smoke.py` ·
**`ui_smoke_persist.mjs`** · `check_llm_connection.py` · `check_ragflow.py` · `probe_topology.py` ·
`probe_two_order.sh` · `diag_ollama_knobs.sh` · `ollama-override.conf` · `compare_ragflow_keyword.py` ·
`collect_signals.py` · `analyze_trend_similarity.py`


