"""evaluate_evidence.py — v6.1 评测：LLM 证据抽取 + 确定性规则裁决（不动 gold）

v6.1 相对 v6.0 的裁决修正：
- R05 去掉"直至公开"（只认 永久/无限/不因终止/无终止条件）。
- R03 拆 termination/suspension/change 三字段，都映射 R03（贴合现有 gold，待 GPT 定夺口径）。
- R04 用 location_party=="乙方"（不再让 LLM 预判"乙方所在地"）。
- R07 用付款节点列表（无息+验收后拖延 → R07）。
- R08 用 basis_type=="none"。
- R09 加 sanity check：has=true 必须有 evidence，否则判缺失。
"""
import sys, json, io, collections
from pathlib import Path

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
    risks = set()
    if not isinstance(ev, dict):
        return risks
    is_delivery = bool(ev.get("is_delivery_type"))

    # R01 违约金过高：日 ≥ 5‰
    r = ev.get("R01_违约金") or {}
    if r.get("exists") and r.get("unit") == "daily":
        rate = r.get("rate")
        if isinstance(rate, (int, float)) and rate >= 0.005:
            risks.add("R01")

    # R02 无限责任：scope/absolute_text 含"预期/间接/无限/一切/全部损失"等超可预见标记（代码关键词匹配）
    r = ev.get("R02_责任") or {}
    scope_text = (r.get("scope") or "") + " " + (r.get("absolute_text") or "")
    if any(k in scope_text for k in ("预期", "间接", "无限", "一切", "全部损失")):
        risks.add("R02")

    # R03 单方权利：termination/suspension/change 任一 任意+无补偿
    r = ev.get("R03_单方权利") or {}
    for key in ("termination", "suspension", "change"):
        sub = r.get(key) or {}
        if sub.get("arbitrary") and sub.get("no_compensation"):
            risks.add("R03")
            break

    # R04 管辖不利：location_party == 乙方
    r = ev.get("R04_管辖") or {}
    if r.get("location_party") == "乙方":
        risks.add("R04")

    # R05 保密期：永久/无限/不因终止 且 非"法定义务"（合同额外约定无限期才标）
    r = ev.get("R05_保密") or {}
    if r.get("duration") in ("永久", "无限", "不因终止而终止") and not r.get("statutory_basis"):
        risks.add("R05")

    # R06 IP 失衡：无对价 + 有失衡事实
    r = ev.get("R06_IP") or {}
    if not r.get("has_consideration") and (r.get("imbalance_text") or "").strip():
        risks.add("R06")

    # R07 付款失衡：预付≥80% 或 尾款≥30% 且无里程碑（回退简单启发式，不做语义裁决）
    r = ev.get("R07_付款") or {}
    prepay = r.get("prepay_ratio") or 0
    tail = r.get("tail_ratio") or 0
    if isinstance(prepay, (int, float)) and prepay >= 0.8:
        risks.add("R07")
    elif isinstance(tail, (int, float)) and tail >= 0.3 and not r.get("has_milestone"):
        risks.add("R07")

    # R08 验收缺失：交付型 + 无客观依据（objective_basis 且 basis_evidence 非空才算有）
    r = ev.get("R08_验收") or {}
    objective = bool(r.get("objective_basis")) and bool((r.get("basis_evidence") or "").strip())
    if is_delivery and not objective:
        risks.add("R08")

    # R09 不可抗力缺失：交付型 + 无"含机制词的不可抗力条款"（风险提示句不算）
    r = ev.get("R09_不可抗力") or {}
    evidence = r.get("force_majeure_evidence") or ""
    mechanism = any(w in evidence for w in ("通知", "免责", "不承担责任", "解除", "顺延", "延期"))
    valid = bool(r.get("has_force_majeure")) and "不可抗" in evidence and mechanism
    if is_delivery and not valid:
        risks.add("R09")

    # R10 竞业过宽：≥5年 + 全国/主营/全行业
    r = ev.get("R10_竞业") or {}
    if r.get("has_noncompete"):
        dur = r.get("duration_years") or 0
        scope = r.get("scope") or ""
        if isinstance(dur, (int, float)) and dur >= 5 and scope in ("全国", "主营业务", "全行业"):
            risks.add("R10")

    # R11 自动续约：沉默自动续约
    r = ev.get("R11_续约") or {}
    if r.get("mode") == "silence_auto_renewal":
        risks.add("R11")

    # R12 数据隐私：有个人信息处理 + 无授权边界
    r = ev.get("R12_数据") or {}
    if r.get("has_personal_data") and not r.get("has_authorization_boundary"):
        risks.add("R12")

    return risks


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
    print(f"=== v6.1 (证据抽取+确定性裁决 v6.1) · 不动 gold ===")
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
