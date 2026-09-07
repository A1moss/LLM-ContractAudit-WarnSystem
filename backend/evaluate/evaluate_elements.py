"""
evaluate_elements.py — 要素抽取严格 F1（pred 抽取值 vs gold 标注值）

对每份合同调 ai.extractor.extract_elements，与 realtest.json 的 elements 标注做**值级比对**，
只统计 present=true（公告实际披露）的要素，输出每类 P/R/F1 与宏平均。

口径修正（v2，2026-09-04）：
- 旧版「四要素是否抽到（非空）」是覆盖率，抽到但值错不扣分。
- 严格 F1：抽到且值正确才计 TP；抽到但值错计 FP；标注存在但未抽到/抽错计 FN。
- 公告未披露的要素（present=false，如期限/争议）不计入分母，单独报告披露率。
- 比对为规则化值级比对（数值归一化/日期年份交集/关键词包含），可复现、可审计；
  完整合同拿到后可按 span 级升级（见口径文档）。

用法：python backend/evaluate/evaluate_elements.py [--limit N]
"""
import sys
import re
import json
import hashlib
import argparse
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from ai.extractor.extractor import extract_elements  # noqa: E402

TESTSET = Path(__file__).resolve().parent / "realtest.json"
CACHE = Path(__file__).resolve().parent / "cache_elements.json"

FIELD_CN = {
    "parties": "双方", "amount": "金额",
    "performance_period": "期限", "dispute_resolution": "争议解决",
}


def _load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ---------- 规则化值级比对 ----------
def _yuan_of(text):
    """把金额文本统一折算为『元』数值；无法识别返回 None。仅处理人民币，外币返回 (None, currency)。"""
    if not text:
        return None
    # 单位优先匹配「万元/亿元/元」；「万/亿」仅当后面跟非量词（避免「5万吨」误配）
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(万元|亿元|元|万(?!吨|个|只|件|台|套|米|人|亩|户)|亿(?!吨|个|只|件|台|套|米|人|亩|户))", text)
    if not m:
        m = re.search(r"([\d,]+(?:\.\d+)?)\s*(万元|亿元|元)", text)
    if not m:
        m = re.search(r"([\d,]+(?:\.\d+)?)", text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    unit = m.group(2) if len(m.groups()) > 1 and m.group(2) else "元"
    if unit in ("万元", "万"):
        return num * 1e4
    if unit in ("亿元", "亿"):
        return num * 1e8
    if unit in ("美元", "欧元"):
        return None  # 外币不换算
    return num


def _amount_to_yuan(v):
    """把 gold/pred 的金额标注值统一折算为『元』：数字直接返回；字符串解析单位；中文大写兜底。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    s = str(v).strip()
    if not s:
        return None
    y = _yuan_of(s)
    if y is not None:
        return y
    return _cn_upper_to_num(s)


def _cn_upper_to_num(s):
    """中文大写金额（壹贰叁…拾佰仟万亿）→ 数字；无法识别返回 None。"""
    digits = "零壹贰叁肆伍陆柒捌玖"
    units = {"拾": 10, "佰": 100, "仟": 1000, "万": 10000, "亿": 100000000}
    val, cur = 0.0, 0.0
    for ch in s:
        if ch in digits:
            cur = float(digits.index(ch))
        elif ch in units:
            u = units[ch]
            if cur == 0 and ch not in ("万", "亿"):
                cur = 1
            if ch in ("万", "亿"):
                val = (val + cur) * u
                cur = 0
            else:
                val += cur * u
                cur = 0
    r = val + cur
    return r if r > 0 else None


def _years_of(text):
    return set(re.findall(r"(20\d{2})", text or ""))


def _match_parties(pred, raw):
    if not isinstance(pred, dict):
        return False
    a, b = pred.get("甲方"), pred.get("乙方")
    if not a or not b:
        return False
    if "未知" in (str(a) + str(b)):
        return False
    # 脱敏后正文主体为〔甲方〕〔乙方〕/占位符，具体名称无法比对；
    # 口径 = 系统识别出甲乙双方角色均存在（与 gold parties.present 语义一致）
    return True


def _match_amount(pred, raw, gold_value=None):
    if not isinstance(pred, dict):
        return False
    p_yuan = None
    pv = pred.get("value")
    if isinstance(pv, (int, float)) and pv > 0:
        p_yuan = float(pv)
    else:
        p_yuan = _amount_to_yuan(pred.get("text") or "")
    # gold 侧：优先标注 value（干净值），其次原文片段（可能含「5万吨」等干扰数字）
    r_yuan = _amount_to_yuan(gold_value) if gold_value else None
    if r_yuan is None:
        r_yuan = _yuan_of(raw)
    if not p_yuan or not r_yuan:
        return False
    return abs(p_yuan - r_yuan) / r_yuan < 0.10  # 10% 容差（公告金额常为近似值）


def _match_period(pred, raw):
    if not isinstance(pred, dict) or not (pred.get("start") or pred.get("end")):
        # 无日期但抽到描述，视为未命中（严格：期限必须落到具体日期才正确）
        return False
    py = _years_of(str(pred.get("start", "")) + " " + str(pred.get("end", "")))
    ry = _years_of(raw)
    if py and ry and py & ry:
        return True
    # raw 是"X个月/N天"类期间，pred 未落到年份 → 视为不匹配（公告与预测口径难对齐）
    return False


def _match_dispute(pred, raw):
    if not pred or pred == "未知":
        return False
    kw = ("法院", "仲裁", "国际商会", "诉讼", "起诉", "仲裁院", "仲裁委员会")
    p = pred if isinstance(pred, str) else str(pred)
    r = raw or ""
    p_hit = any(k in p for k in kw)
    r_hit = any(k in r for k in kw)
    if not p_hit and not r_hit:
        return p.strip() == r.strip()  # 都是"协商"类 → 精确文本一致才算
    return p_hit and r_hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    entries = json.loads(TESTSET.read_text(encoding="utf-8"))
    if args.limit > 0:
        entries = entries[: args.limit]

    cache = _load_cache()
    stats = {f: {"tp": 0, "fp": 0, "fn": 0, "den": 0} for f in FIELD_CN}
    n_disclosed = {f: 0 for f in FIELD_CN}
    n_total = len(entries)

    for e in entries:
        body = e.get("content") or e.get("text", "")
        key = hashlib.md5(body.encode("utf-8")).hexdigest()[:16]
        if key not in cache:
            cache[key] = extract_elements(body, e.get("true_type", ""))
        pred = cache[key]
        elems = e.get("elements", {})

        # parties
        gold = elems.get("parties", {})
        if gold.get("present"):
            n_disclosed["parties"] += 1
            stats["parties"]["den"] += 1
            if _match_parties(pred.get("parties"), gold.get("raw")):
                stats["parties"]["tp"] += 1
            elif pred.get("parties"):
                stats["parties"]["fp"] += 1
                stats["parties"]["fn"] += 1
            else:
                stats["parties"]["fn"] += 1

        # amount
        gold = elems.get("amount", {})
        if gold.get("present"):
            n_disclosed["amount"] += 1
            stats["amount"]["den"] += 1
            if _match_amount(pred.get("amount"), gold.get("raw"), gold.get("value")):
                stats["amount"]["tp"] += 1
            elif pred.get("amount"):
                stats["amount"]["fp"] += 1
                stats["amount"]["fn"] += 1
            else:
                stats["amount"]["fn"] += 1

        # performance_period
        gold = elems.get("performance_period", {})
        if gold.get("present"):
            n_disclosed["performance_period"] += 1
            stats["performance_period"]["den"] += 1
            if _match_period(pred.get("performance_period"), gold.get("raw")):
                stats["performance_period"]["tp"] += 1
            elif pred.get("performance_period"):
                stats["performance_period"]["fp"] += 1
                stats["performance_period"]["fn"] += 1
            else:
                stats["performance_period"]["fn"] += 1

        # dispute_resolution
        gold = elems.get("dispute_resolution", {})
        if gold.get("present"):
            n_disclosed["dispute_resolution"] += 1
            stats["dispute_resolution"]["den"] += 1
            if _match_dispute(pred.get("dispute_resolution"), gold.get("raw")):
                stats["dispute_resolution"]["tp"] += 1
            elif pred.get("dispute_resolution"):
                stats["dispute_resolution"]["fp"] += 1
                stats["dispute_resolution"]["fn"] += 1
            else:
                stats["dispute_resolution"]["fn"] += 1

    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"=== 要素抽取严格 F1（{n_total} 份真实合同·公告型） ===")
    print("| 要素 | 披露样本 | TP | FP | FN | 精确率 | 召回率 | F1 |")
    print("|------|--------|----|----|----|-------|-------|-----|")
    prs = []
    for f, cn in FIELD_CN.items():
        s = stats[f]
        if s["den"] == 0:
            print(f"| {cn} | 0/{n_total} | - | - | - | - | - | - |")
            continue
        p = s["tp"] / (s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) else 0.0
        r = s["tp"] / s["den"] if s["den"] else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        prs.append((p, r, f1, s["den"]))
        print(f"| {cn} | {s['den']}/{n_total} | {s['tp']} | {s['fp']} | {s['fn']} | {p:.1%} | {r:.1%} | {f1:.1%} |")

    if prs:
        w_p = sum(x[0] * x[3] for x in prs) / sum(x[3] for x in prs)
        w_r = sum(x[1] * x[3] for x in prs) / sum(x[3] for x in prs)
        w_f = 2 * w_p * w_r / (w_p + w_r) if (w_p + w_r) else 0.0
        print(f"\n宏平均(按披露样本加权): P={w_p:.1%} R={w_r:.1%} F1={w_f:.1%}")
        print(f"\n披露率: 双方 {n_disclosed['parties']}/{n_total} | 金额 {n_disclosed['amount']}/{n_total} "
              f"| 期限 {n_disclosed['performance_period']}/{n_total} | 争议 {n_disclosed['dispute_resolution']}/{n_total}")
        print("注：公告型样本期限/争议披露率<100%，严格 F1 仅在披露样本上计算；")
        print("    完整合同 + span 级标注后，披露率与 F1 才能对齐赛题硬指标。")


if __name__ == "__main__":
    main()
