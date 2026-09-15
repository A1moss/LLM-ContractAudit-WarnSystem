"""services.warmup — 静启动：启动后台预热重依赖（不阻塞启动、用户无感知）。

背景（为什么要"静启动"）
------------------------
为修「后端启动慢」，torch / sentence_transformers / chromadb 和 PaddleOCR 都改成了
**惰性加载**（首次使用时才 import/实例化）。启动确实快了，但代价被转移到了**第一次
上传合同**上：那一次请求要替整个进程付冷启动成本。分段实测：

    T1 import ai.rag.vector_store（torch+transformers+chromadb）  7.44s
    T2 加载 text2vec-base-chinese 向量模型                        0.28s
    T3 首次 search_similar_templates（含查询编码）                1.37s
    T5 首次 search_knowledge(laws)                                0.07s
    ────────────────────────────────────────────────────────────
    首次上传前要额外付 ≈ 9.2s（其中 7.4s 是 import，占大头）

静启动的做法
------------
进程启动后在**后台守护线程**里把这些重依赖提前加载好：
  * 启动不被阻塞 —— 线程 daemon 化，`lifespan` 立即 yield，接口马上可用；
  * 用户无任何感知 —— 不在启动输出里刷屏，不占请求，不弹任何提示；
  * 等用户真正上传/审核时已是热态，第一次上传不再慢。

预热失败**绝不影响服务**：只记 warning，真正的首次使用时还会照常重试（惰性加载逻辑不变）。

环境变量
--------
    DISABLE_WARMUP=1   关闭静启动（单测/CI 用，避免无谓拖入 torch）
    WARMUP_OCR=1       额外预热 PaddleOCR（首次会下载模型，较慢，默认关闭）
    WARMUP_STRICT=1    预热异常时抛出（默认只告警）
"""
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# 预热状态：供 /api/health 查询（被动可查，不主动打扰用户）
STATE = {
    "rag": "idle",        # idle | warming | ready | failed
    "ocr": "idle",        # idle | warming | ready | unavailable | failed
    "rag_seconds": None,
    "ocr_seconds": None,
}

_lock = threading.Lock()
_started = False


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _warm_rag():
    """预热 RAG 链路：模块导入 → 向量模型 → 范本检索 → 法条检索。

    这正是「上传合同」分类环节（ai.classifier.rag_classifier.classify_by_rag）走的路。
    若向量库尚未建好，这里的首次检索会顺带把库建起来（本来也要在首次上传时建）。
    """
    t0 = time.perf_counter()
    STATE["rag"] = "warming"
    try:
        from ai.rag.vector_store import _get_embedder, search_similar_templates, search_knowledge
        _get_embedder()                                  # 加载向量模型
        search_similar_templates("合同分类预热门控查询", 1)   # contract_templates 集合 + 查询编码
        search_knowledge("法条预热门控查询", "laws", 1)       # laws 集合 + BM25 缓存
    except Exception as e:
        STATE["rag"] = "failed"
        logger.warning("静启动：RAG 预热失败（不影响服务，首次使用时会自动重试）: %s", e)
        if _truthy("WARMUP_STRICT"):
            raise
        return
    STATE["rag"] = "ready"
    STATE["rag_seconds"] = round(time.perf_counter() - t0, 2)
    logger.info("静启动：RAG 预热完成 %ss（首次上传/审核不再付冷启动成本）", STATE["rag_seconds"])


def _warm_ocr():
    """预热 PaddleOCR（仅图片/扫描件上传需要，首次会下载模型，故默认关闭）。"""
    t0 = time.perf_counter()
    STATE["ocr"] = "warming"
    try:
        from ai.parser.ocr_parser import _ensure_ocr
        if _ensure_ocr() is None:
            STATE["ocr"] = "unavailable"
            return
    except Exception as e:
        STATE["ocr"] = "failed"
        logger.warning("静启动：OCR 预热失败（不影响服务，图片上传时会重试）: %s", e)
        if _truthy("WARMUP_STRICT"):
            raise
        return
    STATE["ocr"] = "ready"
    STATE["ocr_seconds"] = round(time.perf_counter() - t0, 2)
    logger.info("静启动：OCR 预热完成 %ss", STATE["ocr_seconds"])


def _run():
    _warm_rag()
    if _truthy("WARMUP_OCR"):
        _warm_ocr()


def start_warmup() -> bool:
    """启动后台预热线程；返回是否真的启动了。幂等（重复调用只生效一次）。"""
    global _started
    if _truthy("DISABLE_WARMUP"):
        logger.info("静启动：已通过 DISABLE_WARMUP 关闭")
        return False
    with _lock:
        if _started:
            return False
        _started = True

    threading.Thread(target=_run, name="a24-warmup", daemon=True).start()
    logger.info("静启动：已在后台线程预热重依赖（启动不阻塞、用户无感知）")
    return True


def status() -> dict:
    """当前预热状态（供 /api/health 被动查询）。"""
    return dict(STATE)
