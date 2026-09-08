"""build_classification_test.py — 构建 147 份分类正式测试集（Gold 冻结）。

构成：
- 第一批 84 份：realtest.json（content 本就无答案元信息）
- 第二批 55 份：05_合同/现实合同/第二批合同/*.md（剥掉头部 > 元信息行：来源/法理类型/业务标签）
- 人工构造 8 份：第二批合同下的「边界样本-*.md」（同上剥元信息，source 显式标「人工构造」）

输出：classification_test.json，字段 {id, true_type, content, source, source_file, is_manual}
口径：true_type 为分类 Gold（11 类法理，取自 realtest.json 或目录名）；content 为「标题+正文」、
已去除所有答案元信息，供 evaluate_rag.py 直接喂分类器。
"""
import sys
import json
import os
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.taxonomy import ENABLED_TYPES  # noqa: E402
from evaluate.deidentify import deidentify  # noqa: E402

ROOT = _SERVICE_DIR
REALTEST = _BACKEND_DIR / "evaluate" / "realtest.json"
BATCH2_DIR = ROOT / "05_合同" / "现实合同" / "第二批合同"
OUT = _BACKEND_DIR / "evaluate" / "classification_test.json"

ENABLED = set(ENABLED_TYPES)


def strip_meta(text: str) -> str:
    """剥掉 .md 头部 > 元信息行（来源/法理类型/业务标签），只留标题+正文。"""
    lines = [ln for ln in text.split("\n") if not ln.strip().startswith(">")]
    return "\n".join(lines).strip()


def map_type(relpath: str):
    parts = relpath.replace("\\", "/").split("/")
    for i, p in enumerate(parts):
        if p == "典型合同" and i + 1 < len(parts):
            return parts[i + 1]
        if p == "无名合同" and i + 1 < len(parts):
            return "保密协议" if parts[i + 1] == "保密协议" else "无名合同"
        if p == "劳动合同":
            return "劳动合同"
    return None


def main():
    entries = []

    # 第一批 84
    rt = json.loads(REALTEST.read_text(encoding="utf-8"))
    for e in rt:
        entries.append({
            "id": e.get("id", ""),
            "true_type": e.get("true_type", ""),
            "content": deidentify(strip_meta(e.get("content", ""))),  # 第一批 content 也含 > 法理类型 等元信息，一并剥掉 + 脱敏
            "source": "第一批",
            "source_file": e.get("source_file", ""),
            "is_manual": False,
        })

    # 第二批 55 + 人工构造 8
    n_real = n_manual = 0
    for dp, dn, fn in sorted(os.walk(BATCH2_DIR)):
        for f in sorted(fn):
            if not f.endswith(".md"):
                continue
            full = os.path.join(dp, f)
            rel = full.replace("\\", "/").replace(str(ROOT).replace("\\", "/") + "/", "")
            t = map_type(rel)
            if t not in ENABLED:
                continue
            is_manual = f.startswith("边界样本")
            body = deidentify(strip_meta(Path(full).read_text(encoding="utf-8")))
            entries.append({
                "id": ("manual_" if is_manual else "batch2_") + f[:-3],
                "true_type": t,
                "content": body,
                "source": "人工构造" if is_manual else "第二批",
                "source_file": rel,
                "is_manual": is_manual,
            })
            if is_manual:
                n_manual += 1
            else:
                n_real += 1

    OUT.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"classification_test.json 已生成：共 {len(entries)} 份（第一批 {len(rt)} + 第二批 {n_real} + 人工构造 {n_manual}）")
    # 类型分布
    from collections import Counter
    c = Counter(e["true_type"] for e in entries)
    for t in ENABLED_TYPES:
        if c.get(t):
            print(f"  {t}: {c[t]}")


if __name__ == "__main__":
    main()
