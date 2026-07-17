from docx import Document


def parse_docx(file_path: str) -> dict:
    doc = Document(file_path)
    full_text = ""
    paragraphs = []

    for idx, para in enumerate(doc.paragraphs):
        text = para.text.strip()
        if not text:
            continue
        style_name = para.style.name if para.style else "Normal"
        is_heading = "Heading" in style_name or "heading" in style_name.lower()

        paragraphs.append({
            "text": text,
            "style": style_name,
            "index": idx,
            "is_heading": is_heading,
            "page": None,
        })
        full_text += text + "\n"

    return {
        "full_text": full_text,
        "paragraphs": paragraphs,
        "format": "docx",
        "page_count": None,
    }
