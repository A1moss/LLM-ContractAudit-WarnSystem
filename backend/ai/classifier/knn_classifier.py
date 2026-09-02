"""
ai.classifier.knn_classifier — 基于合同范本向量库的 kNN 分类（零 LLM 成本）

embed 查询合同 → 检索 top-K 相似范本 → 多数票定法理类型。
作为分类 RAG 的基线策略，与「纯 LLM 零样本」「RAG 少样本」对照。
"""
import logging
from collections import Counter

from ai.rag.vector_store import search_similar_templates

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 30000


def classify_by_knn(full_text: str, top_k: int = 5, exclude_self: str = "") -> dict:
    """
    kNN 分类：检索最相似的 top-K 范本，多数票定类型。

    Args:
        full_text: 查询合同全文
        top_k: 参与投票的范本数量
        exclude_self: 若传入原文本，检索结果中与之完全相同的范本会被剔除
                      （评测时防「自身泄漏」——测试样本本身就在向量库里）

    Returns:
        dict: {contract_type, confidence, method, top_matches, fallback}
    """
    if not full_text or not full_text.strip():
        return {"contract_type": "其他合同", "confidence": 0.0,
                "method": "knn", "top_matches": [], "fallback": True}

    matches = search_similar_templates(full_text[:MAX_QUERY_CHARS], top_k + 1)
    if exclude_self:
        matches = [m for m in matches if m.get("text") != exclude_self]
    matches = matches[:top_k]
    if not matches:
        return {"contract_type": "其他合同", "confidence": 0.0,
                "method": "knn", "top_matches": [], "fallback": True}

    votes = Counter(m["type"] for m in matches)
    best_type, best_count = votes.most_common(1)[0]
    return {
        "contract_type": best_type,
        "confidence": round(best_count / len(matches), 4),
        "method": "knn",
        "top_matches": matches,
        "fallback": False,
    }
