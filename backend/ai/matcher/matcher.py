"""
ai.matcher — 标准条款语义比对与缺失检测

检索策略（参考 PAKTON 的结构化检索思想）：
标准条款库本身带 `type`（合同类型）字段，因此按合同类型做**结构过滤**，
而不是拿"合同类型名"去向量库里做语义检索（后者召回差、甚至为空）。

比对本身仍由 LLM 逐条完成。
"""
import os
import json
import logging

from ai.llm_client import llm_client

logger = logging.getLogger(__name__)

# 标准条款库路径：ai/matcher/matcher.py → ai/ → ai/knowledge/standard_clauses.json
_KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge")
_STANDARD_CLAUSES_FILE = os.path.join(_KNOWLEDGE_DIR, "standard_clauses.json")


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


def _load_standard_clauses() -> list[dict]:
    """加载标准条款库（56 条，带 type/level/priority/depends_on/conflict_with 字段）。"""
    try:
        with open(_STANDARD_CLAUSES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("标准条款库加载失败: %s", e)
        return []


def compare_clauses(full_text: str, contract_type: str) -> dict:
    """将合同全文与对应类型的标准条款模板进行逐条比对。"""
    all_clauses = _load_standard_clauses()

    # 按合同类型结构过滤（PAKTON 结构检索：类型精确匹配，而非语义模糊检索）
    docs = [c for c in all_clauses if c.get("type") == contract_type]
    if not docs:
        # 类型名不匹配（如"其他合同"）时回退到全部条款，避免比对空白
        logger.info("未找到类型 %s 的标准条款，回退到全部 %d 条", contract_type, len(all_clauses))
        docs = all_clauses

    if not docs:
        return {"clauses": [], "summary": {"total": 0, "covered": 0, "partial": 0, "missing": 0, "coverage_rate": 0}, "missing_critical": []}

    # 带上条款名 + 优先级 + 内容，让 LLM 输出能回填 title/priority
    standards = "\n".join(
        f"{i+1}. [{c.get('title','')} | 优先级:{c.get('priority','')}] {c.get('content','')[:300]}"
        for i, c in enumerate(docs)
    )
    truncated = full_text[:6000]

    try:
        resp = llm_client.chat(
            prompt=f"{SYSTEM_PROMPT_COMPARE}\n合同类型:{contract_type}\n标准条款:{standards}\n待比对合同:{truncated}\n请逐条比对输出JSON。",
            temperature=0.1)
        data = _extract_json(resp)
        if data and data.get("clauses"):
            clauses = data["clauses"]
            cov = sum(1 for c in clauses if c.get("status") == "covered")
            par = sum(1 for c in clauses if c.get("status") == "partial")
            mis = sum(1 for c in clauses if c.get("status") == "missing")
            mc = [c.get("title", "") for c in clauses if c.get("status") == "missing" and c.get("priority") == "required"]
            return {"clauses": clauses, "summary": {"total": len(clauses), "covered": cov, "partial": par, "missing": mis, "coverage_rate": round(cov / max(len(clauses), 1), 3)}, "missing_critical": mc}
    except Exception as e:
        logger.error(f"compare_clauses failed: {e}")
    return {"clauses": [], "summary": {"total": 0, "covered": 0, "partial": 0, "missing": 0, "coverage_rate": 0}, "missing_critical": []}
