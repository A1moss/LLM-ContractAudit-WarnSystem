"""
置信度三源校准模块（Confidence Calibration）

背景：
    原系统里规则引擎返回的风险没有 confidence 字段，入库时在 api/contracts.py
    统一兜底为 0.8（硬编码）；LLM 审核在模型未返回置信度时兜底 0.7。这导致
    "所有风险置信度看起来都差不多"，无法区分高可信风险与需人工复核的风险
    —— 这是验收时被老师点名的"最大漏洞"。

方案：三源校准
    不再写死单一默认值，而是由三个来源分别产出置信度，再做跨来源融合：

    1) 规则引擎置信度 (rule_confidence)
       —— 确定型规则：显式正则/关键词命中，置信度较高但非 1.0（正则存在误报）
       —— 缺失型规则：R08/R09 依赖"某词未出现"，证据是缺席，置信度较低

    2) LLM 语义置信度 (llm_confidence)
       —— 直接采用 LLM 在 temperature=0.1 下输出的 confidence 字段
       —— 模型未给出时给中性 0.6（诚实标注不确定性），不再假装 0.7

    3) 多 Agent 一致性置信度 (agent_confidence)
       —— 法务/合规/财务多个 Agent 对同一风险的一致程度（投票一致性，
          参考 multi-agent verification / ensemble calibration 思想）

    跨来源融合 (enrich_confidences)
       —— 同一风险被多个来源（规则 + LLM + 多 Agent）独立检出时，视为
          交叉验证，置信度上调（多源一致 = 更可信）。

论文支撑关键词（答辩引用）：
    LLM confidence calibration / multi-agent verification / ensemble
    calibration / rule-ML hybrid reasoning / PAKTON (multi-agent legal QA)
"""


# 规则引擎：按风险类型给出的确定型置信度。
# 数值阈值型（R01/R07）与强关键词型（R02）确定性最高；
# 缺失型（R08/R09）依赖"某词未出现"，证据是缺席，置信度最低。
RULE_CONFIDENCE = {
    "R01": 0.90,  # 违约金比例：数值阈值 + 正则，确定性高
    "R02": 0.92,  # 无限责任：强关键词命中，确定性高
    "R03": 0.85,  # 单方解约权：句式匹配，较高
    "R04": 0.85,  # 管辖不利：句式匹配
    "R05": 0.88,  # 保密期限不合理：关键词命中
    "R06": 0.86,  # 知识产权归属：句式匹配
    "R07": 0.90,  # 付款不公：数值阈值，确定性高
    "R08": 0.72,  # 验收缺失：基于"未出现"，证据是缺席，较低
    "R09": 0.72,  # 不可抗力缺失：同上
    "R10": 0.85,  # 竞业限制过宽：关键词 + 年限
    "R11": 0.88,  # 自动续约：关键词命中
    "R12": 0.88,  # 数据隐私：关键词命中
    "R13": 0.85,  # 名实不符：名义+实质信号组合，仅示警，具体定性交人工
}
DEFAULT_RULE_CONFIDENCE = 0.80

# LLM 未返回置信度时的中性默认值（诚实标注不确定性）
LLM_FALLBACK_CONFIDENCE = 0.60


def clamp_confidence(value, low: float = 0.05, high: float = 0.99) -> float:
    """把置信度夹到 [low, high]，避免出现无意义的 0 或 1。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, v))


def rule_confidence(risk_type: str) -> float:
    """规则引擎风险的确定型置信度，按风险类型查表。"""
    return RULE_CONFIDENCE.get(risk_type, DEFAULT_RULE_CONFIDENCE)


def agent_confidence(agreements: int, total: int = 4) -> float:
    """多 Agent 一致性置信度：按一致 Agent 占比线性映射到 [0.5, 0.95]。

    一个 Agent 单独检出 → 0.5 左右（存在单视角偏差）；
    4 个来源全部一致 → 0.95（高度可信）。
    """
    if total <= 0:
        return 0.5
    ratio = max(0.0, min(1.0, agreements / total))
    return round(0.5 + 0.45 * ratio, 3)


def _risk_key(risk: dict) -> tuple:
    """风险聚类键：风险类型 + 原文前 30 字（归一化换行/空白）。"""
    clause = (risk.get("clause_text") or "").replace("\n", " ").strip()
    return (risk.get("risk_type", ""), clause[:30])


def enrich_confidences(risks: list[dict]) -> list[dict]:
    """跨来源置信度融合（原地修改并返回同一批 risk）。

    第一步：给缺少 confidence 的风险按来源补基础置信度；
    第二步：同一 (risk_type, 原文前30字) 被 2 个及以上来源检出时，
             视为交叉验证，置信度上调。
    """
    # 第一步：按来源补基础置信度
    for r in risks:
        if not isinstance(r, dict):
            continue
        if r.get("confidence") is not None:
            r["confidence"] = clamp_confidence(r["confidence"])
        else:
            method = r.get("detection_method", "")
            if method == "rule":
                r["confidence"] = rule_confidence(r.get("risk_type", ""))
            else:
                # llm / rag / corex 等语义来源，未给出时用中性默认值
                r["confidence"] = LLM_FALLBACK_CONFIDENCE

    # 第二步：按 (risk_type, clause) 聚类，统计来源数量，做交叉验证上调
    groups: dict = {}
    order: list = []
    for r in risks:
        if not isinstance(r, dict):
            continue
        key = _risk_key(r)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    for key in order:
        items = groups[key]
        methods = {it.get("detection_method", "unknown") for it in items}
        n_sources = len(methods)
        for it in items:
            base = clamp_confidence(it.get("confidence"))
            if n_sources >= 3:
                boosted = min(0.98, base + 0.12)   # 规则 + LLM + Agent 三方一致
            elif n_sources == 2:
                boosted = min(0.95, base + 0.07)   # 两方独立交叉验证
            else:
                boosted = base
            it["confidence"] = round(boosted, 3)

    return risks
