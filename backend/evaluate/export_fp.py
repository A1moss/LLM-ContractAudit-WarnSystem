"""export_fp.py — 导出 precise 全量预测(392条) + 241 FP 归因表 + FP 按类型统计
用法：python backend/evaluate/export_fp.py
输出：
  _tmp_contract/pred_risks_full.json  （全量预测，含 clause_text/reason/confidence）
  02_项目文档/241FP归因表.md          （FP 明细 + 按风险类型统计）
"""
import sys, json, io, collections
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.auditor.rule_engine import run_rules
from ai.auditor.llm_auditor import audit_with_llm

TESTSET = Path(__file__).resolve().parent / "realtest.json"
OUT_FULL = _SERVICE_DIR / "_tmp_contract" / "pred_risks_full.json"
OUT_FP = _SERVICE_DIR / "02_项目文档" / "评测与FP归因" / "241FP归因表.md"

STRICT_TYPES = {f"R{i:02d}" for i in range(1, 13)}
RISK_NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当", "R13": "疑似名实不符",
}


def _norm_type(rt):
    t = str(rt or "").strip().upper()
    return t if t in RISK_NAMES else None


def short_source(s):
    if "公告披露" in s:
        return "补全47"
    return "真实37"


def main():
    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    full = {}
    fp_rows = []
    tp_n = 0
    fn_n = 0
    fp_by_type = collections.Counter()
    tp_by_type = collections.Counter()
    fn_by_type = collections.Counter()

    for i, e in enumerate(entries):
        body = e.get("content") or e.get("text", "")
        gold = {t for t in (_norm_type(r.get("risk_type")) for r in e.get("risks", [])) if t and t in STRICT_TYPES}

        rule_types = {t for t in (_norm_type(r.get("risk_type")) for r in run_rules(body)) if t}
        rule_strict = rule_types & STRICT_TYPES

        llm_items = []
        try:
            for r in audit_with_llm(body):
                t = _norm_type(r.get("risk_type"))
                if t and t in STRICT_TYPES:
                    llm_items.append({
                        "risk_type": t,
                        "level": r.get("level", ""),
                        "clause_text": r.get("clause_text", ""),
                        "reason": r.get("reason", ""),
                        "confidence": float(r.get("confidence", 0.0)),
                    })
        except Exception as ex:
            print(f"  [warn] {e['id']} LLM失败: {ex}", flush=True)

        llm_types = {r["risk_type"] for r in llm_items}
        pred = rule_strict | llm_types

        full[e["id"]] = {
            "true_type": e.get("true_type", ""),
            "source": short_source(e.get("source", "")),
            "gold": sorted(gold),
            "rule": sorted(rule_strict),
            "llm": llm_items,
            "pred": sorted(pred),
        }

        for t in sorted(pred - gold):
            item = next((r for r in llm_items if r["risk_type"] == t), None)
            fp_rows.append({
                "contract_id": e["id"],
                "true_type": e.get("true_type", ""),
                "source": short_source(e.get("source", "")),
                "risk_type": t,
                "clause_text": (item or {}).get("clause_text", ""),
                "reason": (item or {}).get("reason", ""),
                "confidence": (item or {}).get("confidence", 0.0),
                "detection": "llm" if item else "rule",
                "gold": sorted(gold),
            })
            fp_by_type[t] += 1
        for t in sorted(pred & gold):
            tp_n += 1
            tp_by_type[t] += 1
        for t in sorted(gold - pred):
            fn_n += 1
            fn_by_type[t] += 1

        print(f"  [{i+1}/{len(entries)}] {e['id']} gold={len(gold)} pred={len(pred)} fp={len(pred-gold)}", flush=True)

    OUT_FULL.write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# 241 FP 归因表（precise 无过滤 open-set · gold v1.2.4 冻结版）", ""]
    lines.append(f"- 全量预测 {tp_n + len(fp_rows)} 条：TP {tp_n} / FP {len(fp_rows)} / FN {fn_n}")
    lines.append(f"- 精准率 {tp_n/(tp_n+len(fp_rows)):.1%}（{tp_n}/{tp_n+len(fp_rows)}）")
    lines.append("")
    lines.append("## FP 按风险类型统计（制造精准率灾难的元凶排序）")
    lines.append("")
    lines.append("| 类型 | 名称 | FP 数 |")
    lines.append("|---|---|---|")
    for t, n in fp_by_type.most_common():
        lines.append(f"| {t} | {RISK_NAMES.get(t,'')} | {n} |")
    lines.append("")
    lines.append("## FP 明细（contract_id | 类型 | 置信度 | 原文证据 | 模型理由 | gold）")
    lines.append("")
    lines.append("| contract_id | 类型 | conf | 原文证据 | 模型理由 | gold |")
    lines.append("|---|---|---|---|---|---|")
    for r in sorted(fp_rows, key=lambda x: (x["risk_type"], -x["confidence"], x["contract_id"])):
        ct = (r["clause_text"] or "").replace("|", "｜").replace("\n", " ")[:120]
        rs = (r["reason"] or "").replace("|", "｜").replace("\n", " ")[:160]
        g = "+".join(r["gold"]) or "[]"
        lines.append(f"| {r['contract_id']} | {r['risk_type']} | {r['confidence']:.2f} | {ct} | {rs} | {g} |")

    OUT_FP.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n完成：全量预测 {OUT_FULL}，FP 表 {OUT_FP}")
    print(f"TP={tp_n} FP={len(fp_rows)} FN={fn_n}")
    print("FP 按类型:", dict(fp_by_type.most_common()))


if __name__ == "__main__":
    main()
