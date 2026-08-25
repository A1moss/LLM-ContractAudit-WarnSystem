"""
ai.rag.bm25 — 轻量 BM25 关键词检索（无外部分词依赖）

中文没有天然空格分词，这里用「字符 bigram」作为词项：
    "违约金过高" -> ["违约","约金","金过","过高"] + 单字兜底

与稠密向量（语义）检索互补：稠密擅长语义相似，BM25 擅长精确词面命中。
二者经 RRF（Reciprocal Rank Fusion，倒数排名融合）合并，召回率显著高于
单一检索（参考 PAKTON 的混合检索：BM25 + dense + RRF）。

本模块仅依赖标准库，不引入 jieba 等分词依赖。
"""
import os
import math
from collections import Counter

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "knowledge")

_BM25_K1 = 1.5
_BM25_B = 0.75

# 每个 collection 的 BM25 索引缓存（文档内容变才需重建，此处按 collection 名缓存）
_index_cache: dict = {}


def _tokenize(text: str) -> list[str]:
    """字符 bigram + 单字分词，适配中文法律文本（含"第五百八十五条"等数字）。"""
    text = (text or "").replace("\n", "").replace(" ", "").replace("\t", "")
    if not text:
        return []
    bigrams = [text[i:i + 2] for i in range(len(text) - 1)]
    return bigrams + list(text)


class BM25:
    """标准 Okapi BM25。文档集初始化时一次性统计 df/长度。"""

    def __init__(self, docs: list[str]):
        self.docs = docs
        self.n = len(docs)
        self.doc_tokens = [_tokenize(d) for d in docs]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avgdl = sum(self.doc_len) / max(self.n, 1)
        self.df = Counter()
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.df[term] += 1

    def score(self, query: str, idx: int) -> float:
        qterms = _tokenize(query)
        if not qterms:
            return 0.0
        tf = Counter(self.doc_tokens[idx])
        dl = self.doc_len[idx]
        score = 0.0
        for term in qterms:
            df = self.df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            term_tf = tf.get(term, 0)
            if term_tf == 0:
                continue
            denom = term_tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / max(self.avgdl, 1))
            score += idf * (term_tf * (_BM25_K1 + 1)) / denom
        return score


def bm25_search(query: str, collection_name: str, docs: list[dict], top_k: int = 5) -> list[tuple[int, float]]:
    """对知识库文档集做 BM25 检索，返回 [(文档下标, 分数)]，按分数降序取 top_k。"""
    if not docs:
        return []
    if collection_name not in _index_cache:
        _index_cache[collection_name] = BM25([d.get("content", "") for d in docs])
    bm25 = _index_cache[collection_name]
    scored = [(i, bm25.score(query, i)) for i in range(len(docs))]
    scored = [s for s in scored if s[1] > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def rrf_fuse(ranked_lists: list[list], k: int = 60) -> dict:
    """倒数排名融合（RRF）：多路检索结果合并为一个 score。

    score(doc_id) = Σ 1/(k + rank_in_list)
    对多路检索器都命中的文档，分数自然叠加，达到"取并集、交叉印证"的效果。
    """
    fused: dict = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            # item 可能是 (doc_index, score) 或直接是 doc_index
            doc_id = item[0] if isinstance(item, (tuple, list)) else item
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return fused
