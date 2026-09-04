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

from ai.rag.bm25 import bm25_search, rrf_fuse

logger = logging.getLogger(__name__)

CHROMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "chroma_data")
KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge")

# 知识库 JSON 缓存（用于 BM25 词面检索 + 元数据回填）
_json_cache: dict = {}

_client: Optional[chromadb.PersistentClient] = None
_embedder: Optional[SentenceTransformer] = None
_init_lock = threading.Lock()
_init_done = False

COLLECTIONS = {
    "laws": "法律法规库",
    "standard_clauses": "标准条款库",
    "risk_cases": "风险案例库",
    "contract_templates": "合同范本分类库",
}

# ── 建库源 与 评测测试集 分离（防同源泄漏，见 02_项目文档/检索库改造交接.md）──
# TESTSET_PATH（testset.json）：合同范本样本集，由 build_testset.py 从 05_合同/合同范本 抽取生成，
#   按法理类型（taxonomy.ENABLED_TYPES）归类，作为「范本检索库」的唯一建库源。
# REALTEST_PATH（realtest.json）：评测测试集 = 真实合同 + 人工标注，与建库源不同源，
#   供 kNN/RAG 分类做「跨域泛化」评估，防止「用范本考范本」导致指标虚高。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_SERVICE_DIR = os.path.dirname(os.path.dirname(_BACKEND_DIR))
TESTSET_PATH = os.path.join(_SERVICE_DIR, "03_数据集", "测试集", "testset.json")      # 建库源（范本样本集）
REALTEST_PATH = os.path.join(_SERVICE_DIR, "03_数据集", "测试集", "realtest.json")    # 评测测试集（真实合同，待收集+标注）


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
        # 默认 max_seq_length=128（约128汉字）对长合同太短，提到 512（BERT 上限）
        _embedder.max_seq_length = 512
    return _embedder


def _excerpt(text: str, head: int = 256, tail: int = 256) -> str:
    """长文本摘录：首部(head)+尾部(tail)，控制在嵌入模型上下文内，
    首部含主体/目的条款，尾部含违约/争议/成果归属等判别性条款。"""
    if not text:
        return ""
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n…\n" + text[-tail:]


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
        except Exception as e:
            logger.debug("删除集合 %s 失败（首次创建属正常）: %s", coll_name, e)
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
    except Exception as e:
        logger.debug("删除 risk_cases 集合失败（首次创建属正常）: %s", e)
    client.create_collection("risk_cases")
    stats["risk_cases"] = 0

    return stats


def _load_knowledge_json(collection_name: str) -> list[dict]:
    """加载知识库 JSON（用于 BM25 词面检索 + 元数据回填），带缓存。"""
    if collection_name in _json_cache:
        return _json_cache[collection_name]
    file_map = {"laws": "laws.json", "standard_clauses": "standard_clauses.json"}
    filename = file_map.get(collection_name)
    if not filename:
        return []
    path = os.path.join(KNOWLEDGE_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _json_cache[collection_name] = data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("知识库 JSON 加载失败 %s: %s", filename, e)
        _json_cache[collection_name] = []
    return _json_cache[collection_name]


def _to_result(item: dict, score: float) -> dict:
    """把知识库条目转成统一检索结果，保留 content/score 并回填可溯源元数据。"""
    result = {
        "content": item.get("content", ""),
        "score": round(score, 5),
        "id": item.get("id"),
        "title": item.get("title", ""),
    }
    if "law" in item:
        # 法律法规库
        result["law"] = item.get("law", "")
        result["article"] = item.get("article", "")
        result["source"] = f"{item.get('law', '')}{item.get('article', '')}"
        result["tags"] = item.get("tags", [])
    if "type" in item:
        # 标准条款库
        result["type"] = item.get("type", "")
        result["priority"] = item.get("priority", "")
        result["related_law"] = item.get("related_law", "")
        result["source"] = item.get("source", "") or item.get("related_law", "")
    return result


def _dense_search(query: str, collection_name: str, top_k: int) -> list[tuple[int, float]]:
    """稠密（语义）检索：返回 [(文档下标, 分数)]，失败时返回空列表（降级到 BM25）。"""
    global _init_done
    client = _get_client()
    try:
        collection = client.get_collection(collection_name)
    except Exception as e:
        logger.debug("集合 %s 不存在或不可用，尝试初始化: %s", collection_name, e)
        if _init_done:
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
        except Exception as e:
            logger.warning("获取集合 %s 失败，稠密检索降级为空: %s", collection_name, e)
            return []

    try:
        embedder = _get_embedder()
        query_embedding = embedder.encode([query]).tolist()
        results = collection.query(query_embeddings=query_embedding, n_results=min(top_k, 10))
    except Exception as e:
        logger.warning("稠密检索失败: %s", e)
        return []

    # 用 content 反查文档下标，便于与 BM25 在同一个下标空间里做 RRF
    data = _load_knowledge_json(collection_name)
    content_index = {d.get("content", ""): i for i, d in enumerate(data)}

    hits = []
    if results and results.get("documents"):
        for i, doc in enumerate(results["documents"][0]):
            dist = results.get("distances", [[1.0]])[0][i] if results.get("distances") else 1.0
            score = 1.0 - dist
            idx = content_index.get(doc)
            if idx is not None:
                hits.append((idx, score))
    return hits


def search_knowledge(query: str, collection_name: str = "laws", top_k: int = 5) -> list[dict]:
    """混合检索：稠密（语义）+ BM25（词面）经 RRF 融合，返回带元数据的结果。

    Args:
        query: 检索查询文本
        collection_name: laws / standard_clauses
        top_k: 返回文档数量

    Returns:
        [{"content": "...", "score": 0.95, "id":..., "title":..., "law":..., "article":...}, ...]
    """
    data = _load_knowledge_json(collection_name)
    if not data:
        return []

    fetch_k = max(top_k * 2, 5)

    # 两路检索（稠密可能因模型未就绪而失败，BM25 始终可用）
    dense_hits = _dense_search(query, collection_name, fetch_k)
    sparse_hits = bm25_search(query, collection_name, data, fetch_k)

    # RRF 融合
    fused = rrf_fuse([dense_hits, sparse_hits])
    ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]

    return [_to_result(data[idx], score) for idx, score in ranked if 0 <= idx < len(data)]


def init_contract_templates(testset_path: Optional[str] = None) -> int:
    """初始化「合同范本」分类向量库。

    读取 testset.json（[{id, file, true_type, text}]），把范本正文向量化写入
    ChromaDB 的 contract_templates 集合，metadata 存 type（法理分类）+ file。
    供分类 RAG（kNN 投票 / RAG 少样本）检索最相似范本使用。

    Returns:
        写入的范本数量。
    """
    client = _get_client()
    embedder = _get_embedder()
    path = testset_path or TESTSET_PATH

    if not os.path.exists(path):
        logger.warning("合同范本测试集不存在：%s，跳过分类向量库初始化", path)
        return 0

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        return 0

    try:
        client.delete_collection("contract_templates")
    except Exception as e:
        logger.debug("删除 contract_templates 失败（首次创建属正常）: %s", e)
    collection = client.create_collection(
        "contract_templates",
        metadata={"hnsw:space": "cosine"},
    )

    # 嵌入用「首尾摘录」（防截断丢判别条款），但 documents 存全文，供检索返回/留一法比对
    excerpts = [_excerpt(item.get("text", "")) for item in data]
    full_texts = [item.get("text", "") for item in data]
    ids = [item.get("id", f"T{i}") for i, item in enumerate(data)]
    metadatas = [{"type": item.get("true_type", ""), "file": item.get("file", "")} for item in data]

    embeddings = embedder.encode(excerpts).tolist()
    collection.add(embeddings=embeddings, documents=full_texts, ids=ids, metadatas=metadatas)

    logger.info("[ChromaDB] 合同范本分类库: 写入 %d 条", len(full_texts))
    return len(full_texts)


def search_similar_templates(query: str, top_k: int = 5) -> list[dict]:
    """检索与 query 最相似的合同范本，返回 [{type, file, text, score}]。

    用于分类 RAG：拿到 top-K 相似范本后，可做 kNN 投票或作为 LLM 少样本示例。
    """
    client = _get_client()
    try:
        collection = client.get_collection("contract_templates")
    except Exception:
        try:
            init_contract_templates()
        except Exception as e:
            logger.warning("合同范本分类库初始化失败: %s", e)
            return []
        try:
            collection = client.get_collection("contract_templates")
        except Exception as e:
            logger.warning("获取 contract_templates 集合失败: %s", e)
            return []

    try:
        embedder = _get_embedder()
        q_emb = embedder.encode([_excerpt(query)]).tolist()
        results = collection.query(query_embeddings=q_emb, n_results=min(top_k, 10))
    except Exception as e:
        logger.warning("范本相似检索失败: %s", e)
        return []

    out = []
    if results and results.get("documents"):
        for i, doc in enumerate(results["documents"][0]):
            meta = (results.get("metadatas") or [{}])[0][i] if results.get("metadatas") else {}
            dist = (results.get("distances") or [[1.0]])[0][i] if results.get("distances") else 1.0
            out.append({
                "type": meta.get("type", ""),
                "file": meta.get("file", ""),
                "text": doc,
                "score": round(1.0 - dist, 5),
            })
    return out
