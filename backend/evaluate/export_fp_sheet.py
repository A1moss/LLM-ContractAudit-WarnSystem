"""export_fp_sheet.py — 把 466 条 FP 整理成按风险类型分组、附口径规则的 GPT 裁决工作表
输出：02_项目文档/FP归因工作表-按类型.md
"""
import json, io, collections
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parents[3]
SRC = _SERVICE_DIR / "_tmp_contract" / "pred_risks_full.json"
OUT = _SERVICE_DIR / "02_项目文档" / "评测与FP归因" / "FP归因工作表-按类型.md"

RISK_NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当",
}
# 每个类型的 v1.2.4 口径（裁决依据，一两条）
RULE = {
    "R01": "日≥5‰才标（规则B，不按年化突破）；真实政采标准违约金（20%/日2‰且合理）不标",
    "R02": "「全部损失/一切责任」且超可预见规则(584)才标；乙方对自己员工的赔偿、『依法赔偿』不构成R02",
    "R03": "一方随时无理由解约且无补偿(933/787)才标",
    "R04": "管辖明确偏向对方(我方=甲方)才标；甲方/原告所在地=不标；异地≠偏向；仲裁地不推定；法院+仲裁冲突进business_risks",
    "R05": "永久/无限期保密才标(501)；本项目gold中R05=0条",
    "R06": "无对价/明显不对称才标(859)；有对价买断不标；乙方仅保留代码复制权不标",
    "R07": "付款与服务贡献脱钩/期限范围明显失衡(525/526,965)才标；不机械按年限",
    "R08": "缺『客观判断交付是否合格』的验收机制(621-623,845)才标；有标准+程序+后果的不标",
    "R09": "仅交付型(买卖/承揽/建设/技术开发)缺失才标；租赁/中介/委托/物业/框架/劳动/保密不标",
    "R10": "≥5年且全国/主营全禁才标；3年同类排他不标",
    "R11": "沉默即续约(734)才标；有退出通道=弱；公告与披露版冲突按规则C分别记录",
    "R12": "数据共享/提供/转让无授权边界(个保法13/23)才标",
}

full = json.load(io.open(SRC, encoding="utf-8"))
fp_by_type = collections.defaultdict(list)
tp = fn = 0
for cid, c in full.items():
    gold = set(c["gold"])
    pred = set(c["pred"])
    tp += len(pred & gold)
    fn += len(gold - pred)
    for t in sorted(pred - gold):
        item = next((r for r in c["llm"] if r["risk_type"] == t), None)
        fp_by_type[t].append({
            "cid": cid,
            "no_risk": len(gold) == 0,
            "clause": (item or {}).get("clause_text", ""),
            "reason": (item or {}).get("reason", ""),
            "conf": (item or {}).get("confidence", 0.0),
            "gold": "+".join(sorted(gold)) or "[]",
        })

_total_fp = sum(len(v) for v in fp_by_type.values())
lines = ["# FP 归因工作表（466 条 · 按风险类型分组 · 供 GPT 四象限裁决）", ""]
lines.append("> 裁决四象限：**① Gold漏标**（真风险，补 gold）｜**② 口径外真实风险**（法律上有问题但不属 v1.2.4，单列展示）｜**③ 标注争议**（需再仲裁）｜**④ 真误报**（改 prompt/规则抑制）")
lines.append("> 口径依据：风险标注口径清单 v1.2.4。当前真实指标：TP {} / FP {} / FN {}，精准率 {:.1%}。".format(tp, _total_fp, fn, tp / (tp + _total_fp)))
lines.append("")
for t in sorted(fp_by_type, key=lambda x: -len(fp_by_type[x])):
    rows = fp_by_type[t]
    lines.append(f"## {t} {RISK_NAMES[t]} —— FP {len(rows)} 条")
    lines.append(f"**口径**：{RULE[t]}")
    lines.append("")
    lines.append("| # | contract | 无风险合同? | 原文证据 | 模型理由 | conf | 裁决①②③④ |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        ct = (r["clause"] or "").replace("|", "｜").replace("\n", " ")[:90]
        rs = (r["reason"] or "").replace("|", "｜").replace("\n", " ")[:110]
        nr = "✓" if r["no_risk"] else ""
        lines.append(f"| {i} | {r['cid']} | {nr} | {ct} | {rs} | {r['conf']:.2f} | |")
    lines.append("")

io.open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("已生成", OUT)
print("FP 总数", sum(len(v) for v in fp_by_type.values()), "| 各类型:", {t: len(v) for t, v in sorted(fp_by_type.items(), key=lambda x: -len(x[1]))})
