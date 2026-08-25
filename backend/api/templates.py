"""标准条款模板管理（用户可配置）—— 赛题要求「支持加载企业标准条款模板（用户可配置）」"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.template import Template
from api.deps import get_current_user, require_role

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    contract_type: str = Field(..., min_length=1, max_length=50)
    clauses: dict


class TemplateUpdate(BaseModel):
    name: str | None = None
    clauses: dict | None = None


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() + "Z" if ts else None


def _to_dict(t: Template) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "contract_type": t.contract_type,
        "clauses": t.clauses,
        "is_builtin": t.is_builtin,
        "version": t.version,
        "previous_version_id": t.previous_version_id,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
    }


@router.get("")
def list_templates(
    contract_type: str = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Template)
    if contract_type:
        query = query.filter(Template.contract_type == contract_type)
    items = query.order_by(Template.id.desc()).all()
    return {"code": 0, "message": "ok", "data": {"items": [_to_dict(t) for t in items], "total": len(items)}}


@router.post("", status_code=201)
def create_template(
    body: TemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "reviewer")),
):
    t = Template(name=body.name, contract_type=body.contract_type, clauses=body.clauses, is_builtin=False, version=1)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"code": 0, "message": "ok", "data": _to_dict(t)}


@router.get("/{template_id}")
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    t = db.query(Template).filter(Template.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    return {"code": 0, "message": "ok", "data": _to_dict(t)}


@router.put("/{template_id}")
def update_template(
    template_id: int,
    body: TemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "reviewer")),
):
    t = db.query(Template).filter(Template.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    if body.name is not None:
        t.name = body.name
    if body.clauses is not None:
        t.clauses = body.clauses
    t.version += 1
    db.commit()
    db.refresh(t)
    return {"code": 0, "message": "ok", "data": _to_dict(t)}


@router.delete("/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    t = db.query(Template).filter(Template.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")
    db.delete(t)
    db.commit()
    return {"code": 0, "message": "ok", "data": None}
