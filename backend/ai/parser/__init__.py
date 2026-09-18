"""ai.parser — 输入格式统一适配层。

三种输入最终只产出**同一个东西**：`full_text`（→ `contracts.parsed_text`）。
之后的审核 / 修改 / 导出链路对所有格式完全同构。

    DOCX      → parse_docx  → full_text
    普通 PDF   → parse_pdf   → full_text          （**行为未变**）
    扫描 PDF   → scan_pdf    → 逐页栅格化 → OCR → full_text
    JPG/PNG   → parse_image → OCR               → full_text
                （TIFF/TIF/BMP 仍在既有白名单内，走同一条图片路径）

扫描 PDF 的判定放在这里：**先按原样跑 parse_pdf**，只有当它确实没拿到文字
（无文字层）时才转入 OCR。这样普通文字 PDF 的解析路径与结果**逐字不变**。
"""
from .pdf_parser import parse_pdf
from .docx_parser import parse_docx
from .ocr_parser import parse_image
from .scan_pdf import parse_scanned_pdf

IMAGE_EXTS = {"jpg", "jpeg", "png", "tiff", "tif", "bmp"}


def detect_and_parse(file_path: str) -> dict:
    suffix = file_path.split(".")[-1].lower()
    if suffix == "pdf":
        parsed = parse_pdf(file_path)
        # 有文字层 → 原样返回（普通 PDF 行为零改动）
        if (parsed.get("full_text") or "").strip():
            return parsed
        # 无文字层 → 扫描件：逐页栅格化 + OCR
        scanned = parse_scanned_pdf(file_path)
        # OCR 也拿不到内容时，保留原 parse_pdf 的结果形态（让上游沿用既有错误提示）
        if not (scanned.get("full_text") or "").strip() and not scanned.get("error"):
            return parsed
        return scanned
    elif suffix == "docx":
        return parse_docx(file_path)
    elif suffix in IMAGE_EXTS:
        return parse_image(file_path)
    else:
        raise ValueError(f"不支持的文件格式：.{suffix}，支持 pdf/docx/jpg/png/tiff")
