"""
ai.rag.vector_store — ChromaDB 向量知识库封装
本地嵌入式运行，无需 Docker。
"""
import json
import os
import logging
import threading
from typing import Optional

# HuggingFace 不通时避免模型加载长时间卡住，优先使用本地缓存
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

CHROMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "chroma_data")

_client: Optional[chromadb.PersistentClient] = None
_embedder: Optional[SentenceTransformer] = None
_init_lock = threading.Lock()
_init_done = False

COLLECTIONS = {
    "laws": "法律法规库",
    "standard_clauses": "标准条款库",
    "risk_cases": "风险案例库",
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

    # risk_cases 预留空集合
    try:
        client.delete_collection("risk_cases")
    except Exception:
        pass
    client.create_collection("risk_cases")
    stats["risk_cases"] = 0

    return stats


def search_knowledge(query: str, collection_name: str = "laws", top_k: int = 5) -> list[dict]:
    """语义检索知识库，返回 top_k 条最相关文档。

    Args:
        query: 检索查询文本
        collection_name: laws / standard_clauses
        top_k: 返回文档数量

    Returns:
        [{"content": "法条全文", "score": 0.95}, ...]
    """
    global _init_done

    client = _get_client()

    try:
        collection = client.get_collection(collection_name)
    except Exception:
        # 集合缺失时自动初始化一次，保证新拉取的项目开箱即用
        if _init_done:
            logger.warning(f"集合 {collection_name} 不存在")
            return []
        with _init_lock:
            if not _init_done:
                try:
                    init_chroma()
                    _init_done = True
                except Exception as e:
                    logger.error("知识库初始化失败: %s", e)
                    return []
        try:
            collection = client.get_collection(collection_name)
        except Exception:
            logger.warning(f"集合 {collection_name} 初始化后仍不存在")
            return []

    embedder = _get_embedder()

    query_embedding = embedder.encode([query]).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=min(top_k, 10))

    docs = []
    if results and results.get("documents"):
        for i, doc in enumerate(results["documents"][0]):
            score = 1.0 - results.get("distances", [[1.0]])[0][i] if results.get("distances") else 1.0
            docs.append({"content": doc, "score": round(score, 4)})

    return docs
