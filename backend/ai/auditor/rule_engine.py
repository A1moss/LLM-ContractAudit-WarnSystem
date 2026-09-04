import re
import logging

from ai.confidence import rule_confidence

logger = logging.getLogger(__name__)

RISK_RULES = [
    ("R01", r"违约.*?(\d{1,3})\s*[%\％]", "high", "违约金过高",
     "建议将违约金比例修改为不超过合同金额的20%，参考《民法典》第585条"),
    ("R02", r"(全部损失|一切责任|无限责任|所有损失|任何.*?损失)", "high", "无限责任条款",
     "建议将赔偿责任限制为直接损失，并以合同金额为上限"),
    ("R03", r"(甲方.*?有权.*?随时.*?(解除|解约|终止)|任意.*?(解除|解约|终止).*?权)", "medium", "单方解约权",
     "建议增加解除合同的前置条件和补偿条款"),
    ("R04", r"管辖.*?(被告|对方|乙方).*?法院", "medium", "管辖条款不利",
     "建议将管辖法院约定为我方所在地法院"),
    ("R05", r"保密.*?(永久|无限期|长期|终身)", "high", "保密期间不合理",
     "建议明确保密期限为合同终止后3-5年"),
    ("R06", r"知识产权.*?归属.*?(对方|乙方)", "high", "知识产权归属不清",
     "建议明确知识产权归属我方或双方共有"),
    ("R07", r"(预付.*?(\d{2,})\s*[%\％]|验收.*?(后|完成).*?(不.*?付|无.*?付))", "high", "付款条件不公平",
     "建议预付款不超过30%，验收后分期支付尾款"),
    ("R08", None, "medium", "验收标准缺失",
     "建议在合同中增加明确的验收标准条款"),
    ("R09", None, "medium", "不可抗力条款缺失",
     "建议增加不可抗力条款，明确范围和免责条件"),
    ("R10", r"竞业.*?(所有行业|全国|永久|终身|(\d{2,})\s*年)", "medium", "竞业限制过宽",
     "建议将竞业限制限定为同行业、合理地域、不超过2年"),
    ("R11", r"期满.*?自动续", "medium", "自动续约陷阱",
     "建议增加合同期满前30日书面通知是否续约的条款"),
    ("R12", r"(数据|隐私|个人信息).*?(共享|提供|转让|披露)", "high", "数据隐私条款不当",
     "建议增加数据使用限制、用户授权和安全保护义务条款"),
    ("R13", None, "high", "疑似名实不符（真假合同）",
     "建议人工核实合同名义与实质是否一致，按真实法律关系重新定性"),
]

# 名实不符检测信号（名义信号 + 实质信号）：任一「名义+实质」同时命中即示警。
# 只负责"检测/示警"，具体是哪种名实不符（假外包真派遣/名为买卖实为借贷/明股实债等）交人工判定。
NAME_REALITY_SIGNALS = [
    (r"服务外包|业务外包|人力外包|外包", r"劳务派遣|派遣单位|用工单位|被派遣"),
    (r"买卖|购销|采购|销售", r"回购|保底|固定回报|固定收益|年化|本息"),
    (r"投资|入股|增资|股权", r"固定收益|到期回购|保本|固定分红|年化"),
    (r"合作|联营|联合经营", r"固定租金|保底收益|固定费用"),
]

SAFE_PATTERNS = [
    (r"违约金.*?(不超过|不高于|≤|≤).*?(\d{1,2})\s*[%\％]", "R01"),
    (r"保密.*?(合同.*?终止.*?\d\s*年|期满.*?\d\s*年)", "R05"),
    (r"知识产权.*?归属.*?(甲方|我方|双方共有)", "R06"),
    (r"不可抗力.*?(包括|含|如下|范围)", "R09"),
    (r"商业秘密.*?(合同.*?终止.*?\d\s*年|期满.*?\d\s*年)", "R05"),
    (r"违约金.*?(实际损失|直接损失).*?(为限|为上限)", "R01"),
]

# 12 类风险 → 法条映射（用于可溯源证据链）
RULE_LAWS = {
    "R01": "民法典第585条",
    "R02": "民法典第506条",
    "R03": "民法典第562/563条",
    "R04": "民事诉讼法第35条",
    "R05": "民法典第509条",
    "R06": "民法典第859/860/861条",
    "R07": "民法典第511/525/526条",
    "R08": "民法典第510/511条",
    "R09": "民法典第590条",
    "R10": "劳动合同法第24条",
    "R11": "民法典第563/564条",
    "R12": "个人信息保护法第23条",
    "R13": "民法典第146条",
}


def _is_safe(clause_text: str, risk_type: str) -> bool:
    for pattern, rtype in SAFE_PATTERNS:
        if rtype == risk_type and re.search(pattern, clause_text):
            return True
    return False


def _extract_context(text: str, match: re.Match, padding: int = 30) -> str:
    start = max(0, match.start() - padding)
    end = min(len(text), match.end() + padding)
    return text[start:end].replace("\n", " ")


def _refine_level(rule_id: str, clause_text: str, default_level: str) -> str:
    if rule_id == "R01":
        m = re.search(r"(\d{1,3})\s*[%\％]", clause_text)
        if m:
            pct = int(m.group(1))
            if pct > 20:
                return "high"
            elif pct >= 10:
                return "medium"
            return "low"
    return default_level


def _build_reason(rule_id: str, clause_text: str, level: str) -> str:
    reasons = {
        "R01": "违约金比例可能过高，根据《民法典》第585条，违约金超过造成损失的30%可被法院调减",
        "R02": "出现无限责任/全部损失表述，可能违反公平原则（《民法典》第506条）",
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
        "R13": "合同名义与实质可能不符，构成脱法行为风险，请人工核实真实性质",
    }
    return reasons.get(rule_id, f"合同存在{rule_id}类型风险")


def run_rules(text: str) -> list[dict]:
    if not text or not text.strip():
        return []

    results = []
    seen = set()

    for rule_id, pattern, level, name, suggestion in RISK_RULES:
        if pattern is not None:
            for match in re.finditer(pattern, text):
                ctx = _extract_context(text, match)
                if _is_safe(ctx, rule_id):
                    continue
                final_level = _refine_level(rule_id, ctx, level)
                dedup_key = (rule_id, ctx[:40])
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    results.append({
                        "risk_type": rule_id,
                        "level": final_level,
                        "name": name,
                        "clause_text": ctx.strip(),
                        "reason": _build_reason(rule_id, ctx, final_level),
                        "suggestion": suggestion,
                        "detection_method": "rule",
                        "confidence": rule_confidence(rule_id),
                        "related_law": RULE_LAWS.get(rule_id, ""),
                    })
        elif rule_id == "R08":
            if "验收" not in text and "验收标准" not in text and "验收方式" not in text:
                results.append({
                    "risk_type": "R08", "level": "medium", "name": "验收标准缺失",
                    "clause_text": "全文未定义验收标准或验收方式",
                    "reason": "合同未定义验收标准和验收流程",
                    "suggestion": suggestion, "detection_method": "rule",
                    "confidence": rule_confidence("R08"),
                    "related_law": RULE_LAWS.get("R08", ""),
                })
        elif rule_id == "R09":
            if "不可抗力" not in text:
                results.append({
                    "risk_type": "R09", "level": "medium", "name": "不可抗力条款缺失",
                    "clause_text": "全文未出现不可抗力相关条款",
                    "reason": "合同缺少不可抗力条款，一旦发生不可抗力事件将无法免责",
                    "suggestion": suggestion, "detection_method": "rule",
                    "confidence": rule_confidence("R09"),
                    "related_law": RULE_LAWS.get("R09", ""),
                })
        elif rule_id == "R13":
            # 名实不符：任一「名义+实质」信号对同时命中即示警（具体定性交人工）
            for nominal_re, actual_re in NAME_REALITY_SIGNALS:
                if re.search(nominal_re, text) and re.search(actual_re, text):
                    results.append({
                        "risk_type": "R13", "level": "high", "name": name,
                        "clause_text": "合同同时出现名义类型与实质内容不一致的特征",
                        "reason": "合同名义与实质可能不符（如名为外包实为派遣、名为买卖实为借贷等），请人工核实合同真实性质",
                        "suggestion": suggestion, "detection_method": "rule",
                        "confidence": rule_confidence("R13"),
                        "related_law": RULE_LAWS.get("R13", ""),
                    })
                    break

    logger.info(f"规则引擎检出 {len(results)} 条风险")
    return results
