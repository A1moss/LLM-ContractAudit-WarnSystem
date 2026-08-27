"""
build_testset.py — 从 05_合同范本 生成「分类」测试集（脱敏）

用途：把 05_合同范本 里 6 类合同（采购/销售/买卖/保密/服务/劳动）抽成
      {id, file, true_type, text} 的 JSON 测试集，供 evaluate_classifier.py 算分类准确率。

脱敏规则（防止分类器"读标题/文件名"作弊，保证准确率是真实语义判断）：
  1. 删除首行标题（含"合同/协议"且较短的一行）；
  2. 删除"示范文本"、文号"GF—xxxx—xxxx"、版本"（20xx版）"等泄露类型的标记。

说明：
  - 标准库里的 word/pdf 是同一份合同的两种格式，只取 word（优先 docx），不重复计数；
  - .doc/.wps 老格式需 LibreOffice 转换，这里先跳过并告警（劳动合同目前只有 .doc，会缺样本）。

用法（任意位置运行，脚本自动定位路径）：
    python backend/evaluate/build_testset.py
"""
import os
import re
import sys
import json
import logging
from pathlib import Path
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("build_testset")

# backend/evaluate/ → backend/ → LLM-ContractAudit-WarnSystem/ → 服务外包/
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent  # 服务外包/（05_合同范本、03_数据集 都在这一层）
VANBEN_DIR = _SERVICE_DIR / "05_合同范本"
OUT_DIR = _SERVICE_DIR / "03_数据集" / "测试集"

# 6 类 → 范本子目录（相对 05_合同范本）。外包合同归入"服务合同"。
CATEGORY_DIRS = {
    "采购合同": ["采购合同", "标准库/采购合同-word"],
    "销售合同": ["销售合同", "标准库/销售合同-word"],
    "买卖合同": ["买卖合同"],
    "保密合同": ["保密合同"],
    "服务合同": ["服务合同", "标准库/外包合同-word"],
    "劳动合同": [],
}
# 根目录散文件：按文件名关键词归类（文件夹之外的合同）
LOOSE_KEYWORDS = {
    "劳动合同": ["劳动合同"],
    "买卖合同": ["政府采购货物买卖合同"],
    "服务合同": ["数据委托处理服务合同", "委托合同"],
}

# 泄露类型的标记：示范文本 / 文号 GF—xxxx—xxxx / 版本（20xx版）
_MARKER_RE = re.compile(
    r"[（(]?示范文本[)）]?"          # 「示范文本」
    r"|[A-Z]{2}[—-]\d{4}[—-]\d{4}"   # 文号 GF—2021—0136 / SF-2021-0118
    r"|[（(]20\d{2}[^)）]*[)）]"      # 版本「（2021版）」
)
# 类型标题行：短行内出现「XX合同/XX协议」会直接泄露类型
_TYPE_TITLE_RE = re.compile(
    r"(采购|销售|买卖|保密|服务|劳动|委托|外包|技术|承揽|运输|仓储|租赁|借款|担保)\s*(合同|协议)"
)


def deidentify(text: str) -> str:
    """脱敏：删除泄露类型的标题行（短行含"XX合同/XX协议"）+ 示范文本/文号/版本标记。"""
    out = []
    for raw in text.split("\n"):
        line = _MARKER_RE.sub("", raw).strip()
        if not line:
            continue
        # 短行内出现「采购合同/销售合同/…」这类类型词 → 视为标题，删除
        if len(line) <= 40 and _TYPE_TITLE_RE.search(line):
            continue
        out.append(line)
    return "\n".join(out)


def extract_text(path: Path):
    """抽取 docx/pdf 文本；.doc/.wps 暂不支持返回 None。"""
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
        if suffix == ".pdf":
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as e:
        logger.warning("解析失败 %s: %s", path.name, e)
    return None


def main():
    if not VANBEN_DIR.exists():
        logger.error("找不到范本目录：%s", VANBEN_DIR)
        sys.exit(1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    entries = []
    seen_stem = set()  # 同一份合同的 word/pdf 只取一次（优先 docx）

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

    # 1) 目录内的合同（含标准库 word）
    for cat, rels in CATEGORY_DIRS.items():
        for rel in rels:
            d = VANBEN_DIR / rel
            if not d.exists():
                continue
            # 同 stem 优先 docx，其次 pdf；其余跳过（.doc/.wps）
            files = sorted(d.rglob("*"))
            for f in files:
                if f.suffix.lower() not in (".docx", ".pdf"):
                    continue
                if f.stem in seen_stem:
                    continue
                seen_stem.add(f.stem)
                add_entry(f, cat)

    # 2) 根目录散文件（按文件名关键词归类；.doc 老格式跳过）
    for f in VANBEN_DIR.iterdir():
        if not f.is_file() or f.suffix.lower() not in (".docx", ".pdf"):
            continue
        for cat, keywords in LOOSE_KEYWORDS.items():
            if any(k in f.name for k in keywords):
                if f.stem not in seen_stem:
                    seen_stem.add(f.stem)
                    add_entry(f, cat)
                break

    if not entries:
        logger.error("未生成任何样本，检查范本目录是否存在 .docx/.pdf")
        sys.exit(1)

    out = OUT_DIR / "testset.json"
    out.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    cnt = Counter(e["true_type"] for e in entries)
    logger.info("生成 %d 条测试样本 → %s", len(entries), out)
    for cat in CATEGORY_DIRS:
        logger.info("  %s: %d 条", cat, cnt.get(cat, 0))


if __name__ == "__main__":
    main()
