"""
ai.auditor.stance — 审核立场动态切换

根据用户选择的"我方角色"动态生成审核立场 Prompt，
注入 llm_auditor 和 Corex 所有 Agent 的系统提示词。
"""

ROLE_LABELS = {
    "party_a": "甲方（委托方/买方/雇主）",
    "party_b": "乙方（受托方/卖方/雇员）",
    "neutral": "中立审核",
}

ROLE_OPPOSITE = {
    "party_a": "乙方",
    "party_b": "甲方",
    "neutral": "对方",
}


def build_stance(our_role: str) -> str:
    """根据我方角色生成审核立场声明。

    Args:
        our_role: party_a（我是甲方）/ party_b（我是乙方）/ neutral（中立）

    Returns:
        立场声明文本，直接拼接到 System Prompt 开头
    """
    if our_role not in ROLE_LABELS:
        our_role = "neutral"

    if our_role == "neutral":
        return """审核立场：你以中立第三方视角审核本合同。不偏向任何一方，仅根据法律规定和行业惯例识别客观存在的风险。
对某一方有利但对另一方构成风险的条款，应标注并说明对哪一方不利。"""

    our_label = ROLE_LABELS[our_role]
    opposite = ROLE_OPPOSITE[our_role]

    return f"""审核立场：你代表{our_label}的利益审核本合同。你是我方的法律及商业顾问。
对{opposite}有利或对我方不利、加重我方义务、限制我方权利的条款，才构成风险，需要标注并建议修改。
对我方有利的条款（如管辖法院在我方所在地、知识产权归属我方）不构成风险，不应标注。
双方权利义务对等的条款通常不构成风险。"""


def build_audit_prompt(our_role: str, base_prompt: str) -> str:
    """将动态立场注入 LLM 审核的系统提示词。

    替换原本硬编码的"审核立场说明"段，改为根据 our_role 动态生成。
    """
    stance = build_stance(our_role)
    return f"{stance}\n\n{base_prompt}"
