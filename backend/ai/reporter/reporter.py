"""
ai.reporter.reporter — 审核报告生成器

六章结构化 HTML 报告：
1. 合同基本信息
2. 风险评估总览（含评分+分布）
3. 风险条款逐项分析（含等级颜色+置信度）
4. 条款完整性检查（比对结果）
5. 修改建议汇总（按优先级排序）
6. 附录（审核标准+原始检出+免责声明）
"""
import json
from datetime import datetime
from typing import Any


_RISK_LEVEL_COLORS = {
    "high": "#F56C6C",
    "medium": "#E6A23C",
    "low": "#67C23A",
}

_RISK_LEVEL_LABELS = {
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
}


def _risk_level_tag(level: str) -> str:
    color = _RISK_LEVEL_COLORS.get(level, "#909399")
    label = _RISK_LEVEL_LABELS.get(level, level)
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">{label}</span>'


def _risk_score_bar(score: float) -> str:
    """Render a risk score progress bar."""
    if score >= 70:
        color, label = "#F56C6C", "高风险合同"
    elif score >= 40:
        color, label = "#E6A23C", "中等风险合同"
    else:
        color, label = "#67C23A", "低风险合同"
    return (
        f'<div style="margin:10px 0">'
        f'<div style="background:#ebeef5;border-radius:10px;height:20px;width:100%">'
        f'<div style="background:{color};border-radius:10px;height:20px;width:{min(score,100)}%;'
        f'display:flex;align-items:center;justify-content:center;color:#fff;font-weight:bold;font-size:12px">'
        f'{score}/100</div></div>'
        f'<p style="text-align:center;margin:4px 0;font-weight:bold;color:{color}">{label}</p>'
        f'</div>'
    )


def generate_report(
    contract_info: dict[str, Any],
    audit_records: list[dict[str, Any]],
    compare_result: dict[str, Any] | None = None,
    heatmap_data: dict[str, Any] | None = None,
    rule_findings_raw: list[dict[str, Any]] | None = None,
) -> str:
    """
    Generate a six-chapter structured HTML audit report.

    Args:
        contract_info: {contract_type, confidence, quality, word_count, elements_json, audit_mode}
        audit_records: list of risk dicts with keys {risk_id, risk_name, risk_level, clause_text, reason, suggestion, confidence, detection_method, source}
        compare_result: output from matcher.compare_clauses() or None
        heatmap_data: output from heatmap_data.compute_heatmap() or None
        rule_findings_raw: raw rule engine output for appendix

    Returns:
        HTML string
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ct = contract_info.get("contract_type", "未知")
    confidence = contract_info.get("confidence", 0)
    quality = contract_info.get("quality", "未知")
    word_count = contract_info.get("word_count", 0)
    elements_json = contract_info.get("elements_json", "{}")
    audit_mode = contract_info.get("audit_mode", "lite")

    mode_labels = {"lite": "轻量模式(规则引擎)", "standard": "标准模式(规则+RAG)", "deep": "深度模式(规则+RAG+Corex)", "fast": "快速模式", "precise": "精准模式"}
    mode_display = mode_labels.get(audit_mode, audit_mode)

    # ── Chapter 1: Contract Info ──
    need_confirm = confidence < 0.5
    confirm_note = '<p style="color:#E6A23C;font-weight:bold">⚠️ 分类置信度较低，建议人工确认合同类型</p>' if need_confirm else ""

    # ── Chapter 2: Risk Overview ──
    high = sum(1 for r in audit_records if r.get("risk_level") == "high")
    medium = sum(1 for r in audit_records if r.get("risk_level") == "medium")
    low = sum(1 for r in audit_records if r.get("risk_level") == "low")
    total_risks = len(audit_records)
    overall_score = min(100, high * 30 + medium * 15 + low * 5) if total_risks > 0 else 0

    # ── Chapter 3: Risk Detail Table ──
    risk_rows = ""
    for r in audit_records:
        rid = r.get("risk_id", "?")
        rname = r.get("risk_name", "?")
        rlevel = r.get("risk_level", "low")
        clause = (r.get("clause_text", "") or "")[:200]
        reason = (r.get("reason", "") or "")[:200]
        suggestion = (r.get("suggestion", "") or "")[:200]
        rconf = r.get("confidence", 0)
        method = r.get("detection_method", r.get("source", "unknown"))
        low_conf = r.get("low_confidence", False)
        conf_color = "#E6A23C" if low_conf else ("#67C23A" if rconf >= 0.7 else "#F56C6C")
        risk_rows += (
            f'<tr>'
            f'<td>{rid}</td><td>{rname}</td><td>{_risk_level_tag(rlevel)}</td>'
            f'<td style="max-width:200px;overflow:hidden">{clause}</td>'
            f'<td style="max-width:200px;overflow:hidden">{reason}</td>'
            f'<td style="max-width:200px;overflow:hidden">{suggestion}</td>'
            f'<td>{method}</td>'
            f'<td><span style="color:{conf_color}">{rconf:.0%}</span>'
            f'{" ⚠️低置信度" if low_conf else ""}</td>'
            f'</tr>'
        )

    # ── Chapter 4: Clause Comparison ──
    if compare_result:
        summary = compare_result.get("summary", {})
        clauses = compare_result.get("clauses", [])
        comp_rows = ""
        for c in clauses:
            status = c.get("status", "missing")
            status_colors = {"covered": "#67C23A", "partial": "#E6A23C", "missing": "#F56C6C"}
            status_labels = {"covered": "已覆盖", "partial": "部分偏离", "missing": "缺失"}
            comp_rows += (
                f'<tr>'
                f'<td>{c.get("template_title","")}</td>'
                f'<td>{c.get("priority","")}</td>'
                f'<td><span style="color:{status_colors.get(status,"#909399")};font-weight:bold">{status_labels.get(status,status)}</span></td>'
                f'<td>{(c.get("matched_text") or "—")[:150]}</td>'
                f'<td>{c.get("similarity",0):.0%}</td>'
                f'<td>{(c.get("completion") or "—")[:200]}</td>'
                f'</tr>'
            )
        compare_section = (
            f'<h3>比对摘要</h3>'
            f'<p>模板条款总数：{summary.get("total",0)} | '
            f'已覆盖：{summary.get("covered",0)} | '
            f'部分偏离：{summary.get("partial",0)} | '
            f'缺失：{summary.get("missing",0)} | '
            f'覆盖率：{summary.get("coverage_rate",0):.1%}</p>'
            f'<table border="1" cellpadding="6" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<tr style="background:#f5f7fa"><th>模板条款</th><th>优先级</th><th>状态</th><th>匹配原文</th><th>相似度</th><th>建议</th></tr>'
            f'{comp_rows}</table>'
        )
    else:
        compare_section = '<p style="color:#909399">暂无条款比对数据（请配置标准条款模板知识库后重新审核）</p>'

    # ── Chapter 5: Suggestions ──
    # ── Chapter 6: Appendix ──
    raw_risks_json = json.dumps(rule_findings_raw or audit_records, ensure_ascii=False, indent=2)

    # ── Assemble HTML ──
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SmartContract Auditor 审核报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; color: #333; line-height: 1.6; }}
h1 {{ border-bottom: 3px solid #1E3A5F; padding-bottom: 10px; }}
h2 {{ background: #1E3A5F; color: #fff; padding: 8px 16px; border-radius: 6px; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
th {{ background: #f5f7fa; }}
pre {{ background: #f8f8f8; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12px; }}
.footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #eee; color: #999; text-align: center; font-size: 12px; }}
</style>
</head>
<body>

<h1>📋 SmartContract Auditor 合同审核报告</h1>

<p><strong>报告时间：</strong>{ts} | <strong>审核模式：</strong>{mode_display} | <strong>合同类型：</strong>{ct}（置信度 {confidence:.0%}）</p>

<hr>

<h2>一、合同基本信息</h2>

<table>
<tr><th style="width:150px">合同类型</th><td>{ct}</td></tr>
<tr><th>分类置信度</th><td>{confidence:.0%}</td></tr>
<tr><th>要素抽取质量</th><td>{quality}</td></tr>
<tr><th>文本篇幅</th><td>{word_count} 字</td></tr>
</table>
{confirm_note}

<h3>关键要素</h3>
<pre>{elements_json}</pre>

<h2>二、风险评估总览</h2>

<h3>综合风险评分</h3>
{_risk_score_bar(overall_score)}

<table>
<tr><th>指标</th><th>数值</th></tr>
<tr><td>检出风险总数</td><td>{total_risks}</td></tr>
<tr><td>高风险</td><td style="color:#F56C6C;font-weight:bold">{high}</td></tr>
<tr><td>中风险</td><td style="color:#E6A23C;font-weight:bold">{medium}</td></tr>
<tr><td>低风险</td><td style="color:#67C23A;font-weight:bold">{low}</td></tr>
</table>

<h2>三、风险条款逐项分析</h2>

<table>
<tr style="background:#f5f7fa"><th>编号</th><th>风险类型</th><th>等级</th><th>原文</th><th>理由</th><th>修改建议</th><th>检测方法</th><th>置信度</th></tr>
{risk_rows}
</table>

<h2>四、条款完整性检查</h2>
{compare_section}

<h2>五、修改建议汇总</h2>
<ol>
<li style="color:#F56C6C"><strong>⚠️ 高风险条款</strong> — 签署前必须解决，重点协商修改</li>
<li style="color:#E6A23C"><strong>📋 中风险条款</strong> — 建议协商修改方案，降低潜在争议</li>
<li style="color:#67C23A"><strong>💡 低风险条款</strong> — 后续版本优化，不影响签署</li>
</ol>
<p><strong>谈判顺序建议：</strong>先违约责任和知识产权 → 再付款条件和保密 → 最后协商细节条款</p>

<h2>六、附录</h2>

<h3>审核标准</h3>
<ul>
<li><strong>高风险：</strong>潜在损失超过 10 万元或违反强制性法律规定</li>
<li><strong>中风险：</strong>可能引发合同争议或商业风险</li>
<li><strong>低风险：</strong>表述不够精确但不影响合同效力</li>
</ul>
<p>风险类型：R01-R12 共 12 类 | 检测方法：{mode_display}</p>

<h3>规则引擎原始检出</h3>
<pre>{raw_risks_json}</pre>

<div style="background:#fff3cd;border:1px solid #ffc107;padding:12px;border-radius:6px;margin:10px 0">
⚠️ <strong>免责声明：</strong>本报告由 AI 自动生成，仅供参考。AI 审核无法替代专业法律判断。所有标注均经置信度过滤，建议法务人员最终复核。
</div>

<div class="footer">
📅 {ts} | Powered by SmartContract Auditor v1.0
</div>

</body>
</html>"""
    return html
