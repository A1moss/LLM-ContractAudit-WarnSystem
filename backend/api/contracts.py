import json
import logging
import os
import re
import uuid
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from database import get_db
from models.contract import Contract
from models.user import User
from api.deps import get_current_user, require_role
from ai.parser import detect_and_parse
from ai.classifier import classify_contract
from ai.extractor import extract_elements
from ai.auditor import run_rules, audit_with_llm
from ai.confidence import enrich_confidences
from ai.corex import run_review
from ai.rag import search_knowledge
from ai.reporter import generate_report, compute_heatmap
from ai.matcher import compare_clauses
from ai.reviser import revise_clause
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


WORKFLOW_ROLES = {"reviewer", "approver", "admin"}


def _can_view_contract(user: User, c: Contract) -> bool:
    """上传者只能看自己的合同；审核人/验收人/管理员可查看工作流中的全部合同。"""
    if user.role in WORKFLOW_ROLES:
        return True
    return c.user_id == user.id


def _build_evidence(r: dict, rag_ctx: list | None) -> dict | None:
    """按检测来源构建可溯源证据链。

    - 规则引擎：附带命中的法条（related_law）
    - RAG/LLM：附带检索到的知识库法条（law/article/title/source）
    - Corex：附带多 Agent 一致性（agreement_count）
    """
    method = r.get("detection_method", "")
    if method == "rule":
        law = r.get("related_law", "")
        return {"method": "rule", "law": law} if law else None
    if method == "rag" and rag_ctx:
        refs = [
            {"law": it.get("law"), "article": it.get("article"),
             "title": it.get("title"), "source": it.get("source")}
            for it in rag_ctx[:3] if it.get("source")
        ]
        return {"method": "rag", "references": refs} if refs else None
    if method == "corex_review":
        return {"method": "corex", "agreement": r.get("agreement_count", 0)}
    return None


def _locate_clause(full_text: str, clause_text: str) -> dict | None:
    """定位条款位置，返回 {clause_no, clause_title}。

    用 clause_text 前缀在 full_text 中定位，找到该位置之前最近的"第X条"
    标题，返回第几条和该条标题（如"第五条 合同变更与解除"）。无法定位返回 None。
    """
    if not full_text or not clause_text:
        return None
    needle = (clause_text or "").strip()
    if not needle:
        return None
    idx = -1
    for n in (30, 20, 10):
        probe = needle[:n] if len(needle) >= n else needle
        idx = full_text.find(probe)
        if idx >= 0:
            break
    if idx < 0:
        return None
    before = full_text[:idx]
    headings = list(re.finditer(r'第\s*[一二三四五六七八九十百千\d]+\s*[条款]', before))
    if not headings:
        return None
    last = headings[-1]
    clause_no = len(headings)
    # 提取标题：从"第X条"之后到下一个换行/全角空格/标点为止
    seg = full_text[last.end():last.end() + 30]
    parts = [p for p in re.split(r'[\n　\s。；;：，,]', seg) if p.strip()]
    title = parts[0] if parts else ''
    return {"clause_no": clause_no, "clause_title": title or None}


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
    query = db.query(Contract)
    if current_user.role in WORKFLOW_ROLES:
        query = query.filter(Contract.status != "deleted")
    else:
        query = query.filter(Contract.user_id == current_user.id)
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

    # 一次查询汇总本页每个合同的风险数/高中低风险数，避免前端 N+1 逐条调用
    risk_summary: dict = {}
    ids = [c.id for c in items]
    if ids:
        rows = (
            db.query(
                AuditRecord.contract_id,
                func.count(AuditRecord.id),
                func.sum(case((AuditRecord.risk_level == "high", 1), else_=0)),
                func.sum(case((AuditRecord.risk_level == "medium", 1), else_=0)),
                func.sum(case((AuditRecord.risk_level == "low", 1), else_=0)),
            )
            .filter(AuditRecord.contract_id.in_(ids))
            .group_by(AuditRecord.contract_id)
            .all()
        )
        for cid, risk_cnt, high_cnt, mid_cnt, low_cnt in rows:
            risk_summary[cid] = {
                "risk_count": risk_cnt or 0,
                "high_risk_count": high_cnt or 0,
                "mid_risk_count": mid_cnt or 0,
                "low_risk_count": low_cnt or 0,
            }

    def item_dict(c):
        summary = risk_summary.get(c.id, {"risk_count": 0, "high_risk_count": 0, "mid_risk_count": 0, "low_risk_count": 0})
        return {
            "id": c.id,
            "file_name": c.file_name,
            "contract_type": c.contract_type,
            "type_confidence": c.type_confidence,
            "status": c.status,
            "audit_mode": c.audit_mode,
            "risk_count": summary["risk_count"],
            "high_risk_count": summary["high_risk_count"],
            "mid_risk_count": summary["mid_risk_count"],
            "low_risk_count": summary["low_risk_count"],
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
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
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
        rag_ctx = None  # 供证据链使用，precise 模式下会被赋值为检索到的法条

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

        # 跨来源置信度融合：规则/LLM/多Agent 独立检出同一风险时交叉验证上调
        enrich_confidences(all_risks)

        # Save each risk as AuditRecord
        records = []
        for r in all_risks:
            # 定位条款在合同中的位置（第几条 + 标题），供前端标注风险位置
            if not r.get("clause_position"):
                loc = _locate_clause(full_text, r.get("clause_text", ""))
                if loc:
                    r["clause_position"] = loc
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
                confidence=r.get("confidence", 0.5),
                corex_agent_log=r.get("corex_agent_log"),
                evidence=_build_evidence(r, rag_ctx),
                feedback_status="pending",
            )
            db.add(record)
            records.append(record)

        db.commit()
        for record in records:
            db.refresh(record)

        # 条款比对：作为审核流程的一部分同步完成，避免"审核已完成但条款比对仍空白"。
        # 失败时不阻断审核（风险审核结果已入库），报告会标注"待重试"。
        compare_result = None
        try:
            compare_result = compare_clauses(full_text, c.contract_type or "采购合同")
            logger.info("条款比对完成: %s", compare_result.get("summary") if compare_result else None)
        except Exception as e:
            logger.warning("条款比对失败，报告将标注待重试: %s", e)

        # Calculate report stats
        high = sum(1 for r in all_risks if r.get("level") == "high")
        mid = sum(1 for r in all_risks if r.get("level") == "medium")
        low = sum(1 for r in all_risks if r.get("level") == "low")
        risk_score = min(100, high * 30 + mid * 15 + low * 5)

        # Generate report HTML（风险表 + 条款比对章节）
        risk_rows = "".join(
            f"<tr><td>{r.get('risk_type','')}</td><td>{r.get('level','')}</td>"
            f"<td>{r.get('reason','')[:80]}</td><td>{r.get('suggestion','')[:80]}</td></tr>"
            for r in all_risks
        )

        compare_section = ""
        if compare_result and compare_result.get("clauses"):
            s = compare_result.get("summary") or {}
            cov_rate = s.get("coverage_rate") or 0
            miss_cnt = s.get("missing") or 0
            compare_rows = "".join(
                f"<tr><td>{cl.get('title','')}</td><td>{cl.get('status','')}</td>"
                f"<td>{cl.get('deviation','') or ''}</td><td>{cl.get('completion','') or ''}</td></tr>"
                for cl in compare_result["clauses"]
            )
            compare_section = (
                f"<h3>条款比对（覆盖率 {cov_rate:.0%}，缺失 {miss_cnt} 条）</h3>"
                f"<table border='1'><tr><th>条款</th><th>状态</th><th>偏离说明</th><th>补全建议</th></tr>{compare_rows}</table>"
            )
        else:
            compare_section = "<p>⚠️ 条款比对未完成（知识库未初始化或比对失败），可稍后重试</p>"

        report_html = (
            f"<html><body><h2>Audit Report</h2>"
            f"<p>Batch: {audit_batch} | Mode: {c.audit_mode} | Score: {risk_score}</p>"
            f"<table border='1'><tr><th>Type</th><th>Level</th><th>Reason</th><th>Suggestion</th></tr>{risk_rows}</table>"
            f"{compare_section}"
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
            missing_clauses=compare_result if compare_result else None,
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


@router.post("/{contract_id}/review")
def review_contract(
    contract_id: int,
    action: str = Query("approve", pattern="^(approve|reject)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("reviewer")),
):
    """审核人复核：approve(通过→待验收 reviewed) / reject(驳回→退回 parsed)"""
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    if c.status != "completed":
        raise HTTPException(status_code=400, detail=f"当前状态 {c.status} 不可复核，需先完成审核")
    if action == "approve":
        c.status = "reviewed"
        msg = "复核通过，待验收"
    else:
        c.status = "parsed"
        msg = "已驳回，需重新审核"
    db.commit()
    return {"code": 0, "message": "ok", "data": {"id": contract_id, "status": c.status, "msg": msg}}


@router.post("/{contract_id}/approve")
def approve_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("approver")),
):
    """验收人验收：reviewed(待验收) → approved(已验收)"""
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    if c.status != "reviewed":
        raise HTTPException(status_code=400, detail=f"当前状态 {c.status} 不可验收，需先复核通过")
    c.status = "approved"
    db.commit()
    return {"code": 0, "message": "ok", "data": {"id": contract_id, "status": c.status, "msg": "验收通过"}}


class ReviseRequest(BaseModel):
    clause_text: str
    instruction: str
    history: list = []


@router.post("/{contract_id}/revise")
def revise_contract_clause(
    contract_id: int,
    body: ReviseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """多轮对话式改条款（Leader-Follower 多智能体，参考 RCBSF）"""
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    if not body.clause_text.strip():
        raise HTTPException(status_code=400, detail="clause_text is required")
    if not body.instruction.strip():
        raise HTTPException(status_code=400, detail="instruction is required")

    # 检索相关法条作为修订依据
    rag_context = None
    try:
        rag_context = search_knowledge(body.instruction, "laws", 3)
    except Exception as e:
        logger.warning("改条款法条检索失败: %s", e)

    result = revise_clause(body.clause_text, body.instruction, c.contract_type or "", body.history, rag_context)
    return {"code": 0, "message": "ok", "data": result}
@router.get("/{contract_id}/audit-result")
def get_audit_result(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
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
                    "clause_position": r.clause_position,
                    "reason": r.reason,
                    "suggestion": r.suggestion,
                    "detection_method": r.detection_method,
                    "confidence": r.confidence,
                    "evidence": r.evidence,
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
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
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
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
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
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c) or not c.parsed_text:
        return {"code": 0, "message": "ok", "data": None}

    report = db.query(AuditReport).filter(AuditReport.contract_id == contract_id).order_by(AuditReport.created_at.desc()).first()
    if report and report.missing_clauses:
        data = report.missing_clauses
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict) and data.get("clauses"):
            return {"code": 0, "message": "ok", "data": data}

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
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
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
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
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
