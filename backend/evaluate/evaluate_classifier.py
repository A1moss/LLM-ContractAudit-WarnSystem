"""
evaluate_classifier.py — 计算合同分类准确率（需 DEEPSEEK_API_KEY）

读取 build_testset.py 生成的 03_数据集/测试集/testset.json，对每条样本调
ai.classifier.classify_contract，与 true_type 比对，输出：
  - accuracy（总准确率，赛题门槛 ≥85%）
  - 每类 precision / recall / F1
  - 混淆矩阵（true × pred，markdown）

LLM 调用结果按文本 hash 缓存到 cache.json，重复运行不重复花钱。

用法（任意位置运行）：
    python backend/evaluate/evaluate_classifier.py [--limit N] [--cache path]
"""
import os
import sys
import json
import hashlib
import argparse
from pathlib import Path
from collections import Counter, defaultdict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from ai.classifier.classifier import classify_contract, CONTRACT_TYPES  # noqa: E402

_SERVICE_DIR = _BACKEND_DIR.parent.parent  # 服务外包/（03_数据集 在这一层）
TESTSET_REAL = _SERVICE_DIR / "03_数据集" / "测试集" / "realtest.json"   # 真实合同测试集（人工标注，跨域泛化）
TESTSET_FALLBACK = _SERVICE_DIR / "03_数据集" / "测试集" / "testset.json"  # 范本样本集（仅冒烟参考）


def _resolve_testset():
    """评测测试集优先取真实合同；未收集到时回退范本样本集并告警。"""
    if TESTSET_REAL.exists():
        return TESTSET_REAL
    print("[提示] 未找到真实合同测试集 realtest.json，回退用范本样本集 testset.json（仅作冒烟参考，非正式泛化指标）。")
    return TESTSET_FALLBACK


def _text_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def load_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（快速冒烟）")
    ap.add_argument("--cache", type=str, default=str(_BACKEND_DIR / "evaluate" / "cache.json"))
    args = ap.parse_args()

    TESTSET = _resolve_testset()
    if not TESTSET.exists():
        print(f"找不到测试集 {TESTSET}，先运行 build_realtest.py / build_testset.py")
        sys.exit(1)

    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    if args.limit > 0:
        entries = entries[: args.limit]

    cache_path = Path(args.cache)
    cache = load_cache(cache_path)

    conf = defaultdict(list)  # true_type -> pred_type
    wrong = []

    for e in entries:
        key = _text_key(e["text"])
        if key not in cache:
            cache[key] = classify_contract(e["text"])["contract_type"]
        pred = cache[key]
        conf[e["true_type"]].append(pred)
        if pred != e["true_type"]:
            wrong.append((e["id"], e["file"], e["true_type"], pred))

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 统计 ──
    total = sum(len(v) for v in conf.values())
    correct = sum(v.count(k) for k, v in conf.items())
    acc = correct / total if total else 0.0

    print(f"\n=== 分类准确率 ===\n样本数 {total}，正确 {correct}，准确率 {acc:.2%}\n")

    # 每类 P/R/F1
    print("| 类型 | 样本 | 精确率 | 召回率 | F1 |")
    print("|------|-----|-------|-------|-----|")
    for cat in CONTRACT_TYPES:
        preds = sum(v.count(cat) for v in conf.values())
        tps = conf.get(cat, []).count(cat)
        actual = len(conf.get(cat, []))
        p = tps / preds if preds else 0.0
        r = tps / actual if actual else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        print(f"| {cat} | {actual} | {p:.2%} | {r:.2%} | {f1:.2%} |")

    # 混淆矩阵
    print("\n=== 混淆矩阵（行=真实，列=预测） ===")
    header = "类型 | " + " | ".join(CONTRACT_TYPES)
    print(header)
    print("---|" + "---|" * len(CONTRACT_TYPES))
    for cat in CONTRACT_TYPES:
        row = [str(conf.get(cat, []).count(p)) for p in CONTRACT_TYPES]
        print(f"{cat} | " + " | ".join(row))

    if wrong:
        print("\n=== 判错样本 ===")
        for wid, wfile, wt, wp in wrong:
            print(f"{wid} {wfile}: 真实 {wt} → 预测 {wp}")


if __name__ == "__main__":
    main()
