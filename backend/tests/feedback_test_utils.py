"""Feedback RAG 测试基础设施 —— 完全离线，不碰生产向量库与生产数据库。

为什么需要它：

1. 真实向量库在 `backend/chroma_data/`，测试若直接使用会**污染生产经验库** →
   改用内存 `chromadb.EphemeralClient()`；
2. 真实 embedding 模型（`shibing624/text2vec-base-chinese`）加载慢且依赖 HF 缓存 →
   改用确定性字符分布向量（只用于验证检索通路、过滤与撤销，不声称语义质量）；
3. `ai.rag.feedback_store` 的文档源来自数据库 → 必须把它的 `_session()` 指到临时库。

本文件不匹配 unittest 默认发现模式（`test*.py`），不会被当成测试用例收集。
"""
from unittest import mock

import chromadb
import numpy as np

from ai.rag import feedback_store

COLLECTION = "feedback_experiences"


class DeterministicEmbedder:
    """确定性、离线的字符分布向量。

    仅用于验证"检索通路 / metadata 过滤 / 撤销剔除"这些**机制**，
    不代表真实语义检索质量（真实链路由 text2vec 模型提供）。
    """

    DIM = 64

    def encode(self, texts):
        vecs = []
        for t in texts:
            v = np.zeros(self.DIM, dtype="float32")
            s = str(t)
            for i in range(len(s) - 1):
                v[(ord(s[i]) + ord(s[i + 1])) % self.DIM] += 1.0
            for ch in s:
                v[ord(ch) % self.DIM] += 0.5
            n = float(np.linalg.norm(v))
            vecs.append(v / n if n else v)
        return np.asarray(vecs)


class RagEnv:
    """上下文管理器：把 Feedback RAG 的三个外部依赖替换为测试替身。

    用法：
        with RagEnv(session_factory) as env:
            env.client   # 内存 chroma client（每个实例独立，天然隔离）
            env.store    # 被 patch 后的 ai.rag.feedback_store 模块
    """

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self._patchers = []
        self.client = None

    def __enter__(self):
        self.client = chromadb.EphemeralClient()
        self._patchers = [
            mock.patch.object(feedback_store, "_get_client", lambda: self.client),
            mock.patch.object(feedback_store, "_get_embedder", lambda: DeterministicEmbedder()),
            mock.patch.object(feedback_store, "_session", lambda: self.session_factory()),
        ]
        for p in self._patchers:
            p.start()
        self.store = feedback_store
        return self

    def __exit__(self, *_exc):
        for p in reversed(self._patchers):
            p.stop()
        self._patchers = []
        return False

    def doc_ids(self) -> list[str]:
        """当前集合内的文档 id（用于断言"精确删除"）。"""
        try:
            col = self.client.get_collection(COLLECTION)
        except Exception:
            return []
        return sorted(col.get().get("ids") or [])


def collection_count(client) -> int:
    try:
        col = client.get_collection(COLLECTION)
    except Exception:
        return 0
    return len(col.get().get("ids") or [])
