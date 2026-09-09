"""evidence_adjudicator.py — 生产用：证据抽取 + 确定性裁决（v6.4 架构）

对 extract_evidence 产出的结构化证据，按 v1.2.4 硬阈值裁决 R01-R12，
输出完整风险对象（risk_type/level/clause_text/reason/suggestion/confidence/related_law）。
与 evaluate/evaluate_evidence.py 的裁决规则保持一致。
"""
import logging
from ai.confidence import rule_confidence
from ai.auditor.rule_engine import RULE_LAWS

logger = logging.getLogger(__name__)

RISK_NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当",
}

SUGGESTIONS = {
    "R01": "违约金标准达到系统设定的风险阈值，建议结合实际损失与履约风险重新评估违约金比例（民法典第585条）",
    "R02": "赔偿责任范围超出可预见规则，建议明确赔偿范围为直接损失并设定责任上限（民法典第584条）",
    "R03": "存在单方任意解除权且无补偿，建议增加解除条件和补偿条款（民法典第933/787条）",
    "R04": "管辖约定偏向对方，建议协商调整为我方所在地或中立地点（民事诉讼法第35条）",
    "R05": "保密义务约定无限期，建议明确保密期限与信息公开边界（民法典第501条）",
    "R06": "知识产权权益失衡，建议明确成果归属与对价安排（民法典第859条）",
    "R07": "付款安排与履约贡献失衡，建议调整预付款比例与付款节点",
    "R08": "缺少客观验收标准，建议补充明确的验收标准、程序与责任主体",
    "R09": "缺少不可抗力条款，建议补充不可抗力的定义、通知与免责安排（民法典第590条）",
    "R10": "竞业限制过宽，建议限定为同行业、合理地域与期限",
    "R11": "自动续约缺少退出机制，建议增加期满前书面通知条款",
    "R12": "数据处理缺少授权边界，建议增加数据使用限制、用户授权与安全保护条款",
}

LEVELS = {
    "R01": "high", "R02": "high", "R03": "medium", "R04": "medium",
    "R05": "high", "R06": "high", "R07": "high", "R08": "medium",
    "R09": "medium", "R10": "medium", "R11": "medium", "R12": "high",
}


def _num(v):
    """数值归一化：int/float 直接转 float；str 剥离 %/％/‰ 并换算（"5%"→0.05、"5‰"→0.005）；
    None/空/无法解析返回 None。用于 R01/R07/R10 阈值判定，避免 LLM 输出字符串被 isinstance 静默跳过。"""
    if v is None or isinstance(v, bool) or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if s.endswith("%") or s.endswith("％"):
            try:
                return float(s[:-1].strip()) / 100.0
            except ValueError:
                return None
        if s.endswith("‰"):
            try:
                return float(s[:-1].strip()) / 1000.0
            except ValueError:
                return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _normalize_scope(v):
    """R10 scope 同义归一化：全国范围/全球/境内/不限地域 等 → 全国；主营 → 主营业务。"""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if any(k in s for k in ("全国", "全球", "境内", "不限地域", "不限地区", "所有区域")):
        return "全国"
    if "主营" in s:
        return "主营业务"
    if "全行业" in s or "所有行业" in s:
        return "全行业"
    return s


def _normalize_mode(v):
    """R11 mode 中英同义归一化：中文「沉默/自动续约」→ silence_auto_renewal。"""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if s in ("silence_auto_renewal", "沉默自动续约", "沉默续约", "自动续约", "到期自动续", "期满自动续"):
        return "silence_auto_renewal"
    if s in ("主动续签", "主动续约", "active_renewal"):
        return "主动续签"
    return s


def _clause(txt):
    return (txt or "").strip()


def _reason(risk_type):
    reasons = {
        "R01": "违约金比例可能过高，根据《民法典》第585条，违约金超过造成损失的30%可被法院调减",
        "R02": "出现无限责任/全部损失表述，赔偿责任超出可预见范围（《民法典》第584条）",
        "R03": "赋予单方任意解除权，对守约方不公，建议增加解除条件和补偿条款",
        "R04": "管辖法院约定在对方所在地，增加我方诉讼成本",
        "R05": "保密期限为永久/无限期，可能因不合理而无效",
        "R06": "知识产权归属约定不清，默认归对方所有对我不利",
        "R07": "付款条件对乙方不利，预付款比例过高或尾款支付条件苛刻",
        "R08": "合同未定义验收标准，可能在交付时产生争议",
        "R09": "缺少不可抗力条款，一旦发生不可抗力事件将无法免责",
        "R10": "竞业限制范围过宽，可能因不合理而被认定无效",
        "R11": "自动续约无提前通知机制，可能被动续约产生额外成本",
        "R12": "涉及数据共享但未定义保护条款，存在合规风险",
    }
    return reasons.get(risk_type, f"合同存在{risk_type}类型风险")


def adjudicate_risks(evidence: dict) -> list[dict]:
    """把结构化证据裁决为完整风险对象列表（已去重）。"""
    if not isinstance(evidence, dict):
        return []
    risks = []
    is_delivery = bool(evidence.get("is_delivery_type"))

    def add(t, clause_text, detection="evidence"):
        risks.append({
            "risk_type": t,
            "level": LEVELS.get(t, "medium"),
            "name": RISK_NAMES.get(t, t),
            "clause_text": _clause(clause_text),
            "reason": _reason(t),
            "suggestion": SUGGESTIONS.get(t, ""),
            "detection_method": detection,
            "confidence": rule_confidence(t),
            "related_law": RULE_LAWS.get(t, ""),
        })

    # R01 违约金过高：日 ≥ 5‰
    r = evidence.get("R01_违约金") or {}
    if r.get("exists") and r.get("unit") == "daily":
        rate = _num(r.get("rate"))
        if rate is not None and rate >= 0.005:
            # clause_text 只用逐字原文；不再用 basis（概念）或"违约金条款"（兜底描述）当定位文本
            add("R01", r.get("clause_text") or "")

    # R02 无限责任：scope/absolute_text 含超可预见标记
    r = evidence.get("R02_责任") or {}
    scope_text = (r.get("scope") or "") + " " + (r.get("absolute_text") or "")
    if any(k in scope_text for k in ("预期", "间接", "无限", "一切", "全部损失")):
        # clause_text 只用逐字 absolute_text；不再用"赔偿责任条款"兜底描述定位
        add("R02", r.get("absolute_text") or "")

    # R03 单方解约：termination/suspension/change 任一 任意+无补偿
    r = evidence.get("R03_单方权利") or {}
    for key in ("termination", "suspension", "change"):
        sub = r.get(key) or {}
        if sub.get("arbitrary") and sub.get("no_compensation"):
            add("R03", sub.get("clause_text") or "单方权利条款")
            break

    # R04 管辖不利：location_party == 乙方
    r = evidence.get("R04_管辖") or {}
    if r.get("location_party") == "乙方":
        add("R04", r.get("location_text") or r.get("evidence") or "管辖条款")

    # R05 保密期：永久/无限/不因终止 且 非法定义务
    r = evidence.get("R05_保密") or {}
    if r.get("duration") in ("永久", "无限", "不因终止而终止") and not r.get("statutory_basis"):
        add("R05", r.get("duration_evidence") or "保密条款")

    # R06 IP 失衡：无对价 + 有失衡事实
    r = evidence.get("R06_IP") or {}
    if not r.get("has_consideration") and (r.get("imbalance_text") or "").strip():
        add("R06", r.get("imbalance_text") or "知识产权条款")

    # R07 付款失衡：预付≥80% 或 尾款≥30% 且无里程碑
    r = evidence.get("R07_付款") or {}
    prepay = _num(r.get("prepay_ratio"))
    tail = _num(r.get("tail_ratio"))
    if prepay is not None and prepay >= 0.8:
        add("R07", r.get("payment_evidence") or "付款条款")
    elif tail is not None and tail >= 0.3 and not r.get("has_milestone"):
        add("R07", r.get("payment_evidence") or "付款条款")

    # R08 验收缺失：交付型 + 无客观依据
    r = evidence.get("R08_验收") or {}
    objective = bool(r.get("objective_basis")) and bool((r.get("basis_evidence") or "").strip())
    if is_delivery and not objective:
        add("R08", r.get("basis_evidence") or "全文未定义验收标准或验收方式")

    # R09 不可抗力缺失：交付型 + 无含机制词条款
    r = evidence.get("R09_不可抗力") or {}
    evidence_text = r.get("force_majeure_evidence") or ""
    mechanism = any(w in evidence_text for w in ("通知", "免责", "不承担责任", "解除", "顺延", "延期"))
    valid = bool(r.get("has_force_majeure")) and "不可抗" in evidence_text and mechanism
    if is_delivery and not valid:
        add("R09", evidence_text or "全文未出现不可抗力相关条款")

    # R10 竞业过宽：≥5年 + 全国/主营/全行业（scope 同义归一化）
    r = evidence.get("R10_竞业") or {}
    if r.get("has_noncompete"):
        dur = _num(r.get("duration_years"))
        scope = _normalize_scope(r.get("scope"))
        if dur is not None and dur >= 5 and scope in ("全国", "主营业务", "全行业"):
            add("R10", r.get("clause_text") or "竞业条款")

    # R11 自动续约：沉默自动续约（mode 中英同义归一化）
    r = evidence.get("R11_续约") or {}
    if _normalize_mode(r.get("mode")) == "silence_auto_renewal":
        add("R11", r.get("clause_text") or "续约条款")

    # R12 数据隐私：有个人信息处理 + 无授权边界
    r = evidence.get("R12_数据") or {}
    if r.get("has_personal_data") and not r.get("has_authorization_boundary"):
        add("R12", r.get("clause_text") or "数据条款")

    # 去重（同 risk_type 同条款）
    seen = set()
    deduped = []
    for r in risks:
        key = (r["risk_type"], r["clause_text"][:40])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped
