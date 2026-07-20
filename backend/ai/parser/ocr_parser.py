"""
ai.parser.ocr_parser — 图片/扫描件合同 OCR 识别
使用 PaddleOCR 提取图片中的中文文本。
仅在真正需要 OCR 时才初始化模型（懒加载）。
"""
import logging

logger = logging.getLogger(__name__)

_ocr = None
_ocr_init_attempted = False


def _get_ocr():
    """Lazy-init PaddleOCR — only when parse_image() is actually called."""
    global _ocr, _ocr_init_attempted
    if _ocr is not None and _ocr_init_attempted:
        return _ocr
    _ocr_init_attempted = True
    try:
        import os
        # Suppress PaddleOCR verbose model creation logs
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
        from paddleocr import PaddleOCR
        _ocr = PaddleOCR(lang="ch", show_log=False)
        logger.info("PaddleOCR 初始化完成")
        return _ocr
    except Exception as e:
        logger.warning("PaddleOCR 不可用（仅影响扫描件/图片合同）: %s", e)
        return None


def parse_image(file_path: str) -> dict:
    """
    使用 PaddleOCR 识别图片/扫描件中的合同文本。

    Args:
        file_path: 图片文件路径（支持 .jpg/.png/.tiff/.bmp）

    Returns:
        dict: {full_text, paragraphs, format, page_count, ocr_quality}
    """
    ocr = _get_ocr()
    if ocr is None:
        return {
            "full_text": "",
            "paragraphs": [],
            "format": "image",
            "page_count": 1,
            "ocr_quality": "low",
            "error": "PaddleOCR 未正确安装或初始化失败",
        }

    result = _ocr.ocr(file_path)
    if not result or not result[0]:
        return {
            "full_text": "",
            "paragraphs": [],
            "format": "image",
            "page_count": 1,
            "ocr_quality": "low",
            "error": "OCR 未识别到任何文字",
        }

    paragraphs = []
    low_conf_count = 0
    total = 0

    for idx, line in enumerate(result[0]):
        text = line[1][0] if line[1] else ""
        confidence = line[1][1] if len(line[1]) > 1 else 1.0
        if confidence < 0.6:
            low_conf_count += 1
        total += 1

        if text.strip():
            paragraphs.append({
                "text": text.strip(),
                "page": 1,
                "index": idx,
                "style": None,
                "is_heading": False,
                "ocr_confidence": round(confidence, 3),
            })

    full_text = "\n".join(p["text"] for p in paragraphs)
    ocr_quality = "low" if (total > 0 and low_conf_count / total > 0.3) else "normal"

    logger.info(f"OCR 完成: {len(paragraphs)} 行, 质量={ocr_quality}")

    return {
        "full_text": full_text,
        "paragraphs": paragraphs,
        "format": "image",
        "page_count": 1,
        "ocr_quality": ocr_quality,
    }
