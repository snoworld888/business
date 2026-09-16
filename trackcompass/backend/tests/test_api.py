"""API 集成测试 + 端到端业务流程验证。

覆盖《设计文档》核心链路：
  能力画像 → 设定边界 → 趋势扫描 → 机会池 → 七 Agent 评分 → 验证实验 → 决策报告 → 监控复盘
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

P = "/api/v1"


def data(resp, code: int = 200):
    assert resp.status_code == code, f"{resp.status_code}: {resp.text[:400]}"
    body = resp.json()
    assert body["code"] == code, body
    return body["data"]


# ------------------------------------------------------------------ 基础
class TestMeta:
    def test_root(self, client: TestClient):
        """`/` 优先托管前端构建产物；未构建时回退为 JSON 元信息。"""
        r = client.get("/")
        assert r.status_code == 200
        ctype = r.headers.get("content-type", "")
        if "text/html" in ctype:
            assert "赛道罗盘" in r.text
            assert "/assets/" in r.text
        else:
            assert r.json()["app"]

    def test_root_json_fallback(self, monkeypatch):
        """dist 不存在时应回退为 JSON，而不是 404。"""
        from app.core.config import settings
        from app.main import create_app

        monkeypatch.setattr(settings, "FRONTEND_DIST", "/nonexistent/dist")
        app = create_app()
        with TestClient(app) as c:
            r = c.get("/")
            assert r.status_code == 200
            assert r.json()["hint"]

    def test_favicon_never_404(self, client: TestClient):
        """回归：浏览器默认请求 /favicon.ico 时不得返回 404（曾导致控制台报错）。"""
        r = client.get("/favicon.ico")
        assert r.status_code in (200, 204), f"favicon 返回 {r.status_code}"

    def test_frontend_assets_served(self, client: TestClient):
        """构建产物由后端同端口托管，静态资源必须可取。"""
        from app.core.config import settings

        if not settings.frontend_dist.is_dir():
            pytest.skip("前端未构建")
        index = client.get("/")
        assets = re.findall(r'(/assets/[A-Za-z0-9._-]+)', index.text)
        assert assets, "index.html 应引用 /assets/ 资源"
        for asset in assets[:4]:
            assert client.get(asset).status_code == 200, asset

    def test_health(self, client: TestClient):
        d = data(client.get(f"{P}/health"))
        assert d["status"] == "ok"
        assert d["database"]["ok"] is True
        assert d["rag_documents"] >= 40

    def test_agents_catalog(self, client: TestClient):
        d = data(client.get(f"{P}/agents"))
        assert d["count"] == 7
        assert {a["key"] for a in d["agents"]} == {
            "trend", "demand", "market", "competition", "tech", "decision", "monitor"}

    def test_workflow_graph(self, client: TestClient):
        d = data(client.get(f"{P}/workflow"))
        assert len(d["layers"]) == 4
        assert d["layers"][0]["parallel"] is True
        assert len(d["edges"]) >= 8

    def test_modules_and_track_events(self, client: TestClient):
        d = data(client.get(f"{P}/modules"))
        assert len(d["modules"]) == 10
        assert len(d["track_events"]) == 8

    def test_trace_id_header(self, client: TestClient):
        r = client.get(f"{P}/health")
        assert r.headers.get("x-trace-id")


# ------------------------------------------------------------------ 认证
class TestAuth:
    def test_login_success(self, client: TestClient):
        d = data(client.post(f"{P}/auth/login",
                             json={"email": "admin@demo.com", "password": "TrackCompass@2026"}))
        assert d["access_token"] and d["token_type"] == "bearer"
        assert d["user"]["role"] == "admin"

    def test_login_wrong_password(self, client: TestClient):
        r = client.post(f"{P}/auth/login",
                        json={"email": "admin@demo.com", "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["message"] == "邮箱或密码错误"

    def test_login_unknown_email(self, client: TestClient):
        r = client.post(f"{P}/auth/login", json={"email": "nobody@x.com", "password": "x"})
        assert r.status_code == 401

    def test_me(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/auth/me", headers=auth_headers))
        assert d["role"] == "admin"
        assert "user:manage" in d["permissions"]

    def test_permission_matrix(self, client: TestClient):
        d = data(client.get(f"{P}/auth/permissions"))
        assert len(d["roles"]) == 5
        assert "score:edit" in d["all_permissions"]

    def test_invalid_token_rejected(self, client: TestClient):
        r = client.get(f"{P}/auth/me", headers={"Authorization": "Bearer not-a-token"})
        assert r.status_code == 401

    def test_register_creates_tenant(self, client: TestClient):
        import uuid

        email = f"new-{uuid.uuid4().hex[:8]}@demo.com"
        d = data(client.post(f"{P}/auth/register",
                             json={"email": email, "password": "Passw0rd!23",
                                   "name": "新用户", "tenant_name": "新公司"}))
        assert d["user"]["role"] == "admin"

    def test_register_duplicate_email(self, client: TestClient):
        r = client.post(f"{P}/auth/register",
                        json={"email": "admin@demo.com", "password": "Passw0rd!23"})
        assert r.status_code == 400


# ------------------------------------------------------------------ 能力画像
class TestProfile:
    def test_get_seeded_profile(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/company/profile", headers=auth_headers))
        assert d["name"]
        assert d["radar"]["技术"] > 0
        assert d["recommended_tracks"]

    def test_upsert_recomputes_vector_and_tracks(self, client: TestClient, auth_headers):
        payload = {
            "name": "智造未来科技", "industry": "AI+制造", "scale": "50-200人",
            "tech_stack": ["计算机视觉", "深度学习", "边缘计算"],
            "tech_description": "自研缺陷检测算法与轻量化推理引擎",
            "patents": ["缺陷检测方法", "小样本训练方法"],
            "talents": {"算法": 18, "工程": 26, "产品": 6, "销售": 9},
            "funding": {"round": "A", "amount": 100_000_000},
            "channels": ["直销", "系统集成商"], "customers": ["汽车零部件", "3C 制造"],
        }
        d = data(client.post(f"{P}/company/profile", json=payload, headers=auth_headers))
        assert d["vector_dim"] == 1536
        assert 1.0 <= d["radar"]["技术"] <= 5.0
        assert d["boundary"]["radar_weakest"]

    def test_parse_unsupported_binary(self, client: TestClient, auth_headers):
        files = {"file": ("a.bin", b"\x00\x01\x02", "application/octet-stream")}
        d = data(client.post(f"{P}/company/profile/parse", files=files, headers=auth_headers))
        assert d["parsed"] is False

    def test_parse_text_document(self, client: TestClient, auth_headers):
        text = "核心技术：计算机视觉与深度学习\n专利：缺陷检测方法\n".encode()
        files = {"file": ("公司简介.txt", text, "text/plain")}
        d = data(client.post(f"{P}/company/profile/parse", files=files, headers=auth_headers))
        assert d["parsed"] is True
        assert d["suggestion"]["tech_stack"]

    def test_tracks_with_blank_spots(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/company/tracks", headers=auth_headers))
        assert len(d["tracks"]) >= 4
        assert isinstance(d["blank_spots"], list)


class TestMultiCompanyProfile:
    """多公司画像：选择已有公司、切换当前主体、避免重复建档。"""

    PREFIX = "__test__"

    @pytest.fixture(autouse=True)
    def _cleanup_test_companies(self, db):
        """本类只增删「__test__ 前缀」的公司，绝不改动演示画像。"""
        yield
        from sqlalchemy import select as _select

        from app.models import CompanyProfile

        rows = db.scalars(
            _select(CompanyProfile).where(CompanyProfile.name.like(f"{self.PREFIX}%"))
        ).all()
        if rows:
            for r in rows:
                db.delete(r)
            db.commit()

    @classmethod
    def _create(cls, client: TestClient, auth_headers, name: str,
                industry: str = "AI+医疗") -> dict:
        return data(client.post(f"{P}/company/profile", headers=auth_headers, json={
            "name": name, "industry": industry, "tech_stack": ["医疗器械"],
            "patents": ["测试专利"], "talents": {"算法": 5},
            "funding": {"round": "天使", "amount": 1_000_000},
            "channels": ["线上"], "customers": ["医院"],
        }))

    def test_list_marks_exactly_one_current(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/company/profiles", headers=auth_headers))
        assert d["count"] == len(d["profiles"])
        assert d["count"] >= 1
        assert sum(1 for p in d["profiles"] if p["is_current"]) == 1
        assert d["current_id"] == d["profiles"][0]["id"]

    def test_create_second_company_becomes_current(self, client: TestClient, auth_headers):
        before = data(client.get(f"{P}/company/profiles", headers=auth_headers))["count"]
        name = f"{self.PREFIX}公司二"
        res = self._create(client, auth_headers, name)
        assert res["created"] is True
        assert res["name"] == name

        d = data(client.get(f"{P}/company/profiles", headers=auth_headers))
        assert d["count"] == before + 1
        assert d["current_id"] == res["id"]
        # 「当前公司」切换后，默认取画像应返回新公司（分析主体随之切换）
        assert data(client.get(f"{P}/company/profile", headers=auth_headers))["name"] == name

    def test_update_by_id_does_not_duplicate(self, client: TestClient, auth_headers):
        name = f"{self.PREFIX}按id更新"
        created = self._create(client, auth_headers, name)
        before = data(client.get(f"{P}/company/profiles", headers=auth_headers))["count"]

        res = data(client.post(f"{P}/company/profile", headers=auth_headers, json={
            "id": created["id"], "name": name, "industry": "新能源科技",
            "tech_stack": ["固态电池"],
        }))
        assert res["created"] is False
        assert res["id"] == created["id"]

        d = data(client.get(f"{P}/company/profiles", headers=auth_headers))
        assert d["count"] == before
        assert d["current_id"] == created["id"]

    def test_same_name_updates_instead_of_creating(self, client: TestClient, auth_headers):
        before = data(client.get(f"{P}/company/profiles", headers=auth_headers))["count"]
        name = f"{self.PREFIX}同名"
        self._create(client, auth_headers, name)
        mid = data(client.get(f"{P}/company/profiles", headers=auth_headers))["count"]
        assert mid == before + 1

        res = self._create(client, auth_headers, name)
        assert res["created"] is False
        after = data(client.get(f"{P}/company/profiles", headers=auth_headers))["count"]
        assert after == mid, "同名重复提交不应产生第二份档案"

    def test_get_specific_profile_by_id(self, client: TestClient, auth_headers):
        first = data(client.get(f"{P}/company/profiles",
                                headers=auth_headers))["profiles"][0]
        created = self._create(client, auth_headers, f"{self.PREFIX}按id取")

        got = data(client.get(f"{P}/company/profile", headers=auth_headers,
                              params={"profile_id": first["id"]}))
        assert got["id"] == first["id"]
        assert got["id"] != created["id"]
        assert got["updated_at"]

    def test_unknown_profile_id_returns_404(self, client: TestClient, auth_headers):
        r = client.get(f"{P}/company/profile", headers=auth_headers,
                       params={"profile_id": "not-exist"})
        assert r.status_code == 404
        r = client.post(f"{P}/company/profile", headers=auth_headers,
                        json={"id": "not-exist", "name": "x"})
        assert r.status_code == 404


# ------------------------------------------------------------------ 信号
class TestSignals:
    def test_list_signals(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/signals?limit=100", headers=auth_headers))
        assert d["total"] >= 40
        assert d["stats"]["policy"] >= 4

    def test_filter_by_type(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/signals?signal_type=funding", headers=auth_headers))
        assert all(s["signal_type"] == "funding" for s in d["signals"])

    def test_create_signal(self, client: TestClient, auth_headers):
        payload = {"signal_type": "policy", "source": "测试源",
                   "title": "测试信号：支持边缘算力补贴", "content": "给予补贴支持试点",
                   "industry": "AI+制造", "keywords": ["边缘计算"]}
        d = data(client.post(f"{P}/signals", json=payload, headers=auth_headers))
        assert d["indexed"] is True

    def test_invalid_signal_type_rejected(self, client: TestClient, auth_headers):
        r = client.post(f"{P}/signals", headers=auth_headers,
                        json={"signal_type": "不存在的类型", "source": "s", "title": "t"})
        assert r.status_code == 400
        assert r.json()["message"] == "参数错误"

    def test_data_sources_status(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/signals/sources", headers=auth_headers))
        assert d["mapped_sources"] == 11
        assert d["connected"] >= 6
        assert d["coverage_ok"] is True, "验收标准：覆盖至少 5 个数据源"

    def test_reindex(self, client: TestClient, auth_headers):
        d = data(client.post(f"{P}/signals/reindex", headers=auth_headers))
        assert d["indexed_documents"] >= 40
        assert d["graph"]["nodes"] > 0


# ------------------------------------------------------------------ 趋势雷达
class TestTrends:
    def test_scan_then_list(self, client: TestClient, auth_headers):
        scan = data(client.post(f"{P}/trends/scan", params={"industry": "AI+制造"},
                                headers=auth_headers))
        assert scan["scanned_signals"] > 0
        assert scan["generated"] > 0

        listing = data(client.get(f"{P}/trends?limit=20", headers=auth_headers))
        assert listing["trends"]
        top = listing["trends"][0]
        assert 1 <= len(top["evidence"]) <= 4
        assert 0 <= top["confidence"] <= 1
        assert top["direction"] in ("tailwind", "headwind")
        assert top["factors"]

    def test_filter_by_direction(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/trends?direction=tailwind", headers=auth_headers))
        assert all(t["direction"] == "tailwind" for t in d["trends"])

    def test_trend_detail_and_404(self, client: TestClient, auth_headers):
        tid = data(client.get(f"{P}/trends", headers=auth_headers))["trends"][0]["id"]
        d = data(client.get(f"{P}/trends/{tid}", headers=auth_headers))
        assert d["id"] == tid
        assert client.get(f"{P}/trends/not-exist", headers=auth_headers).status_code == 404

    def test_subscribe(self, client: TestClient, auth_headers):
        tid = data(client.get(f"{P}/trends", headers=auth_headers))["trends"][0]["id"]
        d = data(client.post(f"{P}/trends/{tid}/subscribe",
                             json={"subscribed": True}, headers=auth_headers))
        assert d["subscribed"] is True

    def test_trend_to_pool_creates_opportunity(self, client: TestClient, auth_headers):
        """首次入池创建机会卡；重复入池必须**返回既有卡片**（幂等）。

        实测缺陷：`to_pool` 原本没有幂等，反复调用后同一趋势累积出
        17 条同名机会，「赛道评分 · 排序对比」里出现大量重复条目。
        """
        tid = data(client.get(f"{P}/trends", headers=auth_headers))["trends"][0]["id"]
        d = data(client.post(f"{P}/trends/{tid}/to-pool",
                             json={"title": "AI 质检"}, headers=auth_headers))
        assert d["opportunity_id"]
        # 复用既有卡片时状态可能已流转（verifying 等），不再强断言 screening
        assert d["status"] in ("screening", "verifying")

        again = data(client.post(f"{P}/trends/{tid}/to-pool",
                                 json={"title": "AI 质检"}, headers=auth_headers))
        assert again["opportunity_id"] == d["opportunity_id"], "重复入池不能新建卡片"
        assert again.get("reused") is True

    def test_to_pool_missing_trend(self, client: TestClient, auth_headers):
        r = client.post(f"{P}/trends/ghost/to-pool", json={}, headers=auth_headers)
        assert r.status_code == 404


# ------------------------------------------------------------------ 端到端主链路
class TestEndToEnd:
    """完整业务链路：机会 → 七 Agent 分析 → 评分 → 实验回写 → 报告 → 预警处理。"""

    def test_full_workflow(self, client: TestClient, auth_headers, tenant_id):
        # ---- 1. 机会池中挑选一个（用趋势转池，保证有 track）
        tid = data(client.get(f"{P}/trends?limit=1", headers=auth_headers))["trends"][0]["id"]
        opp = data(client.post(f"{P}/trends/{tid}/to-pool",
                               json={"title": "AI 质检商业化"},
                               headers=auth_headers))
        opp_id = opp["opportunity_id"]

        # ---- 2. 七 Agent 全链路分析
        analysis = data(client.post(
            f"{P}/opportunities/{opp_id}/analyze",
            json={"industry": "AI+制造", "days": 3650, "max_loops": 0, "persist": True},
            headers=auth_headers,
        ))
        assert analysis["signal_count"] > 0
        assert len(analysis["trace"]) == 7, "七个 Agent 都应参与执行"
        assert analysis["errors"] == []
        assert len(analysis["evidence"]) > 0
        assert 1.0 <= analysis["total_score"] <= 5.0

        # ---- 3. 评分可解释 + 可下钻证据
        score = data(client.get(f"{P}/opportunities/{opp_id}/score", headers=auth_headers))
        assert len(score["dimensions"]) == 7
        assert score["explained"]["weights_valid"]["ok"] is True
        for dim in score["dimensions"]:
            assert dim["rationale"], f"{dim['key']} 缺少评分理由"
        total_contrib = sum(c["contribution"]
                            for c in score["explained"]["dimensions"])
        assert abs(total_contrib - score["total_score"]) < 0.05
        assert score["verdict"]["code"] in ("go", "verify", "watch", "hold")

        # ---- 4. 权重调整实时预览排序变化
        preview = data(client.post(
            f"{P}/opportunities/{opp_id}/score/preview",
            json={"opportunity_id": opp_id,
                  "weights": {"market_size": 0.1, "demand": 0.4, "tech_maturity": 0.1,
                              "competition": 0.1, "business_model": 0.1, "timing": 0.1,
                              "fit": 0.1}},
            headers=auth_headers,
        ))
        assert preview["total_score"] != analysis["total_score"]
        assert preview["explained"]["weights_valid"]["ok"] is True

        # ---- 5. 需求探针
        probe = data(client.post(f"{P}/demand/probe",
                                 json={"keywords": ["质检"], "industry": "AI+制造",
                                       "sources": ["social", "review", "bidding", "recruit"]},
                                 headers=auth_headers))
        assert probe["status"] == "completed"
        assert probe["pain_points"]
        assert probe["suggested_experiments"]
        got = data(client.get(f"{P}/demand/probe/{probe['task_id']}", headers=auth_headers))
        assert got["task_id"] == probe["task_id"]
        assert client.get(f"{P}/demand/probe/ghost", headers=auth_headers).status_code == 404

        # ---- 6. 验证实验全生命周期
        exp = data(client.post(f"{P}/experiments",
                               json={"opportunity_id": opp_id,
                                     "hypothesis": "制造业客户愿意为 AI 质检付费",
                                     "method": "landing_page"},
                               headers=auth_headers))
        exp_id = exp["id"]
        assert exp["status"] == "running"
        assert exp["metrics"]

        upd = data(client.patch(f"{P}/experiments/{exp_id}",
                                json={"metrics": {"signup_rate": {"current": 0.06,
                                                                  "target": 0.05}}},
                                headers=auth_headers))
        assert upd["progress"] > 0

        illegal = client.patch(f"{P}/experiments/{exp_id}",
                               json={"status": "archived"}, headers=auth_headers)
        assert illegal.status_code == 400, "running 不能直接跳到 archived"
        assert "非法状态流转" in illegal.json()["message"]

        done = data(client.patch(f"{P}/experiments/{exp_id}",
                                 json={"status": "completed",
                                       "conclusion": "注册率超目标，需求成立"},
                                 headers=auth_headers))
        assert done["status"] == "completed"

        wb = data(client.post(f"{P}/experiments/{exp_id}/writeback", headers=auth_headers))
        assert wb["opportunity_status"] in ("validated", "verifying")
        assert wb["new_total_score"] > 0

        export = data(client.get(f"{P}/experiments/{exp_id}/export", headers=auth_headers))
        assert export["format"] == "csv"
        assert "signup_rate" in export["content"]

        # ---- 7. 报告生成与导出
        report = data(client.post(f"{P}/reports",
                                  json={"opportunity_id": opp_id,
                                        "template": "investment_memo",
                                        "format": "markdown"},
                                  headers=auth_headers))
        assert report["version"].startswith("V1.")
        assert len(report["sections"]) == 9
        assert report["file_path"]
        assert report["page_count"] >= 1

        rep_detail = data(client.get(f"{P}/reports/{report['id']}", headers=auth_headers))
        assert rep_detail["content"]["sections"]

        exported = data(client.post(f"{P}/reports/{report['id']}/export?fmt=html",
                                    headers=auth_headers))
        assert exported["format"] == "html"

        # ---- 8. 监控预警
        alerts = data(client.get(f"{P}/alerts", headers=auth_headers))
        assert alerts["total"] > 0
        assert alerts["severity"]["critical"] >= 1
        alert_id = alerts["alerts"][0]["id"]
        acted = data(client.post(f"{P}/alerts/{alert_id}/action",
                                 json={"action": "handle", "note": "已评估"},
                                 headers=auth_headers))
        assert acted["status"] == "handled"

        # ---- 9. 复盘 + 模型校准
        replay = data(client.post(f"{P}/alerts/replay",
                                  json={"decision_id": opp_id,
                                        "outcome": {"signup_rate": 0.06},
                                        "review": "需求验证充分，但竞争壁垒被高估"},
                                  headers=auth_headers))
        assert replay["replay"]["id"].startswith("RP")
        assert "scorecard_version" in replay

        calib = data(client.get(f"{P}/scorecards/calibration/preview", headers=auth_headers))
        assert "suggestions" in calib and "weights" in calib

        applied = data(client.post(f"{P}/scorecards/calibration/apply",
                                   json={"change_reason": "E2E 测试校准"},
                                   headers=auth_headers))
        assert applied["validation"]["ok"] is True
        assert applied["version"].startswith("V1.")

    def test_analysis_preview_does_not_persist(self, client: TestClient, auth_headers):
        created = data(client.post(f"{P}/opportunities",
                                   json={"title": "预览用机会"}, headers=auth_headers))
        d = data(client.post(f"{P}/opportunities/{created['id']}/analyze",
                             json={"industry": "AI+制造", "max_loops": 0, "persist": False},
                             headers=auth_headers))
        assert d["status"] == "preview"
        detail = data(client.get(f"{P}/opportunities/{created['id']}", headers=auth_headers))
        assert detail["score"] == {} or detail["score"].get("total_score") in (None, 0)


# ------------------------------------------------------------------ 机会与图谱
class TestOpportunities:
    def test_list_sorted(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/opportunities?sort=score", headers=auth_headers))
        scores = [o["total_score"] for o in d["opportunities"]]
        assert scores == sorted(scores, reverse=True)
        ranks = [o["rank"] for o in d["opportunities"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_404(self, client: TestClient, auth_headers):
        assert client.get(f"{P}/opportunities/ghost", headers=auth_headers).status_code == 404

    def test_score_before_analysis_raises(self, client: TestClient, auth_headers):
        created = data(client.post(f"{P}/opportunities",
                                   json={"title": "未分析机会"}, headers=auth_headers))
        r = client.post(f"{P}/opportunities/{created['id']}/score/preview",
                        json={"opportunity_id": created["id"], "weights": {}},
                        headers=auth_headers)
        assert r.status_code == 400

    def test_graph(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/graph?rebuild=true", headers=auth_headers))
        assert d["node_count"] > 20
        assert d["edge_count"] > 20
        assert {"node_types", "edge_types"} <= set(d["legend"])

    def test_graph_insights(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/graph/insights", headers=auth_headers))
        assert "blank_spots" in d and "suggestions" in d
        assert d["centrality"]

    def test_rag_search(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/rag/search?q=智能质检&top_k=5", headers=auth_headers))
        assert d["hits"]
        assert d["citation_ratio"] > 0

    def test_rag_search_requires_query(self, client: TestClient, auth_headers):
        assert client.get(f"{P}/rag/search", headers=auth_headers).status_code == 400


# ------------------------------------------------------------------ 评分卡
class TestScorecard:
    def test_default(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/scorecards/default", headers=auth_headers))
        assert d["validation"]["ok"] is True
        assert len(d["dimensions"]) == 7

    def test_update_weights_creates_version(self, client: TestClient, auth_headers):
        card = data(client.get(f"{P}/scorecards/default", headers=auth_headers))
        before = card["version"]
        new_weights = {"market_size": 0.15, "demand": 0.25, "tech_maturity": 0.15,
                       "competition": 0.15, "business_model": 0.1, "timing": 0.1,
                       "fit": 0.1}
        d = data(client.put(f"{P}/scorecards/{card['id']}/weights",
                            json={"weights": new_weights,
                                  "change_reason": "提升需求权重"},
                            headers=auth_headers))
        assert d["version"] != before
        assert d["validation"]["ok"] is True
        assert d["diff"]["demand"]["to"] == 0.25
        assert d["recalculated_opportunities"] >= 0

        versions = data(client.get(f"{P}/scorecards/{card['id']}/versions",
                                   headers=auth_headers))
        assert versions["count"] >= 2
        assert versions["comparison"]

    def test_invalid_weights_reported(self, client: TestClient, auth_headers):
        card = data(client.get(f"{P}/scorecards/default", headers=auth_headers))
        d = data(client.put(f"{P}/scorecards/{card['id']}/weights",
                            json={"weights": {"market_size": 0.1}}, headers=auth_headers))
        assert d["validation"]["ok"] is False

    def test_unknown_scorecard(self, client: TestClient, auth_headers):
        r = client.put(f"{P}/scorecards/ghost/weights",
                       json={"weights": {}}, headers=auth_headers)
        assert r.status_code == 404


# ------------------------------------------------------------------ 实验模板等
class TestMiscEndpoints:
    def test_experiment_templates(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/experiments/templates", headers=auth_headers))
        assert d["count"] >= 5
        assert d["requirement_met"] is True, "验收标准：至少 5 种实验类型"

    def test_report_templates(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/reports/templates", headers=auth_headers))
        assert d["count"] == 3
        assert "pptx" in d["formats"] and "docx" in d["formats"]

    def test_alerts_filter(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/alerts?level=critical", headers=auth_headers))
        assert all(a["level"] == "critical" for a in d["alerts"])

    def test_alerts_scan(self, client: TestClient, auth_headers):
        d = data(client.post(f"{P}/alerts/scan", headers=auth_headers))
        assert d["scanned_signals"] > 0
        assert d["generated"] > 0

    def test_pain_points_aggregate(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/demand/pain-points", params={"industry": "AI+制造"},
                            headers=auth_headers))
        assert d["pain_points"]
        assert d["sample_size"] > 0

    def test_model_config(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/admin/model-config", headers=auth_headers))
        assert len(d["routes"]) == 3
        assert d["llm"]["provider"] == "rule"

    def test_overview(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/admin/overview", headers=auth_headers))
        assert d["signals"] >= 40
        assert "signals_by_type" in d

    def test_roles_and_audit(self, client: TestClient, auth_headers):
        roles = data(client.get(f"{P}/admin/roles", headers=auth_headers))
        assert len(roles["matrix"]) == 5
        logs = data(client.get(f"{P}/admin/audit-logs?limit=50", headers=auth_headers))
        assert logs["retention_days"] == 180
        assert logs["total"] > 0

    def test_users_list(self, client: TestClient, auth_headers):
        d = data(client.get(f"{P}/admin/users", headers=auth_headers))
        assert d["total"] >= 5


# ------------------------------------------------------------------ RBAC
class TestRBAC:
    def test_viewer_cannot_manage_users(self, client: TestClient):
        token = data(client.post(f"{P}/auth/login",
                                 json={"email": "product@demo.com",
                                       "password": "TrackCompass@2026"}))["access_token"]
        r = client.get(f"{P}/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
        assert "缺少权限" in r.json()["message"]

    def test_viewer_can_read_opportunities(self, client: TestClient):
        token = data(client.post(f"{P}/auth/login",
                                 json={"email": "product@demo.com",
                                       "password": "TrackCompass@2026"}))["access_token"]
        r = client.get(f"{P}/opportunities", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
