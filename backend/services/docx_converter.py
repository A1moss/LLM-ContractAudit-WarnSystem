"""
docx -> PDF conversion service.
Uses Microsoft Word COM automation (Windows only).
100% fidelity: fonts, pagination, layout all preserved.
"""
import os
import pythoncom
import win32com.client


def docx_to_pdf(docx_path: str) -> str:
    """Convert .docx to .pdf using Word COM. Returns pdf path."""
    pdf_path = os.path.splitext(docx_path)[0] + '.pdf'

    # Cache: reuse if PDF is newer than docx
    if os.path.exists(pdf_path) and os.path.getmtime(pdf_path) >= os.path.getmtime(docx_path):
        return pdf_path

    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.Dispatch('Word.Application')
        word.Visible = False
        word.DisplayAlerts = False

        doc = word.Documents.Open(docx_path, ReadOnly=True)
        doc.SaveAs(pdf_path, FileFormat=17)  # wdFormatPDF
        doc.Close()
        return pdf_path
    finally:
        if word:
            try:
                word.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
