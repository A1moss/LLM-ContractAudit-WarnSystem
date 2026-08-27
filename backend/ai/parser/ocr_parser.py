"""
ai.parser.ocr_parser — 图片/扫描件合同 OCR 识别
使用 PaddleOCR 提取图片中的中文文本。

适配 PaddleOCR 3.x API（predict() 返回 list[OCRResult]，每个结果
为 dict，通过 ["rec_texts"] / ["rec_scores"] 访问识别文本与置信度）。
为兼容 2.x 结果结构（result[0][i] = [box, (text, conf)]），解析时做
双格式探测，保证新旧版本都能工作。
"""
import logging
import threading

logger = logging.getLogger(__name__)

# 惰性初始化：PaddleOCR 首次实例化会下载模型（较大），
# 不能在模块 import 时触发，否则后端启动会被阻塞。
_ocr = None
_ocr_available = False
_ocr_init_tried = False
_ocr_lock = threading.Lock()


def _ensure_ocr():
    """惰性初始化 PaddleOCR 实例，线程安全，只尝试一次。"""
    global _ocr, _ocr_available, _ocr_init_tried
    if _ocr_init_tried:
        return _ocr
    with _ocr_lock:
        if _ocr_init_tried:
            return _ocr
        _ocr_init_tried = True
        try:
            from paddleocr import PaddleOCR
            kwargs = {"lang": "ch"}
            # PaddlePaddle 3.3.0+ 存在 PIR→oneDNN 回归 bug（ConvertPirAttribute2RuntimeAttribute
            # not support pir::ArrayAttribute），Windows/Linux CPU 推理全崩。FLAGS_use_mkldnn
            # 环境变量无效（PaddleX 独立控制 run_mode），必须显式传 enable_mkldnn=False。
            try:
                import paddle
                ver = tuple(int(x) for x in paddle.__version__.split(".")[:2])
                if ver >= (3, 3):
                    kwargs["enable_mkldnn"] = False
                    logger.warning(
                        f"PaddlePaddle {paddle.__version__}: 禁用 oneDNN 以规避 PIR 转换 bug"
                    )
            except Exception:
                pass
            # lang="ch" 默认映射到 PP-OCRv6 中英文模型，精度最高
            _ocr = PaddleOCR(**kwargs)
            _ocr_available = True
            logger.info("PaddleOCR 初始化成功")
        except Exception as e:
            logger.warning(f"PaddleOCR 初始化失败，OCR 功能不可用: {e}")
            _ocr = None
            _ocr_available = False
    return _ocr


def _parse_result(result) -> dict:
    """
    解析 PaddleOCR 结果，兼容 3.x 与 2.x 两种返回结构。

    3.x: result = [OCRResult, ...]，OCRResult 为 dict，
         ["rec_texts"]=文本列表, ["rec_scores"]=置信度列表。
    2.x: result = [ [ [box, (text, conf)], ... ], ... ]。
    """
    paragraphs = []
    low_conf_count = 0
    total = 0

    # 3.x：每个元素是 dict，带 rec_texts / rec_scores
    if result and isinstance(result[0], dict):
        for page_result in result:
            texts = page_result.get("rec_texts", []) or []
            scores = page_result.get("rec_scores", []) or []
            for i, raw_text in enumerate(texts):
                # 文本可能是 str，也可能是 (text, conf) 元组
                text = raw_text[0] if isinstance(raw_text, (tuple, list)) else raw_text
                confidence = 1.0
                if i < len(scores):
                    confidence = float(scores[i])
                if confidence < 0.6:
                    low_conf_count += 1
                total += 1
                if str(text).strip():
                    paragraphs.append({
                        "text": str(text).strip(),
                        "page": 1,
                        "index": i,
                        "style": None,
                        "is_heading": False,
                        "ocr_confidence": round(confidence, 3),
                    })
    # 2.x：嵌套列表 [box, (text, conf)]
    else:
        for page in result or []:
            for idx, line in enumerate(page):
                try:
                    text = line[1][0] if line[1] else ""
                    confidence = line[1][1] if len(line[1]) > 1 else 1.0
                except Exception:
                    continue
                if confidence < 0.6:
                    low_conf_count += 1
                total += 1
                if str(text).strip():
                    paragraphs.append({
                        "text": str(text).strip(),
                        "page": 1,
                        "index": idx,
                        "style": None,
                        "is_heading": False,
                        "ocr_confidence": round(float(confidence), 3),
                    })

    full_text = "\n".join(p["text"] for p in paragraphs)
    ocr_quality = "low" if (total > 0 and low_conf_count / total > 0.3) else "normal"
    return full_text, paragraphs, ocr_quality, total


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
    ocr = _ensure_ocr()
    if not _ocr_available or ocr is None:
        return {
            "full_text": "",
            "paragraphs": [],
            "format": "image",
            "page_count": 1,
            "ocr_quality": "low",
            "error": "PaddleOCR 未正确安装或初始化失败",
        }

    try:
        with _ocr_lock:
            result = ocr.predict(file_path)
    except Exception as e:
        logger.warning(f"OCR 识别异常: {e}")
        return {
            "full_text": "",
            "paragraphs": [],
            "format": "image",
            "page_count": 1,
            "ocr_quality": "low",
            "error": f"OCR 识别异常: {e}",
        }

    full_text, paragraphs, ocr_quality, total = _parse_result(result)

    if not full_text.strip():
        return {
            "full_text": "",
            "paragraphs": [],
            "format": "image",
            "page_count": 1,
            "ocr_quality": "low",
            "error": "OCR 未识别到任何文字",
        }

    logger.info(f"OCR 完成: {len(paragraphs)} 行, 质量={ocr_quality}")

    return {
        "full_text": full_text,
        "paragraphs": paragraphs,
        "format": "image",
        "page_count": 1,
        "ocr_quality": ocr_quality,
    }
