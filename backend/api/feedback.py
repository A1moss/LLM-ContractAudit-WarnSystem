"""api.feedback — 人工反馈（唯一录入入口）+ 反馈池 + 学习经验管理

## 本模块的边界（Human-in-the-Loop 持续优化的入口层）

- `POST /feedback`：**唯一**录入入口（沿用既有端点，只扩充字段），普通用户即可提交。
- `GET /feedback/{contract_id}`：既有语义不变（只返回**本人**的反馈）。
- `GET /feedback/pool` / `GET /feedback/rule-pool`：反馈池视图（reviewer/approver/admin）。
- `POST /feedback/{id}/review`：审核（reviewer/admin，**不得审核自己提交的**）。
- `POST /feedback/{id}/approve`：批准进入学习（**仅 admin**，且不得是提交者/审核者）。
- `POST /feedback/{id}/revoke`：撤销经验（**仅 admin**）→ 从 Feedback RAG 精确移除。
- `GET /feedback/experiences`：学习经验库视图（只读，用于审计"这条经验为什么在库里"）。

**绝对不做**：不修改任何 R01–R13 规则、不写 `ClauseRevision`、不触发重新审核、
不把反馈直接变成规则。经验正文的构造只在 `services.feedback_experience` 里（静态模板），
本模块不含任何"经验内容生成"逻辑。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.audit_record import AuditRecord
from models.contract import Contract
from models.feedback_log import FeedbackLog
from models.feedback_experience import FeedbackExperience
from api.deps import get_current_user, require_role
from services import feedback_experience as fx

router = APIRouter(prefix="/feedback", tags=["feedback"])

WORKFLOW_ROLES = {"reviewer", "approver", "admin"}

ACTION_MAP = {
    "confirmed": "confirmed",
    "corrected": "corrected",
    "false_positive": "disputed",
    "supplemented": "confirmed",
}

TARGET_TYPES = ("risk", "clause_revision", "comparison_clause")


class FeedbackCreate(BaseModel):
    record_id: int
    action_type: str = Field(pattern=r"^(confirmed|corrected|false_positive|supplemented)$")
    corrected_risk: dict | None = None
    comment: str | None = None
    # 归因（可选；供学习经验统计与筛选）
    feedback_reason: str | None = Field(default=None, pattern=r"^(clause_exists|evidence_gap|semantic_error|contract_type_error|adjudicator_rule_suspect|other)$")
    # 预留：第一阶段只启用 risk
    target_type: str | None = Field(default=None, pattern=r"^(risk|clause_revision|comparison_clause)$")
    revision_id: int | None = None
    target_ref: dict | None = None


class FeedbackReviewIn(BaseModel):
    action: str = Field(pattern=r"^(review|reject)$")
    comment: str | None = None


class FeedbackRevokeIn(BaseModel):
    reason: str | None = None


class FeedbackOut(BaseModel):
    id: int
    record_id: int
    user_id: int
    action_type: str
    original_risk: dict | None
    corrected_risk: dict | None
    comment: str | None
    created_at: datetime | None

    model_config = {"from_attributes": True}


def _iso(ts):
    """naive UTC → 带 Z 的 ISO 字符串（与 contracts.py 的 _iso 同口径）。"""
    return (ts.isoformat() + "Z") if ts else None


def _snapshot_record(record: AuditRecord) -> dict:
    """审核当时的完整风险对象快照。

    为什么必须存快照：重新审核会把旧 `AuditRecord` 置为 `superseded`，
    以 `record_id` 单点关联会让经验失去上下文。
    """
    return {
        "risk_type": record.risk_type,
        "risk_level": record.risk_level,
        "clause_text": record.clause_text,
        "clause_position": record.clause_position,
        "reason": record.reason,
        "suggestion": record.suggestion,
        "detection_method": record.detection_method,
        "confidence": record.confidence,
        "audit_batch": record.audit_batch,
        "result_status": record.result_status,
    }


def _pool_item(fb: FeedbackLog) -> dict:
    return {
        "id": fb.id,
        "record_id": fb.record_id,
        "contract_id": fb.contract_id,
        "contract_type": fb.contract_type,
        "risk_type": fb.risk_type,
        "user_id": fb.user_id,
        "action_type": fb.action_type,
        "feedback_reason": fb.feedback_reason,
        "status": fb.status or fx.ST_PENDING,
        "target_type": fb.target_type or "risk",
        "comment": fb.comment,
        "corrected_risk": fb.corrected_risk,
        "model_result": fb.model_result,
        "model_evidence": fb.model_evidence,
        "reviewed_by": fb.reviewed_by,
        "reviewed_at": _iso(fb.reviewed_at),
        "review_comment": fb.review_comment,
        "approved_by": fb.approved_by,
        "approved_at": _iso(fb.approved_at),
        "created_at": _iso(fb.created_at),
    }


def _experience_item(exp: FeedbackExperience) -> dict:
    return {
        "id": exp.id,
        "source_feedback_id": exp.source_feedback_id,
        "kind": exp.kind,
        "status": exp.status,
        "contract_type": exp.contract_type,
        "risk_type": exp.risk_type,
        "human_label": exp.human_label,
        "feedback_reason": exp.feedback_reason,
        "query_text": exp.query_text,
        "experience_text": exp.experience_text,
        "original_text": exp.original_text,
        "model_evidence": exp.model_evidence,
        "model_result": exp.model_result,
        "human_comment": exp.human_comment,
        "approved_by": exp.approved_by,
        "approved_at": _iso(exp.approved_at),
        "created_at": _iso(exp.created_at),
        "revoked_by": exp.revoked_by,
        "revoked_at": _iso(exp.revoked_at),
        "revoke_reason": exp.revoke_reason,
        "collection_name": exp.collection_name,
        "doc_id": exp.doc_id,
        "index_version": exp.index_version,
    }


# ══════════════════════════════════════════════════════════════════════
# 录入（唯一入口）
# ══════════════════════════════════════════════════════════════════════

@router.post("", status_code=201)
def create_feedback(
    body: FeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交人工反馈（进入 `pending`，**不会**直接生效，也不会进入 Feedback RAG）。"""
    record = db.query(AuditRecord).filter(AuditRecord.id == body.record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="audit record not found")
    # 归属校验：只能标注自己有权查看的合同的记录（BUG-025，防跨用户遍历 record_id 污染他人结果）
    contract = db.query(Contract).filter(Contract.id == record.contract_id).first()
    if not contract or contract.status == "deleted":
        raise HTTPException(status_code=404, detail="contract not found")
    if current_user.role not in WORKFLOW_ROLES and contract.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权限标注该合同的记录")

    original_risk = {
        "risk_type": record.risk_type,
        "risk_level": record.risk_level,
        "clause_text": record.clause_text,
        "detection_method": record.detection_method,
    }

    fb = FeedbackLog(
        record_id=body.record_id,
        user_id=current_user.id,
        action_type=body.action_type,
        original_risk=original_risk,
        corrected_risk=body.corrected_risk,
        comment=body.comment,
        # ── 学习链路新增：归属与过滤键 + 模型侧快照（避免 superseded 后失上下文）──
        contract_id=record.contract_id,
        contract_type=contract.contract_type,
        risk_type=record.risk_type,
        feedback_reason=body.feedback_reason,
        status=fx.ST_PENDING,
        target_type=body.target_type or "risk",
        revision_id=body.revision_id,
        target_ref=body.target_ref,
        model_evidence=record.evidence,
        model_result=_snapshot_record(record),
    )
    db.add(fb)

    new_status = ACTION_MAP.get(body.action_type)
    if new_status:
        record.feedback_status = new_status

    db.commit()
    db.refresh(fb)

    return {
        "code": 0,
        "message": "ok",
        "data": {
            **FeedbackOut.model_validate(fb).model_dump(),
            "status": fb.status,
            "feedback_reason": fb.feedback_reason,
            "target_type": fb.target_type,
        },
    }


# ══════════════════════════════════════════════════════════════════════
# 只读统计 / 反馈池（字面量路由必须声明在 /{contract_id} 之前）
# ══════════════════════════════════════════════════════════════════════

@router.get("/stats/overview")
def feedback_stats(
    contract_id: int = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(FeedbackLog).filter(FeedbackLog.user_id == current_user.id)

    if contract_id:
        query = query.join(AuditRecord).filter(AuditRecord.contract_id == contract_id)

    total = query.count()

    breakdown = (
        query.with_entities(FeedbackLog.action_type, sa_func.count(FeedbackLog.id).label("cnt"))
        .group_by(FeedbackLog.action_type)
        .all()
    )

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "total": total,
            "breakdown": {row.action_type: row.cnt for row in breakdown},
        },
    }


@router.get("/pool")
def list_feedback_pool(
    status_filter: str = Query(None, alias="status"),
    contract_id: int = Query(None),
    contract_type: str = Query(None),
    risk_type: str = Query(None),
    feedback_reason: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("reviewer", "approver", "admin")),
):
    """反馈池：**全部用户**的反馈（审核/批准工作台的数据源）。

    与 `GET /feedback/{contract_id}`（只看本人）不同，这里给 reviewer/approver/admin
    提供跨用户视图——否则"质量审核"这一步在权限上根本无法进行。
    """
    q = db.query(FeedbackLog)
    if status_filter:
        q = q.filter(FeedbackLog.status == status_filter)
    if contract_id:
        q = q.filter(FeedbackLog.contract_id == contract_id)
    if contract_type:
        q = q.filter(FeedbackLog.contract_type == contract_type)
    if risk_type:
        q = q.filter(FeedbackLog.risk_type == risk_type)
    if feedback_reason:
        q = q.filter(FeedbackLog.feedback_reason == feedback_reason)

    total = q.count()
    rows = (
        q.order_by(FeedbackLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "code": 0,
        "message": "ok",
        "data": {"items": [_pool_item(r) for r in rows], "total": total, "page": page, "page_size": page_size},
    }


@router.get("/rule-pool")
def list_rule_pool(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("reviewer", "approver", "admin")),
):
    """规则反馈池：疑似 **Python 裁决规则本身**有问题的反馈。

    这类反馈**禁止自动生效**，也**禁止**批准进入学习（见 approve 的守卫）：
    必须由人工/开发单独修改规则并跑全量回归测试。
    """
    q = db.query(FeedbackLog).filter(
        FeedbackLog.feedback_reason == fx.REASON_ADJUDICATOR_RULE_SUSPECT
    )
    total = q.count()
    rows = (
        q.order_by(FeedbackLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "items": [_pool_item(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "note": "该池仅用于人工排查规则问题；不会自动修改任何规则。",
        },
    }


@router.get("/experiences")
def list_experiences(
    status_filter: str = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("reviewer", "approver", "admin")),
):
    """学习经验库视图：回答"这条经验为什么进入知识库"。"""
    q = db.query(FeedbackExperience)
    if status_filter:
        q = q.filter(FeedbackExperience.status == status_filter)
    total = q.count()
    rows = (
        q.order_by(FeedbackExperience.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "code": 0,
        "message": "ok",
        "data": {"items": [_experience_item(e) for e in rows], "total": total, "page": page, "page_size": page_size},
    }


@router.get("/{contract_id}")
def list_feedback(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    records = (
        db.query(FeedbackLog)
        .join(AuditRecord, FeedbackLog.record_id == AuditRecord.id)
        .filter(
            AuditRecord.contract_id == contract_id,
            FeedbackLog.user_id == current_user.id,
        )
        .order_by(FeedbackLog.created_at.desc())
        .all()
    )

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "items": [FeedbackOut.model_validate(r).model_dump() for r in records],
            "total": len(records),
        },
    }


# ══════════════════════════════════════════════════════════════════════
# 审核 / 批准 / 撤销
# ══════════════════════════════════════════════════════════════════════

def _get_feedback(db: Session, feedback_id: int) -> FeedbackLog:
    fb = db.query(FeedbackLog).filter(FeedbackLog.id == feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="feedback not found")
    return fb


@router.post("/{feedback_id}/review")
def review_feedback(
    feedback_id: int,
    body: FeedbackReviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("reviewer")),
):
    """质量审核：`pending → reviewed`（通过）或 `pending → rejected`（不通过）。

    职责分离：**不得审核自己提交的反馈**。
    """
    fb = _get_feedback(db, feedback_id)
    if (fb.status or fx.ST_PENDING) != fx.ST_PENDING:
        raise HTTPException(status_code=409, detail=f"反馈当前状态为 {fb.status}，只有 pending 可审核")
    if fb.user_id == current_user.id:
        raise HTTPException(status_code=403, detail="不能审核自己提交的反馈")

    fb.status = fx.ST_REVIEWED if body.action == "review" else fx.ST_REJECTED
    fb.reviewed_by = current_user.id
    fb.reviewed_at = fx.utcnow_naive()
    fb.review_comment = body.comment
    db.commit()
    db.refresh(fb)
    return {"code": 0, "message": "ok", "data": {"id": fb.id, "status": fb.status}}


@router.post("/{feedback_id}/approve")
def approve_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """批准进入学习：`reviewed → approved_for_learning → active`，并物化为经验。

    守卫（任一不满足即拒绝，绝不"自动生效"）：
    1. 必须是 `reviewed` 状态；
    2. **批准者不得是提交者**；
    3. **批准者不得是审核者**（审核人与批准人职责分离）；
    4. `adjudicator_rule_suspect` 只进规则反馈池，**禁止进入学习**；
    5. 只支持第一阶段的学习类型（risk 风险判断），`supplemented` 暂不沉淀；
    6. 合同正文若命中国**官方评测语料**，拒绝沉淀（防数据泄漏）。
    """
    fb = _get_feedback(db, feedback_id)
    if fb.user_id == current_user.id:
        raise HTTPException(status_code=403, detail="不能批准自己提交的反馈（职责分离）")
    if fb.reviewed_by is not None and fb.reviewed_by == current_user.id:
        raise HTTPException(status_code=403, detail="不能批准自己审核过的反馈（审核与批准需职责分离）")

    try:
        exp, index_result = fx.approve_and_materialize(db, fb, current_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "feedback_id": fb.id,
            "feedback_status": fb.status,
            "experience_id": exp.id,
            "experience_status": exp.status,
            "indexed": bool(index_result.get("indexed")),
            "doc_id": index_result.get("doc_id"),
            "index_version": index_result.get("index_version"),
            "index_error": index_result.get("error"),
        },
    }


@router.post("/{feedback_id}/revoke")
def revoke_feedback(
    feedback_id: int,
    body: FeedbackRevokeIn = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """撤销经验：`active → revoked`，并从 Feedback RAG **精确删除**该文档。

    撤销后：经验行保留（审计轨迹），检索永不返回它；
    对应反馈的 `status` 置 `revoked`。
    """
    fb = _get_feedback(db, feedback_id)
    exp = (
        db.query(FeedbackExperience)
        .filter(FeedbackExperience.source_feedback_id == fb.id)
        .first()
    )
    if not exp:
        raise HTTPException(status_code=404, detail="该反馈尚未生成学习经验，无需撤销")

    result = fx.revoke_experience(db, exp, current_user, (body.reason if body else None) or "")
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "feedback_id": fb.id,
            "feedback_status": fb.status,
            "experience_id": exp.id,
            "experience_status": exp.status,
            "index_removed": bool(result.get("removed")),
            "index_error": result.get("error"),
        },
    }


# ══════════════════════════════════════════════════════════════════════
# 既有撤销（本人撤回自己的反馈标注）
# ══════════════════════════════════════════════════════════════════════

@router.delete("/{feedback_id}")
def delete_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销一条反馈标注：删除反馈记录，并把对应风险的状态恢复为 pending。

    新增约束（学习闭环的必要保护）：**已批准/已生效的反馈不允许硬删除**，
    否则会在经验库里留下无法追溯的孤立经验。此类情况请走
    `POST /feedback/{id}/revoke`（管理员撤销经验）。
    """
    fb = db.query(FeedbackLog).filter(
        FeedbackLog.id == feedback_id,
        FeedbackLog.user_id == current_user.id,
    ).first()
    if not fb:
        raise HTTPException(status_code=404, detail="feedback not found")

    if (fb.status or fx.ST_PENDING) in (fx.ST_APPROVED_FOR_LEARNING, fx.ST_ACTIVE):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该反馈已被批准进入学习经验库，不能直接删除；如判断有误，请由管理员撤销经验。",
        )

    # 删除后按剩余反馈重设状态（无剩余才置 pending），避免撤销一条把其他反馈也抹掉（BUG-053）
    db.delete(fb)
    db.flush()

    record = db.query(AuditRecord).filter(AuditRecord.id == fb.record_id).first()
    if record:
        remaining = (
            db.query(FeedbackLog)
            .filter(FeedbackLog.record_id == fb.record_id)
            .order_by(FeedbackLog.created_at.desc(), FeedbackLog.id.desc())
            .first()
        )
        record.feedback_status = ACTION_MAP.get(remaining.action_type, "pending") if remaining else "pending"

    db.commit()
    return {"code": 0, "message": "ok", "data": None}
