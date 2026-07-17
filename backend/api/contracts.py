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

    cls_result = {"type": contract_type or "other", "confidence": 0.0}
    try:
        cls_result = classify_contract(full_text)
    except Exception:
        pass
    actual_type = contract_type or cls_result.get("type", "other")
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
    db.delete(c)
    db.commit()
    return {"code": 0, "message": "ok", "data": None}
