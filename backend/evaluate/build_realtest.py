"""
build_realtest.py — 从 05_合同/现实合同 生成真实合同测试集（主体脱敏）

与 build_testset.py 的区别：
1. 输入是 md 文本（上市公司公告转录），直接读，不做 docx/pdf/Office 抽取；
2. 剥离元数据头（含「法理类型：XX」标签，防泄漏答案），仅保留公告正文；
3. 脱敏是「主体脱敏」（统一社会信用代码/金额/公司名替换占位符），
   非 build_testset 的「删标题防读标题作弊」；
4. 真实合同与范本不同源 → 产出 realtest.json，用于跨域泛化评测（正式分类指标口径）。

用法：python backend/evaluate/build_realtest.py
"""
import re
import sys
import json
import logging
from pathlib import Path
from collections import Counter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("build_realtest")

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
REAL_DIR = _SERVICE_DIR / "05_合同" / "现实合同"
OUT_PATH = _SERVICE_DIR / "03_数据集" / "测试集" / "realtest.json"

sys.path.insert(0, str(_BACKEND_DIR))
from ai.taxonomy import ENABLED_TYPES, KIND_TYPICAL, KIND_UNNAMED, kind_of  # noqa: E402

# 主体脱敏正则
_CREDIT_CODE_RE = re.compile(r"[0-9A-Z]{15,18}")                    # 统一社会信用代码/证件号
_AMOUNT_RE = re.compile(r"\d[\d,.]*\s*(?:万元|亿元|万|亿|元)")       # 金额
_COMPANY_RE = re.compile(
    r"[\u4e00-\u9fa5]{2,15}(?:公司|集团|有限|股份|控股|科技|传媒|证券"
    r"|建设|重工|新材|管业|电子|软件|汽车|环保|能源|医疗|银行)"
)
# 合同名称（书名号内的《…合同/协议/合作协议》），剥离防"读合同名作弊"
_BOOK_TITLE_RE = re.compile(r"《[^》]{0,40}(?:合同|协议)[^》]{0,40}》")


def _archive_dir(cat: str) -> Path:
    """按法理归属定位现实合同目录（与 build_testset 同构，但指向现实合同）。"""
    k = kind_of(cat)
    if k == KIND_TYPICAL:
        return REAL_DIR / "民事合同" / "典型合同" / cat
    if k == KIND_UNNAMED:
        return REAL_DIR / "民事合同" / "无名合同" / cat
    return REAL_DIR / cat  # 劳动合同 在顶层（现实公开渠道几乎不可得）


def parse_md(md_text: str):
    """解析 md：返回 (true_type, 正文)。剥离元数据头（到 --- 为止），保留公告正文。"""
    lines = md_text.split("\n")
    true_type = None
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("- 法理类型："):
            true_type = line.split("：", 1)[1].strip()
        if line.strip() == "---":
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    return true_type, body


def deidentify(text: str) -> str:
    """脱敏：合同名 + 信用代码/金额/公司名替换为占位符。"""
    text = _BOOK_TITLE_RE.sub("<合同名>", text)
    text = _CREDIT_CODE_RE.sub("<证件号>", text)
    text = _AMOUNT_RE.sub("<金额>", text)
    text = _COMPANY_RE.sub("<公司>", text)
    return text


def main():
    if not REAL_DIR.exists():
        logger.error("现实合同目录不存在：%s", REAL_DIR)
        sys.exit(1)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    entries = []
    for cat in ENABLED_TYPES:
        d = _archive_dir(cat)
        if not d.exists():
            logger.warning("缺「%s」现实合同目录，跳过", cat)
            continue
        for f in sorted(d.rglob("*.md")):
            if f.name.startswith("_"):  # 跳过 _来源清单.md / _分类说明 等
                continue
            try:
                md_text = f.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("读取失败 %s: %s", f.name, e)
                continue
            true_type, body = parse_md(md_text)
            if not body:
                logger.warning("正文为空，跳过 %s", f.name)
                continue
            label = true_type if true_type in ENABLED_TYPES else cat
            entries.append({
                "id": f"R{len(entries) + 1:03d}",
                "file": str(f.relative_to(_SERVICE_DIR)),
                "true_type": label,
                "text": deidentify(body),
            })

    if not entries:
        logger.error("未生成任何样本")
        sys.exit(1)

    OUT_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    cnt = Counter(e["true_type"] for e in entries)
    logger.info("生成 %d 条真实合同测试样本 → %s", len(entries), OUT_PATH)
    for cat in ENABLED_TYPES:
        if cnt.get(cat):
            logger.info("  %s: %d 条", cat, cnt[cat])


if __name__ == "__main__":
    main()
