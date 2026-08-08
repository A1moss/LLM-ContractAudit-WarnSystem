import json
import logging
import os
import uuid
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
from fastapi.responses import FileResponse
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
            logger.warning("docx-to-pdf conversion failed: %s", e)

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

    try:
        # 1. Rule engine (always runs)
        rule_results = run_rules(full_text)
        all_risks = list(rule_results)

        # 2. LLM auditor (precise mode only)
        if c.audit_mode == "precise":
            # RAG-enhanced LLM audit
            try:
                rag_ctx = search_knowledge(full_text, "laws", 3)
                if not rag_ctx:
                    rag_ctx = search_knowledge(full_text, "standard_clauses", 3)
            except Exception as e:
                logger.warning("RAG search failed: %s", e)
                rag_ctx = None

            try:
                llm_results = audit_with_llm(full_text, rag_ctx if rag_ctx else None)
                for r in llm_results:
                    r["detection_method"] = "rag"
                all_risks.extend(llm_results)
            except Exception as e:
                logger.warning("LLM auditor unavailable, fall back to rule-only: %s", e)

            # 3. Corex multi-agent review (precise mode only)
            try:
                corex_result = run_review(full_text, rule_results)
                # 只保存各 Agent 的检出数量，避免完整日志与风险列表互相引用导致 JSON 序列化循环引用
                corex_agent_log = {
                    name: {"count": info.get("count", 0)}
                    for name, info in (corex_result.get("agent_logs") or {}).items()
                }
                for r in corex_result.get("risks", []):
                    r["detection_method"] = "corex_review"
                    r["corex_agent_log"] = corex_agent_log
                all_risks.extend(corex_result.get("risks", []))
            except Exception as e:
                logger.warning("Corex review unavailable, continue with rule/LLM results: %s", e)

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
    except Exception as e:
        db.rollback()
        c.status = "parsed"
        db.commit()
        logger.exception("Audit failed, contract reset to parsed: %s", e)
        raise HTTPException(status_code=500, detail=f"audit failed: {e}")
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
            "created_at": _iso(report.created_at),
        },
    }

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


@router.get("/{contract_id}/clause-comparison")
def get_clause_comparison(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取条款比对结果；无缓存时当场生成。"""
    report = db.query(AuditReport).filter(AuditReport.contract_id == contract_id).order_by(AuditReport.created_at.desc()).first()
    if report and report.missing_clauses:
        data = report.missing_clauses
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict) and data.get("clauses"):
            return {"code": 0, "message": "ok", "data": data}

    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c or not c.parsed_text:
        return {"code": 0, "message": "ok", "data": None}

    try:
        from ai.matcher import compare_clauses
        result = compare_clauses(c.parsed_text, c.contract_type or "采购合同")
        if report:
            report.missing_clauses = result
            db.commit()
        return {"code": 0, "message": "ok", "data": result}
    except Exception as e:
        logger.warning("条款比对失败: %s", e)
        return {"code": 0, "message": "ok", "data": None}


@router.post("/{contract_id}/clause-comparison")
def trigger_clause_comparison(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """独立触发条款比对：审核完成后前端单独请求，不阻塞审核流程。"""
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    if not c.parsed_text:
        raise HTTPException(status_code=400, detail="no parsed text")

    try:
        from ai.matcher import compare_clauses
        result = compare_clauses(c.parsed_text, c.contract_type or "采购合同")
    except Exception as e:
        logger.warning("条款比对失败: %s", e)
        return {"code": 0, "message": "ok", "data": None}

    # 写入报告
    report = (
        db.query(AuditReport)
        .filter(AuditReport.contract_id == contract_id)
        .order_by(AuditReport.created_at.desc())
        .first()
    )
    if report:
        report.missing_clauses = result
        # 更新报告 HTML 加入条款比对片段
        compare_rows = ""
        for cl in result.get("clauses", []):
            status_cn = {"covered": "已覆盖", "partial": "部分偏离", "missing": "缺失"}
            compare_rows += (
                f"<tr><td>{cl.get('title','')}</td><td>{status_cn.get(cl.get('status',''),'')}</td>"
                f"<td>{cl.get('deviation','') or ''}</td><td>{cl.get('completion','') or ''}</td></tr>"
            )
        if compare_rows:
            report.report_html = (report.report_html or "") + (
                f"<h3>条款比对</h3>"
                f"<table border='1'><tr><th>条款</th><th>状态</th><th>偏离说明</th><th>补全建议</th></tr>{compare_rows}</table>"
            )
        db.commit()

    return {"code": 0, "message": "ok", "data": result}


@router.get("/{contract_id}")
def get_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    return {
        "code": 0, "message": "ok",
        "data": {
            "id": c.id, "user_id": c.user_id, "file_name": c.file_name, "stored_path": c.stored_path,
            "contract_type": c.contract_type, "type_confidence": c.type_confidence, "status": c.status,
            "audit_mode": c.audit_mode, "template_version": c.template_version,
            "parsed_text": c.parsed_text, "extracted_elements": c.extracted_elements,
            "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
        },
    }
