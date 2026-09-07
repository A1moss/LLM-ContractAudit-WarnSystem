"""
evaluate_risks.py — 风险检测严格 P/R/F1（条款级，双引擎 + closed-set + 置信度过滤）

v4 口径（2026-09，与豆包 v7 gold 对齐）：
- 严格 F1 只算 R01-R12；R13「疑似名实不符」单列示警（规则 NAME_REALITY_SIGNALS 主导），
  不进严格召回分母（符合"AI 只示警、定性交人工"）。
- 双引擎：fast（规则引擎 run_rules）/ precise（规则 + LLM，temperature=0）。
- 双口径：open-set（R01-R12 全量）/ closed-set（只取 gold 已标注类型，砍 R08/R09 词表外）。
- 置信度过滤：precise 只砍 LLM 单源低置信（规则命中确定性高、不受阈值影响），
  阈值取 None/0.6/0.7/0.8 看 P/R/F1 曲线。
- "额外发现"单列：precise 报出但 gold 未标的类型，拆词表内/词表外（R08/R09）。

LLM 结果按 body md5 + PROMPT_VERSION 缓存（含置信度），中断可续跑。

用法：python backend/evaluate/evaluate_risks.py [--limit N] [--engine fast|precise|both]
"""
import sys
import json
import hashlib
import argparse
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.auditor.rule_engine import run_rules  # noqa: E402
from ai.auditor.llm_auditor import audit_with_llm  # noqa: E402

TESTSET = Path(__file__).resolve().parent / "realtest.json"
CACHE = Path(__file__).resolve().parent / "cache_risks_llm.json"

# prompt / temperature / 缓存结构变化时递增，避免旧缓存污染新口径
PROMPT_VERSION = "v5-v124-strict-tier"

RISK_NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当", "R13": "疑似名实不符",
}
# 严格 F1 的范围（R13 单列示警，不进分母）
STRICT_TYPES = {f"R{i:02d}" for i in range(1, 13)}

# 置信度过滤阈值曲线（None = 不过滤；其余 = LLM 单源置信度下限）
THRESHOLDS = [None, 0.6, 0.7, 0.8]


def _group_of(entry: dict) -> str:
    src = entry.get("source", "") or ""
    return "补全47" if src.startswith("上市公司公告披露版") else "真实37"


def _norm_type(rt) -> str | None:
    if not rt:
        return None
    t = str(rt).strip().upper()
    return t if t in RISK_NAMES else None


def _gold_strict(entry: dict) -> set[str]:
    """严格 F1 的 gold（R01-R12，R13 排除）。"""
    out = set()
    for r in entry.get("risks", []):
        t = _norm_type(r.get("risk_type"))
        if t and t in STRICT_TYPES:
            out.add(t)
    return out


def _gold_r13(entry: dict) -> bool:
    """该合同是否有 R13 gold（示警，不参与严格 F1）。"""
    return any(_norm_type(r.get("risk_type")) == "R13" for r in entry.get("risks", []))


def _load_cache() -> dict:
    if CACHE.exists():
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("_version") == PROMPT_VERSION:
                return data
        except Exception:
            pass
    return {"_version": PROMPT_VERSION}


def _save_cache(cache: dict) -> None:
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _prf(tp: int, fp: int, fn: int) -> tuple:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


def _llm_type_conf(llm_items: list) -> dict:
    """llm_items=[{"t","c"}] → {type: max confidence}。"""
    out = {}
    for it in llm_items:
        if isinstance(it, dict) and it.get("t"):
            out[it["t"]] = max(out.get(it["t"], 0.0), float(it.get("c", 0.0)))
    return out


def _filter_precise(rule_types: set, llm_conf: dict, threshold) -> set:
    """规则类型全保留；LLM 单源类型按阈值砍（阈值 None = 全保留）。"""
    kept = set(rule_types)
    for t, c in llm_conf.items():
        if t in rule_types:
            continue  # 规则已命中 = 交叉验证，保留
        if threshold is None or c >= threshold:
            kept.add(t)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 份（调试）")
    ap.add_argument("--engine", choices=["fast", "precise", "both"], default="both")
    args = ap.parse_args()

    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    if args.limit > 0:
        entries = entries[: args.limit]

    run_fast = args.engine in ("fast", "both")
    run_precise = args.engine in ("precise", "both")

    cache = _load_cache() if run_precise else {}

    # gold 已标注的严格类型（closed vocab，用于 closed-set 口径）
    gold_vocab = set()
    for e in entries:
        gold_vocab |= _gold_strict(e)

    GROUPS = ["真实37", "补全47", "合计84"]
    # rows[engine][threshold][group] = [tp_open, fp_open, fn_open, tp_closed, fp_closed, fn_closed]
    rows = {
        "fast": {None: {g: [0, 0, 0, 0, 0, 0] for g in GROUPS}},
        "precise": {th: {g: [0, 0, 0, 0, 0, 0] for g in GROUPS} for th in THRESHOLDS},
    }
    n_annotated = {g: 0 for g in GROUPS}   # 有严格条款风险标注的合同数
    n_llm_fallback = 0
    n = len(entries)
    done = 0

    # R13 示警统计（规则引擎，不参与严格 F1）
    r13_gold_n = 0
    r13_rule_hit_n = 0
    r13_positive = []   # (id, rule命中bool)

    for e in entries:
        body = e.get("content") or e.get("text", "")
        gold = _gold_strict(e)
        grp = _group_of(e)

        # ---- R13 示警（单列）----
        if _gold_r13(e):
            r13_gold_n += 1
            rule_types = {r["risk_type"] for r in run_rules(body)}
            hit = "R13" in rule_types
            r13_rule_hit_n += int(hit)
            r13_positive.append((e["id"], hit))

        # 无风险合同（gold 为空）也必须参与评测：模型在其上的任何预测都计入 FP，
        # 否则精准率被系统性高估（只评了"有风险"合同，漏掉 33 份无风险合同的过报）。
        if gold:
            n_annotated[grp] += 1
            n_annotated["合计84"] += 1
        done += 1

        # ---- fast（规则，无阈值，确定性）----
        if run_fast:
            rule_all = {t for t in (_norm_type(r["risk_type"]) for r in run_rules(body)) if t}
            pred = rule_all & STRICT_TYPES
            pred_closed = pred & gold_vocab
            tp = len(gold & pred); fp = len(pred - gold); fn = len(gold - pred)
            tpc = len(gold & pred_closed); fpc = len(pred_closed - gold)
            for g in (grp, "合计84"):
                rows["fast"][None][g][0] += tp
                rows["fast"][None][g][1] += fp
                rows["fast"][None][g][2] += fn
                rows["fast"][None][g][3] += tpc
                rows["fast"][None][g][4] += fpc
                rows["fast"][None][g][5] += fn

        # ---- precise（规则 + LLM，多阈值）----
        if run_precise:
            rule_types = {t for t in (_norm_type(r["risk_type"]) for r in run_rules(body)) if t}
            rule_strict = rule_types & STRICT_TYPES
            key = hashlib.md5(body.encode("utf-8")).hexdigest()[:16]
            try:
                if key not in cache:
                    llm = audit_with_llm(body)
                    # 只存严格类型 + 置信度（R13 已移出 LLM prompt，防御性过滤）
                    cache[key] = [
                        {"t": t, "c": float(r.get("confidence", 0.0))}
                        for r in llm if (t := _norm_type(r.get("risk_type"))) and t in STRICT_TYPES
                    ]
                llm_conf = _llm_type_conf(cache.get(key, []))
            except Exception as ex:
                n_llm_fallback += 1
                llm_conf = {}
                print(f"  [warn] LLM 审核失败退回 rule-only：{e.get('id', '?')} ({ex})", flush=True)

            for th in THRESHOLDS:
                pred = _filter_precise(rule_strict, llm_conf, th)
                pred_closed = pred & gold_vocab
                tp = len(gold & pred); fp = len(pred - gold); fn = len(gold - pred)
                tpc = len(gold & pred_closed); fpc = len(pred_closed - gold)
                for g in (grp, "合计84"):
                    rows["precise"][th][g][0] += tp
                    rows["precise"][th][g][1] += fp
                    rows["precise"][th][g][2] += fn
                    rows["precise"][th][g][3] += tpc
                    rows["precise"][th][g][4] += fpc
                    rows["precise"][th][g][5] += fn
            _save_cache(cache)
            print(f"  [{done}/{n}] {e.get('id', '?')} ({grp}) precise 已跑", flush=True)

    # ================= 输出 =================
    print(f"\n=== 风险检测双引擎评测（{n} 份 · R13 单列示警） ===")
    print(f"  严格条款风险标注合同: 真实37 {n_annotated['真实37']}/37 | 补全47 {n_annotated['补全47']}/47 | 合计 {n_annotated['合计84']}/84")
    print(f"  closed vocab（gold 已标注的严格类型）: {sorted(gold_vocab)}")
    if run_precise and n_llm_fallback:
        print(f"  LLM 失败退回 rule-only 次数: {n_llm_fallback}")

    # R13 示警
    print(f"\n  [R13 名实不符 · 单列示警，不进严格 F1]")
    print(f"    gold {r13_gold_n} 条；规则引擎命中 {r13_rule_hit_n} 条")
    for pid, hit in r13_positive:
        print(f"      {pid}: 规则{'命中 ✓' if hit else '未命中 ✗'}")

    engine_label = {"fast": "fast(规则)", "precise": "precise(规则+LLM)"}
    print("\n  | 分组 | 引擎(阈值) | 口径 | TP FP FN | 精确率 | 召回率 | F1 |")
    print("  |------|-----------|------|----------|--------|--------|-----|")
    for eng in (["fast"] if run_fast and not run_precise else ["precise"] if run_precise and not run_fast else ["fast", "precise"]):
        ths = [None] if eng == "fast" else THRESHOLDS
        for th in ths:
            for grp in GROUPS:
                r = rows[eng][th][grp]
                tp, fp, fn, tpc, fpc, fcl = r
                if tp + fn == 0:
                    continue
                th_label = "无过滤" if th is None else f"θ≥{th}"
                p, rec, f1 = _prf(tp, fp, fn)
                pc, recc, f1c = _prf(tpc, fpc, fcl)
                tag = "(同源)" if grp == "真实37" else ("(不同源)" if grp == "补全47" else "")
                print(f"  | {grp:<5} | {engine_label[eng]:<9} {th_label:<5} | open | {tp:>2} {fp:>3} {fn:>2} | {p:6.1%} | {rec:6.1%} | {f1:5.1%} |  {tag}")
                print(f"  |       |           {'':<5} | close| {tpc:>2} {fpc:>3} {fcl:>2} | {pc:6.1%} | {recc:6.1%} | {f1c:5.1%} |")


if __name__ == "__main__":
    main()
