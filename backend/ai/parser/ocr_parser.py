"""
ai.parser.ocr_parser — 图片/扫描件合同 OCR 识别
使用 PaddleOCR 提取图片中的中文文本。
"""
import logging

logger = logging.getLogger(__name__)

try:
    from paddleocr import PaddleOCR
    _ocr = PaddleOCR(lang="ch")
    _ocr_available = True
except Exception as e:
    logger.warning(f"PaddleOCR 初始化失败，OCR 功能不可用: {e}")
    _ocr = None
    _ocr_available = False


def parse_image(file_path: str) -> dict:
    """
    使用 PaddleOCR 识别图片/扫描件中的合同文本。

    Args:
        file_path: 图片文件路径（支持 .jpg/.png/.tiff/.bmp）

    Returns:
        dict: {
            "full_text": "OCR 提取全文",
            "paragraphs": [{"text": "...", "page": 1, "index": 0, "style": None, "is_heading": False}],
            "format": "image",
            "page_count": 1,
            "ocr_quality": "normal" | "low"
        }
    """
    if not _ocr_available or _ocr is None:
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
