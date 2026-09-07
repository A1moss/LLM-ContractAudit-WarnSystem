"""diff_v4v5.py — v4/v5 TP-FP-FN 差异分析（按风险类型）
v4 预测来自 pred_risks_full.json；v5 预测来自 cache_risks_llm.json(v5) + 规则引擎（不变）。
输出：02_项目文档/v4v5差异分析.md
"""
import json, io, hashlib, collections
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parents[3]
V4 = _SERVICE_DIR / "_tmp_contract" / "pred_risks_full.json"
REALTEST = Path(__file__).resolve().parent / "realtest.json"
CACHE = _SERVICE_DIR / "LLM-ContractAudit-WarnSystem" / "backend" / "evaluate" / "cache_risks_llm.json"
OUT = _SERVICE_DIR / "02_项目文档" / "评测与FP归因" / "v4v5差异分析.md"

STRICT = {f"R{i:02d}" for i in range(1, 13)}
NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当",
}

v4 = json.load(io.open(V4, encoding="utf-8"))
d = json.load(io.open(REALTEST, encoding="utf-8"))
cache = json.load(io.open(CACHE, encoding="utf-8"))
byid = {e["id"]: e for e in d}

def strict_gold(e):
    return {r["risk_type"] for r in e.get("risks", []) if r["risk_type"] in STRICT}

# 统计容器
killed_TP = collections.Counter()   # v4 抓到 gold、v5 漏掉（召回损失）
killed_FP = collections.Counter()   # v4 报、v5 不再报的 FP（v5 杀掉的 FP）
new_FP = collections.Counter()      # v5 新报的 FP（v5 引入的新过报）
residual_FP = collections.Counter() # v4/v5 都报、仍非 gold 的 FP（顽固 FP）

tot = {"v4_tp": 0, "v4_fp": 0, "v4_fn": 0, "v5_tp": 0, "v5_fp": 0, "v5_fn": 0}
detail_ktp = collections.defaultdict(list)
detail_nfp = collections.defaultdict(list)

for cid, rec in v4.items():
    e = byid[cid]
    body = e.get("content", "")
    md5 = hashlib.md5(body.encode("utf-8")).hexdigest()[:16]
    gold = strict_gold(e)
    rule = set(rec["rule"])
    v4_pred = set(rec["pred"])
    v5_llm = {it["t"] for it in cache.get(md5, []) if isinstance(it, dict) and it.get("t") in STRICT}
    v5_pred = rule | v5_llm

    tot["v4_tp"] += len(v4_pred & gold); tot["v4_fp"] += len(v4_pred - gold); tot["v4_fn"] += len(gold - v4_pred)
    tot["v5_tp"] += len(v5_pred & gold); tot["v5_fp"] += len(v5_pred - gold); tot["v5_fn"] += len(gold - v5_pred)

    for t in (v4_pred - v5_pred) & gold:   # v4 抓到、v5 漏掉
        killed_TP[t] += 1
        detail_ktp[t].append(cid)
    for t in (v4_pred - v5_pred) - gold:   # v4 报、v5 不报的 FP
        killed_FP[t] += 1
    for t in (v5_pred - v4_pred) - gold:   # v5 新报 FP
        new_FP[t] += 1
        detail_nfp[t].append(cid)
    for t in (v4_pred & v5_pred) - gold:   # 都报、仍非 gold
        residual_FP[t] += 1

lines = ["# v4/v5 差异分析（v4=泛化prompt / v5=严格prompt）", ""]
lines.append("## 总体")
lines.append("| 版本 | TP | FP | FN | 精准率 | 召回率 |")
lines.append("|---|---|---|---|---|---|")
for v in ("v4", "v5"):
    tp, fp, fn = tot[f"{v}_tp"], tot[f"{v}_fp"], tot[f"{v}_fn"]
    p = tp / (tp + fp) if tp + fp else 0
    r = tp / (tp + fn) if tp + fn else 0
    lines.append(f"| {v} | {tp} | {fp} | {fn} | {p:.1%} | {r:.1%} |")
lines.append("")

lines.append("## 逐类型差异")
lines.append("| 类型 | 名称 | v4FP | v5FP | v5杀掉FP | v5新增FP | 顽固FP(∩) | v5漏报TP |")
lines.append("|---|---|---|---|---|---|---|---|")
for t in sorted(STRICT):
    v4fp = tot["v4_fp"]  # 需按类型重算
    # 用计数器按类型
    pass

# 按类型重算 v4/v5 FP
v4fp_by = collections.Counter(); v5fp_by = collections.Counter()
for cid, rec in v4.items():
    e = byid[cid]
    body = e.get("content", "")
    md5 = hashlib.md5(body.encode("utf-8")).hexdigest()[:16]
    gold = strict_gold(e)
    rule = set(rec["rule"])
    v4_pred = set(rec["pred"])
    v5_llm = {it["t"] for it in cache.get(md5, []) if isinstance(it, dict) and it.get("t") in STRICT}
    v5_pred = rule | v5_llm
    for t in v4_pred - gold: v4fp_by[t] += 1
    for t in v5_pred - gold: v5fp_by[t] += 1

for t in sorted(STRICT, key=lambda x: -(residual_FP[x] + new_FP[x])):
    lines.append(f"| {t} | {NAMES[t]} | {v4fp_by[t]} | {v5fp_by[t]} | {killed_FP[t]} | {new_FP[t]} | {residual_FP[t]} | {killed_TP[t]} |")
lines.append("")

lines.append("## 关键结论数据")
lines.append(f"- v5 杀掉的 FP（v4有v5无）：{sum(killed_FP.values())} 条，主要是 " + ", ".join(f"{t}({n})" for t, n in killed_FP.most_common()) )
lines.append(f"- v5 新增 FP（v5有v4无）：{sum(new_FP.values())} 条，主要是 " + ", ".join(f"{t}({n})" for t, n in new_FP.most_common()) )
lines.append(f"- 顽固 FP（v4/v5都报）：{sum(residual_FP.values())} 条，主要是 " + ", ".join(f"{t}({n})" for t, n in residual_FP.most_common()) )
lines.append(f"- v5 漏报 TP（v4抓到、v5漏）：{sum(killed_TP.values())} 条，主要是 " + ", ".join(f"{t}({n})" for t, n in killed_TP.most_common()) )
lines.append("")
lines.append("## v5 漏报 TP 明细（召回损失，最重要）")
for t in sorted(killed_TP, key=lambda x: -killed_TP[x]):
    lines.append(f"### {t} {NAMES[t]} —— 漏 {killed_TP[t]} 条")
    lines.append(", ".join(detail_ktp[t]))
    lines.append("")

io.open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("已生成", OUT)
print("总体:", tot)
print("v5杀掉FP:", dict(killed_FP.most_common()))
print("v5新增FP:", dict(new_FP.most_common()))
print("顽固FP:", dict(residual_FP.most_common()))
print("v5漏报TP:", dict(killed_TP.most_common()))
