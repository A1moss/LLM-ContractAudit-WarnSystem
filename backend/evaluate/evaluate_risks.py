"""
evaluate_risks.py — 风险检测覆盖率（简化版·规则引擎）

对每份合同跑 ai.auditor.rule_engine.run_rules，统计：命中风险的合同占比 + 各风险类型
触发频次。豆包已为 52 份真实合同标注 risks（风险点描述）。

注意：这是「规则引擎覆盖率」而非严格召回/精准——严格评估需把豆包的 risks 自由文本
映射到 R01-R13 风险类型后再比对（语义映射，需 LLM 或人工），本脚本先给出规则引擎
在真实合同上的"能不能检出风险"的粗粒度答案。
"""
import sys
import json
from pathlib import Path
from collections import Counter

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.auditor.rule_engine import run_rules  # noqa: E402

TESTSET = _SERVICE_DIR / "03_数据集" / "测试集" / "realtest.json"


def main():
    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    hit = 0
    type_count = Counter()
    annotated_total = 0

    for e in entries:
        body = e.get("content") or e.get("text", "")
        risks = run_rules(body)
        annotated_total += len(e.get("risks", []))
        if risks:
            hit += 1
            for r in risks:
                type_count[r["risk_type"]] += 1

    n = len(entries)
    print(f"=== 规则引擎风险覆盖（{n} 份真实合同） ===")
    print(f"  至少命中 1 条风险的合同: {hit}/{n} = {hit / n:.1%}")
    print(f"  人工标注风险点总数: {annotated_total}（平均 {annotated_total / n:.1f} 条/份）")
    print(f"  规则引擎触发类型分布: {dict(sorted(type_count.items()))}")


if __name__ == "__main__":
    main()
