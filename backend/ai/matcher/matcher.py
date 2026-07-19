"""
ai.matcher — 标准条款语义比对与缺失检测
独立于审核流程，前端单独触发。
"""
from ai.rag import search_knowledge
from ai.llm_client import llm_client
import json, logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_COMPARE = """你是合同条款比对专家。请将以下合同内容与标准条款模板进行逐条比对。

输出 JSON（只输出 JSON）：
{
  "clauses": [
    {"title":"标准条款名","priority":"required/recommended","status":"covered/partial/missing",
     "matched_text":"合同中匹配到的条款原文(缺失时为null)","similarity":0.0-1.0,
     "deviation":"偏离说明(null=无偏离)","completion":"补全建议(null=已覆盖)",
     "risk":"缺失或偏离带来的风险说明","related_law":"相关法条"}
  ],
  "summary":{"total":8,"covered":5,"partial":2,"missing":1,"coverage_rate":0.625},
  "missing_critical":["验收标准"]
}"""

def _extract_json(text):
    for fn in [json.loads, lambda t: json.loads(t.split("```json")[1].split("```")[0]) if "```json" in t else None,
               lambda t: json.loads(t.split("```")[1].split("```")[0]) if "```" in t else None]:
        try:
            r = fn(text.strip())
            if isinstance(r, dict): return r
            if isinstance(r, list): return {"clauses": r}
        except: continue
    try:
        s = text.index("{"); e = text.rindex("}") + 1
        return json.loads(text[s:e])
    except: pass
    return None

def compare_clauses(full_text: str, contract_type: str) -> dict:
    """将合同全文与对应类型的标准条款模板进行语义比对。"""
    docs = search_knowledge(contract_type, "standard_clauses", top_k=15)
    if not docs:
        return {"clauses":[],"summary":{"total":0,"covered":0,"partial":0,"missing":0,"coverage_rate":0},"missing_critical":[]}

    standards = "\n".join(f"{i+1}. {d['content'][:300]}" for i,d in enumerate(docs))
    truncated = full_text[:6000]

    try:
        resp = llm_client.chat(
            prompt=f"{SYSTEM_PROMPT_COMPARE}\n合同类型:{contract_type}\n标准条款:{standards}\n待比对合同:{truncated}\n请逐条比对输出JSON。",
            temperature=0.1)
        data = _extract_json(resp)
        if data and data.get("clauses"):
            clauses = data["clauses"]
            cov = sum(1 for c in clauses if c.get("status")=="covered")
            par = sum(1 for c in clauses if c.get("status")=="partial")
            mis = sum(1 for c in clauses if c.get("status")=="missing")
            mc = [c.get("title","") for c in clauses if c.get("status")=="missing" and c.get("priority")=="required"]
            return {"clauses":clauses,"summary":{"total":len(clauses),"covered":cov,"partial":par,"missing":mis,"coverage_rate":round(cov/max(len(clauses),1),3)},"missing_critical":mc}
    except Exception as e:
        logger.error(f"compare_clauses failed: {e}")
    return {"clauses":[],"summary":{"total":0,"covered":0,"partial":0,"missing":0,"coverage_rate":0},"missing_critical":[]}
