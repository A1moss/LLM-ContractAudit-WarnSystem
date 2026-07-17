from .pdf_parser import parse_pdf
from .docx_parser import parse_docx


def detect_and_parse(file_path: str) -> dict:
    suffix = file_path.split(".")[-1].lower()
    if suffix == "pdf":
        return parse_pdf(file_path)
    elif suffix == "docx":
        return parse_docx(file_path)
    else:
        raise ValueError(f"不支持的文件格式：.{suffix}，仅支持pdf、docx")
