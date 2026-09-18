"""ai.parser.scan_pdf — 扫描型 PDF（无文字层）的逐页栅格化 + OCR。

## 何时进入本模块

**只有**当 PDF 明确没有有效文字层时才走这里（`detect_and_parse` 判定）。
普通文字 PDF 仍走原有 `parse_pdf`，**行为完全不变**：
本模块不修改、也不接管文字层解析。

## 文字层判定（与 upload 端点既有提示口径一致）

对每一页看 `page.extract_text()` 与 `page.chars`：
  - 两者都为空 → 该页是"扫描页"（图片页，无文字层）；
  - 只要有任意一页有文字 → 视为文字 PDF，走原有 `parse_pdf`。
另外要求整篇 `extract_text()` 的可见字符数达到 `MIN_TEXT_LAYER_CHARS`，
避免"只有页码/水印几个字"的伪文字层把整份扫描件误判成文字 PDF。

## 栅格化

`pdfplumber.page.to_image()`，底层是 **pypdfium2**（已随 pdfplumber 安装）。
因此**不需要** poppler / ImageMagick / ghostscript，也不需要 Docker。

## 页边界

各页 OCR 结果**按页序拼接**，页与页之间保留真实换行边界（每页文本自成一段落序列），
不会把第 1 页末句与第 2 页首句无分隔地粘在一起。

## 保护

- 页数上限 `MAX_OCR_PAGES`：超限只 OCR 前 N 页并**明确记录**被截断，
  避免一份几百页的扫描件把请求拖死；
- 单页渲染/OCR 失败只跳过该页并记录，不整体失败（只要还有可用页）。
"""
from __future__ import annotations

import logging

from . import ocr_engine

logger = logging.getLogger(__name__)

# 判定"这份 PDF 到底算不算有文字层"的最低可见字符数
MIN_TEXT_LAYER_CHARS = 20

# 单份扫描件最多 OCR 的页数（超出部分截断并在返回值里说明）
MAX_OCR_PAGES = 50

# 栅格化分辨率（DPI）。150 是"清晰可识别 + 体积可控"的常用档位。
RASTER_DPI = 150


def has_text_layer(pdf) -> bool:
    """判断已打开的 pdfplumber 文档是否**存在有效文字层**。"""
    total = 0
    for page in pdf.pages:
        text = page.extract_text() or ""
        if text.strip():
            total += len(text.strip())
            if total >= MIN_TEXT_LAYER_CHARS:
                return True
        # 有字符对象但 extract_text 为空（罕见排版）也算有文字层
        if getattr(page, "chars", None):
            return True
    return False


def scan_page_numbers(pdf) -> list[int]:
    """返回**需要 OCR 的页号**（1 基）。

    文字 PDF 不该调用本函数；对混合文档（部分页扫描）这里只挑出无文字层的页，
    保证"有文字的页不重复 OCR、不重复计入"。
    """
    pages: list[int] = []
    for i, page in enumerate(pdf.pages, start=1):
        if getattr(page, "chars", None):
            continue
        if (page.extract_text() or "").strip():
            continue
        pages.append(i)
    return pages


def parse_scanned_pdf(file_path: str, page_numbers: list[int] | None = None) -> dict:
    """把扫描 PDF 逐页栅格化后 OCR，返回与 `parse_pdf` **同形**的结果。

    :param page_numbers: 指定要 OCR 的页（1 基）；None = 自动挑出无文字层的页
    :returns: ``{full_text, paragraphs, format, page_count, ocr_quality,
                 ocr_confidence, ocr_valid_chars, scanned_pages, truncated_pages, error?}``
    """
    import pdfplumber

    if not ocr_engine.ocr_available():
        return {
            "full_text": "", "paragraphs": [], "format": "pdf", "page_count": 0,
            "ocr_quality": "low", "ocr_confidence": 0.0, "ocr_valid_chars": 0,
            "scanned_pages": [], "truncated_pages": [],
            "error": "该 PDF 没有文字层（扫描件），且 RapidOCR 未正确安装或初始化失败",
        }

    images: list[tuple[int, object]] = []
    truncated: list[int] = []
    render_errors: list[str] = []
    total_pages = 0

    with pdfplumber.open(file_path) as pdf:
        total_pages = len(pdf.pages)
        targets = page_numbers if page_numbers is not None else scan_page_numbers(pdf)
        if not targets:
            return {
                "full_text": "", "paragraphs": [], "format": "pdf",
                "page_count": total_pages, "ocr_quality": "low",
                "ocr_confidence": 0.0, "ocr_valid_chars": 0,
                "scanned_pages": [], "truncated_pages": [],
                "error": "未能从文件中提取到文字（可能是扫描版 PDF 无文字层）",
            }

        if len(targets) > MAX_OCR_PAGES:
            truncated = targets[MAX_OCR_PAGES:]
            targets = targets[:MAX_OCR_PAGES]
            logger.warning("扫描 PDF 页数过多（%d 页），仅 OCR 前 %d 页",
                           len(truncated) + len(targets), MAX_OCR_PAGES)

        for page_no in targets:
            try:
                # to_image() 底层是 pypdfium2：无需 poppler / ghostscript / ImageMagick。
                # 注意：它返回的是 pdfplumber.display.PageImage（**不是** PIL.Image），
                # OCR 引擎只接受 str/bytes/numpy/PIL，因此必须取 .original 转成 PIL。
                page_image = pdf.pages[page_no - 1].to_image(resolution=RASTER_DPI)
                pil_image = getattr(page_image, "original", None)
                if pil_image is None:
                    raise RuntimeError("to_image() 未返回可用位图")
                images.append((page_no, pil_image.convert("RGB")))
            except Exception as e:
                render_errors.append(f"第{page_no}页：{e}")
                logger.warning("扫描 PDF 第 %d 页栅格化失败: %s", page_no, e)

    if not images:
        return {
            "full_text": "", "paragraphs": [], "format": "pdf", "page_count": total_pages,
            "ocr_quality": "low", "ocr_confidence": 0.0, "ocr_valid_chars": 0,
            "scanned_pages": targets, "truncated_pages": truncated,
            "error": "扫描件页面栅格化失败：" + "；".join(render_errors[:3]),
        }

    result = ocr_engine.ocr_pages(images)
    items = result.get("items") or []
    examined = sorted(p for p, _ in images)

    if not items:
        return {
            "full_text": "", "paragraphs": [], "format": "pdf", "page_count": total_pages,
            "ocr_quality": "low", "ocr_confidence": 0.0, "ocr_valid_chars": 0,
            "scanned_pages": examined, "truncated_pages": truncated,
            "error": result.get("error") or "扫描件未识别到任何文字",
        }

    quality = ocr_engine.assess_quality(items, page_count=len(images))
    logger.info("扫描 PDF OCR 完成: %d 页 / %d 行, 均分=%.4f, 有效字数=%d, 可用=%s",
                len(images), len(items), quality["mean_confidence"],
                quality["valid_chars"], quality["usable"])

    return {
        # 每页的识别框各自成行，页间天然以换行分隔（不会跨页粘连）
        "full_text": ocr_engine.items_to_text(items),
        "paragraphs": ocr_engine.items_to_paragraphs(items),
        "format": "pdf",
        "page_count": total_pages,
        "ocr_quality": "normal" if quality["usable"] else "low",
        "ocr_confidence": quality["mean_confidence"],
        "ocr_valid_chars": quality["valid_chars"],
        "ocr_reason": quality["reason"],
        "scanned_pages": examined,
        "truncated_pages": truncated,
    }
