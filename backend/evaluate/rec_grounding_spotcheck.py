"""rec_grounding_spotcheck.py — v6.5 建议接地抽查
对 8 份含风险的合同，并排输出 Evidence + RAG 法条 + LLM 建议，供人工核对"建议是否可追溯"。
"""
import json, io, sys
sys.path.insert(0, r'C:\Users\11040\Desktop\服务外包\LLM-ContractAudit-WarnSystem\backend')
from ai.auditor.evidence_adjudicator import adjudicate_risks
from ai.auditor.recommendation_engine import build_recommendations, _retrieve_legal, RISK_NAMES

E = json.load(io.open(r'C:\Users\11040\Desktop\服务外包\LLM-ContractAudit-WarnSystem\backend\evaluate\evidence.json', encoding='utf-8'))
R = json.load(io.open(r'C:\Users\11040\Desktop\服务外包\LLM-ContractAudit-WarnSystem\backend\evaluate\realtest.json', encoding='utf-8'))
byid = {e['id']: e for e in R}

# 选 8 份覆盖不同风险类型的合同
sample = ['realtest_001', 'realtest_020', 'realtest_035', 'realtest_044', 'realtest_048',
          'realtest_052', 'realtest_074', 'realtest_081']

for cid in sample:
    rec = E.get(cid)
    if not rec:
        continue
    gold = [r['risk_type'] for r in byid[cid]['risks']]
    ev = rec['evidence']
    risks = adjudicate_risks(ev)
    if not risks:
        print(f"\n##### {cid} | gold={gold} | 裁决: 无风险 #####")
        continue
    enriched = build_recommendations(risks, ev)
    print(f"\n##### {cid} | gold={gold} | 裁决: {[r['risk_type'] for r in risks]} #####")
    for r in enriched:
        rt = r['risk_type']
        rag = _retrieve_legal(rt, RISK_NAMES.get(rt, rt), r.get('clause_text', ''))
        print(f"\n--- {rt} {RISK_NAMES.get(rt,'')} ---")
        print(f"条款: {r.get('clause_text','')[:80]}")
        print(f"RAG法条: {rag[:150].replace(chr(10),' / ')}")
        print(f"建议: {r.get('suggestion','')}")
        print(f"接地: {json.dumps(r.get('grounding'), ensure_ascii=False)}")
