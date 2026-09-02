"""
_extract_doc.py — 单文件 .doc/.wps 文本抽取（WPS COM 优先，Word COM 兜底）

供 build_testset.py 用 subprocess + timeout 调用，隔离 Office COM 偶发挂死：
个别损坏文件会让 Office 打开时挂死，放独立子进程里，挂死只杀本子进程。

优先 WPS（Kwps.Application）：这些 .doc 多为 WPS 生成，WPS 对「扩展名与格式
不匹配」不敏感，且不会像 Word 那样在个别损坏文件上挂死；Word 作兜底。

用法：python _extract_doc.py <文件路径>
输出：正文写到 stdout（UTF-8）；失败返回非 0。
"""
import sys
import shutil
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    src = Path(sys.argv[1])
    tmpdir = Path(tempfile.mkdtemp(prefix="docx_"))
    app = None
    co_initialized = False
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        co_initialized = True

        target = src
        progid = None
        for pid in ("Kwps.Application", "Word.Application"):
            try:
                app = win32com.client.Dispatch(pid)
                progid = pid
                break
            except Exception:
                continue
        if app is None:
            sys.stderr.write("no Office COM app available\n")
            return 1

        app.Visible = False
        try:
            app.DisplayAlerts = 0
            app.AutomationSecurity = 3
        except Exception:
            pass

        # Word 对扩展名严格：.doc 内容配 .docx 扩展名会拒绝打开 → 临时拷成 .doc；
        # WPS 不敏感，直接开原文件。
        if progid == "Word.Application" and src.suffix.lower() != ".doc":
            target = tmpdir / "tmp.doc"
            shutil.copy2(src, target)

        doc = app.Documents.Open(str(target))
        text = doc.Content.Text
        try:
            doc.Close(False)
        except Exception:
            try:
                doc.Close()
            except Exception:
                pass
        sys.stdout.write(text.replace("\r\n", "\n").replace("\r", "\n"))
        return 0
    except Exception as e:
        sys.stderr.write(f"{type(e).__name__}: {e}\n")
        return 1
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        if co_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
