"""
evaluate_risks.py — 风险检测严格召回/精确率/F1（条款级）

对每份合同跑 ai.auditor.rule_engine.run_rules，与 realtest.json 的「条款级风险标注」
（risks[].risk_type ∈ R01-R13）做类型级比对，输出宏平均 P/R/F1。

口径修正（v2，2026-09-04）：
- 旧版「至少命中 1 条风险的合同占比」是覆盖率，不是召回。
- 严格召回只统计「条款级风险」（R01-R13）——规则引擎/LLM 审计有能力检测的合同条款风险。
- 业务/履约风险（工期、客户集中、关联公允等）已从 risks 移入 business_risks，不参与召回分母。
- 无条款风险标注的合同（risks 为空）不计入分母，如实反映"公告型样本条款风险可见度低"。

用法：python backend/evaluate/evaluate_risks.py
"""
import sys
import json
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.auditor.rule_engine import run_rules  # noqa: E402

TESTSET = _SERVICE_DIR / "03_数据集" / "测试集" / "realtest.json"

RISK_NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当", "R13": "疑似名实不符",
}


def main():
    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    per_doc = []          # 每份 (tp, fp, fn, gold_set, pred_set)
    n_annotated = 0       # 有条款风险标注的合同数（召回分母样本）
    n_engine_hit = 0

    for e in entries:
        body = e.get("content") or e.get("text", "")
        gold = {r["risk_type"] for r in e.get("risks", []) if r.get("risk_type") and not r["risk_type"].startswith("R-")}
        pred = {r["risk_type"] for r in run_rules(body)}
        if not gold:
            continue          # 无条款风险标注 → 不计入
        n_annotated += 1
        if pred:
            n_engine_hit += 1
        tp = len(gold & pred)
        fp = len(pred - gold)
        fn = len(gold - pred)
        per_doc.append((tp, fp, fn, gold, pred))

    if not per_doc:
        print("无条款风险标注样本，无法计算严格召回（需完整合同标注条款风险）")
        return

    TP = sum(x[0] for x in per_doc)
    FP = sum(x[1] for x in per_doc)
    FN = sum(x[2] for x in per_doc)
    P = TP / (TP + FP) if (TP + FP) else 0.0
    R = TP / (TP + FN) if (TP + FN) else 0.0
    F1 = 2 * P * R / (P + R) if (P + R) else 0.0

    n = len(entries)
    print(f"=== 风险检测严格召回/精确率/F1（条款级，{n} 份真实合同） ===")
    print(f"  有条款风险标注的合同: {n_annotated}/{n}（其余为公告未披露条款风险，不入分母）")
    print(f"  规则引擎检出风险的合同: {n_engine_hit}/{n_annotated}")
    print(f"  标注条款风险总数: {TP + FN}，规则引擎正确命中 {TP}，漏检 {FN}，误报 {FP}")
    print(f"  Precision = {P:.1%} | Recall = {R:.1%} | F1 = {F1:.1%}")

    # 按风险类型看漏检
    miss = {}
    for _, _, _, gold, pred in per_doc:
        for g in gold - pred:
            miss[g] = miss.get(g, 0) + 1
    print("\n  漏检风险类型分布:")
    for rt, c in sorted(miss.items(), key=lambda x: -x[1]):
        print(f"    {rt}({RISK_NAMES.get(rt, rt)}): 漏检 {c} 处")


if __name__ == "__main__":
    main()
