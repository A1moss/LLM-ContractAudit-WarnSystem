"""非 DOCX（本轮：PDF）合同的**后置锚点回填**。

## 定位与边界（严格遵守）

- 只在审核**已经成功落库之后**运行，是一个**独立、幂等、非致命**的后置步骤：
  它失败绝不影响审核结果本身（审核事务已 commit）。
- **只读** `contracts.parsed_text` 与 `audit_records.clause_text`；
  **只写** `audit_records.clause_position`。
- **不触碰**任何风险判定：不改 `risk_type` / `risk_level` / `risk_score` /
  高·中·低计数 / `reason` / `suggestion` / `evidence` / `recommendation`。
- **不调** LLM / RAG / 规则引擎，**不重新审核**，**不新建或删除** AuditRecord。

## 为什么只对非 DOCX 生效

DOCX 合同的锚点链路本来就是好的（实测 4/4 可靠），而这条新链路用的是
「折叠空白 + 唯一命中」——对 DOCX 也成立，但**没有必要去改已冻结的正确行为**。
因此这里显式限定 `stored_path` 不是 `.docx` 时才回填，把风险面收敛到本轮目标。

## 幂等性

同一份 `parsed_text` + 同一条 `clause_text` 必然得到同一个锚点；
重复执行只会写入相同值。且仅当新锚点**比已有锚点更长**时才写回，
因此不会破坏任何既有可用锚点。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models.audit_record import AuditRecord
from models.contract import Contract
from services.pdf_anchor import build_anchor

logger = logging.getLogger(__name__)

__all__ = ["is_pdf_like", "backfill_pdf_anchors"]


def is_pdf_like(contract: Contract) -> bool:
    """是否为「非 DOCX」合同（本轮实际覆盖 PDF；图片在未来阶段复用同一条链路）。"""
    stored = (getattr(contract, "stored_path", "") or "").lower()
    return bool(stored) and not stored.endswith(".docx")


def backfill_pdf_anchors(db: Session, contract: Contract, audit_batch: str | None = None) -> dict:
    """为某合同最新批次的审核记录回填可靠原文锚点。

    :param audit_batch: 只处理该批次；None 表示最新批次（按 created_at/id 取最新）。
    :returns: ``{"total", "anchored", "unchanged", "unreliable"}`` 统计，供日志与测试使用。
    """
    stats = {"total": 0, "anchored": 0, "unchanged": 0, "unreliable": 0}
    parsed_text = getattr(contract, "parsed_text", "") or ""
    if not parsed_text.strip():
        return stats

    query = db.query(AuditRecord).filter(AuditRecord.contract_id == contract.id)
    if audit_batch:
        query = query.filter(AuditRecord.audit_batch == audit_batch)
    else:
        latest = (
            db.query(AuditRecord.audit_batch)
            .filter(AuditRecord.contract_id == contract.id)
            .order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc())
            .first()
        )
        if not latest:
            return stats
        query = query.filter(AuditRecord.audit_batch == latest[0])
    records = query.order_by(AuditRecord.id.asc()).all()

    for rec in records:
        stats["total"] += 1
        built = build_anchor(parsed_text, rec.clause_text or "")
        if built is None:
            stats["unreliable"] += 1
            continue
        original_text, start, end = built

        pos = rec.clause_position if isinstance(rec.clause_position, dict) else {}
        old = (pos.get("original_text") or "").strip()
        if len(original_text) <= len(old):
            # 已有锚点不差于新锚点 → 保持不动（幂等 + 不破坏既有可用锚点）
            stats["unchanged"] += 1
            continue

        new_pos = dict(pos)
        new_pos["original_text"] = original_text
        new_pos["start"] = start
        new_pos["end"] = end
        # 刻意**不**在这里推断 clause_no —— 那是 `api/contracts._locate_at` 的既有职责，
        # 服务层不去反向依赖 API 层。缺失时保持原值（前端展示为「无编号」），
        # 下次重新审核时由既有 `_locate_clause` 照常补齐。

        rec.clause_position = new_pos
        stats["anchored"] += 1

    if stats["anchored"]:
        db.commit()
    return stats
