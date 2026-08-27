from ai.chunker import split_chunks
from ai.llm_client import llm_client
from ai.corex.agents import (
    LEGAL_AGENT_PROMPT, COMPLIANCE_AGENT_PROMPT,
    FINANCE_AGENT_PROMPT, SELF_QA_AGENT_PROMPT,
)
from ai.confidence import agent_confidence, clamp_confidence
import json
import logging

logger = logging.getLogger(__name__)

# 送入每个 Agent 的单块文本上限（字符）。超长合同按条款边界分块逐块审核，
# 尾部内容不再被截断丢弃。
MAX_AGENT_CHARS = 12000


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
            # JSON 解析失败也打上 failed 标记，避免被误判为"该 Agent 无风险"
            logger.warning(f"[Corex] {name} JSON 解析失败")
            return [{"error": "JSON parse failed", "agent_source": name, "failed": True}]
    except Exception as e:
        logger.error(f"[Corex] {name} 异常: {e}")
        return [{"error": str(e), "agent_source": name, "failed": True}]


def _filter_initial_risks(initial_risks: list[dict], chunk_text: str) -> list[dict] | None:
    """过滤出与当前文本块相关的规则引擎风险，供该块 Agent 参考。"""
    if not initial_risks:
        return None
    related = []
    for r in initial_risks:
        if not isinstance(r, dict):
            continue
        probe = (r.get("clause_text") or "").strip()[:20]
        if probe and probe in chunk_text:
            related.append(r)
    return related or None


def _dedup_across_chunks(risks: list[dict]) -> list[dict]:
    """跨块去重：同一 (风险类型, 原文片段前缀) 保留置信度最高者。"""
    grouped = {}
    order = []
    for r in risks:
        if not isinstance(r, dict):
            continue
        key = (r.get("risk_type", ""), (r.get("clause_text") or "")[:30])
        if key not in grouped:
            grouped[key] = r
            order.append(key)
        elif (r.get("confidence") or 0) > (grouped[key].get("confidence") or 0):
            grouped[key] = r
    return [grouped[k] for k in order]


def _run_review_impl(chunk_text: str, initial_risks: list[dict] = None) -> dict:
    """对单个文本块跑 4-Agent 顺序流水线（法务→合规→财务→Self-QA）。"""
    context = f"请审核以下合同：\n\n{chunk_text}"
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

    # 修复 Self-QA 空结果逻辑反转：QA 返回 []（终审判定"无风险"）时不应回退到
    # 未去重的 all_risks；仅当 QA 真正失败（带 failed 标记）时才回退。
    final = qa if not any(r.get("failed") for r in qa) else all_risks

    # 去重 + 多 Agent 一致性置信度
    grouped = {}
    order = []
    for r in final:
        if not isinstance(r, dict):
            continue
        key = (r.get("risk_type", ""), (r.get("clause_text") or "")[:30])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(r)

    deduped = []
    for key in order:
        items = grouped[key]
        rep = items[0]
        # 统计该风险被多少独立来源检出（多 Agent + 规则引擎）
        agent_sources = {it.get("agent_source", "") for it in items if it.get("agent_source")}
        rule_backed = any(it.get("detection_method") == "rule" for it in items)
        agreement = len(agent_sources) + (1 if rule_backed else 0)
        rep["agreement_count"] = agreement

        if len(agent_sources) >= 2:
            # 多个 Agent 独立检出同一风险 → 用一致性置信度（投票一致性）
            rep["confidence"] = agent_confidence(agreement)
        elif rep.get("confidence") is None:
            # 单一来源且无置信度 → 用一致性置信度兜底
            rep["confidence"] = agent_confidence(agreement)
        deduped.append(rep)

    # 置信度过滤
    for r in deduped:
        conf = clamp_confidence(r.get("confidence"))
        r["confidence"] = conf
        if conf < 0.7:
            r["level"] = "low" if r.get("level") == "medium" else r.get("level", "low")
            r["low_confidence"] = True

    return {
        "risks": deduped,
        "agent_logs": agent_logs,
        "failed_agents": failed,
    }


def run_review(full_text: str, initial_risks: list[dict] = None) -> dict:
    """多 Agent 审核入口：长合同分块逐块审核，合并去重，尾部不再漏检。"""
    chunks = split_chunks(full_text, MAX_AGENT_CHARS)
    if len(chunks) > 1:
        logger.info("合同 %d 字超过单块上限，分为 %d 块逐块 Corex 审核", len(full_text), len(chunks))

    if len(chunks) == 1:
        single = _run_review_impl(chunks[0], initial_risks)
        return {
            "risks": single["risks"],
            "agent_logs": single["agent_logs"],
            "total_agents": 4,
            "completed_agents": 4 - len(set(single["failed_agents"])),
            "failed_agents": list(set(single["failed_agents"])),
            "method": "corex_review",
        }

    # 多块：逐块跑完整流水线，合并 Agent 计数与失败集合
    merged_risks = []
    agent_logs = {name: {"count": 0} for name in ["legal", "compliance", "finance", "self_qa"]}
    failed = set()

    for chunk in chunks:
        # 只把与该块相关的规则引擎风险作为参考传入，避免误导后续块
        related = _filter_initial_risks(initial_risks, chunk)
        single = _run_review_impl(chunk, related)
        merged_risks.extend(single["risks"])
        for name in agent_logs:
            agent_logs[name]["count"] += (single["agent_logs"].get(name) or {}).get("count", 0)
        failed.update(single["failed_agents"])

    # 跨块去重：同一 (风险类型, 原文片段) 保留置信度最高者
    deduped = _dedup_across_chunks(merged_risks)

    return {
        "risks": deduped,
        "agent_logs": agent_logs,
        "total_agents": 4,
        "completed_agents": 4 - len(failed),
        "failed_agents": list(failed),
        "method": "corex_review",
        "chunked": True,
        "chunk_count": len(chunks),
    }
