from .pdf_parser import parse_pdf
from .docx_parser import parse_docx
from .ocr_parser import parse_image

IMAGE_EXTS = {"jpg", "jpeg", "png", "tiff", "tif", "bmp"}


def detect_and_parse(file_path: str) -> dict:
    suffix = file_path.split(".")[-1].lower()
    if suffix == "pdf":
        return parse_pdf(file_path)
    elif suffix == "docx":
        return parse_docx(file_path)
    elif suffix in IMAGE_EXTS:
        return parse_image(file_path)
    else:
        raise ValueError(f"不支持的文件格式：.{suffix}，支持 pdf/docx/jpg/png/tiff")

