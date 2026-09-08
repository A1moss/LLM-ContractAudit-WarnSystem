"""evaluate_evidence.py — 证据抽取 + 确定性裁决评测（调用生产 v6.4 裁决器，不动 gold）

评测与生产共用同一裁决函数 ai.auditor.evidence_adjudicator.adjudicate_risks（v6.4），
消除历史 v6.1 内置复制版，避免版本口径错位。读 evidence.json（run_evidence.py 产物），
与 realtest.json 的 risks gold 比对，输出风险严格 P/R/F1。
"""
import sys, json, io, collections
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from ai.auditor.evidence_adjudicator import adjudicate_risks  # noqa: E402  生产裁决器（v6.4）

_SERVICE_DIR = Path(__file__).resolve().parents[3]
EVID = Path(__file__).resolve().parent / "evidence.json"
REALTEST = Path(__file__).resolve().parent / "realtest.json"

STRICT = {f"R{i:02d}" for i in range(1, 13)}
NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当",
}


def adjudicate(ev: dict) -> set:
    """调用生产裁决器 adjudicate_risks（v6.4），返回风险类型集合。

    评测与生产共用同一裁决函数，消除历史 v6.1 内置复制版，避免版本口径错位。
    """
    return {r["risk_type"] for r in adjudicate_risks(ev)}


def main():
    evid = json.load(io.open(EVID, encoding="utf-8"))
    d = json.load(io.open(REALTEST, encoding="utf-8"))
    byid = {e["id"]: e for e in d}

    tp = collections.Counter(); fp = collections.Counter(); fn = collections.Counter()
    for cid, rec in evid.items():
        e = byid[cid]
        gold = {r["risk_type"] for r in e.get("risks", []) if r["risk_type"] in STRICT}
        pred = adjudicate(rec.get("evidence", {}))
        for t in pred & gold: tp[t] += 1
        for t in pred - gold: fp[t] += 1
        for t in gold - pred: fn[t] += 1

    def prf(tpc, fpc, fnc):
        p = tpc / (tpc + fpc) if (tpc + fpc) else 0
        r = tpc / (tpc + fnc) if (tpc + fnc) else 0
        f1 = 2 * p * r / (p + r) if (p + r) else 0
        return p, r, f1

    TP, FP, FN = sum(tp.values()), sum(fp.values()), sum(fn.values())
    P, R, F1 = prf(TP, FP, FN)
    print(f"=== 证据抽取 + 确定性裁决（生产 v6.4 裁决器）· 不动 gold ===")
    print(f"总体: TP {TP} / FP {FP} / FN {FN} | 精准率 {P:.1%} | 召回率 {R:.1%} | F1 {F1:.1%}")
    print()
    print("逐类型: | 类型 | TP FP FN | P | R |")
    for t in sorted(STRICT):
        tpc, fpc, fnc = tp[t], fp[t], fn[t]
        p, r, _ = prf(tpc, fpc, fnc)
        print(f"| {t} {NAMES[t]} | {tpc} {fpc} {fnc} | {p:.0%} | {r:.0%} |")
    print()
    print("FN（漏报）明细:")
    for cid, rec in evid.items():
        e = byid[cid]
        gold = {r["risk_type"] for r in e.get("risks", []) if r["risk_type"] in STRICT}
        pred = adjudicate(rec.get("evidence", {}))
        miss = sorted(gold - pred)
        if miss:
            print(f"  {cid}: 漏 {miss} (gold={sorted(gold)}, pred={sorted(pred)})")
    print()
    print("FP（多报）明细:")
    for cid, rec in evid.items():
        e = byid[cid]
        gold = {r["risk_type"] for r in e.get("risks", []) if r["risk_type"] in STRICT}
        pred = adjudicate(rec.get("evidence", {}))
        extra = sorted(pred - gold)
        if extra:
            print(f"  {cid}: 多报 {extra} (gold={sorted(gold)}, pred={sorted(pred)})")


if __name__ == "__main__":
    main()
