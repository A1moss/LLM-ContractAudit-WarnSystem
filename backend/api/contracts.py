import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
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
from models.audit_record import AuditRecord
from models.audit_report import AuditReport

router = APIRouter(prefix="/contracts", tags=["contracts"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/upload")
async def upload_contract(
    file: UploadFile = File(...),
    name: str = Form(None),
    contract_type: str = Form(None),
    audit_mode: str = Form("fast"),
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
        contract_type=actual_type,
        type_confidence=confidence,
        parsed_text=full_text,
        extracted_elements=elements,
        status="parsed",
        audit_mode=audit_mode,
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
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
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
            "contract_type": c.contract_type,
            "type_confidence": c.type_confidence,
            "status": c.status,
            "audit_mode": c.audit_mode,
            "template_version": c.template_version,
            "parsed_text": c.parsed_text,
            "extracted_elements": c.extracted_elements,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
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

    # 1. Rule engine (always runs)
    rule_results = run_rules(full_text)

    all_risks = list(rule_results)

    # 2. LLM auditor (precise mode only)
    if c.audit_mode == "precise":
        try:
            llm_results = audit_with_llm(full_text)
            for r in llm_results:
                r["detection_method"] = "rag"
            all_risks.extend(llm_results)
        except Exception as e:
            pass  # LLM unavailable, fall back to rule-only

        # 3. Corex multi-agent review (precise mode only)
        try:
            corex_result = run_review(full_text, rule_results)
            for r in corex_result.get("risks", []):
                r["detection_method"] = "corex_review"
                r["corex_agent_log"] = corex_result.get("agent_logs")
            all_risks.extend(corex_result.get("risks", []))
        except Exception as e:
            pass

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

        if r.get("risk_type") in ("R08", "R09", "R10"):
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

    # Generate simple HTML report
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
        .order_by(AuditRecord.audit_batch.desc(), AuditRecord.risk_level.desc())
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
            "created_at": report.created_at.isoformat() if report.created_at else None,
        },
    }
