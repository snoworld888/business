"""决策报告生成：模板引擎 + 多格式导出。

对齐技术文档 §4.7：报告生成模板引擎 Jinja2 + python-pptx + docxtpl。
本模块在无 python-pptx / docxtpl 时仍可产出 Markdown / HTML / JSON，
装齐可选依赖后自动启用 PPTX / DOCX 导出。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from jinja2 import Template

from app.agents.base import as_list, dicts_only, normalize_risk_alerts

logger = logging.getLogger(__name__)


def _as_text_list(items: Any) -> list[str]:
    """把混合列表安全转成字符串列表。

    上游是大模型的输出：`players` / `gaps` / `suggested_experiments` 本应是字符串数组，
    实测模型会返回 `[{"name": "某公司"}]` 这类**对象数组** —— 直接
    `"、".join(...)` 会抛 `sequence item 0: expected str instance, dict found`，
    让报告接口 500。
    """
    out: list[str] = []
    for x in as_list(items):
        if isinstance(x, dict):
            for k in ("name", "title", "label", "text", "description", "value"):
                if x.get(k):
                    out.append(str(x[k]))
                    break
            else:
                out.append("；".join(f"{k}:{v}" for k, v in x.items()))
        elif x is not None:
            out.append(str(x))
    return out


def _pick(value: Any, *keys: str, default: Any = "—") -> Any:
    """按多个候选键从 dict 取值 —— 模型给出的字段名不固定。"""
    if not isinstance(value, dict):
        return default if value in (None, "") else value
    for k in keys:
        if value.get(k) not in (None, ""):
            return value[k]
    return default

# 《设计文档》§16.2 决策报告模板 —— 9 段结构
REPORT_SECTIONS = [
    ("executive_summary", "执行摘要"),
    ("track_definition", "赛道定义与市场空间"),
    ("demand_validation", "需求验证"),
    ("tech_assessment", "技术评估"),
    ("competition", "竞争格局"),
    ("business_model", "商业模式"),
    ("risks", "风险与应对"),
    ("milestones", "里程碑与资源需求"),
    ("evidence_appendix", "证据链附录"),
]

TEMPLATES: dict[str, dict[str, Any]] = {
    "investment_memo": {
        "name": "投资备忘录",
        "sections": [s[0] for s in REPORT_SECTIONS],
        "tone": "面向投资决策委员会，强调回报、风险与退出路径",
    },
    "project_proposal": {
        "name": "立项报告",
        "sections": [s[0] for s in REPORT_SECTIONS],
        "tone": "面向公司管理层，强调可行性、资源与里程碑",
    },
    "trend_brief": {
        "name": "趋势简报",
        "sections": ["executive_summary", "track_definition", "competition",
                     "evidence_appendix"],
        "tone": "面向战略部周会，短平快，突出信号与判断",
    },
}


@dataclass
class ReportContext:
    title: str
    opportunity: dict[str, Any]
    score: dict[str, Any]
    analysis: dict[str, Any]
    evidence: list[dict[str, Any]]
    profile: dict[str, Any] | None = None
    experiments: list[dict[str, Any]] | None = None


def available_templates() -> list[dict[str, Any]]:
    return [{"key": k, "sections": v["sections"], "name": v["name"], "tone": v["tone"]}
            for k, v in TEMPLATES.items()]


def _fmt_money(value: float | None) -> str:
    if not value:
        return "—"
    if value >= 1e8:
        return f"{value / 1e8:.2f} 亿元"
    if value >= 1e4:
        return f"{value / 1e4:.2f} 万元"
    return f"{value:.0f} 元"


def build_content(ctx: ReportContext, template: str = "investment_memo") -> dict[str, Any]:
    """把评分 / Agent 分析 / 证据链渲染为结构化报告内容。"""
    spec = TEMPLATES.get(template, TEMPLATES["investment_memo"])
    dims = {d["key"]: d for d in ctx.score.get("dimensions", [])}
    analysis = ctx.analysis or {}
    market = analysis.get("market", {}) or {}
    demand = analysis.get("demand", {}) or {}
    tech = analysis.get("tech", {}) or {}
    competition = analysis.get("competition", {}) or {}

    sections: list[dict[str, Any]] = []

    def add(key: str, markdown: str, citations: list[str] | None = None) -> None:
        if key not in spec["sections"]:
            return
        sections.append({
            "key": key,
            "title": dict(REPORT_SECTIONS)[key],
            "markdown": markdown.strip(),
            "citations": citations or [],
        })

    # 1. 执行摘要
    add("executive_summary", f"""
**{ctx.title}** 综合评分 **{ctx.score.get('total_score', '—')} / 5.0**，
建议结论：**{_verdict(ctx.score.get('total_score', 0))}**。

- 市场空间：TAM {_fmt_money(market.get('TAM'))} / SAM {_fmt_money(market.get('SAM'))} / SOM {_fmt_money(market.get('SOM'))}
- 需求强度：{demand.get('demand_score_5', '—')} / 5.0（痛点聚类 {len(demand.get('pain_points', []))} 类）
- 技术就绪：{tech.get('overall_5', '—')} / 5.0，最弱维度 {tech.get('weakest', '—')}
- 竞争压力：{competition.get('threat_level', '—')}（威胁分 {competition.get('threat_score', '—')}）
""", [d["ref"] for d in ctx.evidence[:2]])

    # 2. 赛道定义与市场空间
    add("track_definition", f"""
{ctx.opportunity.get('summary', '')}

**市场规模测算**（自下而上 × 自上而下取几何均值）
| 口径 | 数值 |
| --- | --- |
| TAM 总体市场 | {_fmt_money(market.get('TAM'))} |
| SAM 可服务市场 | {_fmt_money(market.get('SAM'))} |
| SOM 可获得市场 | {_fmt_money(market.get('SOM'))} |
| 融合方式 | {(market.get('method') or {}).get('fusion', '—')} |

**赛道评分明细**
| 维度 | 权重 | 得分 | 证据 |
| --- | --- | --- | --- |
""" + "\n".join(
        f"| {d['name']} | {d['weight'] * 100:.0f}% | {d['score']} | "
        f"{len(d.get('evidence', []))} 条 |" for d in ctx.score.get("dimensions", [])
    ), [d["ref"] for d in ctx.evidence[:3]])

    # 3. 需求验证
    pains = dicts_only(demand.get("pain_points"))
    add("demand_validation", f"""
需求强度 **{demand.get('demand_score_5', '—')} / 5.0**。

| 痛点 | 频次 | 情感 | 强度 |
| --- | --- | --- | --- |
""" + ("\n".join(
        f"| {_pick(p, 'name', 'title', 'pain')} | {_pick(p, 'freq', 'frequency', 'count')} "
        f"| {_pick(p, 'sentiment')} | {_pick(p, 'demand_score', 'score', 'strength')} |"
        for p in pains[:5]) or "| — | — | — | — |") + f"""

**建议验证路径**：{"、".join(_as_text_list(demand.get("suggested_experiments")) or ["落地页测试", "客户访谈"])}
""" + (f"""
**已完成的验证实验**
""" + "\n".join(
        f"- {_pick(e, 'title', 'method')}：{_pick(e, 'conclusion', default='进行中')}"
        for e in (ctx.experiments or [])) if ctx.experiments else ""),
        [p.get("evidence_ref", "") for p in pains[:2] if p.get("evidence_ref")])

    # 4. 技术评估
    tech_dims = dicts_only(tech.get("dimensions"))
    add("tech_assessment", "六维就绪度评估（1–9 量纲）：\n\n| 维度 | 得分 | 说明 |\n| --- | --- | --- |\n"
        + "\n".join(f"| {_pick(d, 'name', 'title')}({_pick(d, 'key', 'code')}) "
                    f"| {_pick(d, 'score')} | "
                    f"{_pick(d, 'anchor', 'note', 'rationale')} |" for d in tech_dims),
        [e for d in tech_dims for e in as_list(d.get("evidence"))][:3])

    # 5. 竞争格局
    add("competition", f"""
威胁等级 **{competition.get('threat_level', '—')}**，壁垒分 **{competition.get('barrier_score_5', '—')} / 5.0**。

**主要玩家**：{"、".join(_as_text_list(competition.get("players")) or ["待补充"])}

**空白点**：{"、".join(_as_text_list(competition.get("gaps")) or ["待补充"])}
""", as_list(competition.get("evidence_refs")))

    # 6. 商业模式
    add("business_model", f"""
结合公司能力画像（{', '.join((ctx.profile or {}).get('tech_stack', []) or ['待补充'])}），
建议采用「{_biz_model(ctx.score.get('total_score', 0))}」路径。

- 交付形态：SaaS 订阅 / 私有化部署 / API 按量计费
- 关键假设：客户愿意为提升效果支付溢价
- 单位经济性：以 SOM {_fmt_money(market.get('SOM'))} 为首年可触达收入上限
""", [d["ref"] for d in ctx.evidence[:3]])

    # 7. 风险
    # ⚠️ 必须走 `normalize_risk_alerts` 而不是直接用 `dicts_only`：
    # 真实大模型产出的是**叙事性字符串数组**，`dicts_only` 会把它们全部过滤掉
    # ⇒ 报告里一条风险都不剩（旧数据同理）。归一后字符串条目也能进报告。
    risks = normalize_risk_alerts(ctx.score.get("risk_alerts"))
    add("risks", "\n".join(
        f"- ⚠ **{_pick(r, 'title', 'name')}**：{_pick(r, 'detail', 'description', 'message')}"
        for r in risks
    ) or "- 暂无触发阈值的风险项", [])

    # 8. 里程碑
    add("milestones", """
| 阶段 | 时间 | 关键交付 |
| --- | --- | --- |
| M1 能力对齐 | 第 1 月 | 能力画像确认、赛道边界收敛 |
| M2 需求验证 | 第 1–2 月 | 落地页 + 客户访谈，需求强度实测 |
| M3 MVP 研发 | 第 2–3 月 | 核心功能可演示，获得首批试用客户 |
| M4 决策评审 | 第 3 月末 | 立项/暂缓结论，资源投放决议 |
""")

    # 9. 证据链
    add("evidence_appendix", "| 引用 | 类型 | 来源 | 标题 |\n| --- | --- | --- | --- |\n"
        + ("\n".join(
            f"| {e['ref']} | {e.get('signal_type')} | {e.get('source')} | "
            f"[{e.get('title')}]({e.get('url') or '#'}) |" for e in ctx.evidence)
           or "| — | — | — | — |"))

    evidence_used = sorted({c for s in sections for c in s["citations"] if c})
    word_count = sum(len(s["markdown"]) for s in sections)

    return {
        "template": template,
        "template_name": spec["name"],
        "title": ctx.title,
        "sections": sections,
        "meta": {
            "template_tone": spec["tone"],
            "evidence_count": len(evidence_used),
            "evidence_refs": evidence_used,
            "page_count": max(1, round(word_count / 800)),
            "char_count": word_count,
        },
    }


def _verdict(total: float) -> str:
    if total >= 4.2:
        return "建议立项/投资"
    if total >= 3.5:
        return "建议继续验证"
    if total >= 2.8:
        return "建议观察"
    return "建议暂缓"


def _biz_model(total: float) -> str:
    return "高客单私有化 + 行业模板复用" if total >= 4.0 else "SaaS 订阅快速铺量"


MD_TEMPLATE = Template("""# {{ title }}

> 模板：{{ meta.template_name }} ｜ 生成时间：{{ generated_at }}
> 证据引用：{{ meta.evidence_count }} 条 ｜ 预计页数：{{ meta.page_count }}

{% for s in content.sections %}
## {{ loop.index }}. {{ s.title }}

{{ s.markdown }}
{% if s.citations %}
*证据引用：{{ s.citations | join(', ') }}*
{% endif %}

{% endfor %}
---
*本报告由赛道罗盘 TrackCompass 自动生成，全部结论可下钻至原始信号，请结合专家复核后使用。*
""")


def render_markdown(content: dict[str, Any], generated_at: str) -> str:
    return MD_TEMPLATE.render(title=content.get("title"), content=content,
                              meta=content.get("meta", {}), generated_at=generated_at)


HTML_TEMPLATE = Template("""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{{ title }}</title>
<style>
 body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
      max-width:900px;margin:40px auto;padding:0 24px;color:#0F172A;line-height:1.7}
 h1{font-size:28px;border-bottom:3px solid #2563EB;padding-bottom:12px}
 h2{font-size:20px;margin-top:36px;color:#1E40AF}
 table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}
 th,td{border:1px solid #E2E8F0;padding:8px 10px;text-align:left}
 th{background:#F1F5F9}
 blockquote{background:#F8FAFC;border-left:4px solid #2563EB;margin:12px 0;padding:8px 16px}
 .meta{color:#64748B;font-size:13px}
</style></head><body>
<h1>{{ title }}</h1>
<p class="meta">模板 {{ meta.template_name }} ｜ 证据 {{ meta.evidence_count }} 条 ｜ {{ generated_at }}</p>
{% for s in content.sections %}
<h2>{{ loop.index }}. {{ s.title }}</h2>
{% for line in s.markdown.split('\n') %}
{% if line.strip() %}<p>{{ line }}</p>{% endif %}
{% endfor %}
{% if s.citations %}<blockquote>证据引用：{{ s.citations | join(', ') }}</blockquote>{% endif %}
{% endfor %}
</body></html>""")


def export(content: dict[str, Any], fmt: str, generated_at: str,
           output_dir) -> dict[str, Any]:
    """导出报告。fmt: markdown | html | json | pptx | docx。"""
    from pathlib import Path

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in content.get("title", "report") if c not in r'\/:*?"<>|')[:60]
    stamp = generated_at.replace(":", "").replace("-", "")[:15]

    if fmt in ("markdown", "md"):
        path = output_dir / f"{safe}_{stamp}.md"
        path.write_text(render_markdown(content, generated_at), encoding="utf-8")
        return {"format": "markdown", "path": str(path), "url": path.as_uri()}

    if fmt == "html":
        path = output_dir / f"{safe}_{stamp}.html"
        path.write_text(HTML_TEMPLATE.render(title=content.get("title"), content=content,
                                             meta=content.get("meta", {}),
                                             generated_at=generated_at), encoding="utf-8")
        return {"format": "html", "path": str(path), "url": path.as_uri()}

    if fmt == "json":
        path = output_dir / f"{safe}_{stamp}.json"
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"format": "json", "path": str(path), "url": path.as_uri()}

    if fmt == "pptx":
        try:
            from pptx import Presentation  # type: ignore
            from pptx.util import Pt  # type: ignore
        except ImportError:
            logger.warning("python-pptx 未安装，退化为 markdown 导出")
            return export(content, "markdown", generated_at, output_dir)

        prs = Presentation()
        title_slide = prs.slides.add_slide(prs.slide_layouts[0])
        title_slide.shapes.title.text = content.get("title", "") or "决策报告"
        # ⚠️ `template_name` 在 `content` 的**顶层**（见 `build_report` 的返回结构），
        #    不在 `meta` 里。曾写死 `content['meta']['template_name']` ⇒ PPT 导出
        #    必然 500 `KeyError: 'template_name'`；而该缺陷长期被
        #    「python-pptx 未安装 → 直接降级 markdown」掩盖，装上依赖才暴露。
        #    统一用 `.get()` 兜底，避免同类硬索引。
        meta = content.get("meta") or {}
        template_label = (
            content.get("template_name") or meta.get("template_tone") or "标准模板"
        )
        subtitle = title_slide.placeholders[1]
        subtitle.text = f"{template_label} ｜ 证据 {meta.get('evidence_count', 0)} 条"
        for section in content.get("sections", []):
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = str(section.get("title") or "")
            body = slide.placeholders[1].text_frame
            body.text = str(section.get("markdown") or "")[:1200]
            for para in body.paragraphs:
                para.font.size = Pt(12)
        path = output_dir / f"{safe}_{stamp}.pptx"
        prs.save(str(path))
        return {"format": "pptx", "path": str(path), "url": path.as_uri()}

    if fmt == "docx":
        try:
            from docx import Document  # type: ignore
        except ImportError:
            logger.warning("python-docx 未安装，退化为 markdown 导出")
            return export(content, "markdown", generated_at, output_dir)

        doc = Document()
        doc.add_heading(content.get("title", "") or "决策报告", level=0)
        for section in content.get("sections", []):
            doc.add_heading(str(section.get("title") or ""), level=1)
            doc.add_paragraph(str(section.get("markdown") or ""))
            if section.get("citations"):
                doc.add_paragraph(f"证据引用：{', '.join(map(str, section['citations']))}")
        path = output_dir / f"{safe}_{stamp}.docx"
        doc.save(str(path))
        return {"format": "docx", "path": str(path), "url": path.as_uri()}

    return {"format": fmt, "path": None, "url": None,
            "error": f"不支持的导出格式: {fmt}"}
