"""种子数据：让系统开箱即可演示完整工作流。

覆盖《设计文档》§13.2 指定的三个垂直领域：AI+制造、AI+医疗、新能源科技。
信号条数、来源、指标量级参照真实产业公开信息的数量级设定，
用于验证「信号 → 趋势 → 机会 → 评分 → 报告 → 预警」全链路。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.core.visibility import PUBLIC_TENANT_ID
from app.models import (
    Alert,
    AuditLog,
    CompanyProfile,
    Experiment,
    Role,
    Scorecard,
    ScorecardVersion,
    Signal,
    Tenant,
    Track,
    User,
)
from app.services.embeddings import embed
from app.services.scoring import DEFAULT_DIMENSIONS

logger = logging.getLogger(__name__)

DEMO_TENANT = "智造未来科技"
DEMO_EMAIL = "admin@demo.com"
DEMO_PASSWORD = "TrackCompass@2026"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ 信号定义
# (type, source, 标题, 内容, 行业, 关键词, 指标, 情感, 天数前, 实体)
RAW_SIGNALS: list[tuple] = [
    # ============================== AI+制造 / 智能质检
    ("policy", "工业和信息化部", "《制造业数字化转型行动方案》发布",
     "支持工业视觉、智能质检等关键技术规模化应用，鼓励中小企业上云上平台，对示范项目给予补贴支持。",
     "AI+制造", ["计算机视觉", "智能质检", "工业互联网"],
     {"policy_level": "national", "amount": 0}, 0.6, 3,
     {"company": [], "policy": ["制造业数字化转型行动方案"]}),
    ("policy", "工业和信息化部", "人形机器人创新发展指导意见发布",
     "推动具身智能关键技术攻关，支持整机与核心零部件协同发展，开展试点示范应用。",
     "具身智能", ["具身智能", "机器人", "传感器"],
     {"policy_level": "national"}, 0.7, 12,
     {"policy": ["人形机器人创新发展指导意见"]}),
    ("policy", "某省工信厅", "关于开展智能工厂梯度培育的通知",
     "对通过智能工厂评定的企业给予一次性奖励，重点支持视觉检测、预测性维护场景。",
     "AI+制造", ["智能质检", "预测性维护"], {"policy_level": "provincial"}, 0.5, 20, {}),
    ("patent", "国家知识产权局", "基于深度学习的表面缺陷检测方法及系统",
     "公开了一种多尺度特征融合的表面缺陷检测方法，在相关测试环境下验证，检出率优于传统方法，已在产线小批量试用。",
     "AI+制造", ["深度学习", "计算机视觉", "智能质检"],
     {"claims": 18, "trl_hint": 6}, 0.2, 8,
     {"company": ["某视觉科技有限公司", "某智能装备股份有限公司"], "patent": ["CN2024xxxxxx"]}),
    ("patent", "国家知识产权局", "面向小样本的工业缺陷识别模型训练方法",
     "提出小样本增量学习方案，可在实验室环境下实现快速冷启动，减少标注数据依赖。",
     "AI+制造", ["机器学习", "智能质检"], {"claims": 12, "trl_hint": 4}, 0.2, 26,
     {"company": ["某视觉科技有限公司"]}),
    ("patent", "国家知识产权局", "一种工业机器人柔性抓取装置",
     "涉及机械结构与视觉伺服控制融合方案，提升异形件抓取成功率。",
     "具身智能", ["机器人", "传感器"], {"claims": 9, "trl_hint": 5}, 0.1, 40,
     {"company": ["某智能装备股份有限公司"]}),
    ("funding", "产业投资快讯", "某视觉科技有限公司完成 B 轮 3.2 亿元融资",
     "本轮由头部产业资本领投，资金将用于扩充工业视觉产品线并加速产线规模化落地。",
     "AI+制造", ["计算机视觉", "智能质检"],
     {"amount": 320_000_000, "round": "B", "companies": ["某视觉科技有限公司"]}, 0.8, 5,
     {"company": ["某视觉科技有限公司"]}),
    ("funding", "产业投资快讯", "某工业软件集团设立 20 亿元智能制造产业基金",
     "基金聚焦工业视觉、智能排产、数字孪生方向，计划三年内部署 50 个示范工厂。",
     "AI+制造", ["智能排产", "数字孪生", "工业互联网"],
     {"amount": 2_000_000_000, "round": "fund", "companies": ["某工业软件集团"]}, 0.7, 10,
     {"company": ["某工业软件集团"]}),
    ("funding", "产业投资快讯", "某机器人企业完成 Pre-IPO 轮 8 亿元融资",
     "估值约 120 亿元，主打人形机器人整机与关节模组，已进入小批量量产阶段。",
     "具身智能", ["具身智能", "机器人"],
     {"amount": 800_000_000, "round": "pre-ipo", "companies": ["某机器人股份有限公司"]}, 0.85, 15,
     {"company": ["某机器人股份有限公司"]}),
    ("paper", "arXiv", "面向工业质检的自监督表征学习新方法",
     "提出一种新架构，在多个公开数据集上刷新 SOTA，显著降低标注成本，处于实验室验证阶段。",
     "AI+制造", ["深度学习", "计算机视觉", "智能质检"], {"citations": 42}, 0.3, 2,
     {"company": ["某研究院"]}),
    ("paper", "arXiv", "工业时序数据的预测性维护基线研究",
     "系统对比了多种时序模型在设备故障预测上的表现，提出复杂度可控的落地建议。",
     "AI+制造", ["预测性维护", "机器学习"], {"citations": 18}, 0.3, 18, {}),
    ("recruit", "招聘平台", "某视觉科技急招工业视觉算法工程师 30 人",
     "要求熟悉缺陷检测与模型轻量化部署，薪资区间 40-80 万元，工作地长三角。",
     "AI+制造", ["计算机视觉", "智能质检"],
     {"headcount": 30, "salary_range": "40-80万"}, 0.1, 6,
     {"company": ["某视觉科技有限公司"], "talent": ["算法工程师"]}),
    ("recruit", "招聘平台", "某装备集团招聘智能制造解决方案工程师 15 人",
     "负责产线改造方案设计与现场交付，要求具备老设备适配经验。",
     "AI+制造", ["工业互联网", "智能排产"],
     {"headcount": 15, "salary_range": "25-45万"}, 0.1, 14,
     {"company": ["某智能装备股份有限公司"], "talent": ["解决方案工程师"]}),
    ("recruit", "招聘平台", "具身智能公司招聘机器人运控算法人才",
     "岗位集中在强化学习与运动控制，行业人才紧缺，招聘周期普遍超过 4 个月。",
     "具身智能", ["机器人", "机器学习"],
     {"headcount": 20, "salary_range": "50-100万"}, 0.1, 22,
     {"talent": ["运控算法"]}),
    ("social", "行业社区", "质检漏检问题一直是产线最大痛点",
     "质检总是漏检，客户投诉多，精度不够，还得人工复检，急需能在现有产线上快速部署的低成本方案。",
     "AI+制造", ["智能质检"], {"engagement": 860}, -0.8, 1, {}),
    ("social", "行业社区", "一套视觉检测系统太贵，中小企业用不起",
     "报价动辄百万，预算不够，性价比低，产线才两条，什么时候能有轻量化标准品？很迫切。",
     "AI+制造", ["智能质检"], {"engagement": 540}, -0.7, 4, {}),
    ("social", "行业社区", "老设备改造适配太难，交付周期一拖再拖",
     "我们厂的老设备接口不开放，数据孤岛严重，改造量大，交付慢，响应也慢，排期久到离谱。",
     "AI+制造", ["工业互联网", "数字孪生"], {"engagement": 420}, -0.75, 7, {}),
    ("social", "行业社区", "模型黑盒不敢用，出了问题说不清",
     "效果不可解释，出了事故无法追溯，审计过不了，不敢上产线。",
     "AI+制造", ["智能质检"], {"engagement": 260}, -0.6, 11, {}),
    ("social", "行业社区", "运维成本高，模型漂移没人管",
     "上线半年准确率掉得厉害，调参难，维护贵，还得依赖外部团队。",
     "AI+制造", ["机器学习", "预测性维护"], {"engagement": 310}, -0.65, 16, {}),
    ("review", "应用市场", "智能质检软件使用反馈：识别准确率不足",
     "误检率高，良率误判导致误停线，客服响应慢，希望尽快优化。",
     "AI+制造", ["智能质检"], {"rating": 2.4}, -0.8, 9, {}),
    ("review", "应用市场", "预测性维护平台部署体验",
     "实施周期长，需要大量停机窗口，接口不开放导致无法与 MES 打通。",
     "AI+制造", ["预测性维护", "工业互联网"], {"rating": 2.8}, -0.6, 21, {}),
    ("bidding", "招标网", "某汽车零部件集团智能质检系统公开招标",
     "预算 1800 万元，要求 6 个月内完成 8 条产线部署，接受联合体投标，需具备同类项目业绩。",
     "AI+制造", ["智能质检", "计算机视觉"],
     {"amount": 18_000_000, "deadline_days": 30}, 0.4, 5, {}),
    ("bidding", "招标网", "某新能源电池厂智能排产与数字孪生平台采购",
     "预算 3400 万元，要求与现有 MES/WMS 深度集成，分三期实施。",
     "AI+制造", ["智能排产", "数字孪生"],
     {"amount": 34_000_000, "deadline_days": 45}, 0.4, 13, {}),
    ("news", "产业媒体", "头部集团加速布局工业视觉，生态卡位战开启",
     "某工业软件集团宣布三年投入 50 亿元，通过生态合作与并购锁定渠道。",
     "AI+制造", ["计算机视觉", "智能质检"],
     {"companies": ["某工业软件集团"]}, 0.3, 6, {"company": ["某工业软件集团"]}),
    # ============================== AI+医疗
    ("policy", "国家卫生健康委", "关于推动医疗人工智能规范化应用的指导意见",
     "鼓励辅助诊断类产品开展多中心验证，明确数据合规与安全审查要求，支持基层应用试点。",
     "AI+医疗", ["医疗器械", "深度学习"],
     {"policy_level": "national"}, 0.6, 9, {"policy": ["医疗人工智能规范化应用指导意见"]}),
    ("patent", "国家知识产权局", "一种医学影像病灶分割网络及其训练方法",
     "在实验室环境下验证，分割精度优于基线，已完成样机级验证。",
     "AI+医疗", ["计算机视觉", "医疗器械"], {"claims": 22, "trl_hint": 4}, 0.2, 19,
     {"company": ["某医疗科技有限公司"]}),
    ("funding", "医疗投资周报", "某医疗 AI 公司完成 C 轮 6 亿元融资",
     "资金用于三类证报批与医院渠道铺设，目前覆盖 300 余家医院。",
     "AI+医疗", ["医疗器械", "深度学习"],
     {"amount": 600_000_000, "round": "C", "companies": ["某医疗科技有限公司"]}, 0.8, 17,
     {"company": ["某医疗科技有限公司"]}),
    ("social", "医疗论坛", "基层医院缺少影像诊断能力",
     "医生人手不足，招不到人才，阅片慢，急需轻量化辅助工具，预算有限但愿意付费。",
     "AI+医疗", ["医疗器械"], {"engagement": 380}, -0.7, 8, {}),
    ("recruit", "招聘平台", "医疗 AI 公司招聘算法与注册事务人才",
     "同时招聘算法工程师与法规注册工程师，合规资质是落地关键。",
     "AI+医疗", ["医疗器械", "深度学习"],
     {"headcount": 25, "salary_range": "35-70万"}, 0.1, 25, {"talent": ["法规注册"]}),
    ("bidding", "招标网", "某省级医院影像辅助诊断系统采购",
     "预算 900 万元，要求具备三类医疗器械注册证，数据须本地化部署。",
     "AI+医疗", ["医疗器械"], {"amount": 9_000_000, "deadline_days": 25}, 0.4, 11, {}),
    # ============================== 新能源科技
    ("policy", "国家发展改革委", "新型储能高质量发展实施方案印发",
     "支持固态电池、钠离子电池等新技术路线示范，明确 2027 年装机目标。",
     "新能源科技", ["储能", "固态电池"], {"policy_level": "national"}, 0.75, 7, {}),
    ("patent", "国家知识产权局", "一种高镍正极材料表面包覆方法",
     "已实现小批量试制，循环寿命提升 18%，处于中试阶段。",
     "新能源科技", ["新材料", "固态电池"], {"claims": 15, "trl_hint": 5}, 0.3, 14, {}),
    ("funding", "新能源投资周报", "某储能科技公司完成 A 轮 12 亿元融资",
     "资金用于产能扩张，订单已排至明年二季度，处于规模化交付阶段。",
     "新能源科技", ["储能"], {"amount": 1_200_000_000, "round": "A",
                              "companies": ["某储能科技有限公司"]}, 0.85, 4,
     {"company": ["某储能科技有限公司"]}),
    ("social", "产业社区", "储能系统集成成本与安全焦虑",
     "价格战激烈，成本压力大，安全认证周期长，合规审计严格，中小厂商很难受。",
     "新能源科技", ["储能"], {"engagement": 470}, -0.7, 10, {}),
    ("bidding", "招标网", "某能源集团储能电站 EPC 总承包招标",
     "规模 400MWh，预算约 6.8 亿元，要求具备电力工程施工总承包资质。",
     "新能源科技", ["储能"], {"amount": 680_000_000, "deadline_days": 40}, 0.5, 16, {}),
    ("news", "产业媒体", "头部电池集团宣布固态电池中试线投产",
     "计划 2027 年实现车规级量产，已与多家整车厂签订联合开发协议。",
     "新能源科技", ["固态电池", "新材料"],
     {"companies": ["某电池集团"]}, 0.6, 12, {"company": ["某电池集团"]}),
    ("paper", "arXiv", "固态电解质界面稳定性的新架构研究",
     "提出界面修饰新方案，实验室循环性能显著提升，具备产业化探索价值。",
     "新能源科技", ["固态电池", "新材料"], {"citations": 55}, 0.4, 6, {}),
    # ============================== 具身智能补充
    ("news", "产业媒体", "具身智能进入小批量量产，供应链仍存卡点",
     "关节模组与灵巧手供给紧张，量产良率与成本仍是主要瓶颈，标准化程度低。",
     "具身智能", ["具身智能", "机器人", "传感器"], {}, 0.4, 3, {}),
    ("social", "技术社区", "具身智能数据集获取成本过高",
     "真机数据采集成本高，仿真到现实迁移困难，缺少统一评测标准，研发投入大。",
     "具身智能", ["具身智能", "机器人"], {"engagement": 620}, -0.6, 9, {}),
    ("recruit", "招聘平台", "具身智能企业大规模招聘，算法岗位占比过半",
     "头部企业合计释放 300+ 岗位，运控与感知算法最紧缺。",
     "具身智能", ["机器人", "深度学习"],
     {"headcount": 300, "salary_range": "45-120万"}, 0.2, 5, {"talent": ["运控算法", "感知算法"]}),
]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def seed_all(db: Session, *, reset: bool = False, with_signals: bool = True) -> dict:
    """写入全部种子数据。reset=True 时清空业务表后重建。

    `with_signals=False` 时**不写 `RAW_SIGNALS` 演示信号** —— 这些是 40 条
    `example.com` 占位的假数据，仅供无网络环境验证链路。信号层的正确来源已
    改为采集器（`app/collector/`，见《技术文档》§3.1）；生产/演示环境应当跳过
    演示信号，让趋势雷达完全基于真实采集结果。

    ⚠️ 测试（`tests/conftest.py`）保持默认 `True`：pytest 必须离线可跑，
    需要确定性数据来断言。
    """
    if reset:
        for model in (Alert, AuditLog, Experiment, ScorecardVersion, Scorecard,
                      CompanyProfile, Signal, Track):
            db.query(model).delete()
        db.commit()

    # 只要已经有信号**或**赛道，就认为初始化过；只看信号会在
    # `with_signals=False` 的部署里每次都重跑本函数（进而重复建赛道）。
    existing = db.scalar(select(Signal).limit(1)) or db.scalar(select(Track).limit(1))
    if existing is not None:
        return {"skipped": True, "reason": "已存在数据，使用 reset=True 重建"}

    # ---------------- 租户与用户
    tenant = db.scalar(select(Tenant).where(Tenant.name == DEMO_TENANT))
    if tenant is None:
        tenant = Tenant(name=DEMO_TENANT, plan="enterprise",
                        settings_json={"llm_provider": "rule", "data_sources": 6})
        db.add(tenant)
        db.flush()

    role_defs = [
        ("admin", "管理员", ["opportunity:view", "score:edit", "report:generate",
                             "user:manage", "experiment:manage", "alert:manage",
                             "calibration:apply"]),
        ("strategy", "战略部", ["opportunity:view", "score:edit", "report:generate",
                                "experiment:manage", "alert:manage"]),
        ("product", "产品部", ["opportunity:view", "experiment:manage"]),
        ("investment", "投资部", ["opportunity:view", "report:generate"]),
        ("expert", "领域专家", ["opportunity:view", "score:edit"]),
    ]
    for name, display, perms in role_defs:
        if db.scalar(select(Role).where(Role.name == name)) is None:
            db.add(Role(name=name, display_name=display, permissions=perms))

    users = [
        (DEMO_EMAIL, "张总", "admin"),
        ("strategy@demo.com", "李战略", "strategy"),
        ("product@demo.com", "王产品", "product"),
        ("invest@demo.com", "赵投资", "investment"),
        ("expert@demo.com", "陈专家", "expert"),
    ]
    for email, name, role in users:
        if db.scalar(select(User).where(User.email == email)) is None:
            db.add(User(tenant_id=tenant.id, email=email, name=name, role=role,
                        password_hash=hash_password(DEMO_PASSWORD)))

    # ---------------- 评分卡
    scorecard = db.scalar(select(Scorecard).where(Scorecard.tenant_id == tenant.id))
    if scorecard is None:
        scorecard = Scorecard(
            tenant_id=tenant.id, name="默认赛道评分卡",
            weights={d["key"]: d["weight"] for d in DEFAULT_DIMENSIONS},
            dimensions=[dict(d) for d in DEFAULT_DIMENSIONS],
            is_default=True, version="V1.0",
        )
        db.add(scorecard)
        db.flush()
        db.add(ScorecardVersion(
            scorecard_id=scorecard.id, version="V1.0",
            weights=scorecard.weights, change_reason="初始版本",
            diff={k: {"from": None, "to": v} for k, v in scorecard.weights.items()},
        ))

    # ---------------- 能力画像
    profile = db.scalar(select(CompanyProfile).where(
        CompanyProfile.tenant_id == tenant.id))
    if profile is None:
        profile = CompanyProfile(
            tenant_id=tenant.id, name=DEMO_TENANT, industry="AI+制造",
            founded_at="2020-03", scale="50-200人",
            tech_stack=["计算机视觉", "深度学习", "大数据", "边缘计算"],
            tech_description=("自研多尺度缺陷检测算法与轻量化推理引擎，"
                              "具备产线级部署与老设备适配经验，"
                              "已服务 30 余家制造业客户。"),
            patents=["基于深度学习的表面缺陷检测方法", "小样本工业缺陷识别模型训练方法"],
            talents={"算法": 18, "工程": 26, "产品": 6, "销售": 9},
            funding={"round": "A", "amount": 100_000_000},
            channels=["直销", "系统集成商", "线上"],
            customers=["汽车零部件", "3C 制造", "新能源电池"],
            supply_chain=["工业相机", "边缘算力盒子", "云服务"],
        )
        profile.capability_vector = embed(
            f"{profile.tech_stack} {profile.tech_description} {profile.patents}"
        )
        profile.radar = {"技术": 4.5, "人才": 3.8, "资金": 3.2, "渠道": 3.6, "客户": 4.0}
        profile.recommended_tracks = ["AI+制造", "具身智能"]
        profile.boundary = {
            "industries": ["AI+制造", "具身智能"],
            "regions": ["中国"],
            "note": "技术栈聚焦视觉与边缘部署，跨行业进入医疗需补合规与渠道能力",
        }
        db.add(profile)

    # ---------------- 信号（演示数据；with_signals=False 时跳过）
    # 真实信号的来源是采集器，不是这里 —— 见《技术文档》§3.1 与
    # `scripts/collect_signals.py`。
    # ⚠️ 归属**公共层**（`tenant_id=None`）：信号是外部客观情报，不属于任何租户。
    # 若写成 `tenant.id`，新租户（如 E2E 租户）将看不到任何信号，
    # 「扫描 → 趋势」链路直接断路 —— 见 `core.visibility`。
    now = _now()
    if with_signals:
        for (stype, source, title, content, industry, keywords, metrics,
             sentiment, days_ago, entities) in RAW_SIGNALS:
            sig = Signal(
                tenant_id=PUBLIC_TENANT_ID,
                signal_type=stype, source=source, title=title,
                content=content, url=f"https://example.com/signal/{_hash(title)}",
                published_at=now - timedelta(days=days_ago),
                industry=industry, region="中国", keywords=keywords,
                entities=entities or {}, metrics=metrics or {}, sentiment=sentiment,
                content_hash=_hash(title),
            )
            sig.embedding = embed(f"{title} {content}")
            db.add(sig)

    # ---------------- 赛道
    tracks = {
        "AI+制造": "工业视觉、智能质检、预测性维护、智能排产的统称赛道",
        "AI+医疗": "医学影像辅助诊断与临床决策支持赛道",
        "新能源科技": "储能、固态电池、电池材料赛道",
        "具身智能": "人形机器人整机、关节模组与运控算法赛道",
    }
    track_id_by_name: dict[str, str] = {}
    for name, desc in tracks.items():
        track = Track(name=name, description=desc, industry=name)
        db.add(track)
        db.flush()
        track_id_by_name[name] = track.id

    # ---------------- 实验（对应原型 §5.8 的两条示例）
    exp = Experiment(
        tenant_id=tenant.id, title="落地页测试 · AI 质检",
        hypothesis="制造业客户愿意为 AI 质检付费",
        method="landing_page",
        metrics={"signup_rate": {"current": 0.042, "target": 0.05, "unit": "%"},
                 "dwell_time": {"current": 150, "target": 120, "unit": "s"},
                 "conversion_rate": {"current": 0.018, "target": 0.02, "unit": "%"}},
        progress=80.0, status="running",
        started_at=now - timedelta(days=18),
    )
    exp2 = Experiment(
        tenant_id=tenant.id, title="客户访谈 · 精度溢价意愿",
        hypothesis="客户愿意为精度提升支付溢价",
        method="interview",
        metrics={"premium_acceptance": {"current": 0.7, "target": 0.5, "unit": "比例"}},
        result={"interviews": 10, "willing_to_pay_premium": 7, "premium": 0.2},
        conclusion="10 家受访企业中 7 家愿意支付 20% 溢价",
        progress=100.0, status="completed",
        started_at=now - timedelta(days=30), ended_at=now - timedelta(days=9),
    )
    db.add_all([exp, exp2])
    db.commit()

    logger.info("种子数据写入完成: signals=%d", len(RAW_SIGNALS))
    return {
        "tenant_id": tenant.id, "signals": len(RAW_SIGNALS),
        "tracks": len(tracks), "users": len(users),
        "login": {"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
    }


# ============================================================ E2E 专用租户
#: E2E 冒烟脚本的专用租户 —— 把所有测试写入隔离到它名下，
#: 演示租户（智造未来科技）的数据不再被测试污染。
#: 见 `docs/公共数据层与E2E租户隔离.md`。
E2E_TENANT = "__e2e__ 自动化测试"
E2E_EMAIL = "e2e@test.local"
E2E_PASSWORD = "E2ETest@2026"


def ensure_e2e_tenant(db: Session) -> dict:
    """幂等创建 E2E 专用租户 / 用户 / 默认评分卡，返回 `{tenant_id, email, password}`。

    为什么必需评分卡：`/opportunities/{id}/score` 会调用
    `deps.get_default_scorecard`，缺失时直接 404 —— 没有评分卡的租户跑不完链路。

    本函数**只创建、不删除**，可反复调用；E2E 脚本每轮开始时调用一次，
    结束时清空该租户的业务数据（租户与用户保留，供下轮复用）。
    """
    tenant = db.scalar(select(Tenant).where(Tenant.name == E2E_TENANT))
    if tenant is None:
        tenant = Tenant(name=E2E_TENANT, plan="test",
                        settings_json={"purpose": "e2e", "llm_provider": "rule"})
        db.add(tenant)
        db.flush()

    user = db.scalar(select(User).where(User.email == E2E_EMAIL))
    if user is None:
        db.add(User(tenant_id=tenant.id, email=E2E_EMAIL, name="E2E 自动化",
                    role="admin", password_hash=hash_password(E2E_PASSWORD)))
    elif user.tenant_id != tenant.id:
        # 邮箱被挪到别的租户时纠正回来，避免隔离失效
        user.tenant_id = tenant.id

    card = db.scalar(select(Scorecard).where(Scorecard.tenant_id == tenant.id))
    if card is None:
        card = Scorecard(
            tenant_id=tenant.id, name="默认赛道评分卡",
            weights={d["key"]: d["weight"] for d in DEFAULT_DIMENSIONS},
            dimensions=[dict(d) for d in DEFAULT_DIMENSIONS],
            is_default=True, version="V1.0",
        )
        db.add(card)
        db.flush()
        db.add(ScorecardVersion(
            scorecard_id=card.id, version="V1.0",
            weights=card.weights, change_reason="E2E 租户初始化",
            diff={k: {"from": None, "to": v} for k, v in card.weights.items()},
        ))

    db.commit()
    return {"tenant_id": tenant.id, "email": E2E_EMAIL, "password": E2E_PASSWORD}


#: E2E 租户业务数据的清理顺序（**严格按外键依赖**：先删引用方，再删被引用方）。
#: 顺序错误会触发 `FOREIGN KEY constraint failed`（本项目已踩过一次）。
#: ⚠️ `scorecard_versions` 不在其中 —— 它**没有 tenant_id 列**，
#: 只能经 `scorecard_id` 间接删除，故单独处理。
E2E_PURGE_ORDER: tuple[str, ...] = (
    "experiments",        # → opportunities, tenants
    "reports",            # → opportunities, tenants
    "alerts",             # → tracks, tenants
    "opportunities",      # → tracks, tenants
    "trends",
    "company_profiles",
    "scorecards",
    "audit_logs",
)


def purge_e2e_data(db: Session, tenant_id: str) -> dict[str, int]:
    """清空 E2E 租户的业务数据（保留租户 / 用户 / 角色）。

    单事务执行，失败整体回滚 —— 绝不留下半清理状态。
    清空 `scorecards` 后，下一轮 `ensure_e2e_tenant` 会自动重建评分卡。
    """
    from sqlalchemy import text

    deleted: dict[str, int] = {}

    # ① 先删评分卡版本：该表无 tenant_id，须经 scorecards 关联定位
    res = db.execute(text(
        "DELETE FROM scorecard_versions WHERE scorecard_id IN "
        "(SELECT id FROM scorecards WHERE tenant_id = :tid)"
    ), {"tid": tenant_id})
    deleted["scorecard_versions"] = res.rowcount or 0

    # ② 其余表按外键依赖顺序删除
    for table in E2E_PURGE_ORDER:
        sql = text(f"DELETE FROM {table} WHERE tenant_id = :tid")  # noqa: S608 - 表名来自常量
        deleted[table] = db.execute(sql, {"tid": tenant_id}).rowcount or 0

    db.commit()
    return deleted


def e2e_login_hint() -> dict:
    """返回 E2E 登录凭据（供脚本与文档复用，避免凭据散落多处）。"""
    return {"email": E2E_EMAIL, "password": E2E_PASSWORD}

