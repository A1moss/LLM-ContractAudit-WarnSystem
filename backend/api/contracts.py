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
from sqlalchemy import func, case, or_, and_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models.contract import Contract
from models.user import User
from api.deps import (
    get_current_user, require_role, require_llm_key_configured, ROLE_ADMIN, ROLE_UPLOADER,
)
from ai.parser import detect_and_parse
from ai.classifier import classify_contract
from ai.extractor import extract_elements
from ai.auditor import run_rules
from ai.auditor.evidence_extractor import extract_evidence_detailed
from ai.auditor.evidence_adjudicator import adjudicate_risks
from ai.auditor.recommendation_engine import build_recommendations
from ai.confidence import enrich_confidences
from ai.matcher import compare_clauses
from ai.reviser import revise_clause, generate_clause
from ai.llm_context import submit_with_context
from ai.taxonomy import business_tag_names
from models.audit_record import AuditRecord
from models.template import Template
from services.docx_converter import docx_to_pdf
from models.audit_report import AuditReport
from models.clause_revision import ClauseRevision
from services.docx_reviser import build_revised_docx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["contracts"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _unlink_quiet(path: str | None):
    """静默删除临时文件（用于修订版 DOCX 下载后清理）。"""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


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
    """上传者只能看自己的合同；审核人/验收人/管理员可查看工作流中的全部合同；已删除合同一律不可见。"""
    if c.status == "deleted":
        return False
    if user.role in WORKFLOW_ROLES:
        return True
    return c.user_id == user.id


def _db_template_clauses(db: Session, contract_type: str | None):
    """取该合同类型最新版本的数据库模板 clauses；没有企业模板时返回 None（回退内置 JSON）。"""
    if not contract_type:
        return None
    t = (
        db.query(Template)
        .filter(Template.contract_type == contract_type)
        .order_by(Template.version.desc(), Template.id.desc())
        .first()
    )
    return t.clauses if t else None


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
    if method == "evidence":
        # precise 主链路（证据+确定性裁决）：附上命中法条与条款原文，作为可溯源证据（BUG-020，原恒为 None）
        law = r.get("related_law", "")
        clause = (r.get("clause_text") or "").strip()
        return {"method": "evidence", "law": law, "clause_text": clause[:200]} if (law or clause) else None
    return None


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s: str) -> int | None:
    """中文数字 → 整数（一~九十九），无法解析返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        parts = s.split("十")
        tens = _CN_NUM.get(parts[0], 1) if parts[0] else 1
        ones = _CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return _CN_NUM.get(s)


def _int_to_cn(n: int) -> str:
    """整数 → 中文数字（1~99），用于新增条款位置提示与编号。"""
    digits = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if n <= 0:
        return str(n)
    if n < 10:
        return digits[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + digits[n - 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        return digits[tens] + "十" + ("" if ones == 0 else digits[ones])
    return str(n)


_HEADING_RE = re.compile(r'(第\s*)?([一二三四五六七八九十百千\d]+)\s*(条|、)')


def _parse_headings(text: str) -> list[dict]:
    """从合同正文解析顶层标题（第X条 / X、），返回 [{num, cn, title}]。

    只收「一~九十九」编号的标题；标题取标题之后到下一个换行/标点为止的短句。
    """
    text = text or ""
    out = []
    seen = set()
    for m in _HEADING_RE.finditer(text):
        n = _cn_to_int(m.group(2))
        if n is None:
            continue
        seg = text[m.end():m.end() + 20]
        parts = [p for p in re.split(r'[\n　\s。；;：，,]', seg) if p.strip()]
        title = parts[0] if parts else ""
        if n in seen:
            continue
        seen.add(n)
        out.append({"num": n, "cn": _int_to_cn(n), "title": title})
    return out


def _suggest_position(parsed_text: str) -> dict | None:
    """新增缺失条款的建议插入位置（启发式，仅给建议，不替用户决定）。

    规则：优先插在「争议解决」之前（anchor = 争议解决前一条的编号）；
    否则插在「违约责任」之后；再否则追加到末尾；无标题结构返回 None（要求用户明确）。
    """
    headings = _parse_headings(parsed_text)
    if not headings:
        return None

    def find(title_kw):
        for h in headings:
            if title_kw in (h["title"] or ""):
                return h
        return None

    dispute = find("争议")
    breach = find("违约")
    if dispute:
        # 插在争议解决前一条之后
        idx = next((i for i, h in enumerate(headings) if h["num"] == dispute["num"]), -1)
        prev = headings[idx - 1] if idx > 0 else None
        if prev:
            return {"anchor": prev["cn"], "hint": f"第{prev['cn']}条（{prev['title'] or '上一款'}）之后、争议解决条款之前"}
        return {"append": True, "hint": "追加到合同末尾（争议解决之前无法定位）"}
    if breach:
        return {"anchor": breach["cn"], "hint": f"第{breach['cn']}条（违约责任）之后"}
    last = headings[-1]
    return {"anchor": last["cn"], "hint": f"第{last['cn']}条之后"}


_ITEM_RE = re.compile(r'（\s*[一二三四五六七八九十百千\d]+\s*）')


def _pick_best_hit(full_text: str, probe: str) -> int:
    """定位 probe 在 full_text 的所有命中；多命中时做通用消歧。

    规则：
    - 唯一命中 → 该位置；
    - 多命中且**恰有一个**落在「（N）子项」内 → 该（N）命中；
    - 多命中但多个/零个落在（N）子项 → 返回 -1（显式定位失败，不随便取第一个）。
    """
    matches = []
    s = 0
    while True:
        p = full_text.find(probe, s)
        if p < 0:
            break
        matches.append(p)
        s = p + 1
    if not matches:
        return -1
    if len(matches) == 1:
        return matches[0]
    in_item = [p for p in matches if _ITEM_RE.search(full_text[max(0, p - 40):p])]
    if len(in_item) == 1:
        return in_item[0]
    return -1


def _locate_clause(full_text: str, clause_text: str) -> dict | None:
    """定位条款位置，返回 {clause_no, clause_title, original_text, start, end}。

    用 clause_text 前缀在 full_text 中定位（前缀逐级缩短到 4 字，容忍 LLM 证据对
    原文的改写/重组；多命中时优先（N）子项消歧），并截取命中处的子条款作为 DOCX
    替换锚点。无法定位返回 None。
    """
    if not full_text or not clause_text:
        return None
    needle = (clause_text or "").strip()
    if not needle:
        return None
    idx = -1
    for n in (30, 20, 10, 6, 4):
        probe = needle[:n] if len(needle) >= n else needle
        if not probe:
            continue
        idx = _pick_best_hit(full_text, probe)
        if idx >= 0:
            break
    if idx < 0:
        return None
    return _locate_at(full_text, idx)


def _locate_at(full_text: str, idx: int) -> dict:
    """在已知命中下标处截取子条款窗口 + 解析条号/标题，返回
    {clause_no, clause_title, original_text, start, end}。

    纯提取重构：即 _locate_clause 旧实现中「已知 idx → 结果 dict」的部分，行为逐字节一致
    （同样的边界正则、同样的 150 字回退、同样的句末标点规则、同样的标题正则）。
    只读、无副作用，供 locate-clause 端点按条款编号锚点复用。
    """
    # 截取命中处的「子条款」作为替换锚点：边界为（N）子项/句号/分号/换行，
    # 避免整份合同落在单个段落时锚点变成整份合同
    boundary_re = re.compile(r'（\s*[一二三四五六七八九十百千\d]+\s*）|[。；;]|\n')
    positions = [m.start() for m in boundary_re.finditer(full_text)]
    start = max([p for p in positions if p <= idx], default=max(0, idx - 60))
    end_candidates = [p for p in positions if p > idx]
    end = end_candidates[0] if end_candidates else min(len(full_text), idx + 60)
    if end < len(full_text) and full_text[end] in "。；;":
        end += 1  # 含句末标点
    if end - start > 150:  # 边界过远（整份一段且无标点），回退 ±60 窗口
        start = max(0, idx - 60)
        end = min(len(full_text), idx + 60)
    original_text = full_text[start:end].strip()

    before = full_text[:idx]
    # 只匹配"第X条"（不含"款"），并捕获编号本身——从最后一个标题解析出真实条号，
    # 而非用"标题出现次数"当条号（避免目录/条款混排导致计数错位，BUG-023）。
    headings = list(re.finditer(r'第\s*([一二三四五六七八九十百千\d]+)\s*条', before))
    clause_no = None
    title = None
    if headings:
        last = headings[-1]
        clause_no = _cn_to_int(last.group(1))
        if clause_no is None:
            clause_no = len(headings)  # 编号无法解析时退回计数（罕见）
        # 提取标题：从"第X条"之后到下一个换行/全角空格/标点为止
        seg = full_text[last.end():last.end() + 30]
        parts = [p for p in re.split(r'[\n　\s。；;：，,]', seg) if p.strip()]
        title = parts[0] if parts else ''
    return {"clause_no": clause_no, "clause_title": title or None,
            "original_text": original_text, "start": start, "end": end}


@router.post("/upload")
def upload_contract(
    file: UploadFile = File(...),
    name: str = Form(None),
    contract_type: str = Form(None),
    audit_mode: str = Form("precise"),
    db: Session = Depends(get_db),
    # 上传限 uploader（admin 经 require_role 恒通过）；reviewer/approver 返回 403。
    # 上传者与审核者分离，避免"自己上传自己审"的角色混同。
    current_user: User = Depends(require_role(ROLE_UPLOADER)),
    # 上传会调用 LLM（分类/要素抽取），没有任何可用 Key 时明确提示而不是静默降级
    _llm_ready=Depends(require_llm_key_configured),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".pdf", ".docx", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
        raise HTTPException(status_code=400, detail="仅支持 pdf/docx 或图片格式(jpg/png/tiff/bmp)")

    saved_name = str(uuid.uuid4()) + ext  # 统一小写扩展名（BUG-043，原第二行重复计算且丢 lower）
    file_path = os.path.join(UPLOAD_DIR, saved_name)
    content = file.file.read()
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
        # 删除已落盘的空文件，避免 orphan（BUG-050）
        try:
            os.remove(file_path)
        except Exception:
            pass
        if parsed.get("error"):
            raise HTTPException(status_code=422, detail=f"文本提取失败：{parsed['error']}")
        if ext.lower() in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
            raise HTTPException(status_code=422, detail="图片未识别到文字，请确认图片清晰或上传 PDF/DOCX 格式")
        # PDF/DOCX 空文本：扫描版 PDF 无文字层（BUG-050）
        raise HTTPException(status_code=422, detail="未能从文件中提取到文字（可能是扫描版 PDF 无文字层），请上传含文字层的 PDF/DOCX 或清晰的图片")

    # 分类与要素抽取并行（要素抽取对 contract_type 不敏感，用中性词占位，不必等分类结果）
    from concurrent.futures import ThreadPoolExecutor
    cls_result = {"contract_type": contract_type or "other", "confidence": 0.0, "is_outsourcing": False}
    elements = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        # submit_with_context：把当前请求上下文（含用户个人 DeepSeek Key）复制进工作线程，
        # 否则线程内读不到用户 Key，LLM 会静默回退到 .env 默认 Key（个人 Key 失效）
        cls_fut = submit_with_context(ex, classify_contract, full_text)
        ele_fut = submit_with_context(ex, extract_elements, full_text, contract_type or "合同")
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
    query = db.query(Contract).filter(Contract.status != "deleted")  # 所有角色排除软删除（BUG-026）
    if current_user.role not in WORKFLOW_ROLES:
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

        # Feedback RAG（人工反馈驱动的持续优化）：默认关闭。
        # 只在证据抽取阶段注入"已由人工审核并批准"的历史经验，作为**事实核查提示**。
        # 边界：① 任何失败都降级为不使用经验，绝不导致主审核失败；
        #      ② 不进入 rule_engine / adjudicate_risks（裁决仍为纯 Python 决定）；
        #      ③ 官方评测路径（evaluate/run_evidence.py）不经过本函数、不读该开关。
        feedback_ctx = None
        learning_context = {
            "enabled": False, "collection": None, "index_version": None,
            "experience_ids": [], "applied_chunks": 0, "error": None,
            "reason": "fast_mode" if c.audit_mode != "precise" else "disabled",
        }
        if c.audit_mode == "precise":
            try:
                from services.feedback_experience import feedback_rag_enabled
                if feedback_rag_enabled():
                    from ai.rag.feedback_store import build_feedback_context_provider
                    feedback_ctx = build_feedback_context_provider(contract_type=c.contract_type)
                    learning_context = {
                        "enabled": True, "collection": "feedback_experiences",
                        "index_version": None, "experience_ids": [], "applied_chunks": 0,
                        "error": None, "reason": "enabled",
                    }
            except Exception as e:
                logger.warning("Feedback RAG 初始化失败，本次审核不使用历史经验: %s", e)
                feedback_ctx = None
                learning_context["error"] = str(e)

        # 2. 证据抽取 + 确定性裁决（precise 主口径，v6.4 架构）
        if c.audit_mode == "precise":
            try:
                res = extract_evidence_detailed(full_text, feedback_context=feedback_ctx)
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

        # Feedback RAG 使用留痕：本次实际检索命中的经验 id 与经验库版本
        # （无论启用与否都写，字段结构一致，便于事后回答"这次审核用了哪些经验、哪一版"）
        if feedback_ctx is not None:
            try:
                learning_context = feedback_ctx.audit_summary()
            except Exception as e:
                logger.warning("Feedback RAG 留痕生成失败: %s", e)
                learning_context["error"] = str(e)

        # 条款比对：先于 DB 写事务执行（LLM ~30s；若放进写事务会长时间持有 SQLite 写锁，
        # 导致并发审核 database is locked，BUG-009）。失败不阻断审核，报告标注待重试。
        compare_result = None
        try:
            compare_result = compare_clauses(
                full_text,
                c.contract_type or "买卖合同",
                c.is_outsourcing or False,
                standard_clauses=_db_template_clauses(db, c.contract_type),
            )
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
                learning_context=learning_context,
            )
            db.add(record)
            records.append(record)

        # 注意：此处不 commit。delete + insert records + report + status 合并为一个事务，
        # 在下方统一 commit；中途异常时 except 分支 rollback 会保留旧记录（BUG-010）。

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
    _llm_ready=Depends(require_llm_key_configured),
):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
        raise HTTPException(status_code=404, detail="contract not found")
    if not c.parsed_text:
        raise HTTPException(status_code=400, detail="contract has no parsed text, upload first")
    if c.status == "auditing":
        raise HTTPException(status_code=409, detail="审核进行中，请勿重复提交")

    # 原子占位（乐观锁）：并发下只有一个请求能把 status 从非 auditing 改成 auditing，防重复审核（BUG-009）
    updated = db.query(Contract).filter(
        Contract.id == contract_id,
        Contract.status != "auditing",
    ).update({"status": "auditing"}, synchronize_session=False)
    db.commit()
    if not updated:
        raise HTTPException(status_code=409, detail="审核进行中，请勿重复提交")

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
    # 职责分离：不得复核自己上传的合同（自审自过），否则审核环节形同虚设
    if c.user_id == current_user.id:
        raise HTTPException(status_code=403, detail="不能复核自己上传的合同")
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
    # 职责分离：不得验收自己上传的合同（自验自过）
    if c.user_id == current_user.id:
        raise HTTPException(status_code=403, detail="不能验收自己上传的合同")
    if c.status != "reviewed":
        raise HTTPException(status_code=400, detail=f"当前状态 {c.status} 不可验收，需先复核通过")
    c.status = "approved"
    db.commit()
    return {"code": 0, "message": "ok", "data": {"id": contract_id, "status": c.status, "msg": "验收通过"}}


class ReviseRequest(BaseModel):
    clause_text: str
    instruction: str
    history: list = []
    scope: str = "clause"     # "clause" | "overview"
    clause_key: str = ""      # 条款会话标识（str(风险记录 id) 或 "__overview__"）
    clause_no: str = ""       # 第 X 条（定位可得时）
    operation: str = "replace"   # "replace"（替换已有条款）| "add_clause"（新增缺失条款）
    position: dict | None = None   # add_clause 插入位置：{"anchor":"五","hint":"..."} 或 {"append":true}；显式 null 合法


class AddClauseSuggestionRequest(BaseModel):
    risk_type: str = "R09"     # 缺失条款风险类型（R09 不可抗力等）
    instruction: str = ""      # 用户自然语言补充（可选）


# ===========================================================================
# 只读条款定位（POST /contracts/{id}/locate-clause）
# 硬约束：不调 LLM、不写数据库、不新增/修改 ClauseRevision/AuditRecord/AuditReport。
# 复用既有 _cn_to_int / _parse_headings / _pick_best_hit / _locate_clause / _locate_at，
# 不新写第二套定位算法。
# ===========================================================================

class LocateClauseRequest(BaseModel):
    text: str = ""            # 用户在原文中选中的逐字原文，或自然语言定位描述
    clause_anchor: str = ""   # 方式一：条款编号锚点，中文或阿拉伯数字，如 "五" / "5"


# 关键词模式的停用词：这些词在合同里到处都是，不能作为定位依据
_LOCATE_STOPWORDS = {
    "合同", "条款", "约定", "部分", "内容", "中的", "关于", "以及", "进行",
    "相关", "以下", "上述", "说明", "描述", "其中", "之后", "之前", "里面",
    "该项", "这项", "一节", "一条", "一款", "请把", "帮我", "找到", "定位",
}

# 条款编号锚点用的「第X条」正则（与 _locate_at 内部的条号正则同源）
_LOCATE_CLAUSE_RE = re.compile(r'第\s*([一二三四五六七八九十百千\d]+)\s*条')

# 候选条数上限（前端下拉展示用）
_LOCATE_MAX_CANDIDATES = 8

# 条款编号锚点定位时单个条款窗口的最大长度
_LOCATE_ANCHOR_MAX_LEN = 1200

# 关键词模式：在「整段不命中的连续中文串」中回溯寻找可命中子串时的最长子串长度
_LOCATE_MAX_TOKEN_LEN = 20
# 每个连续串最多采纳几个子串（保持候选集小而精确，最终仍由用户确认）
_LOCATE_MAX_SUBTOKENS = 2


def _iter_occurrences(haystack: str, needle: str) -> list[int]:
    """返回 needle 在 haystack 中的全部出现下标（逐字符推进，允许重叠命中）。只读。"""
    out: list[int] = []
    if not needle:
        return out
    s = 0
    while True:
        p = haystack.find(needle, s)
        if p < 0:
            break
        out.append(p)
        s = p + 1
    return out


def _locate_keyword_tokens(parsed_text: str, text: str) -> list[str]:
    """把自然语言定位描述切成「确实出现在合同正文里的关键词」。

    两步（全部为确定性字符串处理，不调 LLM）：
    1. 直接切词：按非中文/字母/数字边界切出连续串，去掉停用词后保留在正文中出现的串；
    2. 回溯子串：整段连续串在正文中不出现时（自然语言描述常把结构词与实质词连写，
       例如「合同第四部分验收条款中的第六项履约验收标准」），在该串内按长度从长到短
       寻找**最长且确实出现在正文里**的子串，命中即停止（不再继续缩短，避免引入噪声）。
    返回去重保序的 token 列表；空列表表示描述与正文无任何可定位交集。
    """
    tokens: list[str] = []
    for raw in re.findall(r'[\u4e00-\u9fa5A-Za-z0-9]{2,}', text or ""):
        tk = raw.strip()
        if len(tk) < 2 or tk in _LOCATE_STOPWORDS:
            continue
        if tk in parsed_text:
            tokens.append(tk)
            continue
        max_len = min(len(tk), _LOCATE_MAX_TOKEN_LEN)
        for length in range(max_len, 1, -1):
            tier: list[str] = []
            for i in range(0, len(tk) - length + 1):
                sub = tk[i:i + length]
                if sub in _LOCATE_STOPWORDS or sub in tier:
                    continue
                if sub in parsed_text:
                    tier.append(sub)
            if tier:
                tokens.extend(tier[:_LOCATE_MAX_SUBTOKENS])
                break
    seen = set()
    out: list[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _locate_candidate(res: dict) -> dict:
    """把 _locate_at 的结果裁剪成候选条目（字段集合固定）。"""
    return {
        "start": res["start"],
        "end": res["end"],
        "original_text": res["original_text"],
        "clause_no": res["clause_no"],
        "clause_title": res["clause_title"],
    }


def _locate_candidates_at(full_text: str, idxs: list[int]) -> list[dict]:
    """把若干命中下标转成候选列表：按 start 去重、按 start 升序、最多 8 条。"""
    cands = [_locate_candidate(_locate_at(full_text, i)) for i in idxs]
    seen = set()
    out = []
    for c in sorted(cands, key=lambda x: x["start"]):
        if c["start"] in seen:
            continue
        seen.add(c["start"])
        out.append(c)
    return out[:_LOCATE_MAX_CANDIDATES]


def _locate_payload(found: bool, match_mode: str, reason: str,
                    res: dict | None = None, candidates: list | None = None) -> dict:
    """组装 locate-clause 的 data 载荷（字段集合固定，不增不减）。

    found=False 时 original_text/start/end 必须是空/None，由本函数统一保证，
    避免各分支漏置字段。
    """
    res = res or {}
    return {
        "found": bool(found),
        "clause_no": res.get("clause_no") if found else None,
        "clause_title": res.get("clause_title") if found else None,
        "original_text": (res.get("original_text") or "") if found else "",
        "start": res.get("start") if found else None,
        "end": res.get("end") if found else None,
        "match_mode": match_mode,
        "candidates": candidates or [],
        "reason": reason,
    }


@router.post("/{contract_id}/locate-clause")
def locate_contract_clause(
    contract_id: int,
    body: LocateClauseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """只读条款定位工具：把「用户选中原文 / 自然语言描述 / 条款编号」定位到合同正文片段。

    只读保证：不调 LLM（不使用 ai.llm_client / reviser / matcher / rag / auditor）、
    不写数据库（无 add/commit/update/delete）、不建立或修改任何 revision 记录
    （ClauseRevision / AuditRecord / AuditReport 一概不动），可安全重复调用。

    定位优先级：
    1. clause_anchor：按「第X条」条款编号锚点定位整条（编号可用中文或阿拉伯数字）；
    2. text 精确匹配：原文唯一命中即返回；多处命中时复用 _pick_best_hit 的（N）子项消歧；
    3. text 前缀匹配（原文被改写/重组）→ 关键词匹配（自然语言描述），全部确定性规则，无 LLM。

    语义要点：
    - data["original_text"] 才是可以作为 clause_text 提交给 POST /{id}/revise 的**逐字原文**；
      其余候选/描述只用于展示，不得直接当锚点。
    - 返回的 found=True 只表示「本次在合同正文中定位到候选」，**不等于**后端已建立 revision
      锚点——锚点仅由 revise 端点写库时建立，本端点不产生任何持久化影响。
    - found=False 时 original_text=""、start/end 为 None，candidates 给出可选项（可能为空），
      由前端让用户确认后再走 revise（后端不替用户决定）。
    """
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
        raise HTTPException(status_code=404, detail="contract not found")
    # 解析文本是定位的唯一依据（不读文件、不重新解析，避免副作用）
    parsed_text = c.parsed_text or ""
    if not parsed_text:
        raise HTTPException(status_code=400, detail="no parsed text")

    anchor = (body.clause_anchor or "").strip()
    text = (body.text or "").strip()
    if not text and not anchor:
        raise HTTPException(status_code=400, detail="请提供原文片段、定位描述或条款编号")

    def _ok(data: dict) -> dict:
        return {"code": 0, "message": "ok", "data": data}

    # ---- (0) 条款编号锚点模式 -------------------------------------------------
    if anchor:
        n = _cn_to_int(anchor)
        if n is None:
            return _ok(_locate_payload(False, "", f"条款编号「{anchor}」无法识别"))
        hit = None
        for m in _LOCATE_CLAUSE_RE.finditer(parsed_text):
            if _cn_to_int(m.group(1)) == n:
                hit = m
                break
        if hit is None:
            return _ok(_locate_payload(False, "clause_anchor", f"未在合同正文中找到第{anchor}条"))

        res = _locate_at(parsed_text, hit.start())
        # 锚点模式的窗口是「整条」：从标题起点到下一个「第X条」标题（或正文末尾），上限 1200 字
        nxt = _LOCATE_CLAUSE_RE.search(parsed_text, hit.end())
        start = hit.start()
        end = nxt.start() if nxt else len(parsed_text)
        if end - start > _LOCATE_ANCHOR_MAX_LEN:
            end = start + _LOCATE_ANCHOR_MAX_LEN
        res["start"] = start
        res["end"] = end
        res["original_text"] = parsed_text[start:end].strip()
        # 条号即锚点编号本身（_locate_at 在标题起点处只能看到上一条标题，不能用它）
        res["clause_no"] = n
        # 标题优先取 _parse_headings 的解析结果（与新增条款建议位置同一口径）
        for h in _parse_headings(parsed_text):
            if h["num"] == n and h.get("title"):
                res["clause_title"] = h["title"]
                break
        return _ok(_locate_payload(
            True, "clause_anchor", f"已按条款编号定位到第{anchor}条",
            res, [_locate_candidate(res)],
        ))

    # ---- (1)/(2) 原文片段模式 ------------------------------------------------
    idxs = _iter_occurrences(parsed_text, text)

    if len(idxs) == 1:
        # 原文唯一命中：最可靠，直接返回
        res = _locate_at(parsed_text, idxs[0])
        return _ok(_locate_payload(
            True, "exact", "原文唯一命中", res, [_locate_candidate(res)],
        ))

    if len(idxs) > 1:
        # 多处命中：复用审核/修订链路上同一个 _pick_best_hit（（N）子项消歧）
        res = _locate_clause(parsed_text, text)
        if res:
            return _ok(_locate_payload(
                True, "exact", "原文多处出现，已按（N）子项消歧定位",
                res, [_locate_candidate(res)],
            ))
        cands = _locate_candidates_at(parsed_text, idxs)
        return _ok(_locate_payload(
            False, "exact",
            f"原文出现 {len(idxs)} 处且无法唯一消歧，请从候选中选择",
            None, cands,
        ))

    # 原文里完全没有该片段：先试前缀逐级匹配（容忍 LLM 改写/重组）
    res = _locate_clause(parsed_text, text)
    if res:
        return _ok(_locate_payload(
            True, "prefix", "按前缀逐级匹配定位（原文与输入不完全一致）",
            res, [_locate_candidate(res)],
        ))

    # ---- (3) 关键词模式（自然语言描述，纯确定性规则） -------------------------
    tokens = _locate_keyword_tokens(parsed_text, text)
    if not tokens:
        return _ok(_locate_payload(
            False, "", "未在合同正文中找到与描述匹配的文本，请改用原文选中或条款编号",
            None, [],
        ))

    # 打分：优先「只出现一次」（可唯一定位）的 token，其次更长的 token，最后按字典序保证确定性
    scored = []
    for tk in tokens:
        occ = _iter_occurrences(parsed_text, tk)
        scored.append(((len(occ) != 1, -len(tk), tk), occ))
    scored.sort(key=lambda x: x[0])

    cands = _locate_candidates_at(parsed_text, [i for _, occ in scored for i in occ])
    if len(cands) == 1:
        one = cands[0]
        return _ok(_locate_payload(
            True, "keyword", "已根据描述定位到唯一候选", one, cands,
        ))
    return _ok(_locate_payload(
        False, "keyword", f"根据描述找到 {len(cands)} 处候选，请选择确认", None, cands,
    ))


@router.post("/{contract_id}/add-clause-suggestion")
def get_add_clause_suggestion(
    contract_id: int,
    body: AddClauseSuggestionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _llm_ready=Depends(require_llm_key_configured),
):
    """R09 缺失条款的新增建议：RAG 同类范本 + 法律依据 + 建议插入位置。

    仅给建议，不替用户决定位置；无标题结构时 suggested_position=None，前端要求用户明确。
    """
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
        raise HTTPException(status_code=404, detail="contract not found")

    # 风险类型 → 自然语言检索词（指令为空时用，避免拿 R09 这种代码去检索）
    _RT_QUERY = {"R09": "不可抗力条款 通知 免责", "R08": "验收标准 验收方式",
                 "R10": "竞业限制 竞业禁止", "R11": "续约条款 自动续约"}
    query = body.instruction.strip() or _RT_QUERY.get(body.risk_type, body.risk_type or "缺失条款")
    templates, laws = [], []
    try:
        from ai.rag import search_similar_templates, search_knowledge
        templates = [{"text": t.get("text", "")[:300], "type": t.get("type", ""), "score": t.get("score")}
                     for t in (search_similar_templates(query, 3) or [])]
    except Exception as e:
        logger.warning("范本检索失败: %s", e)
    try:
        from ai.rag import search_knowledge
        laws = [{"law": it.get("law", ""), "article": it.get("article", ""),
                 "title": it.get("title", ""), "content": it.get("content", "")[:200]}
                for it in (search_knowledge(query, "laws", 3) or [])]
    except Exception as e:
        logger.warning("法条检索失败: %s", e)

    headings = _parse_headings(c.parsed_text or "")
    suggested = _suggest_position(c.parsed_text or "")
    return {"code": 0, "message": "ok", "data": {
        "risk_type": body.risk_type,
        "templates": templates,
        "legal_basis": laws,
        "suggested_position": suggested,
        "headings": headings,
    }}


@router.post("/{contract_id}/revise")
def revise_contract_clause(
    contract_id: int,
    body: ReviseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _llm_ready=Depends(require_llm_key_configured),
):
    """多轮对话式改条款（Leader-Follower 多智能体，参考 RCBSF）。

    operation="add_clause" 时走「新增条款起草」路径（单次 LLM，不做 Leader-Follower）：
    不要求 clause_text（新条款无原文），用 instruction + RAG 范本/法条起草，并记录插入位置。
    """
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
        raise HTTPException(status_code=404, detail="contract not found")
    if not body.instruction.strip():
        raise HTTPException(status_code=400, detail="instruction is required")

    is_add = body.operation == "add_clause"
    if not is_add and not body.clause_text.strip():
        raise HTTPException(status_code=400, detail="clause_text is required")

    # 检索相关法条作为修订依据（懒加载 RAG，避免启动时拖入 chromadb/torch）
    rag_context = None
    try:
        from ai.rag import search_knowledge
        rag_context = search_knowledge(body.instruction, "laws", 3)
    except Exception as e:
        logger.warning("改条款法条检索失败: %s", e)

    if is_add:
        # 新增条款：RAG 同类范本（供起草参考）+ 法条（依据）
        rag_templates = None
        try:
            from ai.rag import search_similar_templates
            rag_templates = search_similar_templates(body.instruction, 3)
        except Exception as e:
            logger.warning("新增条款范本检索失败: %s", e)
        position_hint = (body.position or {}).get("hint") or None
        result = generate_clause(body.instruction, c.contract_type or "", rag_context, position_hint)
        if not result.get("error") and rag_templates:
            result["templates"] = [t.get("text", "")[:200] for t in rag_templates[:2]]
        # 统一对外字段：新增条款正文用 revised_clause 返回（与替换修订一致，前端/持久化共用）
        result["revised_clause"] = result.get("clause_text", "")
    else:
        result = revise_clause(body.clause_text, body.instruction, c.contract_type or "", body.history, rag_context)

    # 持久化修订记录（仅成功时；result 含 error 说明修订失败，不落库）
    if not result.get("error"):
        # 直接读取审核阶段已保存的「真实原文锚点」（clause_position.original_text）。
        # 不再拿 LLM evidence 去 parsed_text 里重新猜位置——原文锚点权威来源是合同解析文本。
        original_text = ""
        located = None
        # 新增条款没有「原文」可替换：不解析锚点，避免被 _final_clause_map 误当成替换而重复应用
        # （新增条款由 _final_add_clause_map 单独处理，与 scope 无关）。
        if body.scope == "clause" and not is_add:
            try:
                rid = int(body.clause_key)
            except (ValueError, TypeError):
                rid = None
            if rid:
                rec = db.query(AuditRecord).filter(AuditRecord.id == rid).first()
                if rec and rec.clause_position:
                    original_text = (rec.clause_position.get("original_text") or "").strip()
            # 兜底：条款比对等「非风险来源」没有 AuditRecord 锚点。复用审核阶段同一个
            # _locate_clause 在合同正文中定位（不新写第二套定位算法）；否则该修订会因
            # 缺锚点被 DOCX 导出丢弃（_final_clause_map 对无锚点修订直接跳过）。
            if not original_text and (c.parsed_text or "").strip() and (body.clause_text or "").strip():
                located = _locate_clause(c.parsed_text, body.clause_text)
                if located:
                    original_text = (located.get("original_text") or "").strip()
            # 多轮续接：同一会话上一轮已定位过锚点（本轮 clause_text 已是上轮修订稿、
            # 不再出现在原文中）→ 沿用该锚点，保证 DOCX 导出仍能定位并采用最新修订结果。
            if not original_text and body.clause_key:
                prev = (
                    db.query(ClauseRevision)
                    .filter(
                        ClauseRevision.contract_id == contract_id,
                        ClauseRevision.clause_key == body.clause_key,
                        ClauseRevision.original_clause_text.isnot(None),
                    )
                    .order_by(ClauseRevision.id.desc())
                    .first()
                )
                if prev:
                    original_text = (prev.original_clause_text or "").strip()
        rev = ClauseRevision(
            contract_id=contract_id,
            scope=body.scope if body.scope in ("clause", "overview") else "clause",
            operation="add_clause" if is_add else "replace",
            position=body.position if is_add else None,
            clause_key=body.clause_key or "",
            clause_no=body.clause_no or (str(located["clause_no"]) if located and located.get("clause_no") else None),
            clause_text=body.clause_text,
            original_clause_text=original_text or None,
            instruction=body.instruction,
            revised_clause=result.get("revised_clause", ""),
            explanation=result.get("explanation", ""),
            constraints=result.get("constraints", []),
            legal_basis=result.get("legal_basis", []),
            remaining_risks=result.get("remaining_risks", []),
        )
        db.add(rev)
        db.commit()
        result["revision_id"] = rev.id

    # 修订失败（LLM 超时/鉴权失败/JSON 解析失败等）：不再伪装成 HTTP 200 成功响应。
    # 判定条件与上面的「是否落库」完全一致（result 含 error 即失败），因此成功路径的行为不变。
    # 前端据 detail 弹出真实错误，避免出现「（修订完成）」这类伪成功空白消息。
    if result.get("error"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"条款修订失败：{result['error']}",
        )

    return {"code": 0, "message": "ok", "data": result}


@router.get("/{contract_id}/revisions")
def get_contract_revisions(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回该合同全部修订会话（按时间正序），供前端刷新后重建对话。"""
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
        raise HTTPException(status_code=404, detail="contract not found")
    revs = (
        db.query(ClauseRevision)
        .filter(ClauseRevision.contract_id == contract_id)
        .order_by(ClauseRevision.id.asc())
        .all()
    )
    data = [{
        "id": r.id,
        "scope": r.scope,
        "operation": getattr(r, "operation", "replace") or "replace",
        "position": getattr(r, "position", None),
        "clause_key": r.clause_key,
        "clause_no": r.clause_no,
        "clause_text": r.clause_text,
        "instruction": r.instruction,
        "revised_clause": r.revised_clause,
        "explanation": r.explanation,
        "constraints": r.constraints or [],
        "legal_basis": r.legal_basis or [],
        "remaining_risks": r.remaining_risks or [],
        # 采用态：前端据此恢复「已采用」标记（历史数据为 False，语义等同"未采用"）
        "adopted": bool(getattr(r, "adopted", False)),
        "created_at": _iso(r.created_at),
    } for r in revs]
    return {"code": 0, "message": "ok", "data": data}


class AdoptRevisionRequest(BaseModel):
    clause_key: str            # 会话标识，用于二次校验（必须与目标 revision 一致）


@router.post("/{contract_id}/revisions/{revision_id}/adopt")
def adopt_contract_revision(
    contract_id: int,
    revision_id: int,
    body: AdoptRevisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """「确认采用此版」：把用户明确确认的那一轮修订标记为采用态。

    语义（与"生成一轮修改"彻底分开）：
    - **不调用 LLM**、**不新建 ClauseRevision**、**不改动轮次**；
    - 只把目标 revision 置 ``adopted=True``，并把同一 (contract_id, clause_key)
      下其它行的 adopted 清掉（一个会话至多一个采用版本）；
    - 两者在**同一个事务**里完成，一次 commit。

    为什么需要它：``/revise`` 每成功一轮就落一行，DOCX 过去取"同锚点最后一行"，
    于是"继续调整"会静默覆盖用户已经认可的版本。有了采用态，DOCX 归并时优先取 adopted。

    已知边界（本轮接受，不处理）：采用态唯一范围是 (contract_id, clause_key)，
    而 DOCX 最终按 **anchor** 归并；若两个不同会话命中同一段原文且都 adopted，
    导出时仍会后写覆盖前写。
    """
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
        raise HTTPException(status_code=404, detail="contract not found")

    if not (body.clause_key or "").strip():
        raise HTTPException(status_code=400, detail="clause_key is required")

    # 查询同时带 contract_id：防止拿别的合同的 revision_id 来确认
    rev = (
        db.query(ClauseRevision)
        .filter(ClauseRevision.id == revision_id, ClauseRevision.contract_id == contract_id)
        .first()
    )
    if not rev:
        raise HTTPException(status_code=404, detail="revision not found in this contract")
    if (rev.clause_key or "") != body.clause_key.strip():
        raise HTTPException(status_code=400, detail="clause_key 与该修订不一致")

    # 只有"能真正写进修订版合同"的修订才允许被采用
    if (getattr(rev, "operation", "replace") or "replace") == "add_clause":
        pos = getattr(rev, "position", None) or {}
        if not (pos.get("append") or _cn_to_int(str(pos.get("anchor", "")))):
            raise HTTPException(status_code=400, detail="该新增条款尚未确认合法插入位置，不能采用")
    else:
        if not (getattr(rev, "original_clause_text", "") or "").strip():
            raise HTTPException(status_code=400, detail="该修订尚未建立可靠原文定位，不能采用")

    key = rev.clause_key or ""
    try:
        # ① 同会话旧采用版本全部取消 ② 目标行置采用 —— 同一事务，一次 commit
        superseded = (
            db.query(ClauseRevision)
            .filter(
                ClauseRevision.contract_id == contract_id,
                ClauseRevision.clause_key == key,
                ClauseRevision.id != rev.id,
                ClauseRevision.adopted.is_(True),
            )
            .all()
        )
        for old in superseded:
            old.adopted = False
        rev.adopted = True
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("采用修订失败 revision_id=%s: %s", revision_id, e)
        raise HTTPException(status_code=500, detail="确认采用失败，请重试")

    db.refresh(rev)
    return {"code": 0, "message": "ok", "data": {
        "revision_id": rev.id,
        "clause_key": key,
        "adopted": True,
        "superseded_revision_id": superseded[0].id if superseded else None,
        "superseded_count": len(superseded),
    }}


@router.get("/{contract_id}/revised-docx")
def download_revised_docx(
    contract_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成并下载修订版 DOCX（原文件名_修订版.docx）。仅 DOCX 原始合同支持。"""
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
        raise HTTPException(status_code=404, detail="contract not found")
    if not c.stored_path or not c.stored_path.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅 DOCX 原始合同支持导出修订版")
    if not os.path.isfile(c.stored_path):
        raise HTTPException(status_code=404, detail="原始合同文件不存在")

    revs = (
        db.query(ClauseRevision)
        .filter(
            ClauseRevision.contract_id == contract_id,
            or_(
                ClauseRevision.scope == "clause",
                and_(ClauseRevision.scope == "overview", ClauseRevision.operation == "add_clause"),
            ),
        )
        .order_by(ClauseRevision.id.asc())
        .all()
    )
    if not revs:
        raise HTTPException(status_code=400, detail="当前合同还没有任何条款修改，无法生成修订版")

    base = os.path.splitext(c.file_name or "合同")[0]
    out_name = f"{base}_修订版.docx"
    out_path = os.path.join(UPLOAD_DIR, f"revised_{uuid.uuid4().hex}.docx")
    try:
        applied, skipped = build_revised_docx(c.stored_path, revs, out_path)
    except ValueError as e:
        _unlink_quiet(out_path)
        logger.warning("新增条款插入失败: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _unlink_quiet(out_path)
        logger.warning("生成修订版 DOCX 失败: %s", e)
        raise HTTPException(status_code=500, detail="生成修订版 DOCX 失败，原合同文件可能已损坏")
    # _final_clause_map 对「无原文锚点」的条款修订会在返回前直接丢弃（连 skipped 都不计），
    # 因此这里按同一规则在端点侧显式统计，避免交付一份「看起来完整、实际漏改」的修订版。
    anchorless = sum(
        1 for r in revs
        if (r.scope or "clause") == "clause" and r.clause_text and r.revised_clause
        and not (r.original_clause_text or "").strip()
    )
    if anchorless or skipped:
        _unlink_quiet(out_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"有 {anchorless + skipped} 条修订无法在合同原文中定位，未写入修订版；"
                   f"请对该条款重新发起一次修改（保存时会重新定位原文锚点）后再下载",
        )
    if applied == 0:
        _unlink_quiet(out_path)
        raise HTTPException(status_code=400, detail="修订条款未能定位到原文，无法生成修订版")

    background_tasks.add_task(_unlink_quiet, out_path)
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=out_name,
    )


@router.get("/{contract_id}/audit-result")
def get_audit_result(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(current_user, c):
        raise HTTPException(status_code=404, detail="contract not found")

    # 只返回最新批次（按 created_at 取最新记录的 audit_batch），避免并发审核多批次混杂（BUG-009）
    latest = (
        db.query(AuditRecord.audit_batch)
        .filter(AuditRecord.contract_id == contract_id)
        .order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc())
        .first()
    )
    records = []
    if latest:
        records = (
            db.query(AuditRecord)
            .filter(AuditRecord.contract_id == contract_id, AuditRecord.audit_batch == latest[0])
            .order_by(
                case(
                    (AuditRecord.risk_level == "high", 3),
                    (AuditRecord.risk_level == "medium", 2),
                    (AuditRecord.risk_level == "low", 1),
                    else_=0,
                ).desc()
            )
            .all()
        )

    has_current_result = any(r.result_status == "valid" for r in records)

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "contract_id": contract_id,
            "total": len(records),
            # 当前是否存在有效审核结果（最新批次是否 valid）——前端据此判断是否展示「已驳回」等，不靠 status 推导（BUG-028）
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
    
    result = compare_clauses(
        c.parsed_text,
        c.contract_type or "other",
        c.is_outsourcing or False,
        standard_clauses=_db_template_clauses(db, c.contract_type),
    )
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
        result = compare_clauses(
            c.parsed_text,
            c.contract_type or "买卖合同",
            c.is_outsourcing or False,
            standard_clauses=_db_template_clauses(db, c.contract_type),
        )
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
        result = compare_clauses(
            c.parsed_text,
            c.contract_type or "买卖合同",
            c.is_outsourcing or False,
            standard_clauses=_db_template_clauses(db, c.contract_type),
        )
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
