"""
ai.reporter.heatmap_data — 风险热力图数据生成

从 audit_records 计算每页（或每段落）的风险密度，
输出格式适配前端 ECharts 热力图（heatmap）。
"""
from typing import Any


def compute_heatmap(
    audit_records: list[dict[str, Any]],
    paragraphs_count: int | None = None,
    risk_types: list[str] | None = None,
) -> dict[str, Any]:
    """
    Compute per-page risk density data for ECharts heatmap.

    Args:
        audit_records: list of risk dicts, each with {risk_id, risk_level, clause_index (page/paragraph position)}
        paragraphs_count: total number of paragraphs (for x-axis of heatmap)
        risk_types: ordered list of risk type IDs (for y-axis)

    Returns:
        {
            "pages": [{"page": i, "risks": [{"type": "R01", "level": "high", "density": 1}]}],
            "matrix": [[x, y, density], ...],   # ECharts heatmap data format
            "xAxis": [...],   # page labels
            "yAxis": [...],   # risk type labels
            "maxDensity": int,
        }
    """
    # Determine risk types to display
    if risk_types is None:
        risk_types = [f"R{i:02d}" for i in range(1, 13)]

    # Determine paragraph/page count
    if paragraphs_count is None:
        indices = [r.get("clause_index", 0) for r in audit_records if r.get("clause_index", -1) >= 0]
        paragraphs_count = max(indices) + 1 if indices else 20
    paragraphs_count = max(paragraphs_count, 1)

    # Build density matrix: page x risk_type
    density = {}
    for r in audit_records:
        page = r.get("clause_index", -1)
        if page < 0:
            continue  # skip whole-document risks (R08/R09/R12 absence-based)
        risk_type = r.get("risk_id", "R00")
        key = (page, risk_type)
        density[key] = density.get(key, 0) + 1

    # Find max density for color scaling
    max_density = max(density.values()) if density else 1

    # Build ECharts heatmap matrix: [[x_index, y_index, value], ...]
    matrix = []
    y_types = risk_types
    for (page, rtype), value in density.items():
        if rtype in y_types:
            matrix.append([page, y_types.index(rtype), value])

    # Build pages array for detailed view
    pages = []
    for page_idx in range(paragraphs_count):
        page_risks = []
        for (p, rt), value in density.items():
            if p == page_idx:
                page_risks.append({
                    "type": rt,
                    "level": "high" if value >= 3 else ("medium" if value >= 2 else "low"),
                    "density": value,
                })
        if page_risks:
            pages.append({"page": page_idx, "risks": page_risks})

    # xAxis: paragraph/page numbers
    x_axis = [f"第{i+1}段" for i in range(paragraphs_count)]

    return {
        "pages": pages,
        "matrix": matrix,
        "xAxis": x_axis,
        "yAxis": y_types,
        "maxDensity": max_density,
    }
