"""ai.parser.ocr_engine — OCR 引擎适配层（RapidOCR + ONNX Runtime）。

## 为什么是 RapidOCR

原实现用 PaddleOCR（`requirements.txt` 里写 `paddleocr>=2.7.0` + `paddlepaddle>=2.5.0`），
但代码实际按 PaddleOCR **3.x** API 写的，且完整 `paddlepaddle` 框架体积大、CPU 初始化 8–12s、
首次还需联网下载模型。RapidOCR 是「PaddleOCR 模型 + ONNX Runtime」的轻量重打包：
模型（PP-OCRv6 det/rec + cls）**打进 wheel 自带**、无需下载，CPU 更快、内存更低，
输出同样带 bbox 与置信度。本项目只需要「图片 → 文本」，因此单引擎即可。

## 实测确认的 RapidOCR API（3.9.2，不假定）

    from rapidocr import RapidOCR
    engine = RapidOCR()                        # __init__(config_path=None, params=None)
    res = engine(img)                          # 接受 str / Path / bytes / numpy / PIL
    # 识别到文字：
    res.boxes   -> numpy.ndarray  (N, 4, 2) float32  四点坐标
    res.txts    -> tuple[str, ...]
    res.scores  -> tuple[float, ...]
    # **一条都没识别到**：
    res is None                                # ← 不是"空对象"，必须判 None

`engine(...)` 参数里还有 `text_score`（默认 0.5，低于该置信度的结果会被丢弃）。

## 阅读顺序（本轮要求，不用 LayoutParser）

    ① 按页 → ② 同页按文本框垂直中心聚成"阅读行" → ③ 行内按 x 从左到右
    ④ 输出稳定（所有排序键都带确定性 tie-break，含坐标与原始下标）

同一输入重复 OCR 输出顺序必须一致：因此**每一步排序都在显式键上完成**，
不依赖引擎返回顺序。

## 边界

- 不做表格结构恢复、不做多栏版式分析、不做原版式重建（本项目统一输出 DOCX）；
- bbox 只用于**排序**与可选展示，不进入 `ClauseRevision`；
- 惰性初始化（进程内单例 + 锁），不阻塞启动；`WARMUP_OCR=1` 时由 warmup 预热。
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# 低于该置信度的识别结果不计入"有效文字"（与 RapidOCR 默认 text_score 同口径）
LOW_CONF_THRESHOLD = 0.6

# ── 质量阈值（用真实渲染图实测标定，见本轮汇报）──
# 实测：清晰渲染 均分≈0.986 留存 1.00；字号 10~40px 均分 0.986~0.996；
#       高斯模糊 1 → 0.977；模糊 3 → 0.921（已丢字）；模糊 5 / 纯白 / 强噪 → 引擎直接无结果；
#       400x120 的极小图 → 只识别出 28/237 字（留存 0.12）。
# 因此：**无结果**、**有效字数过少**、**均分过低**、**低置信占比过高** 都判为不可用。
MIN_VALID_CHARS = 10        # 有效文字数量下限（低于此基本不是一份可审核的合同）
MIN_MEAN_CONFIDENCE = 0.75  # 平均置信度下限（清晰件实测 ≥0.97，留足余量）
MAX_LOW_CONF_RATIO = 0.5    # 低置信结果占比上限

_engine = None
_engine_lock = threading.Lock()
_engine_init_tried = False


def _ensure_engine():
    """惰性初始化 RapidOCR 单例；失败只告警并返回 None（不抛、不阻塞启动）。"""
    global _engine, _engine_init_tried
    if _engine_init_tried:
        return _engine
    with _engine_lock:
        if _engine_init_tried:
            return _engine
        _engine_init_tried = True
        try:
            from rapidocr import RapidOCR
            _engine = RapidOCR()
            logger.info("RapidOCR 初始化成功（ONNX Runtime）")
        except Exception as e:
            logger.warning("RapidOCR 初始化失败，OCR 功能不可用: %s", e)
            _engine = None
    return _engine


def ocr_available() -> bool:
    """OCR 引擎当前是否可用（供 /api/health 与上传前置提示使用）。"""
    return _ensure_engine() is not None


# ── 坐标与排序 ──────────────────────────────────────────────────────────

def _corners(box) -> list[tuple[float, float]]:
    """把引擎返回的框统一成 4 个 (x, y) 浮点角点。"""
    pts: list[tuple[float, float]] = []
    for p in box:
        try:
            pts.append((float(p[0]), float(p[1])))
        except Exception:
            continue
    return pts


def _metrics(box) -> dict | None:
    """框 → 排序所需几何量。"""
    pts = _corners(box)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    top, bottom = min(ys), max(ys)
    return {
        "x0": min(xs),
        "x1": max(xs),
        "top": top,
        "bottom": bottom,
        "cy": (top + bottom) / 2.0,
        "h": max(1.0, bottom - top),
    }


def _sort_events(events: list[dict]) -> list[dict]:
    """阅读顺序：按页 → 同页垂直聚成阅读行 → 行内从左到右。

    聚类规则：把事件按 ``(页, 垂直中心, 左边, 原始下标)`` 稳定排序后顺序扫描；
    当前事件与已有阅读行**垂直重叠超过各自高度的 50%** 时并入该行。

    ⚠️ 同一个阅读行**只能装同一页**的事件：不同页的文本框写在同一坐标系里，
    若聚类时不看页码，第 2 页的框会与第 1 页的框"垂直重叠"而被并进同一行，
    最终把页序彻底打乱（实测症状：段落 page 变成 1,2,3,2,1,3…）。
    因此行内排序键与行的排序键都带 page，页码是第一优先级。
    """
    ordered = sorted(events, key=lambda e: (e["page"], e["cy"], e["x0"], e["_i"]))
    lines: list[dict] = []
    for ev in ordered:
        for ln in lines:
            ref = ln["items"][0]
            if ref["page"] != ev["page"]:
                continue                     # 绝不跨页聚行
            overlap = min(ev["bottom"], ref["bottom"]) - max(ev["top"], ref["top"])
            if overlap > 0.5 * max(ev["h"], ref["h"]):
                ln["items"].append(ev)
                break
        else:
            lines.append({"items": [ev], "page": ev["page"]})

    # 行内按 x 从左到右（tie-break：垂直中心、原始下标）
    for ln in lines:
        ln["items"].sort(key=lambda e: (e["x0"], e["cy"], e["_i"]))
        ln["key"] = (ln["page"], min(e["cy"] for e in ln["items"]))
    # 行按 (页, 行内最小垂直中心) 排 —— 页是第一优先级
    lines.sort(key=lambda ln: ln["key"])
    return [ev for ln in lines for ev in ln["items"]]


# ── 质量评估 ────────────────────────────────────────────────────────────

def assess_quality(items: list[dict], page_count: int = 1) -> dict:
    """综合判断 OCR 结果是否可用于后续审核。

    :returns: ``{"usable": bool, "reason": str, "mean_confidence": float,
                "valid_chars": int, "low_conf_ratio": float, "box_count": int}``
    """
    total = len(items)
    if total == 0:
        return {"usable": False, "reason": "OCR 未识别到任何文字", "mean_confidence": 0.0,
                "valid_chars": 0, "low_conf_ratio": 0.0, "box_count": 0}

    confs = [float(it.get("confidence") or 0.0) for it in items]
    mean_conf = sum(confs) / total
    low = sum(1 for c in confs if c < LOW_CONF_THRESHOLD)
    low_ratio = low / total
    valid_chars = sum(len(str(it.get("text") or "").strip())
                      for it in items if float(it.get("confidence") or 0.0) >= LOW_CONF_THRESHOLD)

    base = {"mean_confidence": round(mean_conf, 4), "valid_chars": valid_chars,
            "low_conf_ratio": round(low_ratio, 4), "box_count": total}

    # 先判"可信度"，再判"数量"：置信度普遍过低时，"有效字数少"只是它的**结果**，
    # 报出根因（置信度）比报出表象（字数）对用户更有指导意义。
    if mean_conf < MIN_MEAN_CONFIDENCE:
        return {**base, "usable": False,
                "reason": f"OCR 平均置信度过低（{mean_conf:.2f}）"}
    if low_ratio > MAX_LOW_CONF_RATIO:
        return {**base, "usable": False,
                "reason": f"OCR 低置信结果过多（{low_ratio:.0%}）"}
    if valid_chars < MIN_VALID_CHARS:
        return {**base, "usable": False,
                "reason": f"OCR 有效文字过少（仅 {valid_chars} 字）"}
    return {**base, "usable": True, "reason": ""}


# ── 主入口 ──────────────────────────────────────────────────────────────

def ocr_image(image, page: int = 1) -> dict:
    """对**单页**图片做 OCR，返回结构化结果（不抛异常）。

    :param image: 文件路径 / numpy 数组 / PIL Image / bytes
    :param page:  该图对应的真实页码（多页扫描件由调用方逐页传入）
    :returns: ``{"items": [...], "error": str|None}``
              item = ``{"text", "confidence", "page", "bbox", "cy", "x0", ...}``
    """
    engine = _ensure_engine()
    if engine is None:
        return {"items": [], "error": "RapidOCR 未正确安装或初始化失败"}

    try:
        with _engine_lock:
            res = engine(image)
    except Exception as e:
        logger.warning("OCR 识别异常（page=%s）: %s", page, e)
        return {"items": [], "error": f"OCR 识别异常: {e}"}

    # 实测：一条都没识别到时 RapidOCR 返回 None（不是空对象）
    if res is None or not getattr(res, "txts", None):
        return {"items": [], "error": None}

    boxes = getattr(res, "boxes", None)
    txts = list(getattr(res, "txts", ()) or ())
    scores = list(getattr(res, "scores", ()) or ())

    items: list[dict] = []
    for i, raw_text in enumerate(txts):
        text = str(raw_text or "").strip()
        if not text:
            continue
        conf = float(scores[i]) if i < len(scores) else 1.0
        geo = _metrics(boxes[i]) if boxes is not None and i < len(boxes) else None
        items.append({
            "text": text,
            "confidence": round(conf, 4),
            # page 必须在这里显式带上：`_metrics` 只返回几何量，
            # 漏掉它会让排序键 (page, cy, x0) 拿不到页码 → 多页顺序被打乱。
            "page": page,
            "bbox": _corners(boxes[i]) if boxes is not None and i < len(boxes) else [],
            "cy": geo["cy"] if geo else float(i),      # 无框时退化为返回顺序，保证仍有确定性
            "x0": geo["x0"] if geo else 0.0,
            "top": geo["top"] if geo else 0.0,
            "bottom": geo["bottom"] if geo else 0.0,
            "h": geo["h"] if geo else 1.0,
            "_i": i,
        })
    return {"items": items, "error": None}


def ocr_pages(images) -> dict:
    """对**多页**依次 OCR 并按阅读顺序合并。

    :param images: ``[(page_no, image), ...]``
    :returns: ``{"items", "error", "page_count"}``（items 已按页与阅读行排好序）
    """
    all_items: list[dict] = []
    errors: list[str] = []
    for page_no, image in images:
        r = ocr_image(image, page=page_no)
        if r.get("error"):
            errors.append(f"第{page_no}页：{r['error']}")
        all_items.extend(r.get("items") or [])

    ordered = _sort_events(all_items)
    for ev in ordered:
        ev.pop("_i", None)
    return {
        "items": ordered,
        # 单页失败不整体失败（其余页仍可用）；全失败时把原因带出去
        "error": ("；".join(errors) if errors and not ordered else None),
        "page_count": len(list(images)),
    }


def items_to_paragraphs(items: list[dict]) -> list[dict]:
    """OCR 事件 → 现有 `paragraphs` 结构（与 parse_pdf / parse_docx 同形）。

    每个识别框一行文本；`page` 保留真实页码；`is_heading` 由下游 `_parse_headings` 判定，
    这里不猜（保持与 PDF 路径一致）。
    """
    out = []
    for idx, it in enumerate(items):
        out.append({
            "text": it["text"],
            "page": it.get("page", 1),
            "index": idx,
            "style": None,
            "is_heading": False,
            "ocr_confidence": it.get("confidence"),
            "bbox": it.get("bbox") or [],
        })
    return out


def items_to_text(items: list[dict]) -> str:
    """OCR 事件 → 全文（**每行一个识别框**，行间用 ``\\n`` 分隔）。

    这直接决定 `parsed_text`，而 `intermediate_docx.build_from_parsed_text` 是
    「一行一段落」，因此 **行序 = 段序 = R09 的 paragraph_index**，三者天然一致。
    """
    return "\n".join(str(it.get("text") or "") for it in items)
