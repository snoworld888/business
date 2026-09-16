# Hermes 接入评估

| 项目 | 内容 |
| --- | --- |
| 版本 | V1.0 |
| 日期 | 2026-09-14 |
| 评估对象 | 赛道罗盘 TrackCompass ↔ Hermes（hermes-native-serve） |
| 输入文档 | 《AI设计.txt》《hermes接入说明》 |
| 结论 | **当前未对接（代码零引用）；核心链路不应接入；有 2 个边缘场景值得按适配器接入** |
| 判定依据 | 代码库全量检索 + 运行时只读探测，见 §2 |

---

## 一、结论先行

| 问题 | 答案 |
| --- | --- |
| 系统对接了 Hermes 吗？ | **没有。代码库对 Hermes 零引用**（`hermes`/`9219`/`ticket`/`websocket`/`serve_client`/`HERMES_HOME` 全部 0 命中） |
| Hermes 服务本身可用吗？ | **可用。** `192.168.2.192:9219` TCP 可连接（16ms），HTTP 返回 302（登录跳转），与接入说明描述一致 |
| 有对接的必要性吗？ | **核心链路：无必要，且不应接。边缘场景：有 2 处值得接**（非文本文件解析、需真跑代码的取数） |
| 建议动作 | 保持核心链路不动；如需补文件解析能力，新增 `HERMES_ADAPTER` 开关按**异步任务适配器**接入，并先补齐 §6 的 4 项缺口 |

> 一句话：**Hermes 是「会话 + 工作目录 + 文件 IO + Skill」的执行型智能体运行时；
> 赛道罗盘需要的是「结构化 JSON 输出 + 可断言 + 低延迟」的分析型智能体。
> 两者能力定位不同 —— 接进来不是加分，是增加四层不必要的复杂度。**

---

## 二、现状核查（证据链）

### 2.1 代码库对 Hermes 零引用

```bash
grep -ri "hermes" --include="*.py" --include="*.ts" --include="*.tsx" . | grep -v node_modules
```

| 关键词 | 命中数 |
| --- | --- |
| `hermes` | 0（唯一命中是 Ollama 模型名 `hermes3:8b`，**非 Hermes 对接**） |
| `9219` | 0 |
| `serve_client` | 0 |
| `ticket` | 0 |
| `websocket` | 0 |
| `HERMES_HOME` | 0 |
| `schedule-extractor` | 0 |
| `session_id` | 0 |
| `hermes-native` | 0 |

另外：本机未找到 `serve_client.py`（接入说明提到的参考客户端）。

### 2.2 LLM 出口仅两条通道，无 Hermes 通道

`backend/app/services/llm.py` 的路由只有：

```python
if self.provider == "rule" or not self._remote_ready(): ...   # 规则引擎 / 兜底
if self.provider == "ollama": ...                            # Ollama /api/chat
else: ...                                                    # OpenAI 兼容 /v1/chat/completions
```

没有任何「会话式」调用路径。

### 2.3 Hermes 服务在线（只读探测）

```bash
python scripts/check_llm_connection.py --skip-inference
```

```
[3b] 外部会话执行型智能体服务（只读 TCP 探测，不做登录/建会话）
    ✅ 在线  Hermes 智能体服务 (hermes-native-serve)  192.168.2.192:9219  16ms
```

> 本次仅做 **TCP 连通性 + 一次 HTTP GET**（返回 302），
> **未执行**登录、申请 ticket、建 WebSocket、创建会话、提交 prompt 等任何有副作用的操作。

### 2.4 四份原始设计文档从未要求 Hermes

《需求文档》《设计文档》《技术文档》《原型设计》中指定的编排方案是 **LangGraph**，
模型侧是 **GPT-4o / Claude 3.5 / Qwen2.5**，向量库 **Milvus**、图谱 **Neo4j**，
**均未提及 Hermes**。Hermes 是独立于本项目的外部服务。

---

## 三、能力错配分析（为什么核心链路不该接）

### 3.1 两者的本质差异

| 维度 | Hermes | 赛道罗盘的 7 个 Agent |
| --- | --- | --- |
| 形态 | **会话执行型运行时** | **结构化分析型智能体** |
| 调用协议 | 登录 → ticket → WebSocket → 会话 → prompt → 等 `message.complete` | 一次 `chat/completions`，或纯本地函数调用 |
| 上下文 | 带 `cwd` 的工作目录 + `inputs/`/`outputs/` 文件 IO | 内存中的信号列表 + RAG 检索结果 |
| 产出 | Skill 执行结果（含**文件副作用**） | 结构化 JSON（Schema 固定，可断言） |
| 现有资产 | Skill：`/schedule-extractor` 等 | 7 个 Agent × 双实现（`rule()` 确定性 + `build_prompt()`） |
| 隔离模型 | **共用 `backend` 账号，不提供业务用户隔离**（接入说明 §11 自认） | 多租户 `tenant_id` + 5 角色 RBAC + 180 天审计 |

### 3.2 核心链路不应接入的三条理由

**理由 1 — 用重装备做轻活，净增复杂度**
把「一次趋势评分」交给 Hermes，需要额外承担：会话生命周期管理、
ticket 申请与过期、WebSocket 心跳与重连、工作目录与文件校验、产物清理。
换来的是**同样的一个 JSON**。这四层复杂度不产生任何业务价值。

**理由 2 — 破坏可回归性（最致命）**
赛道罗盘的核心资产之一是「**确定性规则基线 + 246 个可断言单测**」。
Hermes 的产出是 Skill 执行结果，含文件副作用与非确定性，
一旦核心分析走 Hermes，单测的断言基础即失效 —— 测试报告里那 246 个用例将失去意义。

**理由 3 — 隔离模型冲突（合规风险）**
接入说明 §11 明确承认：「**原生服务共用 backend 账号，不提供本项目的业务用户隔离**」，
并提醒「检查后端是否在查询、提交、删除和下载前校验用户与会话、任务目录的对应关系」。
而赛道罗盘是要求租户隔离 + RBAC + 审计的系统。
把 Hermes 会话直接暴露给终端用户，会让「用户 A 看到用户 B 的会话」这一已知边界
**穿透到业务层** —— 这是明确的合规风险，不是技术债。

### 3.3 关键补充判断

Hermes 现成的 Skill 资产（`/schedule-extractor` 时间/事件抽取）
面向的是**日程/日历类产品**，与「赛道发现与决策」无关。
即：**即使接了 Hermes，也用不到它最有价值的那部分资产**，只能借它的通用「会话执行」外壳。
这进一步削弱了接入的必要性。

---

## 四、但有 2 个场景，Hermes 明确更优（按 ROI 排序）

### 场景 A — 非文本文件的深度解析 ★ 优先级最高

**现状是真实短板**（非推测）：

| 环节 | 当前实现 | 限制 |
| --- | --- | --- |
| `POST /company/profile/parse` | `_decode_text()` 检测空字节 / 可打印字符占比 | **纯文本可解析；PDF / Word 二进制直接拒绝** |
| 产品使用手册 | — | 已明写「PDF / Word 暂不支持直接解析，请粘贴文本」 |
| 验收标准 | — | 该项未列入已达成 |

Hermes 的「会话 + `cwd` + Skill + `outputs/` 产物校验」正是这类任务的正解：
把 PDF 丢进 `inputs/`，让 Skill 抽取，产物落 `outputs/`，后端校验后下载。

**ROI 判断**：补的是**已记录的功能缺口**，用户可感知，价值确定。

### 场景 B — 需要真跑代码的取数 ★ 优先级中

技术文档 §3.1 要求接入政策 / 论文专利 / 融资 / 招聘 / 招标 / 社媒采集器
（Scrapy / Playwright / 官方 API）。这类任务**不是一次 LLM 调用**，
而是「跑脚本 → 产出文件 → 校验 → 回传」，Hermes 的会话执行模型比 chat API 合适。

**ROI 判断**：属 P2-1（真实数据源接入）的前置能力，但 P2-1 本身尚未启动。

### 场景 C — Office 报告的真实生成与产物校验（优先级低）

现状：报告导出 PPTX/DOCX 依赖 `python-pptx`/`docxtpl`，未安装则降级 Markdown。
接入说明 §113 的警示值得吸收：「**模型回复说已生成文件时，仍必须检查实际文件**」。

**ROI 判断**：现有 `python-pptx` 路径已够用。
**建议只吸收「产物存在性校验」这一条思路到现有导出流程**，不必为此接 Hermes。

---

## 五、若决定接入，必须遵守的约束

接入说明自身列出的「正式系统还要补充」清单，**在本项目必须全部满足**才可上：

| 编号 | 接入说明原文要求 | 本项目强制约束 |
| --- | --- | --- |
| C1 | 业务鉴权 | 走既有 JWT + RBAC；Hermes 凭证仅存服务端，**不下发前端** |
| C2 | 任务记录 | 每次调用落库 `hermes_tasks`（task_id / session_id / 状态 / 产物路径 / 错误） |
| C3 | 并发控制 | 信号量限流；**禁止**并发创建会话打爆服务 |
| C4 | 重连策略 | 有界重试 + 幂等键；**禁止**「超时即无限重发同一 prompt」（§107 明确警示） |
| C5 | 输出文件校验 | 校验产物存在 + JSON Schema + 字段完整性；**解析失败标记业务失败，不得伪造空结果** |
| C6 | 清理策略 | 会话与临时文件定时清理（当前服务无隔离，残留会串号） |
| C7 | 多用户隔离 | **禁止**把 Hermes 会话当作隔离边界；仅限单租户/内部任务，或网关层做映射 |
| C8 | 降级通道 | Hermes 不可达时回退现有本地实现（与现有 `rule()` / 远端 双通道一致） |

---

## 六、建议方案（若裁决为「接」）

**最小侵入式差异化接线**，与现有「开关 + 适配器」架构保持一致：

```
backend/app/services/hermes_client.py     新增：Hermes 客户端（登录/ticket/WS/会话）
backend/app/core/config.py                新增：HERMES_ENABLED / HERMES_BASE_URL /
                                                HERMES_USER / HERMES_PASSWORD_ENV /
                                                HERMES_TASK_CWD / HERMES_TIMEOUT_SECONDS
backend/app/models/                       新增：HermesTask（C2 任务记录表）
backend/app/api/v1/profile.py             改造：/company/profile/parse 支持 PDF/Word
                                                （Hermes 可用则走 Hermes，否则回退现有纯文本逻辑）
```

**硬约束**：
1. **只走异步**：`POST` 提交 → 落库 → 轮询/回调，**绝不阻塞七 Agent 分析链路**
2. **不进 Agent 编排**：7 个 Agent 的 `rule()` / `build_prompt()` 一律不动
3. **默认关闭**：`HERMES_ENABLED=false`，与 `ENABLE_MONITOR_SCHEDULER` 等开关同级
4. **凭证从环境变量读**，不落 `.env` 明文、不进代码库
5. **单租户专用**：不向终端用户暴露 Hermes 会话标识

---

## 七、待裁决项

> 📋 已登记至统一台账 `docs/遗留项台账.md` → **L-12**。状态变更请同时改两处。

| 编号 | 事项 | 需你决定 |
| --- | --- | --- |
| D1 | 是否接入场景 A（PDF/Word 画像文档解析） | 接 / 不接 |
| D2 | 是否接入场景 B（采集器取数执行） | 接 / 不接；若接，是否等 P2-1 一起做 |
| D3 | 场景 C 是否只吸收「产物校验」思路而不接 Hermes | 建议「是」 |
| D4 | Hermes 凭证的存放方式 | 环境变量 / 其他密钥机制 |
| D5 | 是否需要我先出一版 `hermes_client.py` 的接口设计（不落实现）供评审 | 需要 / 不需要 |

---

## 附录：本次探测执行与未执行的操作

**已执行（全部只读）**
- 代码库关键词全量检索
- `TCP connect 192.168.2.192:9219`
- `HTTP GET http://192.168.2.192:9219/`（返回 302）

**明确未执行**
- ❌ 登录、申请 ticket
- ❌ 建立 WebSocket、创建会话
- ❌ 提交任何 prompt、触发任何 Skill
- ❌ 对 Hermes 服务做任何启动 / 停止 / 重启 / 配置修改
- ❌ 读取或写入 Hermes 侧任何文件
