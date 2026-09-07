"""ab_normalize.py — A/B 实验：压扁文本 vs 恢复结构，对证据抽取的影响
比较 R01/R02/R05/R07/R08/R09 六类证据，验证"输入标准化"假说。
"""
import json, io, re, sys
sys.path.insert(0, r'C:\Users\11040\Desktop\服务外包\LLM-ContractAudit-WarnSystem\backend')
from ai.auditor.evidence_extractor import extract_evidence

R = json.load(io.open(r'C:\Users\11040\Desktop\服务外包\LLM-ContractAudit-WarnSystem\backend\evaluate\realtest.json', encoding='utf-8'))
byid = {e['id']: e for e in R}


def normalize_contract(text: str) -> str:
    """恢复合同结构：章节/条/款/项 前插入换行。"""
    # 章节标题：一、二、… 三十、（中文数字 + 顿号）
    t = re.sub(r'(?=[一二三四五六七八九十]{1,3}、)', '\n', text)
    # 款：1、2、3、…（阿拉伯数字 + 顿号）
    t = re.sub(r'(?=\d{1,2}、)', '\n', t)
    # 项：（1）（2）…
    t = re.sub(r'(?=[（(]\d{1,2}[）)])', '\n', t)
    # 节/条：第X节 / 第X条
    t = re.sub(r'(?=第[一二三四五六七八九十百\d]+[节条])', '\n', t)
    # 折叠空行
    t = re.sub(r'\n{2,}', '\n', t)
    return t


def summarize(ev):
    keys = ["R01_违约金", "R02_责任", "R05_保密", "R07_付款", "R08_验收", "R09_不可抗力"]
    out = {}
    for k in keys:
        v = ev.get(k, {})
        if k == "R01_违约金":
            out[k] = {"exists": v.get("exists"), "rate": v.get("rate"), "unit": v.get("unit")}
        elif k == "R02_责任":
            out[k] = {"scope": v.get("scope"), "absolute_text": (v.get("absolute_text") or "")[:30]}
        elif k == "R05_保密":
            out[k] = {"duration": v.get("duration"), "statutory_basis": v.get("statutory_basis")}
        elif k == "R07_付款":
            out[k] = {"prepay_ratio": v.get("prepay_ratio"), "tail_ratio": v.get("tail_ratio")}
        elif k == "R08_验收":
            out[k] = {"objective_basis": v.get("objective_basis"), "basis": (v.get("basis_evidence") or "")[:30]}
        elif k == "R09_不可抗力":
            out[k] = {"has_force_majeure": v.get("has_force_majeure"), "evidence": (v.get("force_majeure_evidence") or "")[:30]}
    return out


for cid in ["realtest_003", "realtest_002"]:
    e = byid[cid]
    flat = e["content"]
    norm = normalize_contract(flat)
    print(f"\n===== {cid} | gold={[r['risk_type'] for r in e['risks']]} =====")
    print(f"压扁文本: {len(flat)} 字, 换行 {flat.count(chr(10))}")
    print(f"恢复结构: {len(norm)} 字, 换行 {norm.count(chr(10))}")
    ev_flat = extract_evidence(flat)
    ev_norm = extract_evidence(norm)
    print("--- 压扁文本证据 ---")
    print(json.dumps(summarize(ev_flat), ensure_ascii=False, indent=1))
    print("--- 恢复结构证据 ---")
    print(json.dumps(summarize(ev_norm), ensure_ascii=False, indent=1))
