from ai.llm_client import llm_client
from ai.corex.agents import (
    LEGAL_AGENT_PROMPT, COMPLIANCE_AGENT_PROMPT,
    FINANCE_AGENT_PROMPT, SELF_QA_AGENT_PROMPT,
)
import json
import logging

logger = logging.getLogger(__name__)


def _extract_json(response: str) -> list | None:
    text = response.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list): return result
        if isinstance(result, dict) and "risks" in result: return result["risks"]
    except json.JSONDecodeError: pass
    for marker in ["```json", "```"]:
        if marker in text:
            try:
                inner = text.split(marker)[1].split("```")[0]
                result = json.loads(inner.strip())
                if isinstance(result, list): return result
                if isinstance(result, dict) and "risks" in result: return result["risks"]
            except (IndexError, json.JSONDecodeError): continue
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError): pass
    return None


def _call_agent(name: str, system_prompt: str, context: str, prev_output: str = "") -> list[dict]:
    full = context
    if prev_output:
        full += f"\n\n前面专家的审核结果供参考：\n{prev_output}"
    try:
        response = llm_client.chat(prompt=f"{system_prompt}\n\n{full}", temperature=0.1)
        risks = _extract_json(response)
        if risks is not None:
            for r in risks:
                if isinstance(r, dict):
                    r.setdefault("agent_source", name)
            logger.info(f"[Corex] {name} 完成，检出 {len(risks)} 条")
            return risks
        else:
            logger.warning(f"[Corex] {name} JSON 解析失败")
            return []
    except Exception as e:
        logger.error(f"[Corex] {name} 异常: {e}")
        return [{"error": str(e), "agent_source": name, "failed": True}]


def run_review(full_text: str, initial_risks: list[dict] = None) -> dict:
    truncated = full_text[:4000]
    context = f"请审核以下合同：\n\n{truncated}"
    agent_logs = {}
    all_risks = list(initial_risks) if initial_risks else []
    failed = []

    if initial_risks:
        context += f"\n\n前置规则引擎和RAG已标注的初始风险（供参考）：\n{json.dumps(initial_risks, ensure_ascii=False, indent=2)}"

    legal = _call_agent("法务Agent", LEGAL_AGENT_PROMPT, context)
    agent_logs["legal"] = {"count": len(legal), "risks": legal}
    if any(r.get("failed") for r in legal): failed.append("法务Agent")
    else: all_risks.extend(legal)

    compliance = _call_agent("合规Agent", COMPLIANCE_AGENT_PROMPT, context, json.dumps(legal, ensure_ascii=False, indent=2))
    agent_logs["compliance"] = {"count": len(compliance), "risks": compliance}
    if any(r.get("failed") for r in compliance): failed.append("合规Agent")
    else: all_risks.extend(compliance)

    finance = _call_agent("财务Agent", FINANCE_AGENT_PROMPT, context, json.dumps({"legal": legal, "compliance": compliance}, ensure_ascii=False, indent=2))
    agent_logs["finance"] = {"count": len(finance), "risks": finance}
    if any(r.get("failed") for r in finance): failed.append("财务Agent")
    else: all_risks.extend(finance)

    qa = _call_agent("Self-QA", SELF_QA_AGENT_PROMPT, context, json.dumps({"legal": legal, "compliance": compliance, "finance": finance}, ensure_ascii=False, indent=2))
    agent_logs["self_qa"] = {"count": len(qa), "risks": qa}
    if any(r.get("failed") for r in qa): failed.append("Self-QA")

    final = qa if qa and not any(r.get("failed") for r in qa) else all_risks

    # 去重
    seen = set()
    deduped = []
    for r in final:
        key = (r.get("risk_type", ""), r.get("clause_text", "")[:30])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    # 置信度过滤
    for r in deduped:
        conf = float(r.get("confidence", 0.7))
        if conf < 0.7:
            r["level"] = "low" if r.get("level") == "medium" else r.get("level", "low")
            r["low_confidence"] = True

    return {
        "risks": deduped,
        "agent_logs": agent_logs,
        "total_agents": 4,
        "completed_agents": 4 - len(failed),
        "failed_agents": failed,
        "method": "corex_review",
    }
