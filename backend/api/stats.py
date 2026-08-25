"""首页仪表盘统计（一次性返回所有卡片/图表数据，替代前端 mock）"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.contract import Contract
from models.audit_record import AuditRecord
from models.audit_report import AuditReport
from api.deps import get_current_user

router = APIRouter(prefix="/stats", tags=["stats"])


def _utcnow() -> datetime:
    """SQLite 的 CURRENT_TIMESTAMP 是 naive UTC，这里保持一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    now = _utcnow()
    day_ago = now - timedelta(hours=24)
    month_ago = now - timedelta(days=30)

    # 今日审核：近 24h 完成的审核批次
    today_audit = (
        db.query(func.count(AuditReport.id))
        .join(Contract, AuditReport.contract_id == Contract.id)
        .filter(Contract.user_id == current_user.id, AuditReport.created_at >= day_ago)
        .scalar() or 0
    )

    # 待处理：待复核(completed) + 待验收(reviewed)
    pending = (
        db.query(func.count(Contract.id))
        .filter(Contract.user_id == current_user.id, Contract.status.in_(["completed", "reviewed"]))
        .scalar() or 0
    )

    # 本月风险：近 30 天检出的风险条数
    month_risks = (
        db.query(func.count(AuditRecord.id))
        .join(Contract, AuditRecord.contract_id == Contract.id)
        .filter(Contract.user_id == current_user.id, AuditRecord.created_at >= month_ago)
        .scalar() or 0
    )

    # 通过率 = 已验收 / 已出审核结论的合同
    audited = (
        db.query(func.count(Contract.id))
        .filter(Contract.user_id == current_user.id, Contract.status.in_(["completed", "reviewed", "approved"]))
        .scalar() or 0
    )
    approved = (
        db.query(func.count(Contract.id))
        .filter(Contract.user_id == current_user.id, Contract.status == "approved")
        .scalar() or 0
    )
    approval_rate = round(approved / audited * 100, 1) if audited else 0.0

    # 最近合同（5 份）+ 各自最高风险等级
    recent = (
        db.query(Contract)
        .filter(Contract.user_id == current_user.id)
        .order_by(Contract.created_at.desc())
        .limit(5)
        .all()
    )
    recent_ids = [c.id for c in recent]
    risk_level_map: dict = {}
    if recent_ids:
        rows = (
            db.query(
                AuditRecord.contract_id,
                func.max(case(
                    (AuditRecord.risk_level == "high", 3),
                    (AuditRecord.risk_level == "medium", 2),
                    (AuditRecord.risk_level == "low", 1),
                    else_=0,
                )),
            )
            .filter(AuditRecord.contract_id.in_(recent_ids))
            .group_by(AuditRecord.contract_id)
            .all()
        )
        level_names = {3: "high", 2: "medium", 1: "low"}
        for cid, lvl in rows:
            if lvl in level_names:
                risk_level_map[cid] = level_names[lvl]

    recent_contracts = [
        {
            "id": c.id,
            "file_name": c.file_name,
            "contract_type": c.contract_type or "未分类",
            "status": c.status,
            "risk_level": risk_level_map.get(c.id),
            "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
        }
        for c in recent
    ]

    # 近 7 天每日审核量
    week_ago = now - timedelta(days=7)
    rows = (
        db.query(AuditReport.created_at)
        .join(Contract, AuditReport.contract_id == Contract.id)
        .filter(Contract.user_id == current_user.id, AuditReport.created_at >= week_ago)
        .all()
    )
    counts: dict = {}
    for (ts,) in rows:
        if ts:
            d = ts.date()
            counts[d] = counts.get(d, 0) + 1
    last7days = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).date()
        last7days.append({"date": d.strftime("%m/%d"), "count": counts.get(d, 0)})

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "today_audit": today_audit,
            "pending": pending,
            "month_risks": month_risks,
            "approval_rate": approval_rate,
            "recent_contracts": recent_contracts,
            "last7days": last7days,
        },
    }
