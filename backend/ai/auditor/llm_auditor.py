from ai.llm_client import llm_client
from ai.auditor.stance import build_stance
import json
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_AUDIT = """你是一位资深合同审核律师。请对以下合同条款逐条审核，识别以下 12 类风险。

风险类型与量化标准：
R01 违约金过高 — 我方承担的违约金 > 合同金额20% 为高风险，10-20% 为中风险；参考《民法典》第585条
R02 无限责任 — 我方承担"全部损失""一切责任""无限"表述 → 高风险
R03 单方解约权 — 仅对方有任意解除权（或我方被剥夺解除权）→ 中风险
R04 管辖条款不利 — 管辖法院约定在对方所在地或对我方诉讼不便 → 中风险。约定在我方所在地不构成风险。
R05 保密期间不合理 — 我方承担的永久/无限期保密 → 高风险，我方保密义务 >5年 → 中风险
R06 知识产权归属不清 — 未明确归属或约定知识产权归对方 → 高风险。归我方或双方共有不构成风险。
R07 付款条件不公平 — 我方预付 > 合同金额50% → 中风险，我方验收后不付尾款 → 高风险
R08 验收标准缺失 — 合同未定义验收标准或标准由对方单方决定 → 中风险
R09 不可抗力条款缺失 — 未出现"不可抗力"相关表述 → 中风险
R10 竞业限制过宽 — 对我方的竞业限制范围/期限明显不合理 → 中风险
R11 自动续约陷阱 — 期满自动续约且我方无提前通知解约机制 → 中风险
R12 数据隐私缺失 — 涉及我方数据/用户数据但未定义保护条款 → 高风险

风险等级判别标准（统一适用）：
- 高风险：潜在损失超过10万元或违反强制性法律规定
- 中风险：可能引发合同争议或商业风险
- 低风险：表述不够精确但不影响合同效力

请以 JSON 数组格式输出（只输出 JSON，不要加任何前缀或后缀）：
[
  {
    "risk_type": "R01-R12",
    "level": "high/medium/low",
    "clause_text": "被标记的原文片段",
    "reason": "判定理由（含法条引用）",
    "suggestion": "具体修改建议",
    "confidence": 0.0-1.0
  }
]
如果未发现风险，输出空数组 []。"""


def _extract_json(response: str) -> list:
    text = response.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "risks" in result:
            return result["risks"]
    except json.JSONDecodeError:
        pass
    for marker in ["```json", "```"]:
        if marker in text:
            try:
                inner = text.split(marker)[1].split("```")[0]
                result = json.loads(inner.strip())
                if isinstance(result, list):
                    return result
                if isinstance(result, dict) and "risks" in result:
                    return result["risks"]
            except (IndexError, json.JSONDecodeError):
                continue
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def audit_with_llm(full_text: str, rag_context: list = None, our_role: str = "neutral") -> list[dict]:
    truncated = full_text[:4000]

    rag_text = ""
    if rag_context:
        rag_items = []
        for item in rag_context[:5]:
            content = item.get("content", "") if isinstance(item, dict) else str(item)
            rag_items.append(f"- {content[:200]}")
        rag_text = "\n".join(rag_items)

    stance = build_stance(our_role)
    system_prompt = stance + "\n\n" + SYSTEM_PROMPT_AUDIT
    if rag_text:
        # Inject RAG context into the system prompt
        rag_block = (
            f"\n\n参考法条和案例（来自知识库）：\n{rag_text}"
        )
        system_prompt = system_prompt.replace(
            "请以 JSON 数组格式输出",
            rag_block + "\n\n请以 JSON 数组格式输出"
        )

    try:
        response = llm_client.chat(
            prompt=f"{system_prompt}\n\n请审核以下合同：\n{truncated}",
            temperature=0.1,
        )
        risks = _extract_json(response)
        if risks is not None:
            validated = []
            for r in risks:
                if not isinstance(r, dict):
                    continue
                entry = {
                    "risk_type": r.get("risk_type", ""),
                    "level": r.get("level", "medium"),
                    "clause_text": r.get("clause_text", ""),
                    "reason": r.get("reason", ""),
                    "suggestion": r.get("suggestion", ""),
                    "confidence": float(r.get("confidence", 0.7)),
                    "detection_method": "llm",
                }
                entry = _suppress_false_positive(entry)
                entry = _align_risk_type(entry)
                validated.append(entry)
            logger.info(f"LLM 审核完成，检出 {len(validated)} 条风险")
            return validated
    except Exception as e:
        logger.error(f"LLM 审核失败: {e}")

    logger.warning("LLM 审核降级：返回空列表")
    return []


# ── 我方视角假阳性抑制 ──

_R04_OUR_PATTERNS = [
    "我方所在地", "我方住所地", "我方注册地",
    "合同签订地", "合同履行地", "标的物所在地",
]


def _suppress_false_positive(entry: dict, our_role: str = "neutral") -> dict:
    """Post-process: if the LLM flagged a clause that is actually favorable to us,
    lower confidence or drop."""
    risk_type = entry.get("risk_type", "")
    clause = entry.get("clause_text", "")
    reason = entry.get("reason", "")
    combined = clause + reason
    confidence = entry.get("confidence", 0.7)

    if our_role == "neutral":
        return entry  # no suppression in neutral mode

    # R04: 管辖在我方所在地 → 对我方有利，不构成风险
    if risk_type == "R04":
        our_markers = ["我方所在地", "我方住所地", "我方注册地"]
        is_our_side = any(p in combined for p in our_markers)
        if is_our_side:
            logger.warning(
                "[假阳性抑制] R04 管辖条款约定在我方所在地，对我方有利，已降至低风险。"
                "clause=%.60s…", clause
            )
            entry["level"] = "low"
            entry["confidence"] = min(confidence, 0.4)
            entry["reason"] = (reason or "") + "（但管辖在我方所在地，对我方有利，风险极低）"
            entry["detection_method"] = "llm+suppress"
            return entry

    # R06: 知识产权归我方或双方共有 → 对我方有利
    if risk_type == "R06":
        is_ours = any(p in combined for p in ["归我方", "归双方共有", "甲乙双方共有", "双方共有"])
        if is_ours and "归对方" not in combined:
            logger.warning("[假阳性抑制] R06 知识产权归属对我方有利，已降级")
            entry["level"] = "low"
            entry["confidence"] = min(confidence, 0.5)
            entry["detection_method"] = "llm+suppress"

    return entry


# ── 关键词 → 风险类型对齐表 ──
_RISK_KEYWORD_MAP = [
    # (risk_type, must-have keywords in clause_text, keywords that should NOT be present)
    ("R01", ["违约金", "罚金", "罚则", "定金"], []),
    ("R02", ["全部损失", "一切责任", "无限责任", "所有损失"], []),
    ("R03", ["解除", "解约", "终止"], ["不可抗力"]),
    ("R04", ["管辖", "法院", "仲裁", "诉讼"], []),
    ("R05", ["保密", "秘密", "商业秘密"], []),
    ("R06", ["知识产权", "著作权", "专利", "商标", "IP"], []),
    ("R07", ["付款", "支付", "预付", "尾款", "价款"], []),
    ("R08", ["验收"], []),
    ("R09", ["不可抗力"], []),
    ("R10", ["竞业", "不竞争"], []),
    ("R11", ["自动续", "续约", "续期"], []),
    ("R12", ["数据", "隐私", "个人信息"], []),
]


def _align_risk_type(entry: dict) -> dict:
    """Post-process: if the LLM's risk_type label doesn't match the clause content,
    correct it based on keyword alignment and lower confidence."""
    clause = entry.get("clause_text", "")
    reason = entry.get("reason", "")
    current_type = entry.get("risk_type", "")
    confidence = entry.get("confidence", 0.7)

    # Find which risk type has the strongest keyword match with the clause+reason text
    combined = clause + reason
    best_type = None
    best_score = 0

    for rtype, must_have, must_not in _RISK_KEYWORD_MAP:
        # Check must-have keywords
        must_match = any(kw in combined for kw in must_have)
        if not must_match:
            continue
        # Check exclusion keywords
        if any(kw in combined for kw in must_not):
            continue
        score = sum(1 for kw in must_have if kw in combined)
        if score > best_score:
            best_score = score
            best_type = rtype

    if best_type and best_type != current_type:
        logger.warning(
            "[标签对齐] LLM 输出 risk_type=%s，但条款内容指向 %s（clause=%.60s…），已自动修正",
            current_type, best_type, clause
        )
        entry["risk_type"] = best_type
        # Lower confidence — we're overriding the LLM's judgment
        entry["confidence"] = min(confidence, 0.7)
        entry["detection_method"] = "llm+align"

    return entry
