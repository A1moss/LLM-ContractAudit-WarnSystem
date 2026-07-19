# .docx -> PDF 转换方案（给 C 角色）

## 需求

`GET /api/contracts/{id}/file` 接口，当合同文件是 .docx 时，应返回转换后的 PDF，使前端 pdf.js 能渲染出和原 Word 完全一致的分页和排版。

## 方案 A：Word COM 自动化（推荐，Windows）

需要服务器安装 Microsoft Word。转换效果 100% 还原。

```python
# backend/services/docx_converter.py
import os, tempfile
import pythoncom
import win32com.client

def docx_to_pdf(docx_bytes: bytes) -> bytes:
    pythoncom.CoInitialize()
    try:
        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
            f.write(docx_bytes)
            docx_path = f.name
        pdf_path = docx_path.replace('.docx', '.pdf')

        word = win32com.client.Dispatch('Word.Application')
        word.Visible = False
        doc = word.Documents.Open(docx_path)
        doc.SaveAs(pdf_path, FileFormat=17)  # wdFormatPDF
        doc.Close()
        word.Quit()

        with open(pdf_path, 'rb') as f:
            return f.read()
    finally:
        try: os.unlink(docx_path)
        except: pass
        try: os.unlink(pdf_path)
        except: pass
        pythoncom.CoUninitialize()
```

## 方案 B：LibreOffice（跨平台，免费）

```python
import subprocess, tempfile, os

def docx_to_pdf(docx_bytes: bytes) -> bytes:
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
        f.write(docx_bytes)
        docx_path = f.name
    out_dir = tempfile.mkdtemp()

    subprocess.run([
        'libreoffice', '--headless', '--convert-to', 'pdf',
        '--outdir', out_dir, docx_path
    ], check=True, timeout=30)

    base = os.path.splitext(os.path.basename(docx_path))[0]
    pdf_path = os.path.join(out_dir, base + '.pdf')

    with open(pdf_path, 'rb') as f:
        result = f.read()

    os.unlink(docx_path)
    os.unlink(pdf_path)
    os.rmdir(out_dir)
    return result
```

## 接口修改

在文件下载路由中加判断：

```python
@router.get("/contracts/{id}/file")
async def get_contract_file(id: int, ...):
    ...
    file_bytes = read_from_storage(contract.file_path)

    # .docx 自动转 PDF
    if contract.file_name.lower().endswith('.docx'):
        file_bytes = docx_to_pdf(file_bytes)
        return Response(content=file_bytes, media_type='application/pdf')

    return Response(content=file_bytes, media_type='application/octet-stream')
```

## 前端侧（已完成）

ContractDetail.vue 已实现：
- .pdf -> pdf.js 直接渲染
- .docx -> 调 API -> 后端转 PDF -> pdf.js 渲染
- 缩放/翻页/跳转 UI 统一
