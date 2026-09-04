"""
evaluate_elements.py — 要素抽取覆盖率（简化版）

对每份合同调 ai.extractor.extract_elements，统计「双方/金额/期限/争议解决」四要素
是否被抽取到（非空）。豆包已为 52 份真实合同标注 elements，本脚本据此做覆盖统计。

注意：这是「抽取覆盖率」而非严格 F1——严格 F1 需要 span 级标注或 LLM 语义比对
（抽取值 vs 标注值是否一致），本脚本只回答"抽没抽到"，不回答"抽得对不对"。
"""
import sys
import json
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.extractor.extractor import extract_elements  # noqa: E402

TESTSET = _SERVICE_DIR / "03_数据集" / "测试集" / "realtest.json"


def _nonempty(v) -> bool:
    if v in (None, "", [], {}):
        return False
    if isinstance(v, dict):
        return any(v.values())
    return True


def main():
    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    field_map = {
        "parties": "双方",
        "amount": "金额",
        "performance_period": "期限",
        "dispute_resolution": "争议解决",
    }
    stats = {f: 0 for f in field_map}
    total = len(entries)

    for e in entries:
        body = e.get("content") or e.get("text", "")
        r = extract_elements(body, e.get("true_type", ""))
        for f in field_map:
            if _nonempty(r.get(f)):
                stats[f] += 1

    print(f"=== 要素抽取覆盖率（{total} 份真实合同） ===")
    for f, cn in field_map.items():
        print(f"  {cn}: {stats[f]}/{total} = {stats[f] / total:.1%}")


if __name__ == "__main__":
    main()
