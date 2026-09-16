"""端到端 HTTP 冒烟脚本：验证真实服务上的完整业务链路。

用法：
    python scripts/e2e_smoke.py [base_url]
默认 base_url = http://127.0.0.1:8020

链路：登录 → 能力画像 → 数据源 → 趋势扫描 → 加入机会池 → 七 Agent 分析
      → 赛道评分 → 需求探针 → 验证实验（含状态机+回写）→ 决策报告 → 监控预警 → 校准预览

## 租户隔离（重要）

脚本使用**专用的 E2E 租户**（`e2e@test.local`），所有写入落在它名下，
演示租户（智造未来科技）的数据不再被测试污染。运行结束后清空该租户的业务数据，
因此**反复运行不会累积**（历史问题：曾累积出 17 条趋势 / 11 条实验 / 10 份报告）。

隔离之所以成立，前提是**信号被划为公共数据**（`tenant_id IS NULL`）：
E2E 租户自己没有任何信号，靠读公共层才能跑通「扫描 → 趋势」链路。
见 `app/core/visibility.py` 与 `docs/公共数据层与E2E租户隔离.md`。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

# 脚本需要直接访问 DB 来初始化 / 清理 E2E 租户（不走 HTTP，避免为此扩大 API 面）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.seed import (  # noqa: E402
    E2E_EMAIL,
    E2E_PASSWORD,
    ensure_e2e_tenant,
    purge_e2e_data,
)

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8020") + "/api/v1"
CLIENT = httpx.Client(base_url=BASE, timeout=180.0, trust_env=False)
PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    icon = "✅" if condition else "❌"
    print(f"{icon} {name}{(' | ' + detail) if detail else ''}")


def data(resp: httpx.Response, expected: int = 200):
    body = resp.json()
    assert resp.status_code == expected, f"{resp.status_code} {resp.text[:200]}"
    return body["data"]


def _setup_tenant() -> dict:
    """确保 E2E 专用租户存在（幂等）。返回其登录凭据与 tenant_id。"""
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        info = ensure_e2e_tenant(db)
    finally:
        db.close()
    return info


def _purge_tenant(tenant_id: str) -> None:
    """清空 E2E 租户的业务数据，并重建全局图谱（去掉本次产生的趋势节点）。"""
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        deleted = purge_e2e_data(db, tenant_id)
        total = sum(deleted.values())
        print(f"\n🧹 已清理 E2E 租户数据 {total} 行"
              f"（趋势 {deleted.get('trends', 0)} / 机会 {deleted.get('opportunities', 0)}"
              f" / 预警 {deleted.get('alerts', 0)} / 实验 {deleted.get('experiments', 0)}"
              f" / 报告 {deleted.get('reports', 0)}）")
        # 图谱是进程内全局单例，包含刚被删的租户趋势 ⇒ 重建以保持一致。
        # 用 HTTP 触发，避免在脚本进程里操作服务端的内存单例。
        CLIENT.get("/graph", params={"rebuild": True})
    except Exception as exc:                                   # noqa: BLE001
        # 清理失败不应让冒烟结果失真，但必须显式提示
        print(f"\n⚠️ E2E 租户清理失败：{exc}")
    finally:
        db.close()


def main() -> int:
    """入口：先建隔离租户，再跑检查，**无论如何**都清理。"""
    tenant = _setup_tenant()
    print(f"== TrackCompass E2E 冒烟 @ {BASE} ==")
    print(f"🔒 隔离租户 {tenant['email']}（{tenant['tenant_id'][:8]}）"
          f"，与演示租户互不可见\n")
    try:
        return run_checks()
    except Exception as exc:                                   # noqa: BLE001
        # 未预期异常（典型如 `httpx.ReadTimeout`）必须以「失败」收尾，而不是抛裸栈：
        # 裸栈会掩盖「后面哪些步骤根本没跑」，也容易让人误判成脚本坏了。
        FAIL.append("未预期异常")
        print(f"\n❌ 冒烟中断：{type(exc).__name__}: {exc}")
        print(f"   已完成 {len(PASS)} 项通过；后续步骤未执行")
        return 1
    finally:
        # 无论成功、失败还是中断，都必须清理 —— 否则测试数据会残留在库里
        _purge_tenant(tenant["tenant_id"])


def run_checks() -> int:

    # 1 健康检查
    health = data(CLIENT.get("/health"))
    check("健康检查", health["status"] == "ok",
          f"db={health['database']['backend']} llm={health['llm']['provider']}")

    # 2 登录（用**隔离租户**的凭据 —— 见文件头「租户隔离」）
    login = data(CLIENT.post("/auth/login",
                             json={"email": E2E_EMAIL, "password": E2E_PASSWORD}))
    CLIENT.headers["Authorization"] = f"Bearer {login['access_token']}"
    check("JWT 登录", bool(login["access_token"]),
          f"role={login['user']['role']} tenant={login['user'].get('tenant_id', '')[:8]}")

    # 3 错误口令应被拒绝
    bad = CLIENT.post("/auth/login", json={"email": E2E_EMAIL, "password": "x"})
    check("错误口令拒绝（401）", bad.status_code == 401)

    # 4 能力画像
    profile = data(CLIENT.post("/company/profile", json={
        "name": "智造未来科技", "industry": "AI+制造", "scale": "50-200人",
        "tech_stack": ["计算机视觉", "深度学习", "边缘计算"],
        "tech_description": "自研缺陷检测算法与轻量化推理引擎，具备产线级部署经验",
        "patents": ["基于深度学习的表面缺陷检测方法", "小样本工业缺陷识别模型训练方法"],
        "talents": {"算法": 18, "工程": 26, "产品": 6, "销售": 9},
        "funding": {"round": "A", "amount": 100000000},
        "channels": ["直销", "系统集成商"], "customers": ["汽车零部件", "3C 制造"],
    }))
    check("能力画像 + 向量化", profile["vector_dim"] == 1536,
          f"推荐赛道={profile['recommended_tracks']}")

    # 4b 多公司画像：选择已有公司 / 切换当前主体 / 避免重复建档
    plist = data(CLIENT.get("/company/profiles"))
    check("公司画像列表（多公司）", plist["count"] >= 1
          and sum(1 for p in plist["profiles"] if p["is_current"]) == 1,
          f"共 {plist['count']} 家，当前={plist['profiles'][0]['name']}")

    test_name = "__e2e__ 烟雾测试公司"
    created = data(CLIENT.post("/company/profile", json={
        "name": test_name, "industry": "AI+医疗", "tech_stack": ["医疗器械"],
        "talents": {"算法": 5}, "funding": {"round": "天使", "amount": 1000000},
        "channels": ["线上"], "customers": ["医院"],
    }))
    check("新建第二家公司并成为当前", created["created"] is True
          and data(CLIENT.get("/company/profiles"))["current_id"] == created["id"])

    by_id = data(CLIENT.get("/company/profile",
                            params={"profile_id": plist["profiles"][0]["id"]}))
    check("按 id 取指定公司", by_id["id"] == plist["profiles"][0]["id"]
          and by_id["name"] != test_name, f"取回={by_id['name']}")

    same = data(CLIENT.post("/company/profile", json={"name": test_name}))
    check("同名不重复建档", same["created"] is False
          and data(CLIENT.get("/company/profiles"))["count"] == plist["count"] + 1)

    missing = CLIENT.get("/company/profile", params={"profile_id": "not-exist"})
    check("不存在的公司 id 返回 404", missing.status_code == 404)

    # 还原：删除本次新建的测试公司，并把基准画像重新置为「当前」。
    # 说明：当前没有 DELETE 接口（避免为测试扩大 API 面），故直连本地 SQLite 清理；
    # 非 SQLite 部署下此步会跳过并提示，测试公司靠「同名 upsert」复用，不会累积。
    # 为什么要还原「当前」：`resolve_keywords` 取最近更新的画像作检索词，
    # 若当前公司停在 tech_stack=["医疗器械"]，趋势扫描会换个领域、结果不可比。
    cleanup_sql = "DELETE FROM company_profiles WHERE name LIKE '__e2e__%'"
    try:
        import sqlite3
        from pathlib import Path as _Path

        _db = _Path(__file__).resolve().parent.parent / "backend" / "trackcompass.db"
        if _db.exists():
            _con = sqlite3.connect(_db)
            try:
                _con.execute(cleanup_sql)
                _con.commit()
            finally:
                _con.close()
        else:
            print("  ⚠️ 未找到本地 SQLite，跳过测试公司清理（非 SQLite 部署属预期）")
    except Exception as exc:  # noqa: BLE001 - 清理失败不应让冒烟失败
        print(f"  ⚠️ 测试公司清理跳过：{exc}")

    data(CLIENT.post("/company/profile", json={
        "name": "智造未来科技", "industry": "AI+制造", "scale": "50-200人",
        "tech_stack": ["计算机视觉", "深度学习", "边缘计算"],
        "tech_description": "自研缺陷检测算法与轻量化推理引擎，具备产线级部署经验",
        "patents": ["基于深度学习的表面缺陷检测方法", "小样本工业缺陷识别模型训练方法"],
        "talents": {"算法": 18, "工程": 26, "产品": 6, "销售": 9},
        "funding": {"round": "A", "amount": 100000000},
        "channels": ["直销", "系统集成商"], "customers": ["汽车零部件", "3C 制造"],
    }))

    # 5 数据源覆盖
    # 口径说明：设计 §7.2「覆盖至少 5 个数据源」指的是**采集能力**（能接哪些源），
    # 不是「库里恰好有数据的类型数」—— 后者随本轮采集恰好命中哪类内容而变
    # （如招标源在无命中检索词时返回 0 条，但适配器本身可用）。
    # 此前用 sources.connected 判定，在种子数据时代（含 9 类信号）恒为真，
    # 属于「被数据掩盖的检查」。
    ad = data(CLIENT.get("/signals/adapters"))
    src = data(CLIENT.get("/signals/sources"))
    check("数据源覆盖 ≥5（采集能力）", ad["available"] >= 5,
          f"可用适配器 {ad['available']}/{ad['total']}，默认启用 {ad['default_enabled']}；"
          f"已有数据类型 {src['connected']}/{src['mapped_sources']}")

    # 6 趋势扫描
    # ⚠️ 放宽超时：真实大模型（Ollama qwen3.8:27b）下趋势聚合可能耗时数分钟，
    # 默认 180s 会以 `httpx.ReadTimeout` 中断整个冒烟。
    scan = data(CLIENT.post("/trends/scan", params={"industry": "AI+制造"},
                            timeout=900.0))
    check("趋势侦察 Agent", scan["generated"] > 0,
          f"扫描 {scan['scanned_signals']} 条信号 → {scan['generated']} 条趋势")

    trends = data(CLIENT.get("/trends", params={"limit": 10}))["trends"]

    # 只检查「非空」是不够的（实测教训 2026-09-14）：修复前 DB 里存的证据是
    # LLM 回的 `["E1","E3"]` **编号串** —— 非空，但前端取 `.title` / `.source`
    # / `.url` 全部落空，证据链渲染成一排 `—`，而 E2E 一直"通过"。
    # 这里强制校验**可渲染性**：元素必须是含 `signal_id` 的 dict。
    def _renderable(t: dict) -> bool:
        ev = t.get("evidence") or []
        return bool(ev) and all(
            isinstance(e, dict) and e.get("signal_id") and e.get("title") for e in ev)

    check("趋势含证据链（可渲染）", all(_renderable(t) for t in trends[:3]),
          f"Top1 = {trends[0]['name']}（{trends[0]['score']:.3f}，"
          f"{trends[0]['direction']}，证据 {len(trends[0].get('evidence') or [])} 条）")

    # 7 加入机会池
    # 不再写死标题：固定标题会让**不同趋势**的机会卡同名
    #（实测「赛道评分 · 排序对比」因此出现大量重复条目）。
    # 留空即用趋势名，天然唯一。
    # 幂等后重复运行会复用既有卡片、状态可能已流转，故不断言 screening。
    pool = data(CLIENT.post(f"/trends/{trends[0]['id']}/to-pool", json={}))
    opp_id = pool["opportunity_id"]
    check("趋势 → 机会卡片", bool(opp_id), f"opp={opp_id[:8]} status={pool['status']}")

    # 8 七 Agent 全链路分析
    analysis = data(CLIENT.post(f"/opportunities/{opp_id}/analyze", json={
        "industry": "AI+制造", "days": 3650, "max_loops": 1, "persist": True,
    }, timeout=900.0))
    # 注意：max_loops=1 时反馈循环会回灌重跑第一层，trace 长度 = 7×轮数，
    # 因此断言「7 个 Agent 全覆盖」而非「trace 恰好 7 条」（后者会随状态抖动）。
    agent_keys = [t["agent"] for t in analysis["trace"]]
    rounds = analysis.get("loops", 1)
    check("七 Agent 全链路分析",
          len(set(agent_keys)) == 7 and len(agent_keys) >= 7 and not analysis["errors"],
          f"7 个 Agent 全覆盖，trace {len(agent_keys)} 条（反馈循环共 {rounds} 轮）/ "
          f"证据 {len(analysis['evidence'])} 条 / 信号 {analysis['signal_count']} 条")
    score = data(CLIENT.get(f"/opportunities/{opp_id}/score"))
    check("赛道评分可解释", len(score["dimensions"]) == 7
          and all(d["rationale"] for d in score["dimensions"]),
          f"综合 {score['total_score']} → {score['verdict']['label']}")
    contrib = sum(c["contribution"] for c in score["explained"]["dimensions"])
    check("解释性贡献度自洽", abs(contrib - score["total_score"]) < 0.05,
          f"Σ贡献 = {contrib:.2f}")

    # 9 权重实时预览
    preview = data(CLIENT.post(f"/opportunities/{opp_id}/score/preview", json={
        "opportunity_id": opp_id,
        "weights": {"market_size": 0.1, "demand": 0.4, "tech_maturity": 0.1,
                    "competition": 0.1, "business_model": 0.1, "timing": 0.1, "fit": 0.1},
    }))
    check("权重调整实时更新排序", preview["total_score"] != score["total_score"],
          f"新权重下 = {preview['total_score']}")

    # 10 需求探针
    #
    # 2026-09-16 口径放宽（用户裁决③）：需求强度不再只认「需求侧五类」
    # （社媒/评论/论坛/招标/招聘），政策/论文/产业媒体同样承载需求信息。
    # 因此这里断言两件事：
    #   (a) **默认口径**（需求侧 ∪ 信号库实际存在的类型）必须能出结果 ——
    #       原先只给需求侧五类时，库里一条都没有 ⇒ 命中恒为 0（L-15）。
    #   (b) 只选需求侧五类时，输出为空是**正确**行为（自洽，不是缺陷）。
    #
    # ⚠️ 关键词与行业都从**库内真实信号**里取，避免断言依赖某条固定数据
    # （否则数据一换就假失败；约定的反面教材是「数据源覆盖 ≥5」曾恒为真）。
    pool_sig = data(CLIENT.get("/signals", params={"limit": 1}))
    stocks = pool_sig.get("stats") or {}
    sample = (pool_sig.get("signals") or [None])[0]
    stock_types = sorted(stocks.keys())
    kw = sample["title"][:6] if sample else "人工智能"
    ind = sample["industry"] if sample and sample.get("industry") else "AI+制造"
    default_sources = list(dict.fromkeys(
        ["social", "review", "bidding", "recruit", "forum"] + stock_types))

    probe = data(CLIENT.post("/demand/probe", json={
        "keywords": [kw], "industry": ind, "sources": default_sources,
    }))
    # ⚠️ 描述串必须在断言**之前**算好：`check(名, 条件, 描述)` 的三个参数
    # 都会先求值，之前写成 `probe['pain_points'][0]['name']` 会在
    # pain_points 为空时直接 IndexError，把「检查失败」变成「脚本崩溃」。
    pps = probe.get("pain_points") or []
    matched = probe.get("matched_signals", 0)
    strongest = f"，最强「{pps[0]['name']}」={pps[0]['demand_score']}" if pps else ""
    check("需求探针（放宽口径）能出结果",
          probe.get("status") == "completed" and matched > 0 and len(pps) > 0
          and 1.0 <= probe.get("demand_score_5", 0) <= 5.0,
          f"{'＋'.join(default_sources)} / 关键词「{kw}」→ 匹配 {matched} 条 → "
          f"聚类 {len(pps)} 类痛点{strongest}")

    demand_only = data(CLIENT.post("/demand/probe", json={
        "keywords": [kw], "industry": ind,
        "sources": ["social", "review", "bidding", "recruit", "forum"],
    }))
    pps2 = demand_only.get("pain_points") or []
    m2 = demand_only.get("matched_signals", 0)
    check("需求探针请求-响应自洽（需求侧无数据时不报错）",
          demand_only.get("status") == "completed" and (m2 > 0) == (len(pps2) > 0),
          f"只选需求侧五类 → 匹配 {m2} 条信号 → 聚类 {len(pps2)} 类痛点"
          + ("" if m2 else "（库内确无需求侧信号，属预期；接通数据源见 L-04）"))

    # 11 验证实验
    exp = data(CLIENT.post("/experiments", json={
        "opportunity_id": opp_id,
        "hypothesis": "制造业客户愿意为 AI 质检付费",
        "method": "landing_page",
    }))
    tpl = data(CLIENT.get("/experiments/templates"))
    check("实验模板 ≥5 种", tpl["requirement_met"], f"{tpl['count']} 种")

    illegal = CLIENT.patch(f"/experiments/{exp['id']}", json={"status": "archived"})
    check("状态机非法流转被拒（400）", illegal.status_code == 400)

    data(CLIENT.patch(f"/experiments/{exp['id']}", json={
        "metrics": {"signup_rate": {"current": 0.06, "target": 0.05}},
    }))
    data(CLIENT.patch(f"/experiments/{exp['id']}", json={
        "status": "completed", "conclusion": "注册率超目标，需求成立",
    }))
    wb = data(CLIENT.post(f"/experiments/{exp['id']}/writeback"))
    check("结论回写赛道评分", wb["new_total_score"] > 0,
          f"达成率 {wb['achievement_rate']:.0%} → 机会状态 {wb['opportunity_status']}，"
          f"新评分 {wb['new_total_score']}")

    # 12 决策报告
    report = data(CLIENT.post("/reports", json={
        "opportunity_id": opp_id, "template": "investment_memo", "format": "markdown",
    }))
    check("决策报告生成（9 段结构）", len(report["sections"]) == 9,
          f"{report['version']} / {report['page_count']} 页 / {report['evidence_count']} 条证据")
    html = data(CLIENT.post(f"/reports/{report['id']}/export", params={"fmt": "html"}))
    check("报告多格式导出", html["format"] == "html")

    # 13 监控预警
    alerts = data(CLIENT.get("/alerts"))
    check("监控预警生成", alerts["total"] > 0,
          f"严重 {alerts['severity']['critical']} / 警告 {alerts['severity']['warning']} / "
          f"提示 {alerts['severity']['info']}")
    acted = data(CLIENT.post(f"/alerts/{alerts['alerts'][0]['id']}/action",
                             json={"action": "handle", "note": "e2e"}))
    check("预警处理流转", acted["status"] == "handled")

    # 14 复盘 + 校准
    replay = data(CLIENT.post("/alerts/replay", json={
        "decision_id": opp_id, "outcome": {"signup_rate": 0.06},
        "review": "需求验证充分，但竞争壁垒被高估",
    }))
    check("复盘记录 + 校准建议", "scorecard_version" in replay,
          f"{replay['replay']['id']}，{len(replay['replay']['calibration_suggestions'])} 条建议")

    calib = data(CLIENT.get("/scorecards/calibration/preview"))
    check("校准预览合法", "weights" in calib,
          f"建议 {len(calib['suggestions'])} 条，权重校验 {calib['validation']['ok']}")

    # 15 知识图谱
    graph = data(CLIENT.get("/graph", params={"rebuild": True}))
    ins = data(CLIENT.get("/graph/insights"))
    check("知识图谱与图算法", graph["node_count"] > 20,
          f"{graph['node_count']} 节点 / {graph['edge_count']} 关系 / "
          f"空白点 {len(ins['blank_spots'])} 个")

    # 16 RBAC
    viewer = httpx.Client(base_url=BASE, timeout=60.0, trust_env=False)
    token = data(viewer.post("/auth/login", json={
        "email": "product@demo.com", "password": "TrackCompass@2026",
    }))["access_token"]
    viewer.headers["Authorization"] = f"Bearer {token}"
    forbidden = viewer.get("/admin/users")
    allowed = viewer.get("/opportunities")
    check("RBAC 越权拦截（403）", forbidden.status_code == 403
          and allowed.status_code == 200)

    # 17 RAG
    rag = data(CLIENT.get("/rag/search", params={"q": "智能质检 缺陷检测", "top_k": 5}))
    check("RAG 混合检索", bool(rag["hits"]),
          f"{len(rag['hits'])} 命中，证据链完整率 {rag['citation_ratio']}")

    print(f"\n== 结果：{len(PASS)} 项通过，{len(FAIL)} 项失败 ==")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
