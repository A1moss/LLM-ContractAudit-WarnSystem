"""
docx -> PDF conversion service.
Priority: Word COM -> LibreOffice CLI -> error.
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

    raise RuntimeError("No Word or LibreOffice available for .docx -> PDF conversion")


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
            doc.SaveAs(pdf_path, FileFormat=17)
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


def _find_soffice():
    """Locate soffice executable. Returns path or None."""
    found = shutil.which('soffice')
    if found:
        return found

    if os.name == 'nt':
        # Common base dirs
        bases = []
        for key in ['ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432']:
            v = os.environ.get(key, '')
            if v:
                bases.append(v)
        bases.append('D:\\Program Files')

        for base in bases:
            if not base or not os.path.isdir(base):
                continue
            try:
                for root, dirs, _ in os.walk(base):
                    depth = root.replace(base, '').count(os.sep)
                    if depth > 2:
                        dirs.clear()
                        continue
                    if 'LibreOffice' in os.path.basename(root):
                        p = os.path.join(root, 'program', 'soffice.exe')
                        if os.path.exists(p):
                            return p
                    lo = os.path.join(root, 'LibreOffice')
                    if os.path.isdir(lo):
                        p = os.path.join(lo, 'program', 'soffice.exe')
                        if os.path.exists(p):
                            return p
            except (PermissionError, OSError):
                pass

        # Registry
        try:
            import winreg
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                for kp in [r"SOFTWARE\LibreOffice\UNO\InstallPath",
                           r"SOFTWARE\WOW6432Node\LibreOffice\UNO\InstallPath"]:
                    try:
                        with winreg.OpenKey(hive, kp) as key:
                            d = winreg.QueryValueEx(key, "")[0]
                            p = os.path.join(d, "program", "soffice.exe")
                            if os.path.exists(p):
                                return p
                    except OSError:
                        pass
        except Exception:
            pass

    # macOS
    mp = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if os.path.exists(mp):
        return mp

    # Linux
    for n in ['libreoffice', 'soffice']:
        f = shutil.which(n)
        if f:
            return f

    return None


def _try_libreoffice(docx_path: str, pdf_path: str) -> bool:
    soffice = _find_soffice()
    if not soffice:
        print("[docx-converter] LibreOffice not found")
        return False

    out_dir = os.path.dirname(docx_path)
    try:
        print(f"[docx-converter] Using LibreOffice: {soffice}")
        subprocess.run(
            [soffice, '--headless', '--convert-to', 'pdf', '--outdir', out_dir, docx_path],
            check=True, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        expected = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + '.pdf')
        if os.path.exists(expected) and expected != pdf_path:
            os.replace(expected, pdf_path)
        if os.path.exists(pdf_path):
            return True
        print("[docx-converter] LibreOffice ran but PDF not found")
        return False
    except subprocess.TimeoutExpired:
        print("[docx-converter] LibreOffice timed out")
        return False
    except Exception as e:
        print(f"[docx-converter] LibreOffice error: {e}")
        return False
