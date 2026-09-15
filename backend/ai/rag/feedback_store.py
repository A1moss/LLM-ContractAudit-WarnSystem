"""ai.rag.feedback_store — Feedback Experience 独立知识源（第 ③ 类 RAG）

## 与现有 RAG 的关系（严格隔离，实测依据）

| 逻辑知识源 | 集合 | 建库源 | 检索入口 |
| --- | --- | --- | --- |
| ① 合同范本 | `contract_templates` | `testset.json` | `search_similar_templates()` |
| ② 法律依据 | `laws` / `standard_clauses` | `laws.json` / `standard_clauses.json` | `search_knowledge()` |
| ③ **反馈经验** | **`feedback_experiences`** | **数据库表 `feedback_experiences`** | **本模块** |

为什么必须独立集合（不是偏好，是硬约束）：
1. `search_knowledge()` 的文档加载走**硬编码 file_map**，只认两份 JSON，数据库驱动的集合无法复用；
2. `init_chroma()` 会对 `laws` / `standard_clauses` 执行 `delete_collection` → 重建。
   经验若写在里面，**每次重建知识库都会把全部经验抹掉**；
3. 经验需要 `contract_type` / `risk_type` / `human_label` 过滤，与法条、范本的 metadata 语义不同。

本模块**不修改**任何现有集合的语义、数据源或生命周期：
- 只 `get_collection` / `create_collection` 自己的集合；
- 只复用底层能力 `_get_client` / `_get_embedder` / `bm25_search` / `rrf_fuse`。

## 安全与降级

- 检索只加载 `status == 'active'` 的经验（撤销即排除）；
- 所有对外函数**任何异常都不上抛**：检索失败 → 返回空；入索引失败 → 返回 `indexed=False`。
  主审核链路绝不能因为 Feedback RAG 而失败。
- 经验正文只描述"应重点核查什么"，不含风险结论（构造见 `services.feedback_experience`）。
"""
import logging
import threading

from typing import Callable, Optional

from ai.rag.bm25 import bm25_search, rrf_fuse
from ai.rag.vector_store import _get_client, _get_embedder

logger = logging.getLogger(__name__)

COLLECTION = "feedback_experiences"

# 单次注入上限（控制 prompt 长度；chunk 本身可到 20k 字符）
DEFAULT_TOP_K = 3
MAX_BLOCK_CHARS = 1600
# 检索查询截断（向量编码成本控制；BM25 一路也截断以控制耗时）
MAX_QUERY_CHARS = 2000

_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════
# 文档源：数据库（唯一真源）
# ══════════════════════════════════════════════════════════════════════

def _session():
    from database import SessionLocal
    return SessionLocal()


def load_active_experiences(
    contract_type: Optional[str] = None, risk_type: Optional[str] = None,
) -> list[dict]:
    """加载可检索经验（`status='active'`），可按合同类型/风险类型过滤。

    过滤在**数据库层与检索层使用同一谓词**，保证 BM25 与稠密两路召回集合一致。
    任何异常（含表尚未迁移）都返回空列表。
    """
    try:
        from models.feedback_experience import FeedbackExperience
        db = _session()
        try:
            q = db.query(FeedbackExperience).filter(FeedbackExperience.status == "active")
            if contract_type:
                q = q.filter(FeedbackExperience.contract_type == contract_type)
            if risk_type:
                q = q.filter(FeedbackExperience.risk_type == risk_type)
            rows = q.order_by(FeedbackExperience.id.asc()).all()
            return [{
                "id": r.id,
                "kind": r.kind,
                "contract_type": r.contract_type,
                "risk_type": r.risk_type,
                "human_label": r.human_label,
                "feedback_reason": r.feedback_reason,
                "query_text": (r.query_text or "").strip(),
                "experience_text": (r.experience_text or "").strip(),
                "doc_id": r.doc_id,
                "index_version": r.index_version,
            } for r in rows]
        finally:
            db.close()
    except Exception as e:
        logger.warning("反馈经验加载失败（按无经验处理）: %s", e)
        return []


def index_version(docs: list[dict] | None = None) -> str:
    """经验库版本号：由"当前 active 经验集合"派生（替代知识库 JSON 的 mtime/size）。

    用途：① 作为 `bm25_search(version=...)` 的缓存失效键；② 写入审核留痕，
    回答"这次审核用的是哪一版经验库"。
    """
    if docs is None:
        docs = load_active_experiences()
    if not docs:
        return "v0-0"
    return "v%d-%d" % (max(d["id"] for d in docs), len(docs))


# ══════════════════════════════════════════════════════════════════════
# 索引写入 / 删除（只动自己的集合）
# ══════════════════════════════════════════════════════════════════════

def _doc_id(exp_id: int) -> str:
    return f"exp_{exp_id}"


def _meta(exp) -> dict:
    """Chroma metadata 只接受标量；同时写入 exp_id 供映射回数据库行。"""
    return {
        "exp_id": int(exp.id),
        "kind": exp.kind or "",
        "contract_type": exp.contract_type or "",
        "risk_type": exp.risk_type or "",
        "human_label": exp.human_label or "",
        "feedback_reason": exp.feedback_reason or "",
        "collection_name": COLLECTION,
    }


def _get_or_create_collection(create: bool = True):
    client = _get_client()
    try:
        return client.get_collection(COLLECTION)
    except Exception:
        if not create:
            return None
        # 与 contract_templates 保持一致的度量；只创建本集合，不触碰其它集合
        return client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def add_experience(exp) -> dict:
    """把一条经验写入索引（upsert，可重复调用）。

    Returns:
        {"indexed": bool, "doc_id": str|None, "index_version": str|None, "error": str|None}
    """
    out = {"indexed": False, "doc_id": None, "index_version": None, "error": None}
    try:
        text = (exp.query_text or "").strip()
        if not text:
            out["error"] = "经验缺少 query_text，无法索引"
            return out
        collection = _get_or_create_collection()
        doc_id = _doc_id(exp.id)
        emb = _get_embedder().encode([text]).tolist()
        collection.upsert(ids=[doc_id], embeddings=emb, documents=[text], metadatas=[_meta(exp)])
        out.update({"indexed": True, "doc_id": doc_id, "index_version": index_version()})
        logger.info("反馈经验已入索引: exp_id=%s doc_id=%s", exp.id, doc_id)
    except Exception as e:
        out["error"] = str(e)
        logger.error("反馈经验入索引失败: exp_id=%s, %s", getattr(exp, "id", None), e)
    return out


def remove_experience(exp_id: int) -> dict:
    """从索引中**精确删除**一条经验（按 doc_id；再按 metadata 兜底），其它经验不受影响。"""
    out = {"removed": False, "error": None}
    try:
        collection = _get_or_create_collection(create=False)
        if collection is None:
            out["error"] = "集合不存在（无需删除）"
            return out
        collection.delete(ids=[_doc_id(exp_id)])
        # 兜底：历史数据可能用别的 doc_id 写入，按 metadata 再删一次（只删本 exp_id）
        try:
            collection.delete(where={"exp_id": int(exp_id)})
        except Exception as e:
            logger.debug("按 metadata 兜底删除失败（通常表示已删净）: %s", e)
        out["removed"] = True
    except Exception as e:
        out["error"] = str(e)
        logger.error("反馈经验从索引删除失败: exp_id=%s, %s", exp_id, e)
    return out


def rebuild_index() -> dict:
    """用数据库里全部 active 经验重建集合（运维/恢复用；只重建本集合）。"""
    docs = load_active_experiences()
    try:
        client = _get_client()
        try:
            client.delete_collection(COLLECTION)
        except Exception as e:
            logger.debug("删除旧反馈经验集合失败（首次创建属正常）: %s", e)
        collection = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
        if docs:
            texts = [(d["query_text"] or "").strip() or "（空）" for d in docs]
            embs = _get_embedder().encode(texts).tolist()
            metas = [{
                "exp_id": int(d["id"]), "kind": d.get("kind") or "",
                "contract_type": d.get("contract_type") or "", "risk_type": d.get("risk_type") or "",
                "human_label": d.get("human_label") or "", "feedback_reason": d.get("feedback_reason") or "",
                "collection_name": COLLECTION,
            } for d in docs]
            collection.add(ids=[_doc_id(d["id"]) for d in docs], embeddings=embs,
                           documents=texts, metadatas=metas)
        return {"rebuilt": True, "count": len(docs), "index_version": index_version(docs), "error": None}
    except Exception as e:
        logger.error("反馈经验索引重建失败: %s", e)
        return {"rebuilt": False, "count": 0, "index_version": None, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════
# 检索：稠密 + BM25 + RRF（复用现有底层，不新写第二套）
# ══════════════════════════════════════════════════════════════════════

def _where_filter(contract_type: Optional[str], risk_type: Optional[str]):
    conds = []
    if contract_type:
        conds.append({"contract_type": contract_type})
    if risk_type:
        conds.append({"risk_type": risk_type})
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


def _dense_search(query: str, docs: list[dict], top_k: int,
                  contract_type: Optional[str], risk_type: Optional[str]) -> list[tuple[int, float]]:
    """稠密检索，返回 [(docs 下标, 分数)]；Chroma 不可用时返回 []（降级为 BM25）。"""
    try:
        collection = _get_or_create_collection(create=False)
        if collection is None:
            return []
        emb = _get_embedder().encode([query]).tolist()
        kwargs = {"query_embeddings": emb, "n_results": min(max(top_k, 1), 10)}
        where = _where_filter(contract_type, risk_type)
        if where:
            kwargs["where"] = where
        res = collection.query(**kwargs)
        ids = (res.get("ids") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        pos = {int(d["id"]): i for i, d in enumerate(docs)}
        hits = []
        for i, _doc_id_val in enumerate(ids):
            meta = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
            exp_id = meta.get("exp_id")
            idx = pos.get(int(exp_id)) if exp_id is not None else None
            if idx is None:
                # metadata 缺失时回退解析 doc_id（形如 exp_12）
                raw = str(_doc_id_val or "")
                if raw.startswith("exp_"):
                    try:
                        idx = pos.get(int(raw[4:]))
                    except ValueError:
                        idx = None
            if idx is None:
                logger.debug("反馈经验稠密命中无法映射到数据库行: %s", _doc_id_val)
                continue
            dist = dists[i] if i < len(dists) else 1.0
            hits.append((idx, 1.0 - float(dist)))
        return hits
    except Exception as e:
        logger.warning("反馈经验稠密检索失败（降级为 BM25）: %s", e)
        return []


def search_feedback_experiences(
    query: str, contract_type: Optional[str] = None, risk_type: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict]:
    """混合检索（稠密 + BM25 + RRF）已批准且 active 的历史人工经验。

    与 `search_knowledge` 同一套融合思路，但文档源是数据库。
    **任何失败都返回空列表**，绝不抛出到调用方。
    """
    try:
        q = (query or "").strip()
        if not q:
            return []
        docs = load_active_experiences(contract_type=contract_type, risk_type=risk_type)
        if not docs:
            return []
        version = index_version(docs)
        fetch_k = max(top_k * 2, 5)
        q_trunc = q[:MAX_QUERY_CHARS]

        dense_hits = _dense_search(q_trunc, docs, fetch_k, contract_type, risk_type)
        # BM25 直接吃数据库正文（Chroma 挂了也能召回）
        sparse_hits = bm25_search(
            q_trunc, COLLECTION, [{"content": d["query_text"]} for d in docs],
            fetch_k, version=version,
        )
        fused = rrf_fuse([dense_hits, sparse_hits])
        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]
        out = []
        for idx, score in ranked:
            if 0 <= idx < len(docs):
                item = dict(docs[idx])
                item["score"] = round(score, 6)
                item["index_version"] = version
                out.append(item)
        return out
    except Exception as e:
        logger.warning("反馈经验检索失败（按无经验处理）: %s", e)
        return []


def format_experience_block(experiences: list[dict], max_chars: int = MAX_BLOCK_CHARS) -> str:
    """把经验拼成注入 prompt 的文本块（自有长度上限）。

    **只承载"应重点核查什么"**：正文由 `services.feedback_experience.build_experience_text`
    生成，逐条只含历史结论陈述、归因、人工意见原文与核查提示，不含"本次应判什么"。
    """
    if not experiences:
        return ""
    lines = []
    used = 0
    for e in experiences:
        rt = e.get("risk_type") or ""
        ct = e.get("contract_type") or ""
        head = f"- [{rt}{' · ' + ct if ct else ''}] ".strip()
        body = (e.get("experience_text") or "").strip().replace("\n", " ")
        line = head + body
        if used + len(line) > max_chars:
            remain = max_chars - used
            if remain < 80:
                break
            line = line[:remain] + "…"
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 生产审核用的上下文提供者
# ══════════════════════════════════════════════════════════════════════

class FeedbackContextProvider:
    """按文本块提供【人工历史参考】块，并累计本次实际使用的经验信息（线程安全）。

    设计要点：
    - 逐块检索（而不是整份合同只检索一次），使参考与当前位置更相关；
    - 任何异常都返回空串，**绝不影响主审核**；
    - `used_ids` / `index_version` / `applied_chunks` 供审核留痕（audit_records.learning_context）。
    """

    def __init__(self, contract_type: Optional[str] = None, top_k: int = DEFAULT_TOP_K):
        self.contract_type = contract_type or None
        self.top_k = top_k
        self.used_ids: set[int] = set()
        self.applied_chunks = 0
        self.index_version: Optional[str] = None
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    def __call__(self, chunk: str) -> str:
        try:
            exps = search_feedback_experiences(
                chunk or "", contract_type=self.contract_type, top_k=self.top_k
            )
            if not exps:
                return ""
            with self._lock:
                for e in exps:
                    self.used_ids.add(int(e["id"]))
                self.applied_chunks += 1
                if exps[0].get("index_version"):
                    self.index_version = exps[0]["index_version"]
            return format_experience_block(exps)
        except Exception as e:  # 双保险：检索层已兜底，这里再兜一次
            with self._lock:
                self.error = str(e)
            logger.warning("反馈经验上下文生成失败（本块不使用历史经验）: %s", e)
            return ""

    def audit_summary(self) -> dict:
        """写入 `audit_records.learning_context` 的留痕内容。"""
        with self._lock:
            return {
                "enabled": True,
                "collection": COLLECTION,
                "index_version": self.index_version,
                "experience_ids": sorted(self.used_ids),
                "applied_chunks": self.applied_chunks,
                "error": self.error,
            }


def build_feedback_context_provider(
    contract_type: Optional[str] = None, top_k: int = DEFAULT_TOP_K,
) -> FeedbackContextProvider:
    """构造生产审核用的经验提供者（不做任何检索，构造本身不会失败）。"""
    return FeedbackContextProvider(contract_type=contract_type, top_k=top_k)


def disabled_audit_summary(reason: str = "disabled") -> dict:
    """未启用时的留痕内容（保持字段结构一致，便于查询）。"""
    return {
        "enabled": False,
        "collection": None,
        "index_version": None,
        "experience_ids": [],
        "applied_chunks": 0,
        "error": None,
        "reason": reason,
    }
