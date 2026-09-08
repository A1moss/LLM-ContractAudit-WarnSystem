import json
import logging
import os
import re
import time
import uuid
import mimetypes
import html

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, case
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models.contract import Contract
from models.user import User
from api.deps import get_current_user, require_role, ROLE_ADMIN
from ai.parser import detect_and_parse
from ai.classifier import classify_contract
from ai.extractor import extract_elements
from ai.auditor import run_rules, audit_with_llm
from ai.auditor.evidence_extractor import extract_evidence_detailed
from ai.auditor.evidence_adjudicator import adjudicate_risks
from ai.auditor.recommendation_engine import build_recommendations
from ai.confidence import enrich_confidences
from ai.matcher import compare_clauses
from ai.reviser import revise_clause
from ai.taxonomy import business_tag_names
from models.audit_record import AuditRecord
from services.docx_converter import docx_to_pdf
from models.audit_report import AuditReport

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["contracts"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _archive_deleted_file(stored_path: str | None):
    """软删除时把落盘文件移到 data/deleted/ 归档目录（BUG-038）。

    不删除文件（保留可恢复性），只从活跃 data/ 目录移走，消除「deleted 但文件散落」。
    文件不存在时静默跳过（历史上传失败/已清理）；归档失败只告警、不阻断删除。
    """
    if not stored_path or not os.path.isfile(stored_path):
        return
    try:
        archive_dir = os.path.join(UPLOAD_DIR, "deleted")
        os.makedirs(archive_dir, exist_ok=True)
        dest = os.path.join(archive_dir, os.path.basename(stored_path))
        # 归档目录同名冲突（uuid 碰撞极小）：追加短哈希，避免覆盖既有文件
        if os.path.exists(dest):
            dest = os.path.join(archive_dir, f"{os.path.basename(stored_path)}.{uuid.uuid4().hex[:6]}")
        os.replace(stored_path, dest)
        logger.info("软删除归档文件: %s -> %s", stored_path, dest)
    except Exception as e:
        logger.warning("软删除归档文件失败（保留原文件）: %s", e)


def _iso(ts) -> str | None:
    """把应用层写入的 naive UTC 时间序列化为带 Z 的 ISO 字符串（前端按 UTC 解析再转本地）。"""
    if ts is None:
        return None
    return ts.isoformat() + "Z"


def _business_tag_label(is_outsourcing: bool):
    """业务属性命名：服务外包合同返回业务标签名（如"服务外包"），否则 None。"""
    if not is_outsourcing:
        return None
    tags = business_tag_names()
    return "、".join(tags) if tags else "服务外包"


def _type_label(contract_type, is_outsourcing):
    """双属性组合命名：法理类型 + 业务标签。如「承揽合同（服务外包）」。"""
    base = contract_type or "未分类"
    tag = _business_tag_label(is_outsourcing)
    return f"{base}（{tag}）" if tag else base


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
    audit_mode: str = Form("precise"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".pdf", ".docx", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
        raise HTTPException(status_code=400, detail="仅支持 pdf/docx 或图片格式(jpg/png/tiff/bmp)")

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
        # 解析异常也清理已落盘文件，避免孤儿（BUG-038）
        try:
            os.remove(file_path)
        except Exception:
            pass
        raise HTTPException(status_code=422, detail="parse failed: " + str(e))

    # OCR（图片）识别失败/无文字时给出明确提示，避免静默产生空合同
    if not full_text.strip():
        if parsed.get("error"):
            raise HTTPException(status_code=422, detail=f"文本提取失败：{parsed['error']}")
        if ext.lower() in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
            raise HTTPException(status_code=422, detail="图片未识别到文字，请确认图片清晰或上传 PDF/DOCX 格式")

    # 分类与要素抽取并行（要素抽取对 contract_type 不敏感，用中性词占位，不必等分类结果）
    from concurrent.futures import ThreadPoolExecutor
    cls_result = {"contract_type": contract_type or "other", "confidence": 0.0, "is_outsourcing": False}
    elements = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        cls_fut = ex.submit(classify_contract, full_text)
        ele_fut = ex.submit(extract_elements, full_text, contract_type or "合同")
        try:
            cls_result = cls_fut.result()
        except Exception as e:
            logger.warning("合同分类失败，回退为 %s: %s", contract_type or "other", e)
        try:
            elements = ele_fut.result()
        except Exception as e:
            logger.warning("要素抽取失败，使用空要素: %s", e)

    actual_type = contract_type or cls_result.get("contract_type", "other")
    confidence = cls_result.get("confidence", 0.0)
    is_outsourcing = bool(cls_result.get("is_outsourcing", False))

    contract = Contract(
        user_id=current_user.id,
        file_name=name or file.filename,
        stored_path=file_path,
        contract_type=actual_type,
        type_confidence=confidence,
        is_outsourcing=is_outsourcing,
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
            "is_outsourcing": c.is_outsourcing,
            "business_tag": _business_tag_label(c.is_outsourcing),
            "type_label": _type_label(c.contract_type, c.is_outsourcing),
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
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    # 管理员可删除任何合同；上传者只能删除自己的合同；审核人/验收人无权删除
    if current_user.role != ROLE_ADMIN and c.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权删除该合同")
    c.status = "deleted"
    # 软删除时同步归档文件，不再留「deleted 但文件散落」（BUG-038）
    _archive_deleted_file(c.stored_path)
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


_COMPARE_SECTION_MARKER = 'id="clause-comparison"'


def _build_risk_rows(all_risks: list[dict]) -> str:
    """生成风险表 HTML（所有动态字段 html.escape，防存储型 XSS，BUG-045）。"""
    return "".join(
        f"<tr><td>{html.escape(r.get('risk_type', ''))}</td><td>{html.escape(r.get('level', ''))}</td>"
        f"<td>{html.escape(r.get('reason', '') or '')[:80]}</td><td>{html.escape(r.get('suggestion', '') or '')[:80]}</td></tr>"
        for r in all_risks
    )


def _build_compare_rows(clauses: list[dict]) -> str:
    """生成条款比对表 HTML（所有动态字段 html.escape，BUG-045）。"""
    status_cn = {"covered": "已覆盖", "partial": "部分偏离", "missing": "缺失"}
    return "".join(
        f"<tr><td>{html.escape(cl.get('title', '') or '')}</td><td>{html.escape(status_cn.get(cl.get('status', ''), ''))}</td>"
        f"<td>{html.escape(cl.get('deviation', '') or '')}</td><td>{html.escape(cl.get('completion', '') or '')}</td></tr>"
        for cl in clauses
    )


def _wrap_compare_section(section: str) -> str:
    return f'<div {_COMPARE_SECTION_MARKER}>{section}</div>'


def _replace_compare_section(report_html: str, new_section: str) -> str:
    """幂等替换条款比对片段（去掉旧片段再插入新片段，避免重复调用无限增长，BUG-045）。"""
    pattern = re.compile(rf'<div {_COMPARE_SECTION_MARKER}>.*?</div>', re.DOTALL)
    base = pattern.sub('', report_html or '')
    return base + _wrap_compare_section(new_section)


def _mark_audit_records(db, contract_id: int, from_status: str, to_status: str):
    """批量更新某合同审核记录的 result_status（BUG-028，业务有效性状态转换）。

    只按 from_status 精确转移，保证「曾被驳回(rejected)」等历史事实不被后续
    重新审核错误改写。
    """
    db.query(AuditRecord).filter(
        AuditRecord.contract_id == contract_id,
        AuditRecord.result_status == from_status,
    ).update({"result_status": to_status}, synchronize_session=False)


def _recover_contract_status(contract_id: int):
    """主审核流程失败后，用独立会话 best-effort 恢复合同状态为 parsed（BUG-010）。

    - 独立 SessionLocal（独立事务，不受主 session 失败状态影响）；
    - database is locked 退避重试 3 次（配合 busy_timeout=30s，恢复窗口充足）；
    - 恢复自身异常不外泄、不覆盖原始异常；全部重试仍失败时显式 error 日志，
      合同停在 auditing 由 BUG-011 重启复位兜底——绝不静默吞错。
    """
    for attempt in range(3):
        db = SessionLocal()
        try:
            c = db.query(Contract).filter(Contract.id == contract_id).first()
            if c and c.status == "auditing":
                c.status = "parsed"
                db.commit()
            return
        except OperationalError as e:
            db.rollback()
            if "locked" in str(e).lower() and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            logger.error("合同状态恢复失败（database error）: contract_id=%s, %s", contract_id, e)
            return
        except Exception as e:
            db.rollback()
            logger.error("合同状态恢复异常: contract_id=%s, %s", contract_id, e)
            return
        finally:
            db.close()


def _run_audit(contract_id: int):
    """后台执行完整审核流水线（独立 DB session，供 BackgroundTasks 调用）。"""
    db = SessionLocal()
    try:
        c = db.query(Contract).filter(Contract.id == contract_id).first()
        if not c or not c.parsed_text:
            return

        audit_batch = str(uuid.uuid4())
        full_text = c.parsed_text

        # 1. Rule engine (fast 基线，始终先跑)
        rule_results = run_rules(full_text)

        # 2. 证据抽取 + 确定性裁决（precise 主口径，v6.4 架构）
        if c.audit_mode == "precise":
            try:
                res = extract_evidence_detailed(full_text)
                evidence = res["evidence"]
                status = res["status"]
                if status == "failed":
                    # 全部块抽取失败：显式降级为规则引擎粗筛，绝不伪装成 0 风险（BUG-003）
                    logger.error(
                        "证据抽取完全失败（%d/%d 块），降级为规则引擎粗筛: contract_id=%s",
                        res["failed_chunks"], res["total_chunks"], contract_id,
                    )
                    all_risks = list(rule_results)
                else:
                    if status == "partial":
                        # 部分块失败：保留成功块证据继续裁决，但显式记录（不静默、不丢弃成功结果，BUG-008）
                        logger.warning(
                            "证据抽取部分失败（%d/%d 块），保留成功块证据继续裁决: contract_id=%s",
                            res["failed_chunks"], res["total_chunks"], contract_id,
                        )
                    adjudicated = adjudicate_risks(evidence)
                    # v6.5 建议层：只消费裁决结果，不反向影响 R01-R12 判定。
                    # 裁决为空 = 合同干净（12 类风险均不成立），如实报 0 风险，绝不回退规则引擎误报。
                    all_risks = build_recommendations(adjudicated, evidence)
            except Exception as e:
                logger.error("证据裁决异常，退回规则引擎: %s", e)
                all_risks = list(rule_results)
        else:
            all_risks = list(rule_results)

        # 条款比对：先于 DB 写事务执行（LLM ~30s，避免在写事务内长时间持有 SQLite 写锁，BUG-009）。
        # 失败不阻断审核（风险审核结果已入库），报告会标注"待重试"。
        compare_result = None
        try:
            compare_result = compare_clauses(full_text, c.contract_type or "买卖合同", c.is_outsourcing or False)
            logger.info("条款比对完成: %s", compare_result.get("summary") if compare_result else None)
        except Exception as e:
            logger.warning("条款比对失败，报告将标注待重试: %s", e)

        # 重新审核：旧「有效」记录置 superseded（曾被驳回 rejected / 已被替代 superseded 保持不变），
        # 不删物理记录，保留审计轨迹（BUG-028）；下方插入的新记录为 valid。
        _mark_audit_records(db, contract_id, "valid", "superseded")

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
                evidence=_build_evidence(r, None),
                recommendation={
                    "risk_description": r.get("risk_description"),
                    "example": r.get("example"),
                    "legal_basis": r.get("legal_basis"),
                    "grounding": r.get("grounding"),
                } if r.get("risk_description") or r.get("example") else None,
                feedback_status="pending",
            )
            db.add(record)
            records.append(record)

        db.commit()
        for record in records:
            db.refresh(record)

        # Calculate report stats
        high = sum(1 for r in all_risks if r.get("level") == "high")
        mid = sum(1 for r in all_risks if r.get("level") == "medium")
        low = sum(1 for r in all_risks if r.get("level") == "low")
        risk_score = min(100, high * 30 + mid * 15 + low * 5)

        # Generate report HTML（风险表 + 条款比对章节）
        risk_rows = _build_risk_rows(all_risks)

        compare_section = ""
        if compare_result and compare_result.get("clauses"):
            s = compare_result.get("summary") or {}
            cov_rate = s.get("coverage_rate") or 0
            miss_cnt = s.get("missing") or 0
            compare_rows = _build_compare_rows(compare_result["clauses"])
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
            f"{_wrap_compare_section(compare_section)}"
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
        # 原始异常先记录，绝不丢失；恢复异常由 _recover_contract_status 独立处理，不覆盖（BUG-010）
        logger.exception("后台审核失败（原始异常）: %s", e)
        db.rollback()  # 释放主 session 可能持有的写锁，避免阻塞 recovery 的独立 session
        _recover_contract_status(contract_id)
    finally:
        db.close()


@router.post("/{contract_id}/audit")
def trigger_audit(
    contract_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Contract).filter(Contract.id == contract_id, Contract.user_id == current_user.id).first()
    if not c:
        raise HTTPException(status_code=404, detail="contract not found")
    if not c.parsed_text:
        raise HTTPException(status_code=400, detail="contract has no parsed text, upload first")

    # 异步审核：立即返回，后台执行完整流水线
    c.status = "auditing"
    db.commit()

    background_tasks.add_task(_run_audit, contract_id)
    return {
        "code": 0,
        "message": "审核已提交，后台处理中",
        "data": {"contract_id": contract_id, "status": "auditing"},
    }


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
        # 驳回：当前有效审核记录置 rejected（保留历史，不删物理记录，BUG-028）
        _mark_audit_records(db, contract_id, "valid", "rejected")
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

    # 检索相关法条作为修订依据（懒加载 RAG，避免启动时拖入 chromadb/torch）
    rag_context = None
    try:
        from ai.rag import search_knowledge
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

    has_current_result = any(r.result_status == "valid" for r in records)

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "contract_id": contract_id,
            "total": len(records),
            # 当前是否存在有效审核结果（是否有 valid 记录）——前端据此判断是否展示「已驳回」，不靠 status 推导（BUG-028）
            "has_current_result": has_current_result,
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
                    "recommendation": r.recommendation,
                    "feedback_status": r.feedback_status,
                    "result_status": r.result_status,
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
    
    result = compare_clauses(c.parsed_text, c.contract_type or "other", c.is_outsourcing or False)
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
        result = compare_clauses(c.parsed_text, c.contract_type or "买卖合同", c.is_outsourcing or False)
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
        result = compare_clauses(c.parsed_text, c.contract_type or "买卖合同", c.is_outsourcing or False)
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
        # 幂等更新报告 HTML 的条款比对片段（转义 + 替换而非追加，避免无限增长，BUG-045）
        clauses = result.get("clauses", [])
        if clauses:
            section = (
                f"<h3>条款比对</h3>"
                f"<table border='1'><tr><th>条款</th><th>状态</th><th>偏离说明</th><th>补全建议</th></tr>"
                f"{_build_compare_rows(clauses)}</table>"
            )
            report.report_html = _replace_compare_section(report.report_html, section)
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
            "contract_type": c.contract_type, "is_outsourcing": c.is_outsourcing,
            "business_tag": _business_tag_label(c.is_outsourcing), "type_label": _type_label(c.contract_type, c.is_outsourcing),
            "type_confidence": c.type_confidence, "status": c.status,
            "audit_mode": c.audit_mode, "template_version": c.template_version,
            "parsed_text": c.parsed_text, "extracted_elements": c.extracted_elements,
            "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
        },
    }
