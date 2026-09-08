"""recommendation_engine.py — v6.5 建议层（风险处置）

架构（与裁决解耦，绝不反向影响 R01-R12 判定）：
    Risk Adjudication（v6.4 冻结） → R01-R12
        ↓
    Recommendation Context（Evidence + 护栏）
        ↓
    LLM 生成人话（风险说明/修改建议/修改示例）
        ↓
    Grounding Check（拦截编造数字/比例/金额/期限/法条）
        ↓
    法律依据（RULE_LAWS，确定性）

原则：LLM 负责"怎么告诉用户、怎么改"，但不给它创造事实的权限。
"""
import json
import re
import logging
from pathlib import Path

from ai.llm_client import llm_client
from ai.utils import extract_json
from ai.auditor.rule_engine import RULE_LAWS

logger = logging.getLogger(__name__)

_LAWS_CACHE = None


def _load_laws() -> list:
    """惰性加载 laws.json（按法条编号精确取条文用）。"""
    global _LAWS_CACHE
    if _LAWS_CACHE is None:
        try:
            p = Path(__file__).resolve().parent.parent / "knowledge" / "laws.json"
            _LAWS_CACHE = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("加载 laws.json 失败: %s", e)
            _LAWS_CACHE = []
    return _LAWS_CACHE


def _digit_to_cn(n: int) -> str:
    """阿拉伯数字 → 中文数字（法条编号，1-999）。"""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    if n < 1000:
        s = digits[n // 100] + "百"
        rest = n % 100
        if rest == 0:
            return s
        if rest < 10:
            return s + "零" + digits[rest]
        return s + _digit_to_cn(rest)
    return str(n)

RISK_NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当", "R13": "疑似名实不符",
}

# 护栏：LLM 生成建议的边界（不是最终答案，是约束）
RISK_GUARDRAILS = {
    "R01": {"direction": "重新评估违约责任约定是否与可能造成的实际损失相匹配",
            "forbidden": "不得自行新增具体比例、金额、期限；不得声称某比例必然违法"},
    "R02": {"direction": "明确赔偿责任范围并设置合理上限",
            "forbidden": "不得自行新增具体比例、金额、期限"},
    "R03": {"direction": "增加解除合同的前置条件与补偿安排",
            "forbidden": "不得自行新增具体期限、金额"},
    "R04": {"direction": "协商将管辖调整为我方所在地或中立地点",
            "forbidden": "不得自行新增具体法院名称、地域"},
    "R05": {"direction": "明确保密期限与信息公开边界",
            "forbidden": "不得自行新增具体年限"},
    "R06": {"direction": "明确成果归属与对价安排，平衡双方权益",
            "forbidden": "不得自行新增具体对价金额"},
    "R07": {"direction": "调整预付款比例与付款节点，使付款与履约进度匹配",
            "forbidden": "不得自行新增具体付款比例"},
    "R08": {"direction": "补充明确的验收标准、程序与责任主体",
            "forbidden": "不得自行新增具体技术指标"},
    "R09": {"direction": "补充不可抗力的定义、通知与免责安排",
            "forbidden": "不得自行新增具体事件、期限"},
    "R10": {"direction": "将竞业限制限定为同行业、合理地域与期限",
            "forbidden": "不得自行新增具体年限、地域"},
    "R11": {"direction": "增加期满前书面通知的退出机制",
            "forbidden": "不得自行新增具体通知期限"},
    "R12": {"direction": "增加数据使用限制、用户授权与安全保护条款",
            "forbidden": "不得自行新增具体数据字段"},
    "R13": {"direction": "人工核实合同真实性质，按真实法律关系重新定性",
            "forbidden": "不得自行判定具体属于哪类名实不符"},
}

# 风险类型 → evidence 字段名（用于抽取该风险的具体合同事实喂给 LLM）
EVIDENCE_KEYS = {
    "R01": "R01_违约金", "R02": "R02_责任", "R03": "R03_单方权利", "R04": "R04_管辖",
    "R05": "R05_保密", "R06": "R06_IP", "R07": "R07_付款", "R08": "R08_验收",
    "R09": "R09_不可抗力", "R10": "R10_竞业", "R11": "R11_续约", "R12": "R12_数据",
    "R13": "R13_名实不符",
}

SYSTEM_PROMPT_RECOMMENDATION = """你是合同审核建议生成助手。基于已由确定性规则裁决的风险类型 + 已抽取的合同事实（Evidence）+ 检索到的法律依据（legal_basis），为用户生成个性化的合同修改建议。

对每条风险，输出三段：
1. risk_description（风险说明）：引用 Evidence 中的具体合同事实，说明"为什么被标出来"；
2. suggestion（修改建议）：可执行的处理方向（遵循给定的 direction 与检索到的法律依据）；
3. example（修改示例）：一条示例条款，末尾必须加"（示例仅供参考，不构成唯一修改方案）"。

硬约束：
- 只基于 Evidence 与 legal_basis 中的事实/法条生成内容，**绝不新增其中没有的数字、比例、金额、期限、法条编号、合同主体或合同事实**；
- example 中不写具体数字/比例（如"百分之十""20%"），只用方向性表述（如"以实际损失为限""设置合理上限"）；
- 不改变风险类型、不下"是否构成风险"的结论（已由裁决层确定）；
- 遵循 forbidden 约束。

输入是 JSON 数组（每条一个风险，含 risk_type/risk_name/evidence/rule_conclusion/direction/forbidden/legal_basis），输出对应 JSON 数组（顺序一致）：
[{"risk_type":"R01","risk_description":"...","suggestion":"...","example":"..."}, ...]"""


def _evidence_for(risk_type: str, evidence: dict) -> dict:
    key = EVIDENCE_KEYS.get(risk_type, "")
    return evidence.get(key, {}) if key else {}


def _generate_recommendations(contexts: list[dict]) -> list:
    """批量调用 LLM 生成建议，返回与 contexts 顺序一致的 list；失败返回 []。"""
    if not contexts:
        return []
    payload = json.dumps(contexts, ensure_ascii=False)
    for attempt in range(2):  # 超时重试一次
        try:
            resp = llm_client.chat(prompt=SYSTEM_PROMPT_RECOMMENDATION + "\n\n" + payload, temperature=0.0)
            arr = extract_json(resp)
            if isinstance(arr, list):
                return arr
        except Exception as e:
            logger.warning("建议层 LLM 生成失败(第%d次): %s", attempt + 1, e)
    return []


def recommendation_grounding_check(rec: dict, evidence: dict, rag_text: str = "") -> dict:
    """接地检查：建议中出现的数字/比例必须能在 evidence / legal_basis / RAG 检索内容 找到来源。"""
    text = " ".join(str(rec.get(k, "")) for k in ("risk_description", "suggestion", "example"))
    legal = rec.get("legal_basis", "") or ""
    ev_text = (json.dumps(evidence, ensure_ascii=False) if evidence else "") + (rag_text or "")
    issues = []
    # 阿拉伯数字/比例
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([%％‰]|天|日|月|年|万|亿|元|条)?", text):
        num = m.group(1)
        if num in legal or num in ev_text:
            continue
        issues.append(f"数字/比例「{m.group(0).strip()}」无证据来源")
    # 中文数字/比例（如 百分之十、千分之五、三十日）——字符类纳入「分之」，避免「百分之十」被拆成单字漏拦（BUG-021-a）
    for m in re.finditer(r"([零一二三四五六七八九十百千万亿分之]+)", text):
        cn = m.group(1)
        if len(cn) < 2:  # 单字（如"一"）干扰大，跳过
            continue
        if cn in legal or cn in ev_text:
            continue
        issues.append(f"中文数字「{cn}」无证据来源")
    return {"passed": len(issues) == 0, "issues": issues}


def _template_fallback(risk_type: str) -> dict:
    """LLM 失败时的规则模板兜底。"""
    return {
        "risk_description": f"合同存在{RISK_NAMES.get(risk_type, risk_type)}风险",
        "suggestion": RISK_GUARDRAILS.get(risk_type, {}).get("direction", "建议进一步核实并完善相关条款"),
        "example": "（示例仅供参考，不构成唯一修改方案）",
    }


def _retrieve_legal(risk_type: str, risk_name: str, clause_text: str) -> str:
    """按 RULE_LAWS 的法条编号精确取条文；取不到时回退语义检索。"""
    law_ref = RULE_LAWS.get(risk_type, "")
    hits = []
    # 精确匹配：law 名 + 条文编号
    for m in re.finditer(r"([\u4e00-\u9fff]+法)第([\d\-\/、]+)条", law_ref):
        law_name = m.group(1)
        nums = re.findall(r"\d+", m.group(2))
        for e in _load_laws():
            if e.get("law") == law_name and e.get("article") in (f"第{_digit_to_cn(int(n))}条" for n in nums):
                hits.append(e.get("content", ""))
    if hits:
        return "\n".join(hits)
    # 法条不在 laws.json（161 条精编库）→ 返回法条编号引用，LLM 凭自身法律知识组织，不做语义检索（避免漂移）
    return law_ref


def build_recommendations(risks: list[dict], evidence: dict) -> list[dict]:
    """为每条风险补充建议层字段（不改变 risk_type/level 等裁决字段）。"""
    if not risks:
        return risks

    contexts = []
    rag_by_type = {}
    for r in risks:
        rt = r.get("risk_type", "")
        guard = RISK_GUARDRAILS.get(rt, {})
        rag_text = _retrieve_legal(rt, RISK_NAMES.get(rt, rt), r.get("clause_text", ""))
        rag_by_type[rt] = rag_text
        contexts.append({
            "risk_type": rt,
            "risk_name": RISK_NAMES.get(rt, rt),
            "evidence": _evidence_for(rt, evidence),
            "rule_conclusion": f"命中 {rt} {RISK_NAMES.get(rt, '')}",
            "direction": guard.get("direction", ""),
            "forbidden": guard.get("forbidden", ""),
            "legal_basis": RULE_LAWS.get(rt, ""),
            "rag_legal_basis": rag_text,
        })

    generated = _generate_recommendations(contexts)

    enriched = []
    for i, r in enumerate(risks):
        rt = r.get("risk_type", "")
        legal_basis = RULE_LAWS.get(rt, "")
        # 按索引取（顺序一致），并校验 risk_type 匹配；缺失/不匹配则回退模板（BUG-022，避免同类型多条建议错位）
        rec = _template_fallback(rt)
        if i < len(generated) and isinstance(generated[i], dict) and generated[i].get("risk_type") == rt:
            rec = generated[i]
        # 空建议/空说明回退到护栏 direction（避免 LLM 部分失败时输出空）
        if not (rec.get("suggestion") or "").strip():
            rec = dict(rec)
            rec["suggestion"] = RISK_GUARDRAILS.get(rt, {}).get("direction", "建议进一步核实并完善相关条款")
        if not (rec.get("risk_description") or "").strip():
            rec = dict(rec)
            rec["risk_description"] = f"合同存在{RISK_NAMES.get(rt, rt)}风险"
        rec_obj = {
            "risk_description": rec.get("risk_description", ""),
            "suggestion": rec.get("suggestion", ""),
            "example": rec.get("example", ""),
            "legal_basis": legal_basis,
        }
        grounding = recommendation_grounding_check(rec_obj, evidence, rag_by_type.get(rt, ""))
        r = dict(r)
        r["risk_description"] = rec_obj["risk_description"]
        r["suggestion"] = rec_obj["suggestion"]
        r["example"] = rec_obj["example"]
        r["legal_basis"] = legal_basis
        r["grounding"] = grounding
        enriched.append(r)
    return enriched
