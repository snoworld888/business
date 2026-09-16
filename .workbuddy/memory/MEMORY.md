# 工作区长期记忆（D:\01code\business）

> 详细过程留 `YYYY-MM-DD.md` 日志；本文件只留**仍在生效的事实、可复用命令、必须遵守的约定**。
> 最后整理：2026-09-15

## 项目：赛道罗盘 TrackCompass

- 路径 `D:\01code\business\trackcompass`
- 定位：AI 驱动的科技公司商机发现与赛道选择决策系统（四层架构 + 七 Agent）
- 技术栈：FastAPI + SQLAlchemy + SQLite(dev) ／ React18 + TS + AntD5 + ECharts
- 状态（2026-09-15）：设计项已全部落地 —— 后端 63 路由（覆盖《设计文档》§10）、
  前端 11 页（§12 的十页 + 信号库 P12）、7 Agent、**数据采集器已实现**

### 端口与账号

| 项 | 值 |
| --- | --- |
| 访问入口 | **http://127.0.0.1:8020/**（单端口同时服务 UI 与 API；8010 被本机其他服务占用，勿动） |
| Swagger | http://127.0.0.1:8020/docs |
| 开发模式前端 | http://127.0.0.1:5180（`npm run dev`，仅改前端时用） |
| 登录 | admin@demo.com / TrackCompass@2026（另 strategy / product / invest / expert 四个角色账号） |

### 环境与命令

- Python venv：`C:\Users\PC\.workbuddy\binaries\python\envs\trackcompass\Scripts\python.exe`
- 一键启动：`bash scripts/start-all.sh`（已构建过加 `SKIP_BUILD=1`）
- 初始化种子：`python -c "from app.main import bootstrap; print(bootstrap(seed=True, reset=True))"`
- 测试：`cd backend && <venv python> -m pytest`（**296 用例**，全离线）
- E2E 冒烟：`python scripts/e2e_smoke.py`（30 项，需服务已启动，含真实 LLM）
- 契约校验：`python scripts/check_api_contract.py`（自动从 `client.ts` 提取前端路径比对 openapi.json）
- 采集器：`python scripts/collect_signals.py --list|--dry-run|--frequency daily`
- 前端：`cd frontend && npm run dev` / `npx tsc --noEmit` / `npm run build`
- **启动顺序敏感**：RAGFlow 必须**先于**后端起；否则 `_build_rag()` 探测失败会**静默降级 memory**。
  判据一行日志：`RAG 后端 = ragflow @ ...`

### 数据采集器（2026-09-15 新增，按《技术文档》§3.1）

- 位置 `backend/app/collector/`：`base`（HTTP 限流/重试/UA）· `normalize`（清洗/语言/行业/质量闸门）·
  `registry`（注册表 + 频率筛选）· `runner`（编排）· `sources/` 5 模块
- **统一入库口** `app/services/signal_ingest.py::persist_signals` —— API 与采集器共用，
  按 `content_hash + url` 双重去重 + 事务 + 审计 + **实体抽取**（`extract_tech_keywords`）
- API：`POST /signals/collect`、`GET /signals/adapters`、`GET /signals/collect/preview`
- **源可用性实测（勿重复试错）**：

  | 源 | 结论 |
  | --- | --- |
  | arXiv API | ❌ `Rate exceeded` → 改用 **OpenAlex + Crossref** |
  | gov.cn `zhengce/zuixin.htm` | ❌ JS 跳转页 → 真入口 `sousuo.www.gov.cn/search-gov/data?t=zhengcelibrary_gw` |
  | ccgp `search.ccgp.gov.cn` | ❌ 反爬「频繁访问」→ 用列表页 `cggg/zygg/` + HTML 解析 |
  | 工信部 | 需 UA（无 UA 返 403） |
  | gov.cn / 发改委 / 36氪 / IT之家 / 界面 / ccgp 列表 / OpenAlex / Crossref | ✅ 200 |

- 13 适配器 / 8 可用（设计要求 ≥5）；需密钥的 5 类（专利/融资/招聘/社媒/供应链）
  请求逻辑已就绪，填 `.env` 的 `*_API_KEY` 即启用（**未联网验证**，首次启用先 `--dry-run`）
- ⚠️ **论文源不能按日期排序**：`sort=publication_date:desc` 覆盖相关性，会返回无关论文
- ⚠️ **相对链接必须基于列表页**：ccgp 的 `./gkzb/...` 若基于站点根会丢 `/cggg/zygg/` → 404
- ⚠️ **行业优先级**：信号自带 > 内容推断（标题权重×3）> 公司行业兜底。
  **公司行业不能优先**，否则所有外部信号同质化、赛道差异消失
- ⚠️ 招标源 `requires_keywords=True`（无检索词会拉回全量无关公告）
- 文档：`docs/信号采集器实现说明.md`

### 大模型 / 智能体接入（2026-09-14 17:00 起生效）

- 7 Agent + 4 层编排（第 1、2 层并行）+ 有界反馈循环
- `LLM_PROVIDER=ollama`，**三档全指 `qwen3.8:27b`**（monitor 走 simple 档也指 27b，指 7b 会引入第 3 个常驻模型）
  - 报错常驻：27b 17.87 GB @131072 + bge-m3 0.66 GB = **18.54 / 32 GB**
  - 七 Agent 分析 **418 s → 47 s**
  - 27b 默认 `num_ctx=131072` ⇒ Hermes / TrackCompass / RAGFlow 三方都不传 num_ctx 时**共用同一 runner**，无重载抖动
- Ollama 在线：`http://localhost:11434`（**必须 localhost**，`127.0.0.1` 返回 000，IPv6-only）
- 切换成本：改 `backend/.env` + 重启，业务代码零改动；自查 `python scripts/check_llm_connection.py`
- `rule` 仍应保留为**零依赖可断言基线**（pytest conftest 强制 rule，296 单测不依赖网络）
- **嵌入**：`EMBEDDING_BACKEND=local`（哈希 1536 维，零依赖）｜切 ollama+bge-m3 时注意
  ① 模型名用 `EMBEDDING_OLLAMA_MODEL=bge-m3`（非 HF 名）② 端点 `/api/embed` + `input`（旧端点兜底）
  ③ bge-m3 是 **1024 维**（`EMBEDDING_DIM` 默认 1536 只适用 OpenAI）。**切后端后向量不可比，需 reindex**

### RAG / RAGFlow

- 开关 `RAG_BACKEND`：`ragflow`（默认）｜`memory`（自建）；缺 Key/dataset/不可达时**自动降级 memory 并告警**
- 适配器 `app/services/ragflow.py: RAGFlowEngine`，与 `RAGEngine` 同契约（基类 `rag.py: BaseRAG` 共享后处理）
- **证据链映射**：上传文件名 = `{signal_id}.txt` ⇒ `document_keyword` 反查 signal_id；
  `source/signal_type/title/url` 存旁路表 `backend/ragflow_index_map.json`
- RAGFlow 写入是四步异步流水线 ⇒ **有不可检索窗口**（`RAGFLOW_PARSE_WAIT_SECONDS` 默认 90s）
- 已激活：dataset `trackcompass-signals` = `19a68252b01311f18c92bfc493440fd4`
- **R6 关键词抽取 = 已关闭**（`.env` → `RAGFLOW_KEYWORD_EXTRACTION=false`）。
  实测收益 0、耗时 ×19（100ms → 1945ms），且短查询相似度**下降 12%**（同义重复 + 逗号未清洗）。
  关闭后 `/rag/search` 1.777s → 0.077s，并**消除一个隐式 GPU 排队点**
- ★ **RAG 不参与趋势/评分/报告的数值产出**，只喂 prompt；`provider=rule` 时 prompt 被完全忽略。
  断言「某页面数据来自 RAG」前必须先做**换桩对照实验**（曾实测 8 条趋势换空桩后完全一致）
- 留档：`docs/RAGFlow接入评估.md`、`docs/RAG关键词抽取开关评估.md`

### 能力画像 / 多公司

- 一个租户可建**多份**画像；**「当前公司」= 最近保存的那一份**（`updated_at desc`）
- ⚠️ 同一口径有**两个调用点必须同步**：`app/api/deps.py:get_profile` 与 `services/analysis.py:build_profile_input`
- `POST /company/profile` 解析顺序：显式 `id` > 同名 > 新建；越权/不存在 → 404
- 接口：`GET /company/profiles`（含 `is_current`）、`GET /company/profile?profile_id=`
- ⬜ 遗留：原型 §5.3 是 4 个 Step 同时可见，实现是 `display:none` 一次只显示一段；无「删除公司」接口

### 趋势雷达

- 机制：`TrendReconAgent._bucket()` 把 `signals.keywords` 当桶名 ⇒ **关键词有则出现，无则永不出现**
- **能力匹配已改为「查询时计算」**（2026-09-15）：趋势落库存**客观标签** `factors._tags`，
  接口层用 `tags ∩ 当前公司能力词` 实时算 `matched` ⇒ **切公司即时生效，无需回算**
- 证据链：LLM 的 prompt 要求回编号（`E1`），**必须用 Agent 自己刚检索的 docs 反查**
  （不能用 `state.evidence` —— 跨 Agent 累积 + 第 1 层并行 + `_dedupe_evidence` 最后重排都会错位）
- ⬜ 遗留：**趋势累积语义待裁决**（同名更新 vs 每次扫描快照）；
  ✅ 种子演示信号已清理（2026-09-15），`seed_all` / `bootstrap` 默认不再写演示信号

### 公共数据层与 E2E 租户隔离（2026-09-16 新增，已落地）

★ **判据：`tenant_id IS NULL` ⇒ 公共数据**（`app/core/visibility.py`）。零 DDL ——
`signals.tenant_id` / `trends.tenant_id` 模型里本就是 `str | None`。
选它而非哨兵租户/`is_public` 字段的理由：无需迁移 + 语义自洽（外部情报没有归属）+
与 RAG（单一 dataset）/ 图谱（全局单例）的**既有全局性**对齐（数据层曾是唯一按租户过滤的一环）。

| 归属 | 数据 |
| --- | --- |
| 🟢 公共 / 全局 | `signals`（公共）、`tracks`、`roles`（后两者本就无 `tenant_id`） |
| 🔴 租户私有 | `trends`（含 `subscribed`/`in_pool` 租户状态）、`company_profiles`、`opportunities`、`scorecards`、`experiments`、`reports`、`alerts`、`audit_logs` |

- 读写点：采集器写 `PUBLIC_TENANT_ID`；`POST /signals` 新增 `is_public`（默认 True）；
  改 `deps.signal_stats` / `signals.py` / `demand.py` / `alerts.py` / `profile.py` / `analysis.py` 为 `visible()`
- ⚠️ `reindex` **只索引公共信号**（RAGFlow 单 dataset，否则跨租户泄露）；
  `build_from_signals` 同理只纳入公共信号
- ⚠️ **E2E 隔离与公共层是同一件事的两面**：信号若私有，E2E 租户看不到信号 →
  `generated=0` → 链路断路。所以「方案 A」单独做不了
- E2E：`seed.ensure_e2e_tenant()`（租户+用户+**评分卡**）+ `seed.purge_e2e_data()`（8 张表，
  **`scorecard_versions` 无 `tenant_id`，须经 `scorecard_id` 子查询删**）
- ⚠️ **遗留已登记并暂缓**（用户 2026-09-16 决定「记录下来，暂不解决」）：
  **L-01** 知识图谱仍是全局单例（`trends`/`opportunities` 节点跨租户可见）、
  **L-02** 私有信号的向量检索未隔离（`reindex` 只索引公共层）。
  单组织部署下无实际泄露，属结构性欠债；**触发条件 = 接入第 2 个真实组织租户之前**。
  ⇒ 全部遗留项见 **`trackcompass/docs/遗留项台账.md`（L-01~L-13，唯一出处）**
- 文档：`trackcompass/docs/公共数据层与E2E租户隔离.md`

### 主机 / 网络

| 项 | 真值 |
| --- | --- |
| 本机主机名 | `DESKTOP-9HDUO7P` |
| **本机 LAN IP** | **192.168.2.192**（旧记录的 `192.168.2.28` 已过期） |
| WSL 客户机 IP | 172.25.252.110，网关 172.25.240.1 |
| 判定手法 | `socket.connect((目标,P)).getsockname()[0]`，**源 IP == 目标 IP ⇒ 目标就是本机** |
| 端口 | 8000 ✅ / 8001 ✅ / 9219 ✅(Hermes) / 2222 ✅(SSH) / 11434 ✅(需 localhost) / 18081 ❌ / 3080 ❌ |

- 本机 `http_proxy` 会污染 curl（返 502/000 假象）⇒ 验证端口一律 `--noproxy '*'`；httpx 用 `trust_env=False`

### 显存优化（2026-09-14 已执行，配置已固化）

- 生效配置：`OLLAMA_MAX_LOADED_MODELS=2` + `OLLAMA_FLASH_ATTENTION=true` + `OLLAMA_KV_CACHE_TYPE=q8_0`
  - **值必须是 2 不是 1**：Ollama 把 embedding 一并计数，设 1 会让 bge-m3 与对话模型互相驱逐
  - 落点 `/etc/systemd/system/ollama.service.d/override.conf`（**原文件已存在，必须合并不能覆盖**）
  - 留档 `scripts/ollama-override.conf`；两级备份 `.bak.20260914-143125` / `.bak.step1-max1.20260914-143506`
  - 实测 KV 8192 → **4352 MiB**（q8_0 实际 8.5 bit，`8192×8.5/16` 数学吻合），n_ctx 仍 131072
- ★ **共存规律**：加载 `bge-m3` 时预测仅 1.1 GiB ⇒ 对话模型已驻留就必然可共存；
  加载 `qwen3.8:27b@128K` 时预测 **42.7 GiB** > 可用 29.6 GiB ⇒ **无条件驱逐**
  ⇒ **RAG 实用做法：让对话模型常驻（预热），嵌入模型随后加载**
- 根因：Ollama 对 27B@128K 的预测**高估 2.4×**（预测 42.7 vs 实测 17.87 GB），闸门在 `sched.go:551`，
  **与 `MAX_LOADED_MODELS` 无关**；该高估无法用配置消除（0.32.15 无相关开关）
- KV 明细（读 `journalctl -u ollama`）：27b @128K f16 → 权重 15,339 + KV 8,192 ≈ 23.0 GiB；
  @64K → 4,096；@32K → 2,048。**KV 与 n_ctx 严格线性**（曾误估「降 num_ctx 释放 15GB+」，真值 ≈6.0 GiB）
- ⚠️ Ollama 按 `n_ctx` 分组调度，不同调用方传不同 num_ctx 会建不同 runner ⇒ 模型反复重载
- 云模型：Free 并发 1 / Pro 3 / Max 10；**服务器在美欧新 ⇒ 数据出境**，与企业本地化冲突

### Hermes（评估结论）

- TrackCompass 对 Hermes **零引用**；Hermes 在线 `192.168.2.192:9219`
- **能力错配**：Hermes 是「会话+cwd+文件IO+Skill」的**执行型**运行时，TrackCompass Agent 是
  **分析型**（结构化 JSON 输出）⇒ **核心链路不应对接**（净增复杂度无收益 / 破坏可回归性 / 无业务用户隔离）
- 唯二值得接的边缘场景：① 非文本文件解析（PDF/Word）② 需真跑代码的取数
- **访问纪律**：只做 TCP + HTTP GET 只读探测；登录/ticket/会话/prompt 等有副作用操作须先获许可
- 文档：`docs/Hermes接入评估.md`

---

## 约定（后续迭代必须遵守）

1. **重型基础设施一律走「开关 + 适配器」**，不写死在业务代码。基类
   `BaseVectorStore`/`BaseGraphStore`/`BaseEmbedding`/`BaseAgent`，开关在 `backend/.env`。
2. **Agent 双实现不得只留一个**：`rule()` 是离线基线与测试依据，`build_prompt()` 是真实模型路径。
3. **评分公式改动必须同步更新单测里的文档公式断言**（`tests/test_scoring.py`），防实现漂移。
4. **输出契约的数值字段必须显式命名并注明量纲**，禁止让下游猜。
5. **同一文件多次编辑必须串行**（并行 Edit 会静默互相覆盖）。
6. **挂在 `/` 的 catch-all `StaticFiles` mount 必须最后注册**，显式路由要先声明。
7. **UI 由后端同端口托管**（`SERVE_FRONTEND=true`），改前端后必须 `npm run build` 再重启后端；
   前端用 HashRouter，无需 history fallback。
8. **改 `config.py` / `main.py` / `.env` 后必须硬重启后端**（未开 `--reload`，`touch` 无效）。
9. **排查前端 4xx 顺序**：服务存活 → 契约校验 → 静态资源 → 浏览器默认请求（`/favicon.ico`）。每步用一条 curl 证伪。
10. **写「检查类工具」必须先自证**：用已知正确样本确认工具不误报，再判断业务代码。
11. **`LLMService.complete()` 三条路径（成功/缓存命中/失败）都必须写 `_meta`**，
    `provider` 如实反映真实调用方；trace 用 `engine` 区分 `rule-engine`/`llm`。
12. **判定「是否真在用大模型」看运行时 trace 耗时**：亚毫秒 = 规则引擎；数十~数百毫秒 = 真实推理。
13. **缓存层返回可变对象必须深拷贝**（`copy.deepcopy`）。
14. **收敛资源上限前先问「有几种类型的模型要常驻」** —— 生成与嵌入是两类，设 1 会互相驱逐。
15. **判定外部服务日志状态必须匹配完整消息文本**，不能只匹配字段尾部
    （Ollama 的 `fits alongside`（正向）与 `predicted to exceed ..., evicting` 尾部格式完全相同）。
16. **清理常驻模型用 `ollama stop <model>`**，不要用 `keep_alive:0` 请求（会「先加载再卸载」且卸载异步）。
17. **「配置值」≠「实际生效值」**，对外一律报实际值（并附 `configured` 对照）。
18. **同一能力的两条模型通道（生成/嵌入）必须分别给出切换与自查手段**，默认都应是零依赖离线实现。
19. **一个查询口径若有多处调用点，改一处必须同步另一处**（如「当前公司画像」）。跨层宁加注释互指也不反向依赖。
20. **新增「选择已有 X」类功能前，先确认数据模型是否支持多份**；原本 upsert 单条时「选择」没有对象。
21. **替换外部组件必须走「适配器 + 开关 + 自动降级」**：抽共同基类让新旧实现共享后处理，上层零改动。
22. **对接外部系统先读官方 API 契约与真实配置，不要只看默认配置文件**（RAGFlow 的 TEI 配置就是未启用模板）。
23. **外部系统返回字段不足以还原领域对象时必须补映射层**：优先「业务 id 当外部主键」+ 旁路元数据表，
    而非解析正文标记（会被对方解析器改写）。
24. 🌟 **批量替换数字绝不能用裸数字做 `replace_all`**，必须带上下文整串替换。
    曾用 `old_string="192"` 把 `192.168.2.192` 改成 `208.168.2.208`（8 处）、KV 值 `8,192`→`8,208`。
    **改完必须全库正则审计**。教训泛化：**任何「按值替换」都可能误伤含该值的其它语义（IP/端口/坐标/尺寸）**。
25. **「加载了适配器」≠「适配器已启用」**：前者代码层，后者运行时层。验证要看**启动日志分支** + **健康接口实际值**。
26. 🌟🌟 **从「规则引擎」切到「真实大模型」必须假定下游契约会被打破**。实测一天暴露 5 类缺陷：
    字段缺失 / 空对象 / 类型错乱 / 数学不自洽 / 输入不匹配。对策：
    ① 每个 Agent 声明 `required_keys`；② 边界一律用 `as_dict/as_list/dicts_only` 收口；
    ③ **派生量（总分/方向/评分）一律重算，不采信模型自报**；④ 提示词写「严格 JSON」不等于模型会遵守。
27. **「规则实现作为校验基线」必须真正接上**，落点在 `run()` 里的独立方法（如 `backfill_contract`），
    **不能塞进 `postprocess()`**（子类都重写且不调 `super()`）。回填分两档：普通字段只补缺失/None，
    契约必需字段空了也必须兜住。
28. **Ollama 跑 Agent 类任务必开 `think=false` + `format=json` + 限 `num_predict`**
    （带 thinking 的模型默认先输出推理链，实测 2.6s → 0.9s；不限 num_predict 单 Agent 曾达 129s）。
29. 🌟 **「只写入、不防重」是本项目的高频缺陷模式**（已出现 4 次：`upsert_trends` 键不一致、
    `persist_scan_result` 重复写、`to-pool` 无幂等、`POST /signals` 不判重）。
    **任何「创建型」接口都必须默认考虑幂等**，去重逻辑收敛到唯一入口。
30. 🌟 **「代码修好了」≠「线上好了」**：修完要问「这个字段是实时算的，还是落库快照？」
    落库快照类字段（如 `factors._capability`）必须**回算历史数据**，否则旧记录仍带错误标注。
31. **契约字段缺失往往三层同时漏**（prompt 没要求 / 后处理没兜 / 前端无防御），任一层补上都不会崩。
    新增测试断言要校验**语义**（如「label 不能是 UUID」「experiments 必须是数组」）而非存在性 ——
    弱检查会导致「假通过」（`e2e_smoke.py` 的「趋势含证据链」就曾因只判非空而假通过）。
32. **`rowKey` 是 TS 管不到的约定字段**（`dataSource` 是 `any[]`），字段不存在只在运行时 warning；
    唯一键必须选**语义上保证唯一**的字段（`agent` 看着唯一但反馈循环会重跑 ⇒ 用后端生成的 `seq`）。
33. **一次报错值得全量审计**：用户报 1 处 `rowKey` 问题，全项目扫出 12 处同类模式（2 处真缺陷）。
34. 🌟 **删除数据必须同时检查「派生数据」与「外部索引」**，不能只删主表。
    实测删 40 条种子信号时发现：44 条趋势的证据引用它们、2 条机会卡 `source_trend_id` 指向它们、
    **RAGFlow 侧还有 79 篇已删文档会被继续召回**（因为 `index()` 是幂等追加语义，不做反向清理）。
    这类残留**不报错、极难察觉** ⇒ 清理清单必须包含：主表 → 引用它的派生表 → 外部索引（RAG/向量/图谱）。
    同理，任何「演示/种子数据」都要有开关，避免重置数据库时假数据回来。
35. 🌟 **断言描述里不要放可能越界的取值**。`check(名, 条件, 描述)` 三个参数**都会先求值**，
    描述串写 `x[0]` 时，若 `x` 为空则描述先崩 —— 把「检查失败」变成「脚本崩溃」。
    同类：`e2e_smoke.py` 需求探针项曾因此 IndexError 中断整个冒烟。
    这类缺陷**在数据充足时永不触发**（属「被数据掩盖的脚本缺陷」），
    断言应表达**请求-响应自洽**而非「必须有值」（如「匹配信号数 > 0」与「痛点非空」一致即可）。
36. **实体抽取的英文分支必须做形态白名单**：论文类信号正文是英文摘要，
    只按「len>1 且 ascii」抽取会把普通词（Rough/Military）、URL 片段（https）、
    作者名（Zhang）、文献格式词（of/vol）全收进 keywords，并经 `trend_tags` 传染到趋势标签。
    只收：显式词表 / 含数字型号 / 全大写缩写 / 驼峰命名。
37. 🌟 **判断两条记录是否「同一实体」，用事实字段而非 LLM 生成的名称。**
    实测（2026-09-16）：趋势名相似度 0.91 的三对，**证据信号集合 Jaccard = 1.000**
    （同一次聚类的产物，只是被 LLM 起了不同名字）。
    而**名称相似度不可靠**：整体相似度被模板词（「AI驱动的」「具身智能在」）系统性抬高 ——
    整体 >0.9 的对，差异词相似度只有 0.45–0.66；且单一阈值分不开
    （跨主题最高 0.7132 > 同主题最低 0.4253，区间重叠）。
    ⚠️ **算这种相似度必须用真语义模型（bge-m3）** —— 项目的 `LocalHashEmbedding`
    是确定性哈希、**不携带语义**，算出来看似合理实则无意义。
    ⚠️ 小集合上用 Jaccard 要带**最小交集数**下限（趋势证据常只有 1–3 条，
    单条共享信号就能把比例抬到 0.5）。
38. **测试脚本必须与真实数据隔离**。`e2e_smoke.py` 连的是真实服务 + 真实库，
    20+ 写操作无清理 ⇒ 实测留下 11 条实验、10 条报告、趋势累积 6 批到 17 条。
    污染的不只是数据：用种子数据判定的断言会**恒为真**、掩盖真实缺陷
    （「数据源覆盖 ≥5」即如此）。
    ✅ **已解决（2026-09-16）**：E2E 改用专用租户 `e2e@test.local`，`finally` 中清理，
    反复运行零累积（实测清理 33 行，八张业务表归零）。做法见「公共数据层」段。
39. **枚举型字段换词表后必须迁移历史数据 + 读取时归一**。`alerts.level` 库里混了
    三套写法（`high/medium/low` 49 条、`High/Medium/Low` 4 条、规范值 5 条），
    而接口只统计 `critical/warning/info` ⇒ **49 条级别不可见，严重度汇总恒为 0/0/0**
    （总数 > 0 但三档全 0，就是这个信号）。
    正解三件套：① 产出方定义规范词表 + 别名映射 + `normalize()`；
    ② 写入即归一（不再产生脏数据）；③ 读取时归一后再计数，**保证分档之和 = 总数**。
    ⚠️ 别顺手改同名的其它概念：`competition.py` 的 `level`、`scoring.py` 的 `threat_level`
    也用 `high/medium/low`，但那是**竞争威胁等级**、不进 `alerts` 表。
    ⚠️ 归一前 `{"critical":0,...}[level]` 这种硬查表遇非规范值会 `KeyError` —— 改 `list.index()` 前先归一。
40. **改动后必须复核落盘内容，不能只看「编辑成功」回执**。本轮踩到：对**同一文件**
    并行发起多个编辑时，部分改动会丢失（实测 `e2e_smoke.py` 的登录行、
    `seed/__init__.py` 的信号归属、`signals.py` 的 import 各丢一次）。
    后果很重 —— 登录凭据没生效，脚本仍以演示账号跑，把 6 条趋势 + 4 条预警
    写进了演示租户（靠审计日志 `auth.login` 的 `tenant_id` 才定位到）。
    ⇒ **同一文件的编辑一律逐个串行执行，并在每批改动后 grep 复核关键行。**
41. **遗留项唯一出处 = `trackcompass/docs/遗留项台账.md`**（L-01~L-13）。
    新增遗留项时**同时**在来源报告对应小节加一行「已登记 L-xx」，两侧对齐；
    解决后**不删行**，改 ✅ 并补日期与依据（台账兼作决策留痕）。
    每项必须写**触发条件**（"出现什么情况就必须做"），不许写"以后再做"。
    **P1 组（L-01/L-02）的触发条件统一为：接入第 2 个真实组织租户之前。**

---

## 参考文档（位于工作区根目录，未纳入版本管理）

`需求文档.txt` / `设计文档.txt` / `技术文档.txt` / `原型设计.txt` / `AI设计.txt`
- **§权威出处**：技术文档 §3.1 数据源 · §3.2 数据管道 · §4.x 评分公式 · §6.2 API 契约；
  设计文档 §7 功能需求 · §10 API · §12 页面；原型设计 §4.1 设计 Token · §11.1 页面清单

## 交付留档（`trackcompass/docs/`）

研发计划 · 测试报告 · 产品使用手册 · 验收标准 · 大模型与智能体接入说明 ·
Hermes接入评估 · 模型调用路径与显存评估 · 显存优化与云模型评估 ·
趋势雷达工作原理与诊断 · 数据来源与真实度说明 · RAGFlow接入评估 ·
RAG关键词抽取开关评估 · 趋势雷达·能力画像与证据链诊断 · 趋势雷达证据链修复与数据清理报告 ·
需求探针与图谱与能力匹配修复报告 · 机会池重复卡片修复报告 · 前端列表key冲突修复报告 ·
趋势雷达Bug复查报告 · **趋势标签化重构报告** · **信号采集器实现说明** ·
**公共数据层与E2E租户隔离** · E2E写入数据清单 · **遗留项台账**（L-01~L-13，唯一出处）

可复用脚本（`trackcompass/scripts/`）：`check_api_contract.py` · `e2e_smoke.py` ·
`check_llm_connection.py` · `check_ragflow.py` · `probe_topology.py` · `probe_two_order.sh` ·
`diag_ollama_knobs.sh` · `ollama-override.conf` · `compare_ragflow_keyword.py` · **`collect_signals.py`**
