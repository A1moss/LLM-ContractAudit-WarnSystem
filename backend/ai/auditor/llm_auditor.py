from ai.llm_client import llm_client
from ai.confidence import clamp_confidence, LLM_FALLBACK_CONFIDENCE
import json
import logging

logger = logging.getLogger(__name__)

# 送入 LLM 的合同文本上限（字符）。超出会截断并告警；
# 更彻底的分块（chunking）方案见 TODO，避免单次上下文超限。
MAX_AUDIT_CHARS = 12000

SYSTEM_PROMPT_AUDIT = """你是一位资深合同审核律师。请对以下合同条款逐条审核，识别以下 12 类风险。

风险类型与量化标准：
R01 违约金过高 — 违约金 >20% 高风险，10-20% 中风险；参考《民法典》第585条
R02 无限责任 — "全部损失""一切责任""无限"表述 → 高风险
R03 单方解约权 — 仅一方有任意解除权 → 中风险
R04 管辖条款不利 — 管辖法院约定在对方所在地 → 中风险
R05 保密期间不合理 — 永久/无限期保密 → 高风险，>5年 → 中风险
R06 知识产权归属不清 — 未明确归属或默认归对方 → 高风险
R07 付款条件不公平 — 预付>50% → 中风险，验收后不付尾款 → 高风险
R08 验收标准缺失 — 合同未定义验收标准 → 中风险
R09 不可抗力条款缺失 — 未出现"不可抗力" → 中风险
R10 竞业限制过宽 — 全国/所有行业/>2年 → 中风险
R11 自动续约陷阱 — 期满自动续约且无提前通知机制 → 中风险
R12 数据隐私缺失 — 涉及数据但无保护条款 → 高风险

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


def audit_with_llm(full_text: str, rag_context: list = None) -> list[dict]:
    truncated = full_text[:MAX_AUDIT_CHARS]
    if len(full_text) > MAX_AUDIT_CHARS:
        logger.warning("合同文本 %d 字超过上限 %d，尾部内容未参与 LLM 审核", len(full_text), MAX_AUDIT_CHARS)

    rag_text = ""
    if rag_context:
        rag_items = []
        for item in rag_context[:5]:
            content = item.get("content", "") if isinstance(item, dict) else str(item)
            rag_items.append(f"- {content[:200]}")
        rag_text = "\n".join(rag_items)

    system_prompt = SYSTEM_PROMPT_AUDIT
    if rag_text:
        system_prompt = SYSTEM_PROMPT_AUDIT.replace(
            "请以 JSON 数组格式输出",
            f"参考法条和案例（来自知识库）：\n{rag_text}\n\n请以 JSON 数组格式输出"
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
                validated.append({
                    "risk_type": r.get("risk_type", ""),
                    "level": r.get("level", "medium"),
                    "clause_text": r.get("clause_text", ""),
                    "reason": r.get("reason", ""),
                    "suggestion": r.get("suggestion", ""),
                    # LLM 自报置信度优先；缺失时给中性 0.6（诚实标注不确定性），不再硬编码 0.7
                    "confidence": clamp_confidence(r.get("confidence", LLM_FALLBACK_CONFIDENCE)),
                    "detection_method": "llm",
                })
            logger.info(f"LLM 审核完成，检出 {len(validated)} 条风险")
            return validated
    except Exception as e:
        logger.error(f"LLM 审核失败: {e}")

    logger.warning("LLM 审核降级：返回空列表")
    return []
