from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.audit_record import AuditRecord
from models.feedback_log import FeedbackLog
from api.deps import get_current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])

ACTION_MAP = {
    "confirmed": "confirmed",
    "corrected": "corrected",
    "false_positive": "disputed",
    "supplemented": "confirmed",
}


class FeedbackCreate(BaseModel):
    record_id: int
    action_type: str = Field(pattern=r"^(confirmed|corrected|false_positive|supplemented)$")
    corrected_risk: dict | None = None
    comment: str | None = None


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


@router.post("", status_code=201)
def create_feedback(
    body: FeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = db.query(AuditRecord).filter(AuditRecord.id == body.record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="audit record not found")

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
        "data": FeedbackOut.model_validate(fb).model_dump(),
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
