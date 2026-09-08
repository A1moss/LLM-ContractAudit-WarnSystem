"""BUG-050 regression：扫描版 PDF/DOCX 空文本 → 422 + 清理孤儿文件（不再静默入库）。
修复前 PDF/DOCX 空文本静默入库，合同永远无法审核。
"""
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-weak-123456")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")

from fastapi import HTTPException  # noqa: E402
import api.contracts as contracts  # noqa: E402


class _FakeUploadFile:
    def __init__(self, filename, content):
        self.filename = filename
        self.file = io.BytesIO(content)


class TestBug050EmptyText(unittest.TestCase):
    def _db(self):
        return mock.MagicMock()

    def _user(self):
        u = mock.MagicMock()
        u.id = 1
        return u

    def test_empty_pdf_422_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(contracts, "UPLOAD_DIR", tmp):
                fake = _FakeUploadFile("scan.pdf", b"%PDF-1.4 empty")
                with mock.patch.object(contracts, "detect_and_parse",
                                       return_value={"full_text": "", "paragraphs": []}):
                    with self.assertRaises(HTTPException) as ctx:
                        contracts.upload_contract(
                            file=fake, name=None, contract_type=None, audit_mode="precise",
                            db=self._db(), current_user=self._user(),
                        )
                self.assertEqual(ctx.exception.status_code, 422)
                # 落盘文件已清理，无孤儿
                self.assertEqual([f for f in Path(tmp).iterdir() if f.is_file()], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
