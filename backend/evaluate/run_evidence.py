"""run_evidence.py — 对 84 份合同跑 LLM 证据抽取，dump 到 evidence.json 供质量检验
用法：python backend/evaluate/run_evidence.py [--limit N]
"""
import sys, json, io, argparse
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.auditor.evidence_extractor import extract_evidence

TESTSET = Path(__file__).resolve().parent / "realtest.json"
OUT = Path(__file__).resolve().parent / "evidence.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    if args.limit > 0:
        entries = entries[: args.limit]

    out = {}
    for i, e in enumerate(entries):
        body = e.get("content") or e.get("text", "")
        ev = extract_evidence(body)
        out[e["id"]] = {
            "true_type": e.get("true_type", ""),
            "gold": [r["risk_type"] for r in e.get("risks", [])],
            "evidence": ev,
        }
        print(f"  [{i+1}/{len(entries)}] {e['id']} 证据抽取完成", flush=True)

    io.open(OUT, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n完成，写出 {OUT}")


if __name__ == "__main__":
    main()
