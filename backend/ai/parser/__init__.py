"""ai.parser — 输入格式统一适配层。

三种输入最终只产出**同一个东西**：`full_text`（→ `contracts.parsed_text`）。
之后的审核 / 修改 / 导出链路对所有格式完全同构。

    DOCX       → parse_docx  → full_text
    纯文本 PDF  → parse_pdf   → full_text          （**行为未变**）
    纯扫描 PDF  → scan_pdf    → 逐页栅格化 → OCR → full_text
    混合 PDF    → 逐页判定：有文字层的页用 parse_pdf 的文本、无文字层的页走 OCR，
                  再按页码升序合并 → full_text
    JPG/PNG    → parse_image → OCR               → full_text
                 （TIFF/TIF/BMP 仍在既有白名单内，走同一条图片路径）

PDF 的逐页判定放在这里，且**只在确实存在无文字层的页时才进入合并路径**：

- 整份都有文字层（纯文本 PDF）→ 原样返回 `parse_pdf` 的结果，逐字不变；
- 整份都没有文字层（纯扫描件）→ 原样走 `parse_scanned_pdf`，逐字不变；
- 同一文件里「文字页 + 扫描页」混合 → 按页码升序合并，两类页面的正文都进入 `full_text`，
  且已有文字层的页**不会被重复 OCR**（`scan_page_numbers()` 只挑出无文字层的页）。

合并只为「不丢页」服务，不引入第二套 PDF 解析：文字来自 `parse_pdf`，
扫描页来自 `parse_scanned_pdf(page_numbers=...)`，二者都未改动。
"""
import logging

from .pdf_parser import parse_pdf
from .docx_parser import parse_docx
from .ocr_parser import parse_image
from .scan_pdf import parse_scanned_pdf, scan_page_numbers

logger = logging.getLogger(__name__)

IMAGE_EXTS = {"jpg", "jpeg", "png", "tiff", "tif", "bmp"}


def _paragraphs_by_page(paragraphs) -> dict:
    """把 parser 产出的 `paragraphs` 按页号归组（页内保持原有顺序，空文本丢弃）。"""
    out: dict = {}
    for para in paragraphs or []:
        page_no = para.get("page")
        if not isinstance(page_no, int) or isinstance(page_no, bool):
            continue
        text = (para.get("text") or "").strip()
        if text:
            out.setdefault(page_no, []).append(text)
    return out


def _merge_hybrid_pdf(text_parsed: dict, scanned: dict, scanned_pages: list) -> dict:
    """把「文字层解析结果」与「扫描页 OCR 结果」按**页码升序**合并成一份完整结果。

    - 有文字层的页：只沿用 `parse_pdf` 已抽出的逐行文本（不重复 OCR）；
    - 无文字层的页：沿用该页 OCR 出来的识别行；
    - 扫描页 OCR 什么都拿不到时，退回该页的文字层内容（如有），不让整页凭空消失。

    返回结构与 `parse_pdf` 同形，供上传端点继续只读 `full_text`。
    """
    text_by_page = _paragraphs_by_page(text_parsed.get("paragraphs"))
    ocr_by_page = _paragraphs_by_page(scanned.get("paragraphs"))
    ocr_pages = sorted({p for p in (scanned_pages or []) if isinstance(p, int)})
    ocr_page_set = set(ocr_pages)

    page_count = text_parsed.get("page_count") or scanned.get("page_count") or 0
    pages = sorted(set(text_by_page) | set(ocr_by_page) | ocr_page_set)

    lines: list = []
    paragraphs: list = []
    for page_no in pages:
        page_lines = ocr_by_page.get(page_no) if page_no in ocr_page_set else None
        if not page_lines:
            page_lines = text_by_page.get(page_no) or []
        for line in page_lines:
            lines.append(line)
            paragraphs.append({
                "text": line,
                "page": page_no,
                "index": len(paragraphs),
                "style": None,
                "is_heading": False,
            })

    empty_ocr_pages = [p for p in ocr_pages if not ocr_by_page.get(p)]
    if empty_ocr_pages:
        logger.warning("混合 PDF：第 %s 页无文字层，且 OCR 未识别到文字", empty_ocr_pages)

    return {
        "full_text": "\n".join(lines),
        "paragraphs": paragraphs,
        "format": "pdf",
        "page_count": page_count,
        # 刻意不设 ocr_quality：文字层内容本身可用，不能因为扫描页 OCR 质量低就把整份合同
        # 判成不可用（上游 upload 的 OCR 质量闸只针对「整份都是 OCR 产物」的输入，
        # 见 api/contracts.py 的 ocr_quality 判定）。质量信息仍以 ocr_* 字段透出，便于排查。
        "ocr_confidence": scanned.get("ocr_confidence"),
        "ocr_valid_chars": scanned.get("ocr_valid_chars"),
        "ocr_reason": scanned.get("ocr_reason"),
        "scanned_pages": ocr_pages,
        "truncated_pages": list(scanned.get("truncated_pages") or []),
        "hybrid_pdf": True,
    }


def _parse_pdf_input(file_path: str) -> dict:
    """PDF 入口：逐页判定文字层，必要时做「文字页 + 扫描页」混合合并。"""
    parsed = parse_pdf(file_path)

    if not (parsed.get("full_text") or "").strip():
        # 整份都没有文字层 → 纯扫描件：沿用既有行为（不传 page_numbers，自动挑页）
        scanned = parse_scanned_pdf(file_path)
        # OCR 也拿不到内容时，保留原 parse_pdf 的结果形态（让上游沿用既有错误提示）
        if not (scanned.get("full_text") or "").strip() and not scanned.get("error"):
            return parsed
        return scanned

    # 有文字层。若每一页都产出了正文，就不可能存在无文字层的页 → 直接返回
    # （纯文本 PDF 零改动，也省掉一次无谓的重复 open）。
    covered_pages = {
        p.get("page") for p in (parsed.get("paragraphs") or [])
        if isinstance(p.get("page"), int)
    }
    page_count = parsed.get("page_count") or 0
    if page_count and len(covered_pages) >= page_count:
        return parsed

    # 逐页确认是否存在**无文字层**的页（混合 PDF）；判定复用既有 scan_page_numbers。
    import pdfplumber

    try:
        with pdfplumber.open(file_path) as pdf:
            scanned_pages = scan_page_numbers(pdf)
    except Exception as e:
        logger.warning("混合 PDF 逐页判定失败，退回整份文本解析: %s", e)
        return parsed

    if not scanned_pages:
        return parsed          # 纯文本 PDF：行为与改造前完全一致

    logger.info("检测到混合 PDF：文字层之外还有第 %s 页需要 OCR", scanned_pages)
    scanned = parse_scanned_pdf(file_path, page_numbers=scanned_pages)
    if not (scanned.get("full_text") or "").strip():
        # 扫描页 OCR 无产出（OCR 不可用或识别失败）：保留文字层内容，
        # 不因为部分页失败而丢掉整份合同（与 parse_scanned_pdf「单页失败不整体失败」一致）。
        logger.warning(
            "混合 PDF：第 %s 页 OCR 未产出任何文字（OCR 不可用或识别失败），仅保留文字层内容",
            scanned_pages,
        )
        return parsed
    return _merge_hybrid_pdf(parsed, scanned, scanned_pages)


def detect_and_parse(file_path: str) -> dict:
    suffix = file_path.split(".")[-1].lower()
    if suffix == "pdf":
        return _parse_pdf_input(file_path)
    elif suffix == "docx":
        return parse_docx(file_path)
    elif suffix in IMAGE_EXTS:
        return parse_image(file_path)
    else:
        raise ValueError(f"不支持的文件格式：.{suffix}，支持 pdf/docx/jpg/png/tiff")
