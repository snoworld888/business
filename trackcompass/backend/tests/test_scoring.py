"""评分算法单元测试：对齐技术文档 §4.2 / §4.4 / §4.5 的原文公式。"""

from __future__ import annotations

import pytest

from app.services.scoring import (
    DEFAULT_DIMENSIONS,
    clamp,
    demand_score,
    explain_score,
    normalize,
    rank_opportunities,
    risk_alerts_from,
    scale_to_5,
    six_dim_assessment,
    tam_sam_som,
    threat_score,
    total_score,
    trend_score,
    trl_anchor,
    validate_weights,
)


class TestNormalize:
    def test_linear_range(self):
        assert normalize(5, 0, 10) == 0.5
        assert normalize(-1, 0, 10) == 0.0
        assert normalize(99, 0, 10) == 1.0

    def test_log_compression_bounded(self):
        assert 0.0 <= normalize(0) < 0.01
        assert 0.0 < normalize(10) < 1.0
        assert normalize(1e9) <= 1.0


class TestTrendScore:
    def test_matches_documented_formula(self):
        r = trend_score(signal_count=10, growth_rate=0.5,
                        policy_support=0.8, capital_heat=1e8)
        f = r["factors"]
        expected = (0.3 * f["signal_count"] + 0.3 * f["growth_rate"]
                    + 0.2 * f["policy_support"] + 0.2 * f["capital_heat"])
        assert r["score"] == pytest.approx(round(expected, 4), abs=1e-6)

    def test_monotonic_in_each_factor(self):
        low = trend_score(1, 0.0, 0.0, 0)["score"]
        high = trend_score(50, 1.0, 1.0, 1e9)["score"]
        assert high > low
        assert 0.0 <= low <= 1.0 and 0.0 <= high <= 1.0

    def test_score_5_within_range(self):
        r = trend_score(100, 1.0, 1.0, 1e10)
        assert 1.0 <= r["score_5"] <= 5.0

    def test_policy_support_clamped(self):
        assert trend_score(1, 0, 5.0, 0)["factors"]["policy_support"] == 1.0
        assert trend_score(1, 0, -3.0, 0)["factors"]["policy_support"] == 0.0


class TestDemandScore:
    def test_matches_documented_formula(self):
        r = demand_score(freq=120, sentiment=-0.8, willingness=0.6, urgency=0.5)
        f = r["factors"]
        expected = (0.3 * f["frequency"] + 0.3 * f["sentiment"]
                    + 0.2 * f["willingness"] + 0.2 * f["urgency"])
        assert r["score"] == pytest.approx(expected, abs=1e-6)
        assert f["sentiment"] == pytest.approx(0.8)

    def test_positive_sentiment_does_not_inflate(self):
        # 情感为正（满意）时不应被计为需求强度
        r = demand_score(10, 0.9, 0.5, 0.5)
        assert r["factors"]["sentiment"] == 0.0

    def test_demand_score_5_range(self):
        r = demand_score(1000, -1.0, 1.0, 1.0)
        assert 1.0 <= r["demand_score_5"] <= 5.0


class TestScorecard:
    def test_total_score_is_weighted_mean(self):
        dims = [{"key": "a", "weight": 0.5, "score": 4.0},
                {"key": "b", "weight": 0.5, "score": 2.0}]
        assert total_score(dims) == 3.0

    def test_weights_auto_normalized(self):
        # 允许用户输入 0-100 量级的权重
        dims = [{"key": "a", "weight": 60, "score": 5.0},
                {"key": "b", "weight": 40, "score": 1.0}]
        assert total_score(dims) == pytest.approx(3.4, abs=0.01)

    def test_explain_score_contributions_sum_to_total(self):
        dims = [{**d, "score": 4.0} for d in DEFAULT_DIMENSIONS]
        explained = explain_score(dims)
        total_contrib = sum(c["contribution"] for c in explained["dimensions"])
        assert total_contrib == pytest.approx(explained["total_score"], abs=0.01)
        assert explained["weights_valid"]["ok"] is True

    def test_explain_score_sorted_by_contribution(self):
        dims = [{"key": "a", "name": "A", "weight": 0.2, "score": 1.0},
                {"key": "b", "name": "B", "weight": 0.8, "score": 5.0}]
        explained = explain_score(dims)
        assert explained["dimensions"][0]["key"] == "b"

    @pytest.mark.parametrize("weights,ok_expected", [
        ({"a": 0.5, "b": 0.5}, True),
        ({"a": 0.3, "b": 0.3}, False),
        ({"a": 1.0}, True),
    ])
    def test_validate_weights(self, weights, ok_expected):
        assert validate_weights(weights)["ok"] is ok_expected

    def test_risk_alerts_threshold(self):
        dims = {"market_size": 4.0, "demand": 4.0, "tech_maturity": 1.5,
                "competition": 4.0, "business_model": 4.0, "timing": 4.0, "fit": 4.0}
        alerts = risk_alerts_from(dims)
        assert len(alerts) == 1
        assert alerts[0]["dimension"] == "tech_maturity"
        assert alerts[0]["level"] == "critical"

    def test_risk_alerts_empty_when_healthy(self):
        dims = {d["key"]: 4.5 for d in DEFAULT_DIMENSIONS}
        assert risk_alerts_from(dims) == []

    def test_rank_opportunities(self):
        items = [{"title": "a", "total_score": 3.0},
                 {"title": "b", "total_score": 4.5},
                 {"title": "c", "total_score": 1.2}]
        ranked = rank_opportunities(items)
        assert [r["title"] for r in ranked] == ["b", "a", "c"]
        assert [r["rank"] for r in ranked] == [1, 2, 3]


class TestMarketSizing:
    def test_geometric_fusion_of_two_sources(self):
        # 自下而上 1000×1000=1e6，自上而下 1e6×0.5=5e5，几何均值 = sqrt(5e11)
        r = tam_sam_som(total_customers=1000, arpu=1000,
                        industry_size=1_000_000, penetration=0.5)
        assert r["TAM"] == pytest.approx(5e11 ** 0.5, rel=1e-3)
        assert r["method"]["bottom_up"] == pytest.approx(1_000_000)
        assert r["method"]["top_down"] == pytest.approx(500_000)
        assert r["method"]["fusion"] == "geometric_mean"

    def test_single_source(self):
        r = tam_sam_som(total_customers=1000, arpu=1000)
        assert r["TAM"] == pytest.approx(1_000_000)
        assert r["method"]["fusion"] == "single_source"

    def test_sam_som_ordering(self):
        r = tam_sam_som(total_customers=1000, arpu=1000,
                        reachable_ratio=0.2, obtainable_ratio=0.1)
        assert r["TAM"] > r["SAM"] > r["SOM"] > 0

    def test_no_input(self):
        r = tam_sam_som()
        assert r["TAM"] == 0 and r["SAM"] == 0 and r["SOM"] == 0

    def test_unit_economics_exposed(self):
        r = tam_sam_som(total_customers=10, arpu=99)
        assert r["unit_economics"]["arpu"] == 99
        assert r["unit_economics"]["total_customers"] == 10


class TestTechAssessment:
    def test_six_dimensions_present(self):
        r = six_dim_assessment({"TRL": 7, "CRL": 6, "BRL": 5,
                                "IPRL": 4, "TMRL": 6, "FRL": 6})
        assert len(r["dimensions"]) == 6
        assert {d["key"] for d in r["dimensions"]} == {
            "TRL", "CRL", "BRL", "IPRL", "TMRL", "FRL"}

    def test_weakest_strongest(self):
        r = six_dim_assessment({"TRL": 9, "CRL": 2, "BRL": 5,
                                "IPRL": 5, "TMRL": 5, "FRL": 5})
        assert r["strongest"] == "TRL"
        assert r["weakest"] == "CRL"

    def test_scores_clamped_to_1_9(self):
        r = six_dim_assessment({"TRL": 99, "CRL": -5})
        vals = {d["key"]: d["score"] for d in r["dimensions"]}
        assert vals["TRL"] == 9.0 and vals["CRL"] == 1.0

    def test_trl_anchor_text(self):
        assert "真实环境" in trl_anchor(7)
        assert "成熟" in trl_anchor(9)


class TestCompetition:
    def test_high_threat_when_many_players(self):
        r = threat_score(major_players=9, patent_density=0.9,
                         funding_momentum=1e9, substitution_risk=0.9)
        assert r["threat_level"] == "high"
        assert r["barrier_score_5"] < 3.0

    def test_low_threat_when_empty(self):
        r = threat_score(major_players=0, patent_density=0.0,
                         funding_momentum=0, substitution_risk=0.0)
        assert r["threat_level"] == "low"
        assert r["barrier_score_5"] > 3.5


class TestHelpers:
    def test_clamp(self):
        assert clamp(0) == 1.0 and clamp(9) == 5.0 and clamp(3.14) == 3.14

    def test_scale_to_5(self):
        assert scale_to_5(0) == 1.0
        assert scale_to_5(1) == 5.0
        assert scale_to_5(2) == 5.0  # 越界钳制
