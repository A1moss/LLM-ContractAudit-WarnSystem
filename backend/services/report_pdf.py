"""审核报告 PDF 导出服务。

职责边界（**严格遵守**）：
- 只做「已存在的审核结果 → PDF 排版」这一件事：**只读**、不调 LLM、不调 RAG、
  不跑规则引擎、不写任何表（导出不会新建审核记录、不会改变 contract.status）；
- 报告内容**全部来自数据库里已经落库的真实字段**：
    contracts            → file_name / contract_type / status / audit_mode / extracted_elements
    audit_reports (最新)  → risk_score / high|mid|low_risk_count / missing_clauses / created_at
    audit_records (最新批次) → risk_type / risk_level / clause_text / clause_position /
                              reason / suggestion / detection_method / confidence /
                              evidence / recommendation
   **不重新计算**任何指标（评分/计数直接取报告快照，与前端 ReportPanel / AuditReportDetail 同源）：
   `high_risk_count` 等一律取快照值，缺失时才退化为对当前批次记录的计数，并**不新增 total 字段**。
- **绝不虚构**数据库里不存在的字段：审核人 / 报告编号 / 报告版本 / 审批状态 /
  审核机构 / 生成机构 一律不出现。`contracts.status` 只以「合同状态」名义展示。
- 条款中文名 / 等级中文名 / 检测方式中文名 / 比对状态中文名与前端展示表逐字一致
  （frontend/src/constants/riskTypes.js）。这里**镜像**前端展示映射，不 import 判定模块，
  以确保与本功能的唯一跨文件入口就是「报告数据」本身。

中文字体方案（三层，逐层降级，见 `resolve_font`）：
1. 项目自带字体 `backend/assets/fonts/`（若存在则优先，便于离线/容器化部署）；
2. 系统常见中日韩字体（Windows: simsun/simhei/msyh；macOS: Songti/PingFang；Linux: Noto CJK/WQY）；
3. reportlab 自带 CID 字体 STSong-Light（Adobe-GB1，**随 reportlab 一起安装，无需任何字体文件**）。
   实测三者在 Windows / Linux / CI 上均可嵌入并正常输出中文，且**不依赖 Word / LibreOffice / Office 环境**。
"""
from __future__ import annotations

import io
import logging
import os
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

logger = logging.getLogger(__name__)

# ===========================================================================
# 展示映射（与 frontend/src/constants/riskTypes.js / reportTokens.js 逐字一致）
# ===========================================================================

REPORT_TITLE = "合同智能审核报告"

REPORT_DISCLAIMER = (
    "本报告由 AI 合同审核系统自动生成，仅供法务与业务参考，不构成法律意见。"
)

# 与 ai/auditor/recommendation_engine.RISK_NAMES 逐条一致（此处只做展示镜像，不参与判定）
RISK_NAMES = {
    "R01": "违约金过高", "R02": "无限责任", "R03": "单方解约权", "R04": "管辖条款不利",
    "R05": "保密期间不合理", "R06": "知识产权归属不清", "R07": "付款条件不公平",
    "R08": "验收标准缺失", "R09": "不可抗力条款缺失", "R10": "竞业限制过宽",
    "R11": "自动续约陷阱", "R12": "数据隐私条款不当", "R13": "疑似名实不符",
}

RISK_LEVEL_LABELS = {"high": "高风险", "medium": "中风险", "low": "低风险"}
RISK_LEVEL_COLORS = {"high": "#DC2626", "medium": "#D97706", "low": "#2563EB"}
RISK_LEVEL_ORDER = ["high", "medium", "low"]

DETECTION_METHOD_LABELS = {
    "rule": "规则引擎",
    "rag": "检索引用",
    "corex_review": "多智能体验证",
    "evidence": "证据裁决",
}

CONTRACT_STATUS_LABELS = {
    "uploaded": "已上传",
    "parsed": "已解析",
    "auditing": "审核中",
    "completed": "审核完成",
    "reviewed": "待验收",
    "approved": "已验收",
}

AUDIT_MODE_LABELS = {"precise": "精细审核", "fast": "快速初筛"}

CMP_STATUS_LABELS = {"covered": "已覆盖", "partial": "部分偏离", "missing": "缺失"}
CMP_COLORS = {"covered": "#16A34A", "partial": "#D97706", "missing": "#DC2626"}
PRIORITY_LABELS = {"required": "必需条款", "recommended": "建议条款"}

# 金额币种 → 展示用**文字**缩写。
# 刻意不用 ¥ / € / £ 符号：实测系统字体对货币符号的覆盖参差不齐（如 simhei 缺 ¥/€/£，
# 会渲染成空白方框），而 reportlab 降级用的内置 CID 字体没有 €。用币种代码既无字形风险，
# 也不改变金额数值本身（金额一律按后端真实 value/currency 格式化，不换算、不推断）。
CURRENCY_SYMBOLS = {"CNY": "CNY", "USD": "USD", "EUR": "EUR", "GBP": "GBP", "JPY": "JPY"}

# 全角数字 / 全角百分比 → 半角（合同正文由解析结果带入，可能含全角字符；仅做显示层归一，
# 不改变任何数值含义）。全角标点（、。，；：！？（）《》等）保留 —— 已实测各候选字体均覆盖。
_FULLWIDTH_MAP = {ord("０") + i: ord("0") + i for i in range(10)}
_FULLWIDTH_MAP[ord("％")] = ord("%")
_FULLWIDTH_MAP[ord("＋")] = ord("+")
_FULLWIDTH_MAP[ord("－")] = ord("-")

INK = colors.HexColor("#111827")
TEXT = colors.HexColor("#374151")
MUTED = colors.HexColor("#6B7280")
FAINT = colors.HexColor("#9CA3AF")
LINE = colors.HexColor("#E5E7EB")
SOFT_BG = colors.HexColor("#F9FAFB")
BRAND = colors.HexColor("#1935C3")


# ===========================================================================
# 中文字体解析
# ===========================================================================

# 项目自带字体目录（可选；存在即优先使用，便于完全离线/容器化部署）
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
_VENDOR_CANDIDATES = (
    "NotoSansSC-Regular.ttf",
    "SourceHanSansSC-Regular.otf",
    "msyh.ttc",
    "simsun.ttc",
    "simhei.ttf",
    "wqy-zenhei.ttc",
)

# 系统字体候选（**顺序即优先级**）。
# 顺序依据「实测字形覆盖」而定：PDF 里会出现 ¥/€/£ 等货币符号与「、。“”《》……」等中文标点，
# 实测 simhei.ttf 缺 ¥/€/£（会渲染成空白方框），msyh 全字符集覆盖完整，故 msyh 优先；
# 每个候选都会被逐个尝试，全部不可用时才降级到 reportlab 自带 CID 字体。
_SYSTEM_CANDIDATES = (
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",           # 微软雅黑：CJK + ¥€£ + 中文标点全覆盖
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf",  # Noto Sans SC
    r"C:\Windows\Fonts\simsun.ttc",         # 宋体：覆盖面广
    r"C:\Windows\Fonts\simhei.ttf",         # 黑体（缺 ¥/€/£，故排在其后）
    r"C:\Windows\Fonts\msjh.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux / 容器
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)

_CID_FALLBACK = "STSong-Light"
_FONT_CACHE: dict | None = None


def resolve_font() -> dict:
    """解析一个可用于中文排版的正文字体（结果缓存）。

    返回 ``{"ttf": bool, "regular": str}``：
      - ``ttf=True``  → regular 是已注册的 TTF/TTC 字体名；
      - ``ttf=False`` → regular 是 reportlab 自带 CID 字体名（STSong-Light）。
    """
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE

    env_font = os.environ.get("AUDIT_REPORT_FONT", "").strip()
    candidates = []
    if env_font:
        candidates.append(env_font)
    candidates.extend(os.path.join(_VENDOR_DIR, n) for n in _VENDOR_CANDIDATES)
    candidates.extend(_SYSTEM_CANDIDATES)

    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        name = "AuditReportCJK-" + re.sub(r"[^A-Za-z0-9]", "", os.path.basename(path))
        try:
            pdfmetrics.registerFont(TTFont(name, path))
        except Exception as e:  # 不支持的字体格式（如 CFF/OTF）继续尝试下一个
            logger.debug("注册中文字体失败 %s: %s", path, e)
            continue
        logger.info("审核报告 PDF 使用字体: %s", path)
        _FONT_CACHE = {"ttf": True, "regular": name, "source": path}
        return _FONT_CACHE

    # 兜底：reportlab 自带 CID 字体（Adobe-GB1），随包安装、无需字体文件
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(_CID_FALLBACK))
    except Exception as e:  # pragma: no cover - reportlab 自带 CMap，正常不会失败
        logger.error("注册内置 CID 中文字体失败: %s", e)
        raise
    logger.info("审核报告 PDF 使用内置 CID 字体: %s", _CID_FALLBACK)
    _FONT_CACHE = {"ttf": False, "regular": _CID_FALLBACK, "source": "reportlab-cid"}
    return _FONT_CACHE


# ===========================================================================
# 纯展示工具
# ===========================================================================

def risk_name(risk_type: str | None) -> str:
    if not risk_type:
        return ""
    return RISK_NAMES.get(risk_type, risk_type)


def risk_level_label(level: str | None) -> str:
    return RISK_LEVEL_LABELS.get(level or "", level or "—")


def detection_label(method: str | None) -> str:
    if not method:
        return ""
    return DETECTION_METHOD_LABELS.get(method, method)


def cmp_status_label(status: str | None) -> str:
    return CMP_STATUS_LABELS.get(status or "", status or "—")


def priority_label(priority: str | None) -> str:
    return PRIORITY_LABELS.get(priority or "", priority or "")


def normalize_text(value) -> str:
    """显示层字符归一：全角数字 / 全角百分号 → 半角，并统一换行符。

    **只做字符形态归一，不改变任何数值或语义**；中文标点原样保留。
    """
    s = "" if value is None else str(value)
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.translate(_FULLWIDTH_MAP)


def _esc(value) -> str:
    """归一 + 转义 reportlab Paragraph 的 XML 标记（合同正文里可能出现 & < >）。

    同时对**每一行**做首尾去空白：合同正文由解析结果带入，真实数据里存在
    「…组织验收。\n    第三条 …」这类行首缩进，直接渲染会在字段名后产生突兀的大段空隙；
    去空白只影响排版，不改变任何字符内容。换行本身保留，由调用方决定是否转成 <br/>。
    """
    s = normalize_text(value)
    if "\n" in s:
        s = "\n".join(line.strip() for line in s.split("\n"))
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_block(value) -> str:
    """字段内容用转义：保留原始换行 → ``<br/>``（逐字原文的行结构不丢）。"""
    return _esc(value).replace("\n", "<br/>")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def format_audit_time(ts) -> str:
    """审核时间 → 本地可读时间。

    入参可以是 ``audit_reports.created_at``（naive UTC datetime，与 api/contracts._iso 同口径）
    或已经是 ISO 字符串的值；两者都按 UTC 解析后转本地时区（与前端 formatTime 同口径）。
    """
    if ts is None or ts == "":
        return ""
    try:
        if isinstance(ts, datetime):
            dt = ts
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def amount_parts(amount) -> tuple[str, str]:
    """合同金额拆分：只按后端真实 ``{value, currency, text}`` 格式化，**不换算、不推断**。"""
    if not isinstance(amount, dict):
        return "", ""
    value, currency, text = amount.get("value"), amount.get("currency"), amount.get("text")
    main = ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        code = CURRENCY_SYMBOLS.get(currency or "", currency or "")
        main = f"{value:,.2f}"
        if code:
            main = f"{code} {main}"
    elif currency:
        main = str(currency)
    return main, (text if isinstance(text, str) else "")


def performance_period_text(period) -> str:
    if not isinstance(period, dict):
        return ""
    start, end = period.get("start"), period.get("end")
    if start and end:
        return f"{start} 至 {end}"
    if start:
        return f"{start} 起"
    if end:
        return f"至 {end}"
    return ""


def safe_filename(name: str, fallback: str = "合同") -> str:
    """生成跨平台安全的下载文件名（去掉 Windows/Linux 不允许的字符，长度收敛）。"""
    base = _text(name) or fallback
    base = re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", base)
    base = re.sub(r"[\x00-\x1f]", "", base)
    base = re.sub(r"\.(docx|doc|pdf|png|jpe?g|tiff?|bmp)$", "", base, flags=re.I)
    base = base.strip(" ._")
    return (base or fallback)[:80]


def normalize_level_counts(report, records: list) -> dict:
    """风险三档计数。

    优先取**审核报告快照**（与前端完全同源）；快照缺失（无报告/字段为 None）时才退化为
    对当前批次记录的计数 —— 两种口径都来自后端真实数据，**不做任何加权/推算**。
    """
    if report is not None:
        return {
            "high": report.high_risk_count or 0,
            "medium": report.mid_risk_count or 0,
            "low": report.low_risk_count or 0,
        }
    return {
        "high": len([r for r in records if r.risk_level == "high"]),
        "medium": len([r for r in records if r.risk_level == "medium"]),
        "low": len([r for r in records if r.risk_level == "low"]),
    }


def build_report_data(contract, report, records: list) -> dict:
    """把已落库的真实行对象整理成 PDF 渲染所需的纯数据结构。

    **只读、无副作用**：不查库、不算分、不调 LLM，全部字段直接映射自入参行对象。
    """
    elements = contract.extracted_elements if isinstance(contract.extracted_elements, dict) else {}
    counts = normalize_level_counts(report, records)

    comparison = None
    if report is not None and isinstance(report.missing_clauses, dict) and report.missing_clauses.get("summary"):
        comparison = report.missing_clauses

    missing_critical = []
    cross_risks = []
    clauses = []
    if comparison:
        mc = comparison.get("missing_critical")
        missing_critical = [c for c in mc if c] if isinstance(mc, list) else []
        cr = comparison.get("cross_clause_risks")
        cross_risks = cr if isinstance(cr, list) else []
        cl = comparison.get("clauses")
        clauses = [c for c in cl if isinstance(c, dict)] if isinstance(cl, list) else []

    return {
        "contract": {
            "file_name": _text(contract.file_name),
            "contract_type": _text(contract.contract_type),
            "type_label": _text(getattr(contract, "type_label", "")) or _text(contract.contract_type),
            "status": _text(contract.status),
            "status_label": CONTRACT_STATUS_LABELS.get(contract.status or "", contract.status or ""),
            "audit_mode_label": AUDIT_MODE_LABELS.get(contract.audit_mode or "", ""),
            "elements": elements,
            "parties": elements.get("parties") if isinstance(elements.get("parties"), dict) else {},
        },
        "report": {
            "created_at": getattr(report, "created_at", None),
            "audit_time": format_audit_time(getattr(report, "created_at", None)),
            "risk_score": None if report is None else report.risk_score,
            # 当前批次是否存在有效审核结果（与 GET /audit-result 的 has_current_result 同口径）
            "result_valid": any(getattr(r, "result_status", None) == "valid" for r in records),
        },
        "counts": counts,
        "total_risks": counts["high"] + counts["medium"] + counts["low"],
        "records": records,
        "comparison": comparison,
        "comparison_summary": (comparison or {}).get("summary"),
        "missing_critical": missing_critical,
        "cross_risks": cross_risks,
        "clauses": clauses,
        "disclaimer": REPORT_DISCLAIMER,
    }


# ===========================================================================
# 样式
# ===========================================================================

def _styles(font: str) -> dict:
    def s(name, **kw):
        kw.setdefault("fontName", font)
        kw.setdefault("textColor", TEXT)
        return ParagraphStyle(name, **kw)

    return {
        # 章标题：keepWithNext 保证「标题不会孤零零留在页尾」
        "h1": s("h1", fontSize=15, leading=21, textColor=INK, spaceBefore=12, spaceAfter=8, keepWithNext=1),
        "h2": s("h2", fontSize=12, leading=18, textColor=INK, spaceBefore=10, spaceAfter=6, keepWithNext=1),
        "title": s("title", fontSize=21, leading=28, textColor=INK, spaceAfter=4),
        "sub": s("sub", fontSize=11, leading=17, textColor=MUTED),
        "meta": s("meta", fontSize=9, leading=14, textColor=MUTED),
        "body": s("body", fontSize=10.5, leading=16.5, wordWrap="CJK"),
        "body_lg": s("body_lg", fontSize=11.5, leading=19, textColor=INK, wordWrap="CJK"),
        "note": s("note", fontSize=9.5, leading=15, textColor=MUTED, wordWrap="CJK"),
        "field_k": s("field_k", fontSize=9, leading=13, textColor=MUTED),
        # 「风险原文」字段：字段名 + 逐字原文同一段
        "quote_field": s("quote_field", fontSize=10, leading=15.5, textColor=colors.HexColor("#1F2937"),
                         wordWrap="CJK", leftIndent=6, borderPadding=(4, 6, 4, 6),
                         borderWidth=0, spaceBefore=2, spaceAfter=2),
        "risk_title": s("risk_title", fontSize=11.5, leading=16, textColor=INK, wordWrap="CJK"),
        "cell": s("cell", fontSize=9.5, leading=14, wordWrap="CJK"),
        "cell_head": s("cell_head", fontSize=9.5, leading=14, textColor=INK),
        "disclaimer": s("disclaimer", fontSize=9.5, leading=15, textColor=MUTED, wordWrap="CJK"),
        "empty": s("empty", fontSize=10, leading=15, textColor=FAINT, wordWrap="CJK"),
    }


# ===========================================================================
# 页眉页脚
# ===========================================================================

def _make_page_decorator(font: str):
    def decorate(canv, doc):
        canv.saveState()
        canv.setFont(font, 9)
        canv.setFillColor(FAINT)
        # 页眉：固定标题（**不含**报告编号 / 版本号 —— 数据库没有这两个字段）
        canv.drawString(20 * mm, A4[1] - 12 * mm, REPORT_TITLE)
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.5)
        canv.line(20 * mm, A4[1] - 14.5 * mm, A4[0] - 20 * mm, A4[1] - 14.5 * mm)
        # 页脚：页码
        canv.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
        canv.drawCentredString(A4[0] / 2, 10.5 * mm, f"第 {canv.getPageNumber()} 页")
        canv.restoreState()

    return decorate


# ===========================================================================
# Story 组装
# ===========================================================================

def _kv_table(rows, styles: dict, font: str, col1: float = 26 * mm):
    """两列信息表（label / value）；rows 为 (label, value) 列表，空值行由调用方过滤。"""
    data = [[Paragraph(_esc(k), styles["field_k"]), Paragraph(_esc(v), styles["cell"])] for k, v in rows]
    t = Table(data, colWidths=[col1, None])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _build_contract_info(data: dict, styles: dict, font: str):
    c = data["contract"]
    elements = c["elements"] or {}
    rows = [("合同文件名", c["file_name"] or "—")]
    if c["type_label"]:
        rows.append(("合同类型", c["type_label"]))
    if c["status_label"]:
        # 明确叫「合同状态」：contracts.status 是合同生命周期状态，不是审批状态
        rows.append(("合同状态", c["status_label"]))
    if data["report"]["audit_time"]:
        rows.append(("审核时间", data["report"]["audit_time"]))
    if c["audit_mode_label"]:
        rows.append(("审核方式", c["audit_mode_label"]))

    # 以下字段全部来自 extracted_elements：没有就整行不渲染，**不填占位**
    for k, v in (c["parties"] or {}).items():
        if v:
            rows.append((str(k), str(v)))
    main, sub = amount_parts(elements.get("amount"))
    if main or sub:
        rows.append(("合同金额", main or sub))
    if elements.get("sign_date"):
        rows.append(("签订日期", str(elements["sign_date"])))
    period = performance_period_text(elements.get("performance_period"))
    if period:
        rows.append(("履行期限", period))
    if elements.get("dispute_resolution"):
        rows.append(("争议解决", str(elements["dispute_resolution"])))
    if isinstance(elements.get("governing_law"), str) and elements["governing_law"].strip():
        rows.append(("适用法律", elements["governing_law"].strip()))

    return [Paragraph("一、合同基本信息", styles["h1"]), _kv_table(rows, styles, font)]


def _conclusion_text(data: dict) -> str:
    total = data["total_risks"]
    counts = data["counts"]
    summary = data["comparison_summary"] or {}
    coverage = summary.get("coverage_rate")
    coverage_pct = round(coverage * 100) if isinstance(coverage, (int, float)) else None

    if total > 0:
        parts = []
        if counts["high"]:
            parts.append(f"高风险 {counts['high']} 项")
        if counts["medium"]:
            parts.append(f"中风险 {counts['medium']} 项")
        if counts["low"]:
            parts.append(f"低风险 {counts['low']} 项")
        text = f"本次审核共识别 {total} 项风险，其中{'、'.join(parts) if parts else '各等级均无检出'}。"
        if coverage_pct is not None:
            text += (
                f"条款完整性覆盖率为 {coverage_pct}%（标准条款 {summary.get('total', 0)} 条，"
                f"已覆盖 {summary.get('covered', 0)} 条、部分偏离 {summary.get('partial', 0)} 条、"
                f"缺失 {summary.get('missing', 0)} 条）"
            )
            if data["missing_critical"]:
                text += f"，另有 {len(data['missing_critical'])} 项关键条款缺失"
            text += "。"
    else:
        text = "本次审核未识别到 R01–R13 风险项。"
        if coverage_pct is not None:
            text += f"条款完整性覆盖率为 {coverage_pct}%。"
            if data["missing_critical"]:
                text += f"另有 {len(data['missing_critical'])} 项关键条款缺失。"

    return text + "建议结合下方「风险问题明细」与「条款完整性检查」结果进行进一步复核。"


def _build_conclusion(data: dict, styles: dict, font: str):
    story = [Paragraph("二、审核结论", styles["h1"]), Paragraph(_esc(_conclusion_text(data)), styles["body"], )]
    story.append(Spacer(1, 6))
    story.append(Paragraph(_esc(data["disclaimer"]), styles["disclaimer"]))
    return story


def _build_risk_stats(data: dict, styles: dict, font: str):
    counts = data["counts"]
    total = data["total_risks"]
    score = data["report"]["risk_score"]

    rows = []
    if score is not None:
        rows.append(("风险评分", f"{score} / 100（分数越高风险越高）"))
    rows.append(("风险项合计", f"{total} 项"))
    for lv in RISK_LEVEL_ORDER:
        rows.append((risk_level_label(lv), f"{counts[lv]} 项"))

    summary = data["comparison_summary"] or {}
    if summary:
        rate = summary.get("coverage_rate")
        if isinstance(rate, (int, float)):
            rows.append(("条款覆盖率", f"{round(rate * 100)}%"))
        rows.append(("标准条款", f"{summary.get('total', 0)} 条"))

    story = [Paragraph("三、风险统计", styles["h1"]), _kv_table(rows, styles, font, col1=32 * mm)]

    # 等级分布：用真实计数画水平条，计数全为 0 时不出图（不画空条）
    seg_total = sum(counts.values())
    if seg_total > 0:
        story.append(Spacer(1, 8))
        cells, widths = [], []
        for lv in RISK_LEVEL_ORDER:
            n = counts[lv]
            if not n:
                continue
            cells.append(Paragraph(
                f"<font color='{RISK_LEVEL_COLORS[lv]}'>{_esc(risk_level_label(lv))} {n}</font>",
                styles["cell"]))
            widths.append(None)
        bar = Table([cells], colWidths=[(170 * mm) / max(1, len(cells))] * len(cells))
        bar.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SOFT_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(bar)
    return story


def _risk_record_views(records: list) -> list:
    """把 audit_records 行对象整理成展示视图（分组顺序 = 高→中→低，组内按判断把握度降序）。"""
    views = []
    for r in records:
        pos = r.clause_position if isinstance(r.clause_position, dict) else {}
        rec = r.recommendation if isinstance(r.recommendation, dict) else {}
        ev = r.evidence if isinstance(r.evidence, dict) else {}
        anchor = pos.get("original_text") if isinstance(pos.get("original_text"), str) else ""

        clause_no = ""
        no = pos.get("clause_no")
        if no not in (None, ""):
            clause_no = f"第 {no} 条"
            if pos.get("clause_title"):
                clause_no += f" · {pos['clause_title']}"

        anchor_range = ""
        if isinstance(pos.get("start"), int) and isinstance(pos.get("end"), int):
            anchor_range = f"字符区间 {pos['start']}–{pos['end']}"

        confidence = r.confidence if isinstance(r.confidence, (int, float)) else None

        views.append({
            "id": r.id,
            "level": r.risk_level,
            "level_label": risk_level_label(r.risk_level),
            "name": risk_name(r.risk_type),
            "code": r.risk_type or "",
            "clause_no": clause_no,
            "clause_text": r.clause_text or "",
            "anchor": anchor,
            "anchor_range": anchor_range,
            "reason": r.reason or "",
            "suggestion": r.suggestion or "",
            "example": rec.get("example") or "",
            "risk_description": rec.get("risk_description") or "",
            "legal_basis": rec.get("legal_basis") or "",
            "evidence_law": ev.get("law") or "",
            "method": detection_label(r.detection_method),
            "confidence_pct": None if confidence is None else round(confidence * 100),
            "grounding_issues": (
                [x for x in (rec.get("grounding") or {}).get("issues", []) if x]
                if isinstance(rec.get("grounding"), dict) and rec["grounding"].get("passed") is False
                else []
            ),
            "result_status": r.result_status,
        })

    order = {lv: i for i, lv in enumerate(RISK_LEVEL_ORDER)}
    # 稳定排序：先按等级档位，再按判断把握度降序（与页面 groupRisksByLevel + sortByConfidence 同序）
    indexed = list(enumerate(views))
    indexed.sort(key=lambda p: (order.get(p[1]["level"], 99), -(p[1]["confidence_pct"] or 0), p[0]))
    return [v for _, v in indexed]


def _field(label: str, value: str, styles: dict, style_key: str = "body"):
    """字段块：**字段名与内容在同一个 Paragraph 里**（label 走 <b> 前缀）。

    为什么要合并：如果只在字段名上设 keepWithNext，字段名的「下一段」可能是整块引用，
    一旦该引用放不进当前页剩余空间，reportlab 就会在字段名之后分页，出现「字段名孤立在页尾」；
    合并成一段后 label 与内容天然同页，且**首行与 label 同行**，读起来是标准的
    「字段名：内容」结构，不会出现 label 与内容视觉粘连。
    """
    if not value:
        return None
    return Paragraph(
        f"<b>{_esc(label)}</b>&nbsp;&nbsp;{_esc_block(value)}",
        styles[style_key],
    )


def _build_risk_card(v: dict, styles: dict, font: str):
    """单条风险卡片（一个 KeepTogether 单元，避免标题/卡片主体被拆到两页）。

    卡片内容按真实字段可用性逐项渲染，缺字段即整项不产出（不显示「暂无」占位）。
    """
    color = colors.HexColor(RISK_LEVEL_COLORS.get(v["level"], "#6B7280"))
    head = Paragraph(
        f"<font color='{RISK_LEVEL_COLORS.get(v['level'], '#6B7280')}'>【{_esc(v['level_label'])}】</font> "
        f"<b>{_esc(v['name'])}</b>"
        + (f" <font color='#9CA3AF'>{_esc(v['code'])}</font>" if v["code"] else ""),
        styles["risk_title"],
    )

    inner = [head]

    # 涉及条款 / 原文位置：真实字段可用时才有这一行
    rows = []
    if v["clause_no"]:
        rows.append(("涉及条款", v["clause_no"]))
    if v["anchor"]:
        rows.append(("原文位置", v["anchor_range"] or "已建立原文锚点"))
    if rows:
        inner.append(_kv_table(rows, styles, font, col1=24 * mm))

    # 风险原文：优先后端的 clause_position.original_text 逐字原文，其次 clause_text
    origin_text = v["anchor"] or v["clause_text"]
    if origin_text:
        f = _field("风险原文", origin_text, styles, "quote_field")
        if f:
            inner.append(f)

    for label, key in (("风险原因", "reason"), ("修改建议", "suggestion"),
                       ("修改示例", "example"), ("风险说明", "risk_description")):
        f = _field(label, v[key], styles)
        if f:
            inner.append(f)

    if v["legal_basis"] or v["evidence_law"]:
        parts = [p for p in (v["legal_basis"], v["evidence_law"]) if p]
        f = _field("法律依据", "　".join(parts), styles)
        if f:
            inner.append(f)

    if v["grounding_issues"]:
        inner.append(Paragraph(
            _esc("该建议含未经证据核实的数值，请结合合同原文确认。（"
                 + "；".join(str(x) for x in v["grounding_issues"]) + "）"),
            styles["note"],
        ))

    meta_bits = []
    if v["method"]:
        meta_bits.append(v["method"])
    if v["confidence_pct"] is not None:
        meta_bits.append(f"判断把握度 {v['confidence_pct']}%")
    if meta_bits:
        inner.append(Paragraph(
            f"<font color='#9CA3AF'>{_esc(' · '.join(meta_bits))}</font>", styles["note"]))

    box = Table([[inner]], colWidths=[None])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEBEFORE", (0, 0), (0, 0), 2.5, color),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return box


def _build_risks(data: dict, styles: dict, font: str):
    story = [Paragraph("四、风险条款列表", styles["h1"])]
    views = _risk_record_views(data["records"])
    total = data["total_risks"]

    # 报告快照计数与当前批次可见明细不一致时，如实说明而不是让读者以为报告前后矛盾。
    # 两种数都来自后端真实数据：计数取审核报告快照（与页面同源），明细取最新一批 audit_records。
    if total != len(views):
        story.append(Paragraph(
            f"说明：审核报告记录的本次风险项为 {total} 项，当前可读取的风险明细为 {len(views)} 项"
            f"（两者分别取自审核报告快照与最新一批审核记录）。",
            styles["note"]))
        story.append(Spacer(1, 4))

    if not views:
        story.append(Paragraph("本次审核未检出风险项。", styles["empty"]))
        return story

    if not data["report"].get("result_valid", True):
        story.append(Paragraph(
            "注意：该合同的当前审核结果已被驳回，以下内容为已失效的历史审核结果。", styles["note"]))
        story.append(Spacer(1, 4))

    current_level = None
    for v in views:
        if v["level"] != current_level:
            current_level = v["level"]
            story.append(Paragraph(
                f"{_esc(risk_level_label(current_level))}（{len([x for x in views if x['level'] == current_level])} 项）",
                styles["h2"]))
        story.append(KeepTogether([_build_risk_card(v, styles, font), Spacer(1, 8)]))
    return story


def _build_completeness(data: dict, styles: dict, font: str):
    story = [Paragraph("五、条款完整性检查", styles["h1"])]
    if not data["comparison"]:
        story.append(Paragraph(
            "本次审核报告中未包含条款比对结果（审核时比对未完成或该次比对未产出逐条结果）。",
            styles["empty"]))
        return story

    summary = data["comparison_summary"] or {}
    rate = summary.get("coverage_rate")
    rows = [("标准条款总数", f"{summary.get('total', 0)} 条")]
    if isinstance(rate, (int, float)):
        rows.append(("覆盖率", f"{round(rate * 100)}%"))
    rows.append(("已覆盖", f"{summary.get('covered', 0)} 条"))
    rows.append(("部分覆盖", f"{summary.get('partial', 0)} 条"))
    rows.append(("缺失", f"{summary.get('missing', 0)} 条"))
    story.append(_kv_table(rows, styles, font, col1=32 * mm))

    if data["missing_critical"]:
        story.append(Paragraph("缺失关键条款", styles["h2"]))
        story.append(Paragraph(_esc("、".join(str(c) for c in data["missing_critical"])), styles["body"]))

    clauses = data["clauses"]
    if clauses:
        missing = [c for c in clauses if c.get("status") == "missing"]
        partial = [c for c in clauses if c.get("status") == "partial"]

        story.append(Paragraph(f"缺失条款（{len(missing)} 项）", styles["h2"]))
        story.append(Paragraph(
            _esc("、".join(_text(c.get("title")) or "未命名条款" for c in missing)) if missing
            else "无。", styles["body"]))

        story.append(Paragraph(f"部分覆盖条款（{len(partial)} 项）", styles["h2"]))
        story.append(Paragraph(
            _esc("、".join(_text(c.get("title")) or "未命名条款" for c in partial)) if partial
            else "无。", styles["body"]))

        story.append(Paragraph("标准条款逐项比对", styles["h2"]))
        story.append(_clause_table(clauses, styles, font))
    return story


def _clause_table(clauses: list, styles: dict, font: str):
    head = ["状态", "标准条款", "优先级", "合同中匹配条款 / 偏离说明"]
    data = [[Paragraph(f"<b>{_esc(h)}</b>", styles["cell_head"]) for h in head]]
    for c in clauses:
        status = c.get("status")
        status_cell = f"<font color='{CMP_COLORS.get(status, '#6B7280')}'>{_esc(cmp_status_label(status))}</font>"
        detail_bits = []
        if c.get("matched_text"):
            detail_bits.append(_esc(c["matched_text"]))
        elif status == "missing":
            detail_bits.append("本合同中未匹配到对应条款。")
        for key, label in (("deviation", "偏离说明"), ("completion", "补全建议"), ("risk", "风险说明"), ("related_law", "相关法条")):
            if c.get(key):
                detail_bits.append(f"<font color='#6B7280'>{label}：</font>{_esc(c[key])}")
        data.append([
            Paragraph(status_cell, styles["cell"]),
            Paragraph(_esc(_text(c.get("title")) or "未命名条款"), styles["cell"]),
            Paragraph(_esc(priority_label(c.get("priority"))) or "—", styles["cell"]),
            Paragraph("<br/>".join(detail_bits) or "—", styles["cell"]),
        ])

    t = Table(data, colWidths=[16 * mm, 38 * mm, 18 * mm, None], repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), SOFT_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _build_cross(data: dict, styles: dict, font: str):
    """交叉风险：**仅在真实存在时**产出该章节（不存在时不生成空章节）。"""
    if not data["cross_risks"]:
        return []
    story = [Paragraph("六、关联风险", styles["h1"])]
    for c in data["cross_risks"]:
        if not isinstance(c, dict):
            continue
        bits = [f"<b>{_esc(c.get('type'))}</b>"]
        if c.get("risk"):
            bits.append(_esc(c["risk"]))
        ref = _esc(c.get("clause"))
        if c.get("depends_on"):
            ref += f" → 依赖：{_esc(c['depends_on'])}"
        if c.get("conflicts_with"):
            ref += f" → 与「{_esc(c['conflicts_with'])}」互斥"
        bits.append(f"<font color='#6B7280'>{ref}</font>")
        story.append(Paragraph("<br/>".join(bits), styles["body"]))
        story.append(Spacer(1, 4))
    return story


def build_report_pdf(data: dict) -> bytes:
    """把 `build_report_data` 的产物渲染为 PDF 字节流。**不查库、无副作用**。"""
    font = resolve_font()["regular"]
    styles = _styles(font)

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=24 * mm, bottomMargin=22 * mm,
        title=REPORT_TITLE, author="", subject="合同智能审核报告",
        creator="LLM-ContractAudit-WarnSystem",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=_make_page_decorator(font))])

    story = []
    # 报告头部：标题 + 真实可用的副标题/元信息（不出现报告编号 / 版本 / 审核人）
    story.append(Paragraph(REPORT_TITLE, styles["title"]))
    c = data["contract"]
    head_sub = " · ".join(x for x in (c["type_label"], c["file_name"]) if x)
    if head_sub:
        story.append(Paragraph(_esc(head_sub), styles["sub"]))
    meta_bits = []
    if data["report"]["audit_time"]:
        meta_bits.append(f"审核时间 {data['report']['audit_time']}")
    if c["audit_mode_label"]:
        meta_bits.append(f"审核方式 {c['audit_mode_label']}")
    if c["status_label"]:
        meta_bits.append(f"合同状态 {c['status_label']}")
    if meta_bits:
        story.append(Paragraph(_esc("　".join(meta_bits)), styles["meta"]))
    story.append(Spacer(1, 6))

    story += _build_contract_info(data, styles, font)
    story += _build_conclusion(data, styles, font)
    story += _build_risk_stats(data, styles, font)
    story += _build_risks(data, styles, font)
    story += _build_completeness(data, styles, font)
    story += _build_cross(data, styles, font)

    doc.build(story)
    return buf.getvalue()
