"""
ai.reviser — 多轮对话式改条款（参考 RCBSF 的 Stackelberg 博弈多智能体框架）

架构（Leader-Follower，对应 RCBSF 的博弈层级）：
1. Leader（约束设定方）：解析用户指令 + 法条 → 给出硬性修订约束（先动，锁定策略空间）
2. Follower（执行方）：在 Leader 约束诱导的策略空间内，迭代修订条款原文
3. Self-QA（终审复核）：校验修订是否满足约束、是否引入新风险

多轮对话：每轮的 instruction + revised_clause 作为 history 带入下一轮，支持持续迭代细化。
"""
import json
import logging

from ai.llm_client import llm_client

logger = logging.getLogger(__name__)


LEADER_PROMPT = """你是合同修订的「约束设定方」（Leader）。请解析用户的修改指令，结合当前条款原文和相关法条，给出明确的修订硬性约束。

输出 JSON（只输出 JSON）：
{
  "target": "本次要修改的条款主题",
  "constraints": ["修订必须满足的硬性约束，如违约金不得超过20%"],
  "legal_basis": ["约束依据的法条，如民法典第585条"],
  "risk_type": "R01-R12，无则填null"
}"""


FOLLOWER_PROMPT = """你是合同修订的「执行方」（Follower）。请在 Leader 给定的约束下，修订条款原文，使其严格满足所有约束，且保持条款原意和整体一致性。

输出 JSON（只输出 JSON）：
{
  "revised_clause": "修订后的条款全文",
  "changes": ["逐条改动说明"],
  "explanation": "一句话修订说明"
}"""


QA_PROMPT = """你是合同修订的「终审复核方」（Self-QA）。请校验修订后的条款是否满足 Leader 的约束、是否引入了新的风险。

输出 JSON（只输出 JSON）：
{
  "verified": true,
  "remaining_risks": ["仍未满足的约束或新引入的风险，无则空数组"],
  "final_advice": "最终建议"
}"""


def _extract_json(text: str) -> dict | None:
    """三重容错解析：直接解析 → 代码块提取 → 花括号截取。"""
    if not text:
        return None
    t = text.strip()
    try:
        r = json.loads(t)
        if isinstance(r, dict):
            return r
    except json.JSONDecodeError:
        pass
    for marker in ("```json", "```"):
        if marker in t:
            try:
                inner = t.split(marker)[1].split("```")[0]
                r = json.loads(inner.strip())
                if isinstance(r, dict):
                    return r
            except (IndexError, json.JSONDecodeError):
                continue
    try:
        s = t.index("{")
        e = t.rindex("}") + 1
        r = json.loads(t[s:e])
        if isinstance(r, dict):
            return r
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def _call_agent(system_prompt: str, context: str, name: str) -> dict:
    """调用一次 LLM，返回解析后的 dict；失败返回带 error 的 dict。"""
    try:
        resp = llm_client.chat(prompt=f"{system_prompt}\n\n{context}", temperature=0.1)
        data = _extract_json(resp)
        if data is not None:
            return data
        logger.warning("[reviser] %s JSON 解析失败", name)
        return {"error": "JSON 解析失败", "agent": name}
    except Exception as e:
        logger.error("[reviser] %s 异常: %s", name, e)
        return {"error": str(e), "agent": name}


def revise_clause(
    clause_text: str,
    instruction: str,
    contract_type: str = "",
    history: list | None = None,
    rag_context: list | None = None,
) -> dict:
    """多轮对话式改条款：Leader 设约束 → Follower 修订 → Self-QA 校验。

    Args:
        clause_text: 当前条款原文
        instruction: 用户的修改指令
        contract_type: 合同类型（可选）
        history: 历史轮次 [{instruction, revised_clause}, ...]（多轮对话）
        rag_context: 相关法条（可选，来自知识库检索）

    Returns:
        {revised_clause, constraints, legal_basis, risk_type, changes, explanation, verified, remaining_risks, final_advice}
    """
    # 历史对话上下文（多轮）
    hist_text = ""
    if history:
        rounds = []
        for i, h in enumerate(history[-5:], 1):
            rounds.append(f"第{i}轮指令：{h.get('instruction', '')}\n第{i}轮修订：{h.get('revised_clause', '')}")
        hist_text = "\n".join(rounds)

    # 法条上下文
    law_text = ""
    if rag_context:
        law_text = "\n".join(
            f"- {it.get('law', '')}{it.get('article', '')} {it.get('title', '')}: {it.get('content', '')[:200]}"
            for it in rag_context[:3]
        )

    # 1) Leader：约束设定
    leader_ctx = f"合同类型：{contract_type or '未指定'}\n当前条款：{clause_text}\n用户指令：{instruction}"
    if hist_text:
        leader_ctx += f"\n\n历史修订记录：\n{hist_text}"
    if law_text:
        leader_ctx += f"\n\n相关法条：\n{law_text}"
    leader = _call_agent(LEADER_PROMPT, leader_ctx, "Leader")

    if leader.get("error"):
        return {"error": leader["error"]}

    # 2) Follower：修订执行
    follower_ctx = (
        f"当前条款：{clause_text}\n用户指令：{instruction}\n"
        f"Leader 约束：{json.dumps(leader.get('constraints', []), ensure_ascii=False)}\n"
        f"法律依据：{json.dumps(leader.get('legal_basis', []), ensure_ascii=False)}"
    )
    if hist_text:
        follower_ctx += f"\n\n历史修订记录：\n{hist_text}"
    if law_text:
        follower_ctx += f"\n\n相关法条：\n{law_text}"
    follower = _call_agent(FOLLOWER_PROMPT, follower_ctx, "Follower")

    if follower.get("error"):
        return {"error": follower["error"]}

    # 3) Self-QA：终审复核
    qa_ctx = (
        f"原条款：{clause_text}\n修订后条款：{follower.get('revised_clause', '')}\n"
        f"Leader 约束：{json.dumps(leader.get('constraints', []), ensure_ascii=False)}"
    )
    qa = _call_agent(QA_PROMPT, qa_ctx, "Self-QA")

    return {
        "revised_clause": follower.get("revised_clause", ""),
        "constraints": leader.get("constraints", []),
        "legal_basis": leader.get("legal_basis", []),
        "risk_type": leader.get("risk_type"),
        "target": leader.get("target", ""),
        "changes": follower.get("changes", []),
        "explanation": follower.get("explanation", ""),
        "verified": qa.get("verified", False),
        "remaining_risks": qa.get("remaining_risks", []),
        "final_advice": qa.get("final_advice", ""),
    }
