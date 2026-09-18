"""ai.parser.ocr_parser — 图片/扫描件合同 OCR 识别（RapidOCR 引擎）。

职责分工（**本文件只做"适配"**）：
  - `ai.parser.ocr_engine`   ：引擎封装、阅读顺序、质量评估（唯一 OCR 引擎入口）；
  - `ai.parser.ocr_parser`   ：把引擎结果整理成与 `parse_pdf` / `parse_docx` **同形**的
                               `{full_text, paragraphs, format, page_count, ocr_quality}`；
  - `ai.parser.scan_pdf`     ：扫描 PDF 逐页栅格化 → 复用本文件的 `parse_image`。

历史沿革：本文件原先是 PaddleOCR 直连实现（含 2.x/3.x 双格式探测）。
改为 RapidOCR 后，双引擎不再维护；对上层保持**完全相同的函数签名与返回字段**，
因此 `detect_and_parse` / `upload_contract` 的调用方式不需要变。
"""
import logging

from . import ocr_engine

logger = logging.getLogger(__name__)


def _ensure_ocr():
    """兼容入口（`services/warmup.py` 用它预热）。

    返回可用的引擎实例，或 None；语义与改造前一致：
      - 惰性、只尝试一次、线程安全；
      - 失败只告警，不抛异常、不阻塞启动。
    """
    return ocr_engine._ensure_engine()


def parse_image(file_path: str) -> dict:
    """识别**单张图片**中的合同文本（.jpg/.jpeg/.png/.tiff/.tif/.bmp）。

    Returns:
        dict: {
            "full_text": "OCR 提取全文（每行一个识别框）",
            "paragraphs": [{"text", "page", "index", "style", "is_heading",
                            "ocr_confidence", "bbox"}],
            "format": "image",
            "page_count": 1,
            "ocr_quality": "normal" | "low",
            "ocr_confidence": 平均置信度,
            "ocr_valid_chars": 有效字数,
            "error": 仅在失败时出现
        }
    """
    if not ocr_engine.ocr_available():
        return {
            "full_text": "", "paragraphs": [], "format": "image", "page_count": 1,
            "ocr_quality": "low", "ocr_confidence": 0.0, "ocr_valid_chars": 0,
            "error": "RapidOCR 未正确安装或初始化失败",
        }

    result = ocr_engine.ocr_pages([(1, file_path)])
    items = result.get("items") or []
    if result.get("error") and not items:
        return {
            "full_text": "", "paragraphs": [], "format": "image", "page_count": 1,
            "ocr_quality": "low", "ocr_confidence": 0.0, "ocr_valid_chars": 0,
            "error": result["error"],
        }
    if not items:
        return {
            "full_text": "", "paragraphs": [], "format": "image", "page_count": 1,
            "ocr_quality": "low", "ocr_confidence": 0.0, "ocr_valid_chars": 0,
            "error": "OCR 未识别到任何文字",
        }

    quality = ocr_engine.assess_quality(items, page_count=1)
    full_text = ocr_engine.items_to_text(items)
    paragraphs = ocr_engine.items_to_paragraphs(items)
    logger.info("OCR 完成: %d 行, 均分=%.4f, 有效字数=%d, 可用=%s",
                len(items), quality["mean_confidence"], quality["valid_chars"], quality["usable"])

    return {
        "full_text": full_text,
        "paragraphs": paragraphs,
        "format": "image",
        "page_count": 1,
        # 兼容既有字段：可用即 normal，不可用即 low（质量细节另附）
        "ocr_quality": "normal" if quality["usable"] else "low",
        "ocr_confidence": quality["mean_confidence"],
        "ocr_valid_chars": quality["valid_chars"],
        "ocr_reason": quality["reason"],
    }
