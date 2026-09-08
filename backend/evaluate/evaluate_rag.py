"""
evaluate_rag.py — RAG 少样本分类评测（含 LLM 调用）

检索 top-K 相似范本作示例喂 LLM，与 classification_test.json（147 份分类正式测试集）
的 true_type 比对。输出 accuracy / 每类 P/R/F1，并保存预测快照 rag_predictions.json。
口径：147 份 = 第一批 84（realtest.json）+ 第二批 55（现实合同）+ 人工构造 8（边界样本），
content 已去除「法理类型」等答案元信息，Gold 冻结。
"""
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.classifier.rag_classifier import classify_by_rag
from ai.taxonomy import ENABLED_TYPES

TESTSET_REAL = Path(__file__).resolve().parent / "classification_test.json"  # 分类正式测试集（147 份，Gold 冻结）
TESTSET_FALLBACK = _SERVICE_DIR / "03_数据集" / "测试集" / "testset.json"  # 范本样本集（与检索库同源，仅冒烟参考）
PRED_FILE = Path(__file__).resolve().parent / "rag_predictions.json"  # 预测快照（可离线复核）


def _resolve_testset():
    """评测测试集优先取分类正式测试集（147 份）；未收集到时回退范本样本集并告警。"""
    if TESTSET_REAL.exists():
        return TESTSET_REAL
    print("[提示] 未找到分类正式测试集 classification_test.json，回退用范本样本集 testset.json。")
    print("[提示] 注意：范本样本集与检索库 contract_templates 同源，结果仅作冒烟/回归参考，不作为正式泛化指标。")
    return TESTSET_FALLBACK


def main():
    TESTSET = _resolve_testset()
    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    top_k = 3

    conf = defaultdict(list)
    wrong = []
    predictions = []  # 预测快照（可离线复核 145/147 等结果）
    for e in entries:
        body = e.get("content") or e.get("text", "")
        r = classify_by_rag(body, top_k=top_k, exclude_self=body)
        pred = r.get("contract_type", "")
        conf[e["true_type"]].append(pred)
        if pred != e["true_type"]:
            wrong.append((e["id"], e["true_type"], pred))
        predictions.append({
            "contract_id": e["id"],
            "gold": e["true_type"],
            "prediction": pred,
            "correct": pred == e["true_type"],
            "retrieved_templates": r.get("top_matches", []),
            "reason": r.get("reason", ""),
        })

    total = sum(len(v) for v in conf.values())
    correct = sum(v.count(k) for k, v in conf.items())
    acc = correct / total if total else 0.0

    # 保存预测快照，使分类结果可离线复核（含每条检索到的范本 + 判错样本）
    snapshot = {
        "meta": {
            "script": "evaluate_rag.py",
            "model": "deepseek-chat",
            "prompt": "SYSTEM_PROMPT_RAG",
            "top_k": top_k,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "correct": correct,
            "accuracy": round(acc, 4),
            "wrong": [{"contract_id": w[0], "gold": w[1], "prediction": w[2]} for w in wrong],
        },
        "predictions": predictions,
    }
    PRED_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"预测快照已保存: {PRED_FILE.name}（{correct}/{total}）")

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
