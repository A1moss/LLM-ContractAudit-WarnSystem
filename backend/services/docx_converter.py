"""
docx -> PDF conversion service.
Priority: Word COM → LibreOffice CLI → error.
"""
import os
import subprocess
import shutil


def docx_to_pdf(docx_path: str) -> str:
    """Convert .docx to .pdf. Returns pdf path or raises on failure."""
    pdf_path = os.path.splitext(docx_path)[0] + '.pdf'

    # Cache: reuse if PDF is newer than docx
    if os.path.exists(pdf_path) and os.path.getmtime(pdf_path) >= os.path.getmtime(docx_path):
        return pdf_path

    # 1) Try Word COM (best quality, Windows only)
    if _try_word_com(docx_path, pdf_path):
        return pdf_path

    # 2) Try LibreOffice CLI (cross-platform)
    if _try_libreoffice(docx_path, pdf_path):
        return pdf_path

    raise RuntimeError("No Word or LibreOffice available for .docx → PDF conversion")


def _try_word_com(docx_path: str, pdf_path: str) -> bool:
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return False

    try:
        pythoncom.CoInitialize()
        word = None
        try:
            word = win32com.client.Dispatch('Word.Application')
            word.Visible = False
            word.DisplayAlerts = False
            doc = word.Documents.Open(docx_path, ReadOnly=True)
            doc.SaveAs(pdf_path, FileFormat=17)  # wdFormatPDF
            doc.Close()
            return os.path.exists(pdf_path)
        finally:
            if word:
                try:
                    word.Quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()
    except Exception:
        return False


def _try_libreoffice(docx_path: str, pdf_path: str) -> bool:
    # Common soffice paths
    candidates = [
        "soffice",                                   # in PATH
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    soffice = None
    for c in candidates:
        if shutil.which(c) or (os.name == 'nt' and os.path.exists(c)):
            soffice = c
            break
    if not soffice:
        return False

    out_dir = os.path.dirname(docx_path)
    try:
        subprocess.run(
            [soffice, '--headless', '--convert-to', 'pdf', '--outdir', out_dir, docx_path],
            check=True, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # LibreOffice names output as <basename>.pdf in out_dir
        expected = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + '.pdf')
        if os.path.exists(expected) and expected != pdf_path:
            os.replace(expected, pdf_path)
        return os.path.exists(pdf_path)
    except Exception:
        return False
