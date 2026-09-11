"""标准条款模板管理（用户可配置）—— 赛题要求「支持加载企业标准条款模板（用户可配置）」"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
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
    """把应用层写入的 naive UTC 时间序列化为带 Z 的 ISO 字符串（前端按 UTC 解析再转本地）。"""
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
    include_history: bool = Query(False, description="为 true 时返回历史版本，否则只返回每个模板的最新版本"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Template)
    if contract_type:
        query = query.filter(Template.contract_type == contract_type)
    if not include_history:
        # 被其它版本作为 previous_version_id 引用的记录是历史版本，默认不返回
        used = select(Template.previous_version_id).where(Template.previous_version_id.isnot(None))
        query = query.filter(~Template.id.in_(used))
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
    old = db.query(Template).filter(Template.id == template_id).first()
    if not old:
        raise HTTPException(status_code=404, detail="template not found")

    # 版本留档：不覆盖旧记录，而是新增一条 version+1 的记录，
    # previous_version_id 指向旧版本，便于追溯和回退（原实现只改同一行 version+1）。
    new = Template(
        name=body.name if body.name is not None else old.name,
        contract_type=old.contract_type,
        clauses=body.clauses if body.clauses is not None else old.clauses,
        is_builtin=old.is_builtin,
        version=old.version + 1,
        previous_version_id=old.id,
    )
    db.add(new)
    db.commit()
    db.refresh(new)
    return {"code": 0, "message": "ok", "data": _to_dict(new)}


@router.get("/{template_id}/history")
def template_history(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回某模板从最早到最新的完整版本链（沿 previous_version_id 向前追溯）。"""
    t = db.query(Template).filter(Template.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="template not found")

    chain = []
    seen = set()
    cur = t
    while cur and cur.id not in seen:
        seen.add(cur.id)
        chain.append(cur)
        cur = (
            db.query(Template).filter(Template.id == cur.previous_version_id).first()
            if cur.previous_version_id
            else None
        )
    chain.reverse()
    return {"code": 0, "message": "ok", "data": {"items": [_to_dict(x) for x in chain], "total": len(chain)}}


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
