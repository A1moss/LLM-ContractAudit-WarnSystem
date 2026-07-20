import os
import uuid
import mimetypes
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import case
from sqlalchemy.orm import Session

from database import get_db
from models.contract import Contract
from models.user import User
from api.deps import get_current_user
from ai.parser import detect_and_parse
from ai.classifier import classify_contract
from ai.extractor import extract_elements
from ai.auditor import run_rules, audit_with_llm
from ai.corex import run_review
from ai.rag import search_knowledge
from ai.reporter import generate_report, compute_heatmap
from ai.matcher import compare_clauses
from models.audit_record import AuditRecord
from services.docx_converter import docx_to_pdf
from models.audit_report import AuditReport

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["contracts"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _iso(ts) -> str | None:
    """Return UTC-aware ISO string. SQLite CURRENT_TIMESTAMP is UTC."""
    if ts is None:
        return None
    return ts.isoformat() + "Z"


@router.post("/upload")
async def upload_contract(
    file: UploadFile = File(...),
    name: str = Form(None),
    contract_type: str = Form(None),
    audit_mode: str = Form("fast"),
    our_role: str = Form("neutral"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not file.filename or not file.filename.endswith((".pdf", ".docx")):
        raise HTTPException(status_code=400, detail="only pdf/docx supported")

    ext = os.path.splitext(file.filename)[1]
    saved_name = str(uuid.uuid4()) + ext
    file_path = os.path.join(UPLOAD_DIR, saved_name)
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    full_text = ""
    try:
        parsed = detect_and_parse(file_path)
        full_text = parsed.get("full_text", "")
    except Exception as e:
        raise HTTPException(status_code=422, detail="parse failed: " + str(e))

    cls_result = {"contract_type": contract_type or "other", "confidence": 0.0}
    try:
        cls_result = classify_contract(full_text)
    except Exception:
        pass
    actual_type = contract_type or cls_result.get("contract_type", "other")
    confidence = cls_result.get("confidence", 0.0)

    elements = {}
    try:
        elements = extract_elements(full_text, actual_type)
    except Exception:
        pass

    contract = Contract(
        user_id=current_user.id,
        file_name=name or file.filename,
        stored_path=file_path,
        contract_type=actual_type,
        type_confidence=confidence,
        parsed_text=full_text,
        extracted_elements=elements,
        status="parsed",
        audit_mode=audit_mode,
        our_role=our_role,
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)

    return {"code": 0, "message": "ok", "data": {"id": contract.id}}


@router.get("")
def list_contracts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None),
    contract_type: str = Query(None),
    status_filter: str = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Contract).filter(Contract.user_id == current_user.id)
    if keyword:
        query = query.filter(Contract.file_name.contains(keyword))
    if contract_type:
        query = query.filter(Contract.contract_type == contract_type)
    if status_filter:
        query = query.filter(Contract.status == status_filter)

    total = query.count()
    items = (
        query.order_by(Contract.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    def item_dict(c):
        return {
            "id": c.id,
            "file_name": c.file_name,
            "contract_type": c.contract_type,
            "type_confidence": c.type_confidence,
            "status": c.status,
            "audit_mode": c.audit_mode,
            "our_role": c.our_role,
            "created_at": _iso(c.created_at),
            "updated_at": _iso(c.updated_at),
        }

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "items": [item_dict(c) for c in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/{contract_id}")
def get_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = (
        db.query(Contract)
        .filter(Contract.id == contract_id, Contract.user_id == current_user.id)
        .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "id": c.id,
            "user_id": c.user_id,
            "file_name": c.file_name,
            "stored_path": c.stored_path,
            "contract_type": c.contract_type,
            "type_confidence": c.type_confidence,
            "status": c.status,
            "audit_mode": c.audit_mode,
            "template_version": c.template_version,
            "parsed_text": c.parsed_text,
            "extracted_elements": c.extracted_elements,
            "created_at": _iso(c.created_at),
            "updated_at": _iso(c.updated_at),
        },
    }


@router.delete("/{contract_id}")
def delete_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = (
        db.query(Contract)
        .filter(Contract.id == contract_id, Contract.user_id == current_user.id)
        .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    c.status = "deleted"
    db.commit()
    return {"code": 0, "message": "ok", "data": None}


@router.get("/{contract_id}/file")
def get_contract_file(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Serve the original contract file. .docx files are converted to PDF on-the-fly."""
    c = (
        db.query(Contract)
        .filter(Contract.id == contract_id, Contract.user_id == current_user.id)
        .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    if not c.stored_path or not os.path.isfile(c.stored_path):
        raise HTTPException(status_code=404, detail="file not found on disk")

    file_path = c.stored_path

    # .docx -> PDF conversion (preserves original pagination & fonts)
    if (c.file_name and c.file_name.lower().endswith('.docx')) or (c.stored_path and c.stored_path.lower().endswith('.docx')):
        try:
            file_path = docx_to_pdf(file_path)
        except Exception as e:
            # Fallback: serve the original .docx if conversion fails
            print(f"WARNING: docx-to-pdf conversion failed: {e}")

    mime_type = 'application/pdf' if file_path.endswith('.pdf') else None
    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(c.file_name)

    return FileResponse(
        path=file_path,
        media_type=mime_type or "application/octet-stream",
        filename=c.file_name,
    )


@router.post("/{contract_id}/audit")
def trigger_audit(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    if not c.parsed_text:
        raise HTTPException(status_code=400, detail="contract has no parsed text, upload first")

    # Mark auditing
    c.status = "auditing"
    db.commit()

    audit_batch = str(uuid.uuid4())
    full_text = c.parsed_text
    our_role = c.our_role or "neutral"

    # 1. Rule engine (always runs)
    rule_results = run_rules(full_text)

    # Enrich rule results with risk card RAG — match each detected risk to the card library
    try:
        from ai.rag import search_risk_cards
        for r in rule_results:
            risk_code = r.get("risk_type", "")
            if risk_code:
                cards = search_risk_cards(r.get("clause_text", ""), risk_code=risk_code, top_k=1)
                if cards:
                    card = cards[0]
                    # Enrich with risk card details
                    r["card_id"] = card.get("title", "")
                    r["card_detection"] = card.get("detection", "")
                    r["card_law_basis"] = card.get("law_basis", "")
                    r["card_suggestion"] = card.get("suggestion", "")
                    # Boost reason with card context if available
                    if card.get("law_basis"):
                        r["reason"] = f"{r.get('reason', '')}（风险卡片匹配：{card.get('title', '')}）"
        logger.info("[RiskCards] Rule results enriched with risk card library")
    except Exception as e:
        logger.debug("Risk card enrichment skipped: %s", e)

    all_risks = list(rule_results)

    # 2. LLM auditor (precise mode only)
    if c.audit_mode == "precise":
        # RAG-enhanced LLM audit — use retrieve_for_audit which now includes risk_cases
        try:
            from ai.rag import retrieve_for_audit
            rag_ctx = retrieve_for_audit(full_text, top_k=5)
        except Exception as e:
            logger.warning("RAG search failed: %s", e)
            rag_ctx = None

        try:
            llm_results = audit_with_llm(full_text, rag_ctx if rag_ctx else None, our_role=our_role)
            for r in llm_results:
                r["detection_method"] = "rag"
            all_risks.extend(llm_results)
        except Exception as e:
            pass  # LLM unavailable, fall back to rule-only

        # 3. Corex multi-agent review (precise mode only)
        try:
            corex_result = run_review(full_text, rule_results, our_role=our_role)
            for r in corex_result.get("risks", []):
                r["detection_method"] = "corex_review"
                # Only keep agent summary counts, not the full logs (too large + circular)
                r["corex_agent_log"] = {
                    "total_agents": corex_result.get("total_agents"),
                    "completed_agents": corex_result.get("completed_agents"),
                    "failed_agents": corex_result.get("failed_agents"),
                    "method": corex_result.get("method"),
                }
            all_risks.extend(corex_result.get("risks", []))
        except Exception as e:
            pass

    # 4. Clause comparison via matcher (all modes)
    compare_result = None
    try:
        from ai.matcher import compare_clauses as match_clauses, detect_missing
        compare_result = match_clauses(full_text, c.contract_type or "other")
        missing_findings = detect_missing(full_text, c.contract_type or "other")
        all_risks.extend(missing_findings)
        logger.info("[Matcher] 条款比对完成: coverage_rate=%.1f%%",
                     compare_result.get("summary", {}).get("coverage_rate", 0) * 100)
    except Exception as e:
        logger.warning("Matcher comparison failed: %s", e)

    # ── 后处理：标签对齐 + 甲方视角假阳性抑制（所有来源统一过）──
    try:
        from ai.auditor.llm_auditor import _align_risk_type, _suppress_false_positive
    except ImportError:
        _align_risk_type = _suppress_false_positive = None

    if _align_risk_type and _suppress_false_positive:
        cleaned = []
        for r in all_risks:
            # Only clean LLM/Corex/rag entries — rule engine and matcher are deterministic
            if r.get("detection_method") in ("rag", "llm", "corex_review"):
                r = _suppress_false_positive(r, our_role)
                r = _align_risk_type(r)
            cleaned.append(r)
        all_risks = cleaned
        logger.info("[后处理] all_risks 标签对齐 + 假阳性抑制完成")

    # Save each risk as AuditRecord
    records = []
    missing_clauses = []
    for r in all_risks:
        record = AuditRecord(
            contract_id=contract_id,
            audit_batch=audit_batch,
            risk_type=r.get("risk_type", "R00"),
            risk_level=r.get("level", "low"),
            clause_text=r.get("clause_text", ""),
            clause_position=r.get("clause_position"),
            reason=r.get("reason"),
            suggestion=r.get("suggestion"),
            detection_method=r.get("detection_method", "rule"),
            confidence=r.get("confidence", 0.8),
            corex_agent_log=r.get("corex_agent_log"),
            feedback_status="pending",
        )
        db.add(record)
        records.append(record)

        if r.get("risk_type") in ("R08", "R09", "R10", "CLAUSE_MISSING"):
            missing_clauses.append({
                "risk_type": r["risk_type"],
                "clause": r.get("name", r["risk_type"]),
                "suggestion": r.get("suggestion"),
            })

    db.commit()
    for record in records:
        db.refresh(record)

    # Calculate report stats
    high = sum(1 for r in all_risks if r.get("level") == "high")
    mid = sum(1 for r in all_risks if r.get("level") == "medium")
    low = sum(1 for r in all_risks if r.get("level") == "low")
    risk_score = min(100, high * 30 + mid * 15 + low * 5)

    # Generate structured HTML report via ai.reporter
    try:
        from ai.reporter import generate_report as gen_report, compute_heatmap
        import json as _json

        contract_info = {
            "contract_type": c.contract_type or "other",
            "confidence": c.type_confidence or 0,
            "quality": "good" if len(full_text) > 500 else "low",
            "word_count": len(full_text),
            "elements_json": _json.dumps(c.extracted_elements or {}, ensure_ascii=False, indent=2),
            "audit_mode": c.audit_mode or "fast",
        }

        audit_records = [
            {
                "risk_id": r.get("risk_type", f"R{i:02d}"),
                "risk_name": r.get("name", r.get("risk_type", "?")),
                "risk_level": r.get("level", "medium"),
                "clause_text": r.get("clause_text", ""),
                "reason": r.get("reason", ""),
                "suggestion": r.get("suggestion", ""),
                "confidence": r.get("confidence", 0.7),
                "detection_method": r.get("detection_method", "rule"),
                "source": r.get("detection_method", "rule"),
                "low_confidence": r.get("confidence", 0.7) < 0.7,
            }
            for i, r in enumerate(all_risks)
        ]

        heatmap = compute_heatmap(audit_records)
        report_html = gen_report(
            contract_info=contract_info,
            audit_records=audit_records,
            compare_result=compare_result,
            heatmap_data=heatmap,
            rule_findings_raw=rule_results,
        )
    except Exception as e:
        logger.warning("Report generation failed, using fallback: %s", e)
        risk_rows = "".join(
            f"<tr><td>{r.get('risk_type','')}</td><td>{r.get('level','')}</td>"
            f"<td>{r.get('reason','')[:80]}</td><td>{r.get('suggestion','')[:80]}</td></tr>"
            for r in all_risks
        )
        report_html = (
            f"<html><body><h2>Audit Report</h2>"
            f"<p>Batch: {audit_batch} | Mode: {c.audit_mode} | Score: {risk_score}</p>"
            f"<table border='1'><tr><th>Type</th><th>Level</th><th>Reason</th><th>Suggestion</th></tr>{risk_rows}</table>"
            f"</body></html>"
        )

    report = AuditReport(
        contract_id=contract_id,
        audit_batch=audit_batch,
        report_html=report_html,
        risk_score=risk_score,
        high_risk_count=high,
        mid_risk_count=mid,
        low_risk_count=low,
        risk_heatmap_data={"high": high, "mid": mid, "low": low},
        missing_clauses=missing_clauses if missing_clauses else None,
    )
    db.add(report)

    c.status = "completed"
    db.commit()

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "audit_batch": audit_batch,
            "risk_score": risk_score,
            "high_risk_count": high,
            "mid_risk_count": mid,
            "low_risk_count": low,
            "total_risks": len(all_risks),
            "records": len(records),
        },
    }
@router.get("/{contract_id}/audit-result")
def get_audit_result(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")

    records = (
        db.query(AuditRecord)
        .filter(AuditRecord.contract_id == contract_id)
        .order_by(
            AuditRecord.audit_batch.desc(),
            case(
                (AuditRecord.risk_level == "high", 1),
                (AuditRecord.risk_level == "medium", 2),
                (AuditRecord.risk_level == "low", 3),
                else_=4,
            ),
        )
        .all()
    )

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "contract_id": contract_id,
            "total": len(records),
            "items": [
                {
                    "id": r.id,
                    "audit_batch": r.audit_batch,
                    "risk_type": r.risk_type,
                    "risk_level": r.risk_level,
                    "clause_text": r.clause_text,
                    "reason": r.reason,
                    "suggestion": r.suggestion,
                    "detection_method": r.detection_method,
                    "confidence": r.confidence,
                    "feedback_status": r.feedback_status,
                }
                for r in records
            ],
        },
    }


@router.get("/{contract_id}/audit-report")
def get_audit_report(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")

    report = (
        db.query(AuditReport)
        .filter(AuditReport.contract_id == contract_id)
        .order_by(AuditReport.created_at.desc())
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="no audit report found")

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "id": report.id,
            "contract_id": report.contract_id,
            "audit_batch": report.audit_batch,
            "report_html": report.report_html,
            "risk_score": report.risk_score,
            "high_risk_count": report.high_risk_count,
            "mid_risk_count": report.mid_risk_count,
            "low_risk_count": report.low_risk_count,
            "risk_heatmap_data": report.risk_heatmap_data,
            "missing_clauses": report.missing_clauses,
            "created_at": _iso(report.created_at),
        },
    }


@router.get("/{contract_id}/heatmap")
def get_heatmap_data(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return paragraph-level risk heatmap data for ECharts rendering."""
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")

    records = (
        db.query(AuditRecord)
        .filter(AuditRecord.contract_id == contract_id)
        .order_by(AuditRecord.audit_batch.desc())
        .all()
    )

    if not records:
        return {"code": 0, "message": "ok", "data": {"matrix": [], "xAxis": [], "yAxis": [], "maxDensity": 0}}

    # Build audit_records in the format compute_heatmap expects
    audit_records = []
    for i, r in enumerate(records):
        audit_records.append({
            "risk_id": r.risk_type,
            "risk_level": r.risk_level,
            "clause_index": i,  # paragraph position = record order (simplified)
    })

    from ai.reporter.heatmap_data import compute_heatmap
    heatmap = compute_heatmap(audit_records)

    return {"code": 0, "message": "ok", "data": heatmap}


@router.get("/{contract_id}/report/pdf")
def export_report_pdf(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the audit report as a print-optimized HTML page.
    The page auto-triggers the browser's print dialog for PDF export.
    """
    c = db.query(Contract).filter(
        Contract.id == contract_id, Contract.user_id == current_user.id
    ).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")

    report = (
        db.query(AuditReport)
        .filter(AuditReport.contract_id == contract_id)
        .order_by(AuditReport.created_at.desc())
        .first()
    )
    if not report or not report.report_html:
        raise HTTPException(status_code=404, detail="no audit report found")

    print_html = _wrap_printable(report.report_html, c.file_name or "contract")
    return HTMLResponse(content=print_html)


def _wrap_printable(report_html: str, contract_name: str) -> str:
    """Wrap the report HTML in a print-optimized page that auto-triggers window.print()."""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>合同审核报告 — {contract_name}</title>
<style>
  @page {{
    size: A4;
    margin: 15mm 12mm 15mm 12mm;
  }}

  @media print {{
    body {{
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
      color-adjust: exact;
    }}

    /* force page break before each h2 chapter */
    h2 {{
      break-before: page;
      page-break-before: always;
    }}
    h2:first-of-type {{
      break-before: avoid;
      page-break-before: avoid;
    }}

    /* keep tables together */
    table {{
      break-inside: avoid;
      page-break-inside: avoid;
    }}

    /* hide print button strip */
    .no-print, .print-overlay {{
      display: none !important;
    }}

    /* ensure bg colors print */
    span[style*="background"] {{
      -webkit-print-color-adjust: exact;
    }}
  }}

  .print-overlay {{
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(255,255,255,0.95);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    z-index: 9999;
    font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
    text-align: center;
  }}
  .print-overlay h3 {{
    color: #1E3A5F;
    margin-bottom: 8px;
  }}
  .print-overlay p {{
    color: #909399;
    font-size: 14px;
    margin: 4px 0;
  }}
  .print-overlay .print-btn {{
    margin-top: 20px;
    padding: 10px 32px;
    background: #1E3A5F;
    color: #fff;
    border: none;
    border-radius: 6px;
    font-size: 15px;
    cursor: pointer;
  }}
  .print-overlay .print-btn:hover {{
    background: #2a5088;
  }}
</style>
</head>
<body>

<div class="print-overlay" id="printOverlay">
  <h3>正在准备打印...</h3>
  <p>打印对话框将自动弹出</p>
  <p style="font-size:12px">请选择「另存为 PDF」作为目标打印机</p>
  <button class="print-btn no-print" onclick="window.print()">
    如未自动弹出，点击此处打印
  </button>
</div>

{report_html}

<script>
// Auto-trigger print after page loads
window.addEventListener('DOMContentLoaded', function() {{
  // Hide overlay when print dialog opens (or after a short delay)
  setTimeout(function() {{
    var overlay = document.getElementById('printOverlay');
    if (overlay) overlay.style.display = 'none';
  }}, 300);
  // Trigger print
  window.print();
}});
</script>
</body>
</html>"""


@router.post("/{contract_id}/compare")
def compare_contract_clauses(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Compare contract against standard clause templates"""
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    if not c.parsed_text:
        raise HTTPException(status_code=400, detail="contract has no parsed text")

    result = compare_clauses(c.parsed_text, c.contract_type or "other")
    return {"code": 0, "message": "ok", "data": result}
