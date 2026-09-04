"""
evaluate_rag.py — RAG 少样本分类评测（含 LLM 调用）

检索 top-K 相似范本作示例喂 LLM，与 testset.json 的 true_type 比对。
留一法防自身泄漏。输出 accuracy / 每类 F1 / 混淆矩阵。
"""
import sys
import json
from pathlib import Path
from collections import defaultdict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.classifier.rag_classifier import classify_by_rag
from ai.taxonomy import ENABLED_TYPES

TESTSET_REAL = _SERVICE_DIR / "03_数据集" / "测试集" / "realtest.json"   # 真实合同测试集（人工标注，待收集）
TESTSET_FALLBACK = _SERVICE_DIR / "03_数据集" / "测试集" / "testset.json"  # 范本样本集（与检索库同源，仅冒烟参考）


def _resolve_testset():
    """评测测试集优先取真实合同；未收集到时回退范本样本集并告警。"""
    if TESTSET_REAL.exists():
        return TESTSET_REAL
    print("[提示] 未找到真实合同测试集 realtest.json（待收集+人工标注），回退用范本样本集 testset.json。")
    print("[提示] 注意：范本样本集与检索库 contract_templates 同源，结果仅作冒烟/回归参考，不作为正式泛化指标。")
    return TESTSET_FALLBACK


def main():
    TESTSET = _resolve_testset()
    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    top_k = 3

    conf = defaultdict(list)
    wrong = []
    for e in entries:
        body = e.get("content") or e.get("text", "")
        pred = classify_by_rag(body, top_k=top_k, exclude_self=body)["contract_type"]
        conf[e["true_type"]].append(pred)
        if pred != e["true_type"]:
            wrong.append((e["id"], e["true_type"], pred))

    total = sum(len(v) for v in conf.values())
    correct = sum(v.count(k) for k, v in conf.items())
    acc = correct / total if total else 0.0

    print(f"\n=== RAG 少样本分类准确率 (top_k={top_k}) ===\n样本数 {total}，正确 {correct}，准确率 {acc:.2%}\n")

    print("| 类型 | 样本 | 精确率 | 召回率 | F1 |")
    print("|------|-----|-------|-------|-----|")
    for cat in ENABLED_TYPES:
        actual = len(conf.get(cat, []))
        if actual == 0:
            continue
        preds = sum(v.count(cat) for v in conf.values())
        tps = conf.get(cat, []).count(cat)
        p = tps / preds if preds else 0.0
        r = tps / actual if actual else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        print(f"| {cat} | {actual} | {p:.2%} | {r:.2%} | {f1:.2%} |")


if __name__ == "__main__":
    main()
