"""采集器测试：清洗规则、各源解析、去重、注册表。

**不联网**：所有适配器测试都注入假 `HttpClient`，喂预置的报文片段。
这样 pytest 保持「离线可跑」——与项目既有约定一致（见 requirements.txt 注释）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.collector.base import CollectContext, RawSignal, parse_datetime, strip_html
from app.collector.normalize import (
    clean_title, content_hash, detect_language, infer_industry, is_low_quality, normalize,
)
from app.collector.registry import ADAPTERS, DEFAULT_ENABLED, describe_adapters
from app.collector.sources.ccgp_bidding import CCGPBiddingAdapter
from app.collector.sources.gov_policy import GovPolicyAdapter
from app.collector.sources.tech_news import TechNews36KrAdapter


class _FakeClient:
    """假 HTTP 客户端：按 URL 关键字返回预置报文。"""

    def __init__(self, payloads: dict[str, object]):
        self.payloads = payloads
        self.calls: list[str] = []

    def get(self, url, *, params=None, headers=None, interval=0.0, expect="auto"):
        self.calls.append(url)
        for key, value in self.payloads.items():
            if key in url:
                return value
        raise RuntimeError(f"未预置的 URL: {url}")

    def close(self):
        pass


# ---------------------------------------------------------------- 清洗
class TestNormalize:
    def test_clean_title_strips_site_suffix(self):
        assert clean_title("某公司完成B轮融资_36氪") == "某公司完成B轮融资"
        assert clean_title("政策发布 - IT之家") == "政策发布"
        # 正常的连字符不应被误伤
        assert clean_title("AI-制造白皮书") == "AI-制造白皮书"

    def test_clean_title_strips_html(self):
        assert clean_title("<em>人工智能</em>+行动") == "人工智能+行动"

    def test_infer_industry_title_weighted(self):
        # 标题命中权重高于正文：标题说医疗，正文顺带提了工厂，应判医疗
        got = infer_industry("医疗影像辅助诊断系统", "该系统部署于工厂边缘设备")
        assert got == "AI+医疗"

    def test_infer_industry_fallback_when_no_hit(self):
        assert infer_industry("今天天气不错", fallback="AI+制造") == "AI+制造"
        assert infer_industry("今天天气不错") is None

    def test_detect_language(self):
        assert detect_language("这是一段中文正文") == "zh"
        assert detect_language("this is an english body") == "en"
        assert detect_language("") == "unknown"

    def test_content_hash_stable(self):
        assert content_hash("标题A") == content_hash("标题A")
        assert content_hash("标题A") != content_hash("标题B")
        assert len(content_hash("标题A")) == 32

    def test_is_low_quality_blocks_short_and_spam(self):
        assert is_low_quality("短", "")[0] is True
        assert is_low_quality("频繁访问!中国政府采购网", "")[0] is True
        assert is_low_quality("一条正常的产业新闻标题", "正文")[0] is False

    def test_normalize_returns_none_for_junk(self):
        raw = RawSignal(signal_type="news", source="s", title="短")
        assert normalize(raw) is None

    def test_normalize_industry_priority(self):
        """信号自带行业 > 内容推断 > 公司行业兜底。"""
        raw = RawSignal(signal_type="paper", source="s",
                        title="面向工业质检的缺陷检测算法", content="")
        # 无 industry、无 hint → 从标题推断出 AI+制造
        assert normalize(raw)["industry"] == "AI+制造"
        # 有 hint 但标题能推断出别的 → 以内容为准
        got = normalize(raw, industry_hint="新能源科技")["industry"]
        assert got == "AI+制造", "公司行业不得覆盖信号自身的行业线索"
        # 标题无任何线索 → 才用 hint 兜底
        raw2 = RawSignal(signal_type="news", source="s", title="某公司宣布完成新一轮融资", content="")
        assert normalize(raw2, industry_hint="AI+制造")["industry"] == "AI+制造"

    def test_normalize_always_has_collection_metadata(self):
        raw = RawSignal(signal_type="news", source="s",
                        title="一条足够长的产业新闻标题", content="正文")
        payload = normalize(raw)
        assert payload["metrics"]["language"] == "zh"
        assert "collected_at" in payload["metrics"]


# ---------------------------------------------------------------- 政策源
class TestGovPolicy:
    def _adapter(self):
        payload = {
            "search-gov/data": {
                "code": 200,
                "searchVO": {
                    "listVO": [
                        {
                            "title": "国务院关于深入实施“<em>人工智能</em>+”行动的意见",
                            "pcode": "国发〔2025〕11号", "puborg": "国务院",
                            "pubtime": 1789117200,
                            "summary": "推进人工智能与实体经济深度融合。",
                            "url": "https://www.gov.cn/zhengce/content_1.htm",
                            "childtype": "科技、教育\\科技",
                        },
                    ],
                    "totalCount": 1,
                },
            },
        }
        return GovPolicyAdapter(_FakeClient(payload))

    def test_parses_json_api(self):
        rows = self._adapter().fetch(CollectContext(keywords=["人工智能"], limit=3))
        assert rows, "应从政策文件库接口解析出条目"
        first = rows[0]
        assert "人工智能" in first.title
        assert "<em>" not in first.title, "标题中的高亮标签必须被清除"
        assert first.signal_type == "policy"
        assert first.url.startswith("https://www.gov.cn/")
        assert first.metrics["policy_code"] == "国发〔2025〕11号"
        assert first.published_at is not None


# ---------------------------------------------------------------- 论文源
class TestAcademic:
    def test_abstract_deinvert(self):
        from app.collector.sources.academic import _deinvert

        inverted = {"视觉": [1], "检测": [0], "算法": [2]}
        assert _deinvert(inverted) == "检测 视觉 算法"
        assert _deinvert(None) == ""


# ---------------------------------------------------------------- 新闻源
class TestRSS:
    RSS = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>36氪</title>
      <item>
        <title>某公司完成亿元融资_36氪</title>
        <link><![CDATA[https://36kr.com/p/123]]></link>
        <pubDate>2026-09-15 11:48:11  +0800</pubDate>
        <description><![CDATA[<p>正文<b>内容</b></p>]]></description>
      </item>
    </channel></rss>"""

    def test_parses_rss20(self):
        adapter = TechNews36KrAdapter(_FakeClient({"36kr.com/feed": self.RSS}))
        rows = adapter.fetch(CollectContext(limit=5))
        assert len(rows) == 1
        row = rows[0]
        assert row.url == "https://36kr.com/p/123"
        assert "<p>" not in row.content, "description 里的 HTML 必须清除"
        assert "正文" in row.content
        assert row.published_at is not None
        assert row.published_at.year == 2026

    def test_bad_xml_raises(self):
        adapter = TechNews36KrAdapter(_FakeClient({"36kr.com/feed": "not xml at all"}))
        with pytest.raises(ValueError, match="RSS 解析失败"):
            adapter.fetch(CollectContext(limit=5))


# ---------------------------------------------------------------- 招标源
class TestBidding:
    HTML = """
    <html><body><ul>
      <li><a href="./gkzb/202609/t20260915_27327769.htm" target="_blank"
             title="某单位智能巡检系统采购公开招标公告">某单位智能巡检系统采购公开招标公告</a></li>
      <li><a href="./cjgg/202609/t20260915_27327731.htm" target="_blank"
             title="某园区视觉检测设备采购成交公告">某园区视觉检测设备采购成交公告</a></li>
      <li><a href="https://other.example.com/x.htm" title="外站链接应被忽略">外站链接</a></li>
    </ul></body></html>
    """

    def test_extracts_and_resolves_urls(self):
        adapter = CCGPBiddingAdapter(_FakeClient({"ccgp.gov.cn": self.HTML}))
        rows = adapter.fetch(CollectContext(keywords=["视觉"], limit=10))
        assert len(rows) == 1, "只应保留命中检索词的公告"
        row = rows[0]
        # ⚠️ 必须基于列表页解析，不能丢 /cggg/zygg/ 前缀
        assert row.url.startswith("http://www.ccgp.gov.cn/cggg/zygg/")
        assert row.published_at == datetime(2026, 9, 15, tzinfo=timezone.utc)
        assert row.metrics["announce_type"] == "成交公告"

    def test_requires_keywords(self):
        adapter = CCGPBiddingAdapter(_FakeClient({"ccgp.gov.cn": self.HTML}))
        assert adapter.requires_keywords is True


# ---------------------------------------------------------------- 注册表
class TestRegistry:
    def test_default_enabled_are_public_and_present(self):
        for key in DEFAULT_ENABLED:
            assert key in ADAPTERS, f"默认启用集里的 {key} 未注册"
            assert ADAPTERS[key].requires_key is False, f"{key} 需要密钥，不应默认启用"

    def test_coverage_meets_design_requirement(self):
        """《设计文档》§7.2 验收标准：覆盖至少 5 个数据源。"""
        available = [r for r in describe_adapters() if r["available"]]
        assert len(available) >= 5, "可用数据源不足 5 个，不满足设计验收标准"

    def test_keyed_adapters_declare_env(self):
        keyed = [r for r in describe_adapters() if r["requires_key"]]
        assert keyed, "需密钥的适配器（专利/融资/招聘/社媒/供应链）应当存在"
        for row in keyed:
            assert row["key_env"], f"{row['key']} 未声明密钥环境变量名"


# ---------------------------------------------------------------- 入库去重
class TestIngest:
    def _payload(self, title: str, url: str | None = None) -> dict:
        return {
            "signal_type": "news", "source": "unit-test", "title": title,
            "content": "正文内容", "url": url, "industry": "AI+制造",
            "keywords": [], "_raw_title": title,
        }

    def test_dedupes_by_hash_and_url(self, db, tenant_id):
        from app.services.signal_ingest import persist_signals

        title = "单元测试专用信号标题（勿删）"

        first = persist_signals(db, tenant_id, [self._payload(title, "https://unit.test/a")])
        assert first.inserted == 1

        # 同标题不同 URL —— 仍应判重（content_hash 命中）
        second = persist_signals(db, tenant_id, [self._payload(title, "https://unit.test/b")])
        assert second.inserted == 0 and second.duplicated == 1

        # 同 URL 不同标题 —— 也应判重（URL 命中）
        third = persist_signals(db, tenant_id, [self._payload("另一个完全不同的标题啊啊啊",
                                                             "https://unit.test/a")])
        assert third.inserted == 0 and third.duplicated == 1

    def test_extracts_keywords_automatically(self, db, tenant_id):
        """实体抽取：即使入参 keywords 为空，也要从标题抽技术词。"""
        from app.models import Signal
        from app.services.signal_ingest import persist_signals

        title = "面向工业场景的计算机视觉缺陷检测方案（单元测试）"
        report = persist_signals(db, tenant_id, [self._payload(title)])
        assert report.inserted == 1
        sig = db.query(Signal).filter(Signal.id == report.inserted_ids[0]).one()
        assert "计算机视觉" in (sig.keywords or []), "应从标题抽取出技术词"


# ---------------------------------------------------------------- 工具函数
def test_strip_html_and_parse_datetime():
    assert strip_html("<p>a&nbsp;b</p>") == "a b"
    assert parse_datetime("2026-09-15 11:48:11  +0800") is not None
    assert parse_datetime("Tue, 15 Sep 2026 06:38:00 GMT") is not None
    assert parse_datetime("2026.09.11").year == 2026
    assert parse_datetime(1789117200).year == 2026      # 秒
    assert parse_datetime(1789117200000).year == 2026   # 毫秒
    assert parse_datetime("不是时间") is None
