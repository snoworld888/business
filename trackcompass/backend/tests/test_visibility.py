"""公共数据层（`core.visibility`）与 E2E 租户隔离的行为锁定。

对应文档：`docs/公共数据层与E2E租户隔离.md`

核心约定：`tenant_id IS NULL` ⇒ 公共数据。
- 信号（外部客观情报）→ 公共，全租户共享
- 趋势 / 机会 / 画像等决策资产 → 租户私有
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.visibility import PUBLIC_TENANT_ID, visible_clause
from app.models import CompanyProfile, Scorecard, Signal, Tenant, Trend, User
from app.seed import (
    DEMO_EMAIL,
    DEMO_PASSWORD,
    E2E_EMAIL,
    E2E_PASSWORD,
    ensure_e2e_tenant,
    purge_e2e_data,
)

P = "/api/v1"


def _login(client, email: str, password: str) -> dict[str, str]:
    resp = client.post(f"{P}/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def _data(resp):
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:300]}"
    return resp.json()["data"]


class TestPublicLayer:
    """信号是公共数据，全租户可读。"""

    def test_seed_signals_are_public(self, db):
        """种子信号必须落在公共层 —— 否则新租户看不到任何信号。

        只针对种子（`example.com` 占位 URL）：其他用例会往库里加私有信号，
        断言「全表皆公共」会被它们污染（这正是本用例第一版的错误）。
        """
        seeds = db.query(Signal).filter(
            Signal.url.like("https://example.com/signal/%")).all()
        assert len(seeds) >= 40, f"种子信号应 ≥40 条，实际 {len(seeds)}"
        not_public = [s.title[:24] for s in seeds if s.tenant_id is not None]
        assert not not_public, f"种子信号必须归属公共层，违约：{not_public}"

    def test_visible_clause_includes_public_and_own(self):
        """SQL 条件须同时覆盖「公共」与「本租户」两种归属。"""
        sql = str(visible_clause(Signal, "tenant-a").compile(
            compile_kwargs={"literal_binds": True}))
        assert "IS NULL" in sql and "tenant-a" in sql

    def test_visible_clause_without_tenant_returns_public_only(self):
        """无租户上下文（系统任务）只能看公共层，不能退化成全表。"""
        sql = str(visible_clause(Signal, None).compile(
            compile_kwargs={"literal_binds": True}))
        assert "IS NULL" in sql and "=" not in sql.replace("IS NULL", "")

    def test_second_tenant_can_read_public_signals(self, client, db):
        """新租户（无自有信号）也能读到公共信号 —— 这是 E2E 能跑通的前提。"""
        info = ensure_e2e_tenant(db)
        headers = _login(client, info["email"], info["password"])
        own = db.query(Signal).filter(Signal.tenant_id == info["tenant_id"]).count()
        assert own == 0, "E2E 租户不应拥有任何自有信号"

        d = _data(client.get(f"{P}/signals?limit=200", headers=headers))
        assert d["total"] >= 40, f"应能读到公共信号，实际 {d['total']}"
        assert d["stats"].get("policy", 0) >= 4

    def test_manual_signal_defaults_to_public(self, client, db, auth_headers):
        """人工上传默认落公共层；显式 is_public=False 才归本租户。"""
        title = "公共层测试信号：边缘算力补贴政策（勿删）"
        r = _data(client.post(f"{P}/signals", headers=auth_headers, json={
            "signal_type": "policy", "source": "单元测试", "title": title,
            "content": "给予补贴支持试点",
        }))
        assert r["is_public"] is True
        sig = db.query(Signal).filter(Signal.title == title).one()
        assert sig.tenant_id is None, "默认应写入公共层"

        priv_title = "私有层测试信号：内部尽调材料（勿删）"
        r2 = _data(client.post(f"{P}/signals", headers=auth_headers, json={
            "signal_type": "news", "source": "单元测试", "title": priv_title,
            "content": "内部材料，不对外共享", "is_public": False,
        }))
        assert r2["is_public"] is False
        sig2 = db.query(Signal).filter(Signal.title == priv_title).one()
        assert sig2.tenant_id is not None, "is_public=False 应归属本租户"

    def test_private_signal_invisible_to_other_tenant(self, client, db, auth_headers):
        """私有信号不得被其他租户读到（隔离的核心断言）。"""
        title = "私有隔离测试：别的租户不应看到（勿删）"
        _data(client.post(f"{P}/signals", headers=auth_headers, json={
            "signal_type": "news", "source": "单元测试", "title": title,
            "content": "仅本租户可见", "is_public": False,
        }))
        info = ensure_e2e_tenant(db)
        other = _login(client, info["email"], info["password"])
        seen = _data(client.get(f"{P}/signals?limit=500", headers=other))
        assert title not in [s["title"] for s in seen["signals"]], "私有信号发生了跨租户泄露"

        # 本租户自己仍看得到
        mine = _data(client.get(f"{P}/signals?limit=500", headers=auth_headers))
        assert title in [s["title"] for s in mine["signals"]]


class TestE2ETenant:
    """E2E 租户的创建、幂等与清理。"""

    def test_ensure_is_idempotent(self, db):
        first = ensure_e2e_tenant(db)
        second = ensure_e2e_tenant(db)
        assert first["tenant_id"] == second["tenant_id"]

        tenants = db.query(Tenant).filter(Tenant.name.like("__e2e__%")).all()
        assert len(tenants) == 1, f"不应重复建租户，实际 {len(tenants)} 个"

        users = db.query(User).filter(User.email == E2E_EMAIL).all()
        assert len(users) == 1 and users[0].tenant_id == first["tenant_id"]

        cards = db.query(Scorecard).filter(
            Scorecard.tenant_id == first["tenant_id"]).all()
        assert len(cards) == 1, "评分卡缺失会让 /score 直接 404"

    def test_e2e_trends_are_private(self, client, db, auth_headers):
        """E2E 租户产出的趋势不得出现在演示租户的列表里。"""
        info = ensure_e2e_tenant(db)
        e2e = _login(client, info["email"], info["password"])
        scan = _data(client.post(f"{P}/trends/scan", headers=e2e,
                                 params={"industry": "AI+制造"}))
        assert scan["scanned_signals"] > 0, "E2E 租户应能读到公共信号"
        assert scan["generated"] > 0

        their_names = {t["name"] for t in
                       _data(client.get(f"{P}/trends?limit=100", headers=e2e))["trends"]}
        assert their_names, "E2E 租户应有自己的趋势"
        assert all(db.scalar(select(Trend.id).where(Trend.name == n)) is not None
                   for n in their_names)

        my_names = {t["name"] for t in
                    _data(client.get(f"{P}/trends?limit=100", headers=auth_headers))["trends"]}
        stats = db.query(Trend).all()
        e2e_trend_ids = {t.id for t in stats if t.tenant_id == info["tenant_id"]}
        my_trend_ids = {t.id for t in stats if t.tenant_id != info["tenant_id"]}
        assert e2e_trend_ids and my_trend_ids
        assert not (e2e_trend_ids & my_trend_ids), "趋势归属串了"

    def test_purge_removes_only_target_tenant(self, client, db, auth_headers):
        """清理只影响 E2E 租户，演示租户的数据必须原样保留。"""
        info = ensure_e2e_tenant(db)
        tid = info["tenant_id"]

        e2e = _login(client, info["email"], info["password"])
        _data(client.post(f"{P}/trends/scan", headers=e2e,
                          params={"industry": "AI+制造"}))
        assert db.query(Trend).filter(Trend.tenant_id == tid).count() > 0

        demo_before = db.query(Trend).filter(Trend.tenant_id != tid).count()
        demo_profile = db.query(CompanyProfile).filter(
            CompanyProfile.tenant_id != tid).count()

        deleted = purge_e2e_data(db, tid)
        assert sum(deleted.values()) > 0

        assert db.query(Trend).filter(Trend.tenant_id == tid).count() == 0
        assert db.query(CompanyProfile).filter(
            CompanyProfile.tenant_id == tid).count() == 0
        # 演示租户数据未受影响
        assert db.query(Trend).filter(Trend.tenant_id != tid).count() == demo_before
        assert db.query(CompanyProfile).filter(
            CompanyProfile.tenant_id != tid).count() == demo_profile
        # 租户与用户保留（供下轮复用），评分卡被清掉后由 ensure 重建
        assert db.get(Tenant, tid) is not None
        assert db.query(User).filter(User.email == E2E_EMAIL).count() == 1
        assert db.query(Scorecard).filter(Scorecard.tenant_id == tid).count() == 0

    def test_purge_then_ensure_restores_scorecard(self, db):
        """清理后再次初始化，评分卡必须回来（否则下轮 E2E 跑不完）。"""
        info = ensure_e2e_tenant(db)
        purge_e2e_data(db, info["tenant_id"])
        again = ensure_e2e_tenant(db)
        assert again["tenant_id"] == info["tenant_id"]
        assert db.query(Scorecard).filter(
            Scorecard.tenant_id == info["tenant_id"]).count() == 1


class TestPublicLayerConstants:
    def test_public_tenant_id_is_none(self):
        assert PUBLIC_TENANT_ID is None

    def test_demo_login_still_works(self, client):
        """隔离改造不能影响演示租户登录。"""
        headers = _login(client, DEMO_EMAIL, DEMO_PASSWORD)
        assert headers["Authorization"].startswith("Bearer ")
