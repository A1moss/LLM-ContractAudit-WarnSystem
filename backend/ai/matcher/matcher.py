"""
ai.matcher — 标准条款语义比对与缺失检测 + 跨条款关联风险分析

检索策略（参考 PAKTON 的结构化检索思想）：
标准条款库本身带 `type`（合同类型）字段，因此按合同类型做**结构过滤**，
而不是拿"合同类型名"去向量库里做语义检索（后者召回差、甚至为空）。

跨条款关联风险（参考 GRAPH-GRPO-LEX 的合同图建模、NCKG 的嵌套合同知识图谱、
LegalGraphRAG 的图检索增强）：
标准条款库带 `depends_on`（前置依赖）与 `conflict_with`（互斥冲突）图论字段，
据此做图分析，检测「前置条款缺失导致下游条款失效」与「互斥条款同时存在」两类
单条比对发现不了的跨条款联动风险。
"""
import os
import json
import logging

from ai.chunker import split_chunks
from ai.llm_client import llm_client
from ai.utils import extract_json

logger = logging.getLogger(__name__)

# 单块待比对合同文本上限（字符）。超长合同分块逐块比对，尾部条款不再漏检。
MAX_COMPARE_CHARS = 6000

# 标准条款库路径：ai/matcher/matcher.py → ai/ → ai/knowledge/standard_clauses.json
_KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge")
_STANDARD_CLAUSES_FILE = os.path.join(_KNOWLEDGE_DIR, "standard_clauses.json")

# 分类器输出 6 类（采购/销售/买卖/保密/服务/劳动），但标准条款库内部按 5 类组织
# （采购/销售/保密协议/服务外包/劳动）。此处做别名归一，避免改动 76 条数据、重建向量库：
#   买卖合同 → 销售合同（中性货物交易，复用销售侧通用条款）
#   保密合同 → 保密协议；服务合同 → 服务外包合同（名称对齐）
_TYPE_ALIAS = {
    "买卖合同": "销售合同",
    "保密合同": "保密协议",
    "服务合同": "服务外包合同",
}


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


def _load_standard_clauses() -> list[dict]:
    """加载标准条款库（带 type/level/priority/depends_on/conflict_with 字段）。"""
    try:
        with open(_STANDARD_CLAUSES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("标准条款库加载失败: %s", e)
        return []


def _cross_clause_risks(clause_results: list[dict], standard_clauses: list[dict]) -> list[dict]:
    """跨条款关联风险分析：利用图论字段（depends_on / conflict_with）检测联动风险。

    返回两类风险：
    1. 前置依赖缺失：某条款已覆盖/部分覆盖，但其依赖的条款缺失
       → 该条款虽"存在"，实际可能因前置缺失而无法有效执行
    2. 条款互斥冲突：两个互斥（conflict_with）条款同时被覆盖 → 约定矛盾
    """
    title_to_sc = {c.get("title"): c for c in standard_clauses if c.get("title")}
    id_to_sc = {c.get("id"): c for c in standard_clauses if c.get("id")}
    status_by_title = {cl.get("title", ""): cl.get("status", "missing") for cl in clause_results if cl.get("title")}

    risks = []
    for cl in clause_results:
        title = cl.get("title", "")
        status = cl.get("status", "missing")
        if status not in ("covered", "partial"):
            continue
        sc = title_to_sc.get(title)
        if not sc:
            continue

        # 1) 前置依赖缺失
        for dep_id in (sc.get("depends_on") or []):
            dep_sc = id_to_sc.get(dep_id)
            if not dep_sc:
                continue
            dep_title = dep_sc.get("title", dep_id)
            dep_status = status_by_title.get(dep_title, "missing")
            if dep_status == "missing":
                risks.append({
                    "type": "前置依赖缺失",
                    "clause": title,
                    "depends_on": dep_title,
                    "risk": f"条款「{title}」依赖「{dep_title}」，但「{dep_title}」缺失，可能导致「{title}」无法有效执行",
                })

        # 2) 条款互斥冲突
        for conf_id in (sc.get("conflict_with") or []):
            conf_sc = id_to_sc.get(conf_id)
            if not conf_sc:
                continue
            conf_title = conf_sc.get("title", conf_id)
            conf_status = status_by_title.get(conf_title, "missing")
            if conf_status in ("covered", "partial"):
                risks.append({
                    "type": "条款互斥冲突",
                    "clause": title,
                    "conflicts_with": conf_title,
                    "risk": f"条款「{title}」与「{conf_title}」约定互斥，存在条款矛盾",
                })

    return risks


def _merge_clause_results(clause_list: list[dict]) -> list[dict]:
    """合并多块比对结果：同一标准条款取覆盖状态最优的一条（covered > partial > missing）。"""
    status_rank = {"covered": 3, "partial": 2, "missing": 1}
    best = {}
    order = []
    for cl in clause_list:
        if not isinstance(cl, dict):
            continue
        title = cl.get("title", "")
        if not title:
            continue
        if title not in best:
            best[title] = cl
            order.append(title)
        else:
            if status_rank.get(cl.get("status"), 0) > status_rank.get(best[title].get("status"), 0):
                best[title] = cl
    return [best[t] for t in order]


def _compare_chunk(chunk_text: str, contract_type: str, standards: str) -> list[dict]:
    """对单个文本块做标准条款比对，返回 clauses 列表。"""
    try:
        resp = llm_client.chat(
            prompt=f"{SYSTEM_PROMPT_COMPARE}\n合同类型:{contract_type}\n标准条款:{standards}\n待比对合同:{chunk_text}\n请逐条比对输出JSON。",
            temperature=0.1)
        r = extract_json(resp)
        data = r if isinstance(r, dict) else ({"clauses": r} if isinstance(r, list) else None)
        if data and data.get("clauses"):
            return data["clauses"]
    except Exception as e:
        logger.error(f"compare_clauses 分块比对失败: {e}")
    return []


def compare_clauses(full_text: str, contract_type: str) -> dict:
    """将合同全文与对应类型的标准条款模板进行逐条比对（长合同分块）。"""
    all_clauses = _load_standard_clauses()

    # 用户侧 6 类 → 内部条款库 5 类的别名归一（买卖合同→销售合同 等）
    alias_type = _TYPE_ALIAS.get(contract_type, contract_type)

    # 按合同类型结构过滤（PAKTON 结构检索：类型精确匹配，而非语义模糊检索）
    docs = [c for c in all_clauses if c.get("type") == alias_type]
    if not docs:
        # 类型名不匹配（如"其他合同"）时回退到全部条款，避免比对空白
        logger.info("未找到类型 %s 的标准条款，回退到全部 %d 条", contract_type, len(all_clauses))
        docs = all_clauses

    if not docs:
        return {"clauses": [], "summary": {"total": 0, "covered": 0, "partial": 0, "missing": 0, "coverage_rate": 0}, "missing_critical": [], "cross_clause_risks": []}

    # 带上条款名 + 优先级 + 内容，让 LLM 输出能回填 title/priority
    standards = "\n".join(
        f"{i+1}. [{c.get('title','')} | 优先级:{c.get('priority','')}] {c.get('content','')[:300]}"
        for i, c in enumerate(docs)
    )

    # 分块：长合同逐块比对，尾部条款不再漏检
    chunks = split_chunks(full_text, MAX_COMPARE_CHARS)
    if len(chunks) > 1:
        logger.info("条款比对：合同 %d 字超过单块上限，分为 %d 块", len(full_text), len(chunks))

    merged = []
    for chunk in chunks:
        merged.extend(_compare_chunk(chunk, contract_type, standards))

    clauses = _merge_clause_results(merged)
    if clauses:
        cov = sum(1 for c in clauses if c.get("status") == "covered")
        par = sum(1 for c in clauses if c.get("status") == "partial")
        mis = sum(1 for c in clauses if c.get("status") == "missing")
        mc = [c.get("title", "") for c in clauses if c.get("status") == "missing" and c.get("priority") == "required"]
        # 跨条款关联风险（图分析：前置依赖缺失 + 互斥冲突）
        cross_risks = _cross_clause_risks(clauses, docs)
        return {
            "clauses": clauses,
            "summary": {"total": len(clauses), "covered": cov, "partial": par, "missing": mis, "coverage_rate": round(cov / max(len(clauses), 1), 3)},
            "missing_critical": mc,
            "cross_clause_risks": cross_risks,
        }
    return {"clauses": [], "summary": {"total": 0, "covered": 0, "partial": 0, "missing": 0, "coverage_rate": 0}, "missing_critical": [], "cross_clause_risks": []}
