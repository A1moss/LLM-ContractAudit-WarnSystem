"""
build_testset.py — 从 05_合同/合同范本 生成「分类」测试集（脱敏）

类别来源：ai.taxonomy.ENABLED_TYPES（11 类），归档目录名与类别名一一对应；
无样本的目录（服务外包等）会被跳过并告警。

文本抽取三级策略：
  1. 真 .docx（zip 容器）→ python-docx；
  2. 老 .doc/.wps（OLE 魔数 D0CF11E0，含被改名成 .docx 的）→ WPS/Word COM（子进程+超时隔离）；
  3. .pdf → pdfplumber。
覆盖归档里「.doc 老格式被改名成 .docx」的情况，避免大量样本漏抽。

脱敏规则（防止分类器"读标题/文件名"作弊）：
  删除泄露类型的标题行（短行以「合同/协议」结尾，或含「XX合同/XX协议」）
  + 删除"示范文本"/文号/版本标记。
"""
import re
import sys
import json
import subprocess
import logging
from pathlib import Path
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("build_testset")

# backend/evaluate/ → backend/ → LLM-ContractAudit-WarnSystem/ → 服务外包/
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
VANBEN_DIR = _SERVICE_DIR / "05_合同" / "合同范本"
OUT_DIR = _SERVICE_DIR / "03_数据集" / "测试集"
_HELPER = Path(__file__).parent / "_extract_doc.py"

sys.path.insert(0, str(_BACKEND_DIR))
from ai.taxonomy import ENABLED_TYPES, KIND_TYPICAL, KIND_UNNAMED, kind_of  # noqa: E402

CATEGORIES = ENABLED_TYPES

# OLE 复合文档魔数（老 .doc 二进制格式）
_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")

# 泄露类型的标记：示范文本 / 文号 GF—xxxx—xxxx / 版本（20xx版）
_MARKER_RE = re.compile(
    r"[（(]?示范文本[)）]?"
    r"|[A-Z]{2}[—-]\d{4}[—-]\d{4}"
    r"|[（(]20\d{2}[^)）]*[)）]"
)
# 类型词（19典型+无名+特别法），识别「XX合同/XX协议」标题
_TYPE_TITLE_RE = re.compile(
    r"(买卖|租赁|承揽|建设工程|建设|技术|委托|中介|服务外包|外包|保密|劳动"
    r"|采购|销售|服务|供用电|赠与|借款|保证|融资租赁|保理|运输|保管|仓储|物业|行纪|合伙)"
    r"\s*(合同|协议)"
)


def _archive_dir(cat: str) -> Path:
    """按法理归属定位归档目录：典型→民事合同/典型合同/<名>，无名→…/无名合同/<名>，特别法→顶层/<名>。"""
    k = kind_of(cat)
    if k == KIND_TYPICAL:
        return VANBEN_DIR / "民事合同" / "典型合同" / cat
    if k == KIND_UNNAMED:
        return VANBEN_DIR / "民事合同" / "无名合同" / cat
    return VANBEN_DIR / cat  # 劳动合同 在顶层；其它兜底


def _is_ole(path: Path) -> bool:
    """判断文件是否为 OLE 老 .doc 二进制（与扩展名无关）。"""
    try:
        return path.read_bytes()[:8] == _OLE_MAGIC
    except Exception:
        return False


def _kill_office():
    """清理超时后残留的 Office 进程。"""
    for img in ("wps.exe", "WINWORD.EXE"):
        try:
            subprocess.run(["taskkill", "/F", "/IM", img],
                           capture_output=True, timeout=10)
        except Exception:
            pass


def _extract_doc(path: Path) -> str | None:
    """用独立子进程 + 超时抽取 .doc/.wps 文本，避免 Office 挂死拖垮主流程。"""
    try:
        r = subprocess.run(
            [sys.executable, str(_HELPER), str(path)],
            capture_output=True, timeout=25, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        logger.warning("Office 抽取失败 %s: %s", path.name, (r.stderr or "").strip()[:100])
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Office 抽取超时，跳过 %s", path.name)
        _kill_office()
        return None


def extract_text(path: Path) -> str | None:
    """按真实格式抽文本。"""
    suffix = path.suffix.lower()

    # 老 .doc 二进制（可能被改名成 .docx）→ Office COM
    if _is_ole(path) or suffix in (".doc", ".wps"):
        return _extract_doc(path)

    if suffix == ".docx":
        try:
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
        except Exception:
            return _extract_doc(path)  # 兜底：可能仍是其它 Office 格式

    if suffix == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                return "\n".join((page.extract_text() or "") for page in pdf.pages)
        except Exception as e:
            logger.warning("pdf 解析失败 %s: %s", path.name, e)
            return None

    return None


def deidentify(text: str) -> str:
    """脱敏：删标题行 + 删示范文本/文号/版本标记。"""
    out = []
    for raw in text.split("\n"):
        line = _MARKER_RE.sub("", raw).strip()
        if not line:
            continue
        stripped = line.rstrip("）)")
        if len(line) <= 40 and (stripped.endswith(("合同", "协议")) or _TYPE_TITLE_RE.search(line)):
            continue
        out.append(line)
    return "\n".join(out)


def main():
    if not VANBEN_DIR.exists():
        logger.error("找不到归档目录：%s", VANBEN_DIR)
        sys.exit(1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    entries = []
    seen_stem = set()

    def add_entry(path: Path, cat: str):
        text = extract_text(path)
        if not text:
            return
        entries.append({
            "id": f"T{len(entries) + 1:03d}",
            "file": str(path.relative_to(_SERVICE_DIR)),
            "true_type": cat,
            "text": deidentify(text),
        })

    for cat in CATEGORIES:
        d = _archive_dir(cat)
        if not d.exists():
            logger.warning("缺「%s」目录，跳过（待补样本）", cat)
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() not in (".docx", ".pdf", ".doc", ".wps"):
                continue
            if f.stem in seen_stem:
                continue
            seen_stem.add(f.stem)
            add_entry(f, cat)

    if not entries:
        logger.error("未生成任何样本")
        sys.exit(1)

    out = OUT_DIR / "testset.json"
    out.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    cnt = Counter(e["true_type"] for e in entries)
    logger.info("生成 %d 条测试样本 → %s", len(entries), out)
    for cat in CATEGORIES:
        logger.info("  %s: %d 条", cat, cnt.get(cat, 0))


if __name__ == "__main__":
    main()
