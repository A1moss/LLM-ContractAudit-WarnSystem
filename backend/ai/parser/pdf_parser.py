import pdfplumber


def parse_pdf(file_path: str) -> dict:
    full_text = ""
    paragraphs = []
    para_idx = 0

    with pdfplumber.open(file_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if not text:
                continue
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                paragraphs.append({
                    "text": line,
                    "page": page_no,
                    "index": para_idx,
                    "style": None,
                    "is_heading": False,
                })
                para_idx += 1
            full_text += text + "\n"

    return {
        "full_text": full_text,
        "paragraphs": paragraphs,
        "format": "pdf",
        "page_count": len(pdf.pages),
    }
