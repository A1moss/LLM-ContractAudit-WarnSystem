"""
ai.rag.vector_store — ChromaDB 向量知识库封装
本地嵌入式运行，无需 Docker。
"""
import json
import os
import logging
from typing import Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

CHROMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "chroma_data")

_client: Optional[chromadb.PersistentClient] = None
_embedder: Optional[SentenceTransformer] = None

COLLECTIONS = {
    "laws": "法律法规库",
    "standard_clauses": "标准条款库",
    "risk_cases": "风险识别卡片库",
    "audit_history": "审核经验库",
}


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer("shibing624/text2vec-base-chinese")
    return _embedder


def init_chroma() -> dict:
    """初始化所有知识库：读取 JSON → 向量化 → 写入 ChromaDB。首次启动时调用一次即可。"""
    client = _get_client()
    embedder = _get_embedder()
    knowledge_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge")
    stats = {}

    file_map = {
        "laws": "laws.json",
        "standard_clauses": "standard_clauses.json",
        "risk_cases": "risk_cases.json",
    }

    for coll_name, filename in file_map.items():
        filepath = os.path.join(knowledge_dir, filename)
        if not os.path.exists(filepath):
            logger.warning(f"跳过 {coll_name}: {filepath} 不存在")
            stats[coll_name] = 0
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data:
            stats[coll_name] = 0
            continue

        # 删除已有集合，重新创建（幂等操作）
        try:
            client.delete_collection(coll_name)
        except Exception:
            pass
        collection = client.create_collection(coll_name)

        # 取 content 字段用于检索
        texts = [item.get("content", "") for item in data]
        ids = [f"{coll_name}_{item.get('id', i)}" for i, item in enumerate(data)]

        # 编码 + 写入
        embeddings = embedder.encode(texts).tolist()
        collection.add(embeddings=embeddings, documents=texts, ids=ids)

        stats[coll_name] = len(texts)
        logger.info(f"[ChromaDB] {COLLECTIONS.get(coll_name, coll_name)}: 写入 {len(texts)} 条")

    # audit_history is runtime data — ensure collection exists, don't wipe
    _ensure_collection("audit_history")
    stats["audit_history"] = 0  # count is dynamic, not from JSON

    return stats


def _ensure_collection(name: str):
    """Create a ChromaDB collection if it doesn't exist (idempotent, does not wipe data)."""
    client = _get_client()
    try:
        client.get_collection(name)
    except Exception:
        client.create_collection(name)
        logger.info(f"[ChromaDB] 创建集合: {COLLECTIONS.get(name, name)}")


def index_feedback(
    risk_type: str,
    clause_text: str,
    action_type: str,
    level: str = "",
    suggestion: str = "",
    comment: str = "",
) -> bool:
    """将用户反馈（确认/修正/误报/补充）向量化写入审核经验库。

    写入后，未来的 RAG 检索可匹配到历史反馈记录，辅助 LLM 判断。

    Args:
        risk_type: R01-R12 或 CLAUSE_MISSING
        clause_text: 被标记的合同原文
        action_type: confirmed / corrected / false_positive / supplemented
        level: 当前风险等级（high / medium / low）
        suggestion: AI 或用户修正后的修改建议
        comment: 用户反馈备注

    Returns:
        True if indexed successfully, False otherwise
    """
    try:
        _ensure_collection("audit_history")
        client = _get_client()
        embedder = _get_embedder()
        collection = client.get_collection("audit_history")

        label_map = {
            "confirmed": "已确认",
            "corrected": "已修正",
            "false_positive": "已标记误报",
            "supplemented": "已补充",
        }
        action_label = label_map.get(action_type, action_type)

        # Build a dense document for semantic search
        parts = [f"审核经验：用户{action_label}了以下风险标注"]
        if level:
            parts.append(f"风险等级：{level}")
        if risk_type:
            parts.append(f"风险类型：{risk_type}")
        if clause_text:
            parts.append(f"涉及条款：{clause_text[:500]}")
        if suggestion:
            parts.append(f"修改建议：{suggestion[:300]}")
        if comment:
            parts.append(f"用户备注：{comment[:300]}")
        if action_type == "false_positive":
            parts.append("注意：此项被标记为误报，类似表述在历史审核中不构成风险。")

        content = "\n".join(parts)

        # Generate a unique but stable ID: hash of the key fields
        import hashlib
        doc_id = hashlib.md5(f"{risk_type}:{clause_text[:80]}:{action_type}".encode()).hexdigest()[:24]

        embedding = embedder.encode([content]).tolist()

        # Upsert: update if exists, insert if new
        collection.upsert(
            embeddings=embedding,
            documents=[content],
            ids=[doc_id],
        )

        logger.info(f"[审核经验库] 写入成功: {action_label} | {risk_type} | {clause_text[:50]}...")
        return True
    except Exception as e:
        logger.warning(f"[审核经验库] 写入失败: {e}")
        return False


def search_knowledge(query: str, collection_name: str = "laws", top_k: int = 5) -> list[dict]:
    """语义检索知识库，返回 top_k 条最相关文档。

    Args:
        query: 检索查询文本
        collection_name: laws / standard_clauses / risk_cases
        top_k: 返回文档数量

    Returns:
        [{"content": "法条全文", "score": 0.95}, ...]
    """
    client = _get_client()
    embedder = _get_embedder()

    try:
        collection = client.get_collection(collection_name)
    except Exception:
        logger.warning(f"集合 {collection_name} 不存在，请先运行 init_chroma()")
        return []

    query_embedding = embedder.encode([query]).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=min(top_k, 10))

    docs = []
    if results and results.get("documents"):
        for i, doc in enumerate(results["documents"][0]):
            score = 1.0 - results.get("distances", [[1.0]])[0][i] if results.get("distances") else 1.0
            docs.append({"content": doc, "score": round(score, 4)})

    return docs


def retrieve_for_audit(full_text: str, top_k: int = 5) -> list[dict]:
    """为合同审核检索相关法条和标准条款，合并去重后注入 llm_auditor。

    策略：
    1. 从 laws 检索相关法条
    2. 从 standard_clauses 检索相关标准条款
    3. 从 risk_cases 检索匹配的风险卡片
    4. 合并、去重、按相关性排序

    Args:
        full_text: 合同全文
        top_k: 每个知识库返回条数

    Returns:
        [{"content": "...", "score": 0.95, "source": "laws"}, ...]
    """
    laws = search_knowledge(full_text, "laws", top_k)
    clauses = search_knowledge(full_text, "standard_clauses", top_k)
    risk_cards = search_knowledge(full_text, "risk_cases", top_k)
    history = search_knowledge(full_text, "audit_history", top_k)

    # Tag sources
    for item in laws:
        item["source"] = "laws"
    for item in clauses:
        item["source"] = "standard_clauses"
    for item in risk_cards:
        item["source"] = "risk_cases"
    for item in history:
        item["source"] = "audit_history"

    # Merge, deduplicate by content prefix, sort by score desc
    merged = laws + clauses + risk_cards + history
    seen = set()
    deduped = []
    for item in sorted(merged, key=lambda x: x.get("score", 0), reverse=True):
        key = item["content"][:60]
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    logger.info(f"[RAG] 检索完成: laws={len(laws)}条, clauses={len(clauses)}条, risk_cards={len(risk_cards)}条, history={len(history)}条, 去重后={len(deduped)}条")
    return deduped[:top_k]


def search_risk_cards(query: str, risk_code: str | None = None, top_k: int = 5) -> list[dict]:
    """搜索风险卡片库，返回匹配的风险识别卡片，含完整的识别方法、法律依据和修改建议。

    用途：规则引擎或 LLM 检出风险后，用风险描述或风险编码检索最匹配的卡片，
         获取详细的识别标准、法条引用和修改建议模板。

    Args:
        query: 风险描述文本（如"违约金超过20%"）
        risk_code: 可选，按风险编码过滤（如 "R01" 只返回违约金相关卡片）
        top_k: 返回卡片数量

    Returns:
        [{"card_id": "R01-1", "risk_code": "R01", "risk_type": "违约金过高",
          "title": "...", "level": "高", "contract_types": "全部",
          "detection": "识别方法...", "law_basis": "法律依据...",
          "suggestion": "修改建议...", "score": 0.95}, ...]
    """
    import json as _json

    docs = search_knowledge(query, "risk_cases", top_k * 2)
    cards = []
    for doc in docs:
        content = doc["content"]
        # Parse the structured content back to fields
        # The content format is: 【风险卡片】title\n risk_code | ...\n detection\n law\n suggestion
        card = {"score": doc.get("score", 0)}
        lines = content.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("【风险卡片】"):
                card["title"] = line.replace("【风险卡片】", "").strip()
            elif line.startswith("风险编码："):
                parts = line.replace("风险编码：", "").split("|")
                if len(parts) >= 3:
                    card["risk_code"] = parts[0].strip()
                    card["risk_type"] = parts[1].replace("风险类型：", "").strip()
                    card["level"] = parts[2].replace("风险等级：", "").strip()
            elif line.startswith("适用合同类型："):
                card["contract_types"] = line.replace("适用合同类型：", "").strip()
            elif line.startswith("识别方法："):
                card["detection"] = line.replace("识别方法：", "").strip()
            elif line.startswith("法律依据："):
                card["law_basis"] = line.replace("法律依据：", "").strip()
            elif line.startswith("修改建议："):
                card["suggestion"] = line.replace("修改建议：", "").strip()

        # Fill defaults
        card.setdefault("title", "")
        card.setdefault("risk_code", "")
        card.setdefault("risk_type", "")
        card.setdefault("level", "中")
        card.setdefault("contract_types", "全部")

        # Filter by risk_code if specified
        if risk_code and card.get("risk_code") != risk_code:
            continue

        cards.append(card)

    return cards[:top_k]
