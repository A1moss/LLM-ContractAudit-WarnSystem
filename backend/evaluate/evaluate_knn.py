"""
evaluate_knn.py — kNN 分类评测（零 LLM 成本）

用「合同范本向量库」做 kNN 分类，与 testset.json 的 true_type 比对。
留一法：每个测试样本检索时剔除自身（防泄漏）。输出 accuracy / 每类 F1 / 混淆矩阵。
"""
import sys
import json
from pathlib import Path
from collections import Counter, defaultdict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.classifier.knn_classifier import classify_by_knn
from ai.taxonomy import ENABLED_TYPES

TESTSET = _SERVICE_DIR / "03_数据集" / "测试集" / "testset.json"


def main():
    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    top_k = 5

    conf = defaultdict(list)
    wrong = []
    for e in entries:
        pred = classify_by_knn(e["text"], top_k=top_k, exclude_self=e["text"])["contract_type"]
        conf[e["true_type"]].append(pred)
        if pred != e["true_type"]:
            wrong.append((e["id"], e["true_type"], pred))

    total = sum(len(v) for v in conf.values())
    correct = sum(v.count(k) for k, v in conf.items())
    acc = correct / total if total else 0.0

    print(f"\n=== kNN 分类准确率 (top_k={top_k}) ===\n样本数 {total}，正确 {correct}，准确率 {acc:.2%}\n")

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

    print("\n=== 混淆矩阵（行=真实，列=预测） ===")
    print("类型 | " + " | ".join(ENABLED_TYPES))
    print("---|" + "---|" * len(ENABLED_TYPES))
    for cat in ENABLED_TYPES:
        if not conf.get(cat):
            continue
        row = [str(conf.get(cat, []).count(p)) for p in ENABLED_TYPES]
        print(f"{cat} | " + " | ".join(row))


if __name__ == "__main__":
    main()
