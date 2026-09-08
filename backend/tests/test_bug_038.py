"""BUG-038 回归测试：文件生命周期（解析失败清理 + 软删除归档 + 不误删）。

覆盖：
1. 解析失败(422) → 已落盘文件被清理，不留孤儿；
2. 正常上传 → 文件保留（不误删）；
3. 软删除 → 文件归档到 data/deleted/；
4. 归档只动目标文件，不影响其他合同文件；文件不存在时不报错。

运行（backend 目录下）：
    python -m unittest tests.test_bug_038 -v
注意：import api.contracts 会拉 classifier/rag(chromadb)/auditor 等（约 40s，一次性）。
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
    def __init__(self, filename, content: bytes):
        self.filename = filename
        self.file = io.BytesIO(content)


class TestBug038Archive(unittest.TestCase):
    """软删除归档：_archive_deleted_file 的行为。"""

    def test_archive_moves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.docx"
            f.write_bytes(b"x")
            with mock.patch.object(contracts, "UPLOAD_DIR", tmp):
                contracts._archive_deleted_file(str(f))
            self.assertFalse(f.exists())  # 活跃目录已移除
            self.assertTrue((Path(tmp) / "deleted" / "a.docx").exists())  # 归档到 deleted/

    def test_archive_does_not_touch_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.docx"
            b = Path(tmp) / "b.docx"
            a.write_bytes(b"a")
            b.write_bytes(b"b")
            with mock.patch.object(contracts, "UPLOAD_DIR", tmp):
                contracts._archive_deleted_file(str(a))
            self.assertFalse(a.exists())
            self.assertTrue(b.exists())
            self.assertEqual(b.read_bytes(), b"b")  # 其他文件不受影响

    def test_archive_missing_file_no_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(contracts, "UPLOAD_DIR", tmp):
                contracts._archive_deleted_file(str(Path(tmp) / "nope.docx"))  # 不抛异常


class TestBug038UploadCleanup(unittest.TestCase):
    """上传解析失败清理 vs 正常上传保留。"""

    def _db(self):
        return mock.MagicMock()

    def _user(self):
        u = mock.MagicMock()
        u.id = 1
        return u

    def test_parse_fail_removes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(contracts, "UPLOAD_DIR", tmp):
                fake = _FakeUploadFile("test.pdf", b"%PDF-1.4 dummy")
                with mock.patch.object(contracts, "detect_and_parse", side_effect=Exception("bad pdf")):
                    with self.assertRaises(HTTPException) as ctx:
                        contracts.upload_contract(
                            file=fake, name=None, contract_type=None, audit_mode="precise",
                            db=self._db(), current_user=self._user(),
                        )
                self.assertEqual(ctx.exception.status_code, 422)
                # 落盘文件已被清理，无孤儿
                self.assertEqual([f for f in Path(tmp).iterdir() if f.is_file()], [])

    def test_upload_success_keeps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(contracts, "UPLOAD_DIR", tmp):
                fake = _FakeUploadFile("test.pdf", b"%PDF-1.4 valid text")
                with mock.patch.object(contracts, "detect_and_parse", return_value={"full_text": "甲乙双方签订本合同。"}), \
                     mock.patch.object(contracts, "classify_contract", return_value={"contract_type": "买卖", "confidence": 0.9, "is_outsourcing": False}), \
                     mock.patch.object(contracts, "extract_elements", return_value={"parties": {}, "amount": None}):
                    res = contracts.upload_contract(
                        file=fake, name="test.pdf", contract_type=None, audit_mode="precise",
                        db=self._db(), current_user=self._user(),
                    )
            self.assertEqual(res["code"], 0)
            files = [f for f in Path(tmp).iterdir() if f.is_file()]
            self.assertEqual(len(files), 1)  # 正常上传保留文件


if __name__ == "__main__":
    unittest.main(verbosity=2)
