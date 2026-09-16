"""api.overview — 「总体修改会话」（scope=overview / clause_key=__overview__）的真实能力。

产品语义（本模块是这句话的唯一后端实现）
--------------------------------------
总体会话**不是**纯聊天区，也**不是**"把整份合同重写一遍覆盖原 DOCX"。它是「修改合同」
工作台的总控入口，链路固定为：

    读取全部专项修改会话
      → 形成综合上下文
      → AI 分析整份合同的修改关系
      → 形成**结构化修改方案**（逐项：replace / add_clause）
      → 用户查看并逐项确认
      → 每一项转换成**现有安全的 clause / add_clause revision**
      → 复用现有定位 + 现有 DOCX 安全导出

三条硬约束（在本模块里是结构性保证，不是文案承诺）
--------------------------------------------------
1. **方案不等于修改**：本模块只写 ``revision_proposals``（方案表），用户确认后才写
   ``ClauseRevision``。方案表不参与、也无法参与 DOCX 导出。
2. **overview+replace 永不写 DOCX**：``download_revised_docx`` 的取数口径保持原样
   （``scope == 'clause'`` 或 ``scope == 'overview' AND operation == 'add_clause'``），
   本模块**不修改**该口径；历史上"整体讨论稿"仍然只作为讨论记录保存。
3. **不新写第二套系统**：定位复用 ``api.contracts`` 的 ``_locate_clause`` /
   ``_locate_at`` / ``_parse_headings`` / ``_suggest_position``，落库复用与
   ``POST /{id}/revise`` 完全相同的字段与锚点规则，导出复用 ``services.docx_reviser``。
"""
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.contract import Contract
from models.user import User
from models.audit_record import AuditRecord
from models.clause_revision import ClauseRevision
from models.revision_proposal import RevisionProposal
from api.deps import get_current_user, require_llm_key_configured
from api.contracts import (
    _can_view_contract, _cn_to_int, _int_to_cn, _iso, _locate_at, _locate_clause,
    _parse_headings, _suggest_position,
)
from ai.llm_client import llm_client
from ai.utils import extract_json_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["overview"])

# 总体会话的固定标识（与 models.clause_revision 的注释、前端 OVERVIEW_KEY 一致）
OVERVIEW_KEY = "__overview__"
# 条款比对来源会话的 key 前缀（前端 workspaceLogic.CMP_PREFIX 同源）
CMP_PREFIX = "__cmp__"

# 整份合同正文进入 prompt 的字符预算。超出时不静默截断，
# 而是降级为「结构化纲要 + 风险条目」并在上下文里显式标注（避免 LLM 基于被腰斩的正文乱改）。
CONTRACT_TEXT_BUDGET = 24000
# 单个专项会话结果进入 prompt 的字符上限
SESSION_RESULT_PREVIEW = 800
# 一次方案最多接受多少个修改项（防止 LLM 把整份合同拆成 200 项把用户淹没）
MAX_PROPOSAL_ITEMS = 30


# ===========================================================================
# 只读汇总：合同全部专项修改会话的当前结果
# ===========================================================================

def _text(value) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def _chain_root_anchor(revs: list) -> dict:
    """复刻 services.docx_reviser._final_clause_map 的链条口径。

    DOCX 导出对同一个 ``clause_key`` 的多轮修订做链式归并：最终写入文件的是
    **最后一条带原文锚点的修订**，而它替换的锚点文本 = 首条修订的 ``clause_text``
    （链条根）。这里返回该链条根，保证专项会话展示的"原文"与 DOCX 实际替换的
    文本完全一致；返回 ``{"text": ..., "found": bool}``。
    """
    chain_root = {}
    anchor_of = {}
    for rev in revs:
        if (getattr(rev, "scope", "clause") or "clause") != "clause":
            continue
        if not _text(rev.clause_text) or not _text(rev.revised_clause):
            continue
        root = chain_root.get(rev.clause_text, rev.clause_text)
        anchor = _text(getattr(rev, "original_clause_text", None))
        if anchor:
            anchor_of[root] = anchor
        chain_root[rev.revised_clause] = root
    if not anchor_of:
        return {"text": "", "found": False}
    # 取"最后写入"的链条（与导出端按 id 升序遍历后最终生效的那条一致）
    last_root = None
    for rev in revs:
        if (getattr(rev, "scope", "clause") or "clause") != "clause":
            continue
        if not _text(rev.clause_text) or not _text(rev.revised_clause):
            continue
        root = chain_root.get(rev.clause_text, rev.clause_text)
        if root in anchor_of:
            last_root = root
    if last_root is None:
        return {"text": "", "found": False}
    return {"text": anchor_of.get(last_root, ""), "found": True}


def _session_raw_text(revs: list) -> tuple[str, str]:
    """专项会话的"当前原文"及其来源。

    返回 ``(text, source)``，source ∈ {``anchor``: DOCX 锚点原文（可导出）,
    ``first``: 首轮 clause_text（仅展示，未建立锚点）, ``none``: 无原文（新增型）}。
    """
    chain = _chain_root_anchor(revs)
    if chain["found"] and chain["text"]:
        return chain["text"], "anchor"
    for rev in revs:
        if (getattr(rev, "operation", "replace") or "replace") == "add_clause":
            continue
        t = _text(rev.clause_text)
        if t:
            return t, "first"
    return "", "none"


def _session_anchor(revs: list) -> str:
    """会话已建立的 DOCX 原文锚点（取最后一条带锚点的修订）。无锚点返回空串。"""
    for rev in reversed(revs):
        a = _text(getattr(rev, "original_clause_text", None))
        if a:
            return a
    return ""


def _rev_exportable(rev) -> bool:
    """与 download_revised_docx 取数口径、与前端 isExportableRevision 完全一致的镜像判据。"""
    scope = (getattr(rev, "scope", "clause") or "clause")
    op = (getattr(rev, "operation", "replace") or "replace")
    if scope == "clause":
        return True
    return scope == "overview" and op == "add_clause"


def _session_export_state(revs: list) -> dict:
    """会话是否能被写进修订版 DOCX（依据后端真实判据，不做乐观假设）。

    - 替换型（scope=clause 的 replace）：必须有原文锚点，否则 ``_final_clause_map`` 会把它
      丢掉、导出端点会显式 400；
    - 新增型（operation=add_clause）：不需要原文锚点（由插入位置定位），只要位置可解析即可。
    """
    exportable = [r for r in revs if _rev_exportable(r)]
    if not exportable:
        return {"exportable": False, "applied": 0, "blocker": "记录不参与修订版合同导出"}
    blockers = []
    for r in exportable:
        op = (getattr(r, "operation", "replace") or "replace")
        if op == "add_clause":
            if not (getattr(r, "position", None) or {}):
                blockers.append(r)
            continue
        if not _text(getattr(r, "original_clause_text", None)):
            blockers.append(r)
    if blockers:
        return {
            "exportable": False,
            "applied": max(0, len(exportable) - len(blockers)),
            "blocker": f"有 {len(blockers)} 条修改尚未建立可靠原文定位",
        }
    return {"exportable": True, "applied": len(exportable), "blocker": ""}


def _classify_session(key: str, revs: list, risk_ids: set) -> str:
    """会话来源分类（与前端 classifySessionGroup 的优先级保持一致，后端可独立复核）。"""
    k = str(key or "")
    if k == OVERVIEW_KEY:
        return "overview"
    if revs and all((getattr(r, "operation", "replace") or "replace") == "add_clause" for r in revs):
        return "add"
    if k.startswith(CMP_PREFIX):
        return "cmp"
    if k in risk_ids:
        return "risk"
    return "history"


def _latest(revs: list):
    """同一会话的最后一轮修订（按 id 升序集合的末条；无则 None）。"""
    ordered = sorted([r for r in revs if r is not None], key=lambda r: r.id or 0)
    return ordered[-1] if ordered else None


def _build_sessions(db: Session, contract_id: int, revs: list) -> list[dict]:
    """把全部 ClauseRevision 按 clause_key 汇总成会话列表。

    总体会话自身（``__overview__``）的 replace 讨论稿不算"专项会话"，但挂在它下面的
    ``add_clause`` 是唯一会被写进 DOCX 的 overview 记录，因此单独列出来。
    """
    groups: dict[str, list] = {}
    order: list[str] = []
    for r in revs:
        if not r.clause_key:
            # 无会话标识的修订（历史脏数据）单独成组，不丢弃
            key = f"__orphan__{r.id}"
        else:
            key = r.clause_key
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    risk_ids = {
        str(rid)
        for (rid,) in db.query(AuditRecord.id).filter(AuditRecord.contract_id == contract_id).all()
    }
    risk_meta: dict[str, AuditRecord] = {}
    numeric = [k for k in order if k.isdigit()]
    if numeric:
        for rec in db.query(AuditRecord).filter(AuditRecord.id.in_([int(k) for k in numeric])).all():
            risk_meta[str(rec.id)] = rec

    out: list[dict] = []
    for key in order:
        group = groups[key]
        if key == OVERVIEW_KEY and not all(
            (getattr(r, "operation", "replace") or "replace") == "add_clause" for r in group
        ):
            # 总体会话自身不是"专项会话"；但挂在其下的**新增条款**是真实生效的修改
            # （overview+add_clause 是唯一会被写入 DOCX 的 overview 记录），必须列出来，
            # 否则用户在修订版合同里会看到"工作台里没有的新条款"。
            continue
        last = _latest(group)
        kind = _classify_session(key, group, risk_ids)
        raw, raw_source = _session_raw_text(group)
        anchor = _session_anchor(group)
        rec = risk_meta.get(key)
        if kind == "overview":
            title = "总体会话下的新增条款"
        elif kind == "risk" and rec is not None:
            title = f"{rec.risk_type}（{rec.risk_level}）"
        elif kind == "cmp":
            title = f"条款比对·{key[len(CMP_PREFIX):] or '未命名'}"
        elif kind == "add":
            title = "新增条款会话"
        else:
            title = "历史修改会话"
        export = _session_export_state(group)
        clause_no = None
        for r in reversed(group):
            if _text(getattr(r, "clause_no", None)):
                clause_no = _text(r.clause_no)
                break
        out.append({
            "key": key,
            "kind": kind,
            "title": title,
            "scope": (getattr(last, "scope", "clause") or "clause"),
            "operation": (getattr(last, "operation", "replace") or "replace"),
            "revision_count": len(group),
            "last_revision_id": getattr(last, "id", None),
            "created_at": _iso(getattr(group[0], "created_at", None)),
            "updated_at": _iso(getattr(last, "created_at", None)),
            "clause_no": clause_no,
            "position": getattr(last, "position", None),
            # ① 原文
            "original_text": raw,
            "original_source": raw_source,
            # ② 当前最新修改结果
            "revised_clause": _text(getattr(last, "revised_clause", None)),
            "explanation": _text(getattr(last, "explanation", None)),
            "instruction": _text(getattr(last, "instruction", None)),
            # ③ 法律依据 / ④ 剩余风险
            "legal_basis": list(getattr(last, "legal_basis", None) or []),
            "remaining_risks": list(getattr(last, "remaining_risks", None) or []),
            "constraints": list(getattr(last, "constraints", None) or []),
            # ⑤ 定位状态
            "located": bool(anchor),
            "anchor_text": anchor,
            "location_state": "located" if anchor else ("pending" if raw_source == "first" else "none"),
            "export": export,
        })
    return out


def _aggregate(db: Session, contract_id: int) -> dict:
    """构造总体会话所需的全部只读汇总数据（不含 LLM 调用）。"""
    revs = (
        db.query(ClauseRevision)
        .filter(ClauseRevision.contract_id == contract_id)
        .order_by(ClauseRevision.id.asc())
        .all()
    )
    sessions = _build_sessions(db, contract_id, revs)
    overview_revs = [r for r in revs if r.clause_key == OVERVIEW_KEY]
    overview_last = _latest(overview_revs)

    exportable = [r for r in revs if _rev_exportable(r)]
    anchorless = [
        r for r in exportable
        if r.scope == "clause" and _text(r.clause_text) and _text(r.revised_clause)
        and not _text(r.original_clause_text)
    ]
    by_kind = {"risk": 0, "cmp": 0, "add": 0, "history": 0}
    for s in sessions:
        by_kind[s["kind"]] = by_kind.get(s["kind"], 0) + 1

    return {
        "contract_id": contract_id,
        "overview": {
            "key": OVERVIEW_KEY,
            "scope": "overview",
            "revision_count": len(overview_revs),
            "last_revision_id": getattr(overview_last, "id", None),
            "last_instruction": _text(getattr(overview_last, "instruction", None)),
            "last_result": _text(getattr(overview_last, "revised_clause", None)),
            "updated_at": _iso(getattr(overview_last, "created_at", None)),
            # 明示：总体会话自身的 replace 讨论稿**永不**写入修订版 DOCX（后端口径未变）
            "writes_docx": False,
        },
        "sessions": sessions,
        "counts": {
            "sessions": len(sessions),
            "revise_sessions": len([s for s in sessions if s["operation"] != "add_clause"]),
            "by_kind": by_kind,
            "total_revisions": len(revs),
        },
        "export": {
            "exportable_count": len(exportable),
            "blocker_count": len(anchorless),
            "ready": bool(exportable) and not anchorless,
        },
    }


def _session_brief(s: dict) -> dict:
    """总体会话创建方案时的会话摘要（精简字段，进入 LLM prompt 用）。"""
    return {
        "session_key": s["key"],
        "title": s["title"],
        "kind": s["kind"],
        "operation": s["operation"],
        "rounds": s["revision_count"],
        "clause_no": s["clause_no"],
        "original_text": s["original_text"],
        "located": s["located"],
        "exportable": s["export"]["exportable"],
        "export_blocker": s["export"]["blocker"],
        "revised_clause": s["revised_clause"],
        "legal_basis": s["legal_basis"],
        "remaining_risks": s["remaining_risks"],
        "position": s["position"],
    }


# ===========================================================================
# 总体会话专用 AI：整份合同 + 各专项会话结果 → 结构化修改方案
# ===========================================================================

PROPOSAL_SYSTEM_PROMPT = """你是企业合同审核系统的「合同修改方案规划方」。

你的任务：从整份合同的角度，统筹已有的各专项修改会话结果与用户本次的整体要求，
输出一份**结构化的具体修改项清单**，供用户逐项确认后落地。

硬性要求：
1. **绝对不要输出整份合同的重写稿**。只输出"要改哪几条、改成什么"的具体修改项。
2. 每个修改项必须二选一：
   - operation="replace"：替换合同里**已经存在**的条款。必须给出 original_quote（该条款在合同正文中的**逐字原文**，
     不得改写、不得概括），以及 revised_clause（改写后的条款全文，不含条款编号）。
   - operation="add_clause"：新增合同里**不存在**的条款。original_quote 必须为空串，
     必须给出 position（见第 4 条），revised_clause 为新条款正文（不含条款编号）。
3. 如果某个修改项是在已有专项会话成果基础上继续细化，请把 target_session_key 填成该专项会话的 session_key，
   这样系统会把这次修改挂到同一个会话上（同一链条只保留最终结果，不会重复替换）。
   如果是全新目标条款，target_session_key 填空串。
4. position 只能取以下两种形式之一：
   - {"anchor": "<条款编号，如 五>", "hint": "<可读位置说明>"} 表示插在该编号条款之后；
   - {"append": true, "hint": "追加到合同末尾"}
   不要编造其它形式；不要用"第X条之前"这种无法表达的写法。
5. 不要重复罗列各专项会话已经完成的修改；只输出**尚未落地**或**需要整体协调调整**的项。
6. 法条/数字必须来自给定材料，不得编造。
7. 修改项按重要性排序，最多 %d 项。

只输出 JSON（不要任何解释文字）：
{
  "summary": "整体修改方案的思路与影响范围（中文，2-5 句）",
  "items": [
    {
      "operation": "replace 或 add_clause",
      "target_session_key": "已有专项会话 key 或空串",
      "clause_no": "该条款编号（无则空串）",
      "original_quote": "replace 才填：合同正文中的逐字原文",
      "revised_clause": "改后/新增的条款全文",
      "reason": "为什么这么改",
      "legal_basis": ["依据的法条，无则空数组"],
      "position": null
    }
  ]
}""" % MAX_PROPOSAL_ITEMS


def _headings_outline(parsed_text: str) -> str:
    """合同标题结构纲要（长合同降级上下文用）。"""
    hs = _parse_headings(parsed_text or "")
    if not hs:
        return "（未识别出条款编号结构）"
    return "\n".join(f"- 第{h['cn']}条 {h['title'] or ''}".rstrip() for h in hs)


def _risk_briefs(db: Session, contract_id: int, limit: int = 40) -> list[dict]:
    """当前有效审核风险的精简清单（长合同降级上下文用；只读，不改风险判定逻辑）。"""
    latest = (
        db.query(AuditRecord.audit_batch)
        .filter(AuditRecord.contract_id == contract_id)
        .order_by(AuditRecord.created_at.desc(), AuditRecord.id.desc())
        .first()
    )
    if not latest:
        return []
    recs = (
        db.query(AuditRecord)
        .filter(AuditRecord.contract_id == contract_id, AuditRecord.audit_batch == latest[0])
        .order_by(AuditRecord.id.asc())
        .limit(limit)
        .all()
    )
    return [{
        "id": r.id,
        "risk_type": r.risk_type,
        "risk_level": r.risk_level,
        "clause_text": _text(r.clause_text)[:200],
        "suggestion": _text(r.suggestion)[:200],
        "located": bool(_text((r.clause_position or {}).get("original_text"))),
    } for r in recs]


def _build_plan_context(db: Session, c: Contract, sessions: list[dict], focus_keys: list[str]) -> dict:
    """组装进入 LLM 的综合上下文（整份合同 + 全部专项会话结果 + 用户整体要求）。"""
    parsed = c.parsed_text or ""
    truncated = len(parsed) > CONTRACT_TEXT_BUDGET
    focus = set(focus_keys or [])
    picked = [s for s in sessions if not focus or s["key"] in focus]

    sess_lines = []
    for i, s in enumerate(picked, 1):
        b = _session_brief(s)
        lines = [
            f"{i}. session_key={b['session_key']} 归属={b['kind']} 操作={b['operation']} 轮次={b['rounds']}"
            f" 定位={'已建立可靠原文定位' if b['located'] else '尚未定位'}",
            f"   当前原文：{b['original_text'] or '（无原文：缺失型/新增型）'}",
            f"   当前最新修改结果：{b['revised_clause'][:SESSION_RESULT_PREVIEW] or '（无）'}",
        ]
        if b["legal_basis"]:
            lines.append(f"   法律依据：{'；'.join(str(x) for x in b['legal_basis'])}")
        if b["remaining_risks"]:
            lines.append(f"   剩余风险：{'；'.join(str(x) for x in b['remaining_risks'])}")
        if b["position"]:
            lines.append(f"   插入位置：{json.dumps(b['position'], ensure_ascii=False)}")
        if b["export_blocker"]:
            lines.append(f"   导出状态：{b['export_blocker']}")
        sess_lines.append("\n".join(lines))

    headings = _headings_outline(parsed)
    if truncated:
        contract_block = (
            f"【整份合同正文】\n（合同正文过长（{len(parsed)} 字），本次按标题结构与风险条目提供纲要；"
            f"正文片段如下）\n{parsed[:CONTRACT_TEXT_BUDGET]}"
        )
        risk_block = _risk_briefs(db, c.id)
    else:
        contract_block = f"【整份合同正文】\n{parsed}"
        risk_block = []

    return {
        "contract_block": contract_block,
        "headings_block": headings,
        "sessions_block": "\n".join(sess_lines) if sess_lines else "（当前没有任何专项修改会话）",
        "risks_block": json.dumps(risk_block, ensure_ascii=False) if risk_block else "",
        "truncated": truncated,
    }


def _normalize_item(raw: dict, index: int) -> dict:
    """LLM 输出的一个修改项 → 规范化结构（不做定位，定位由 _resolve_item 负责）。"""
    op = "add_clause" if str(raw.get("operation") or "").strip() == "add_clause" else "replace"
    legal = raw.get("legal_basis")
    if isinstance(legal, str):
        legal = [legal] if legal.strip() else []
    elif not isinstance(legal, list):
        legal = []
    pos = raw.get("position")
    if not isinstance(pos, dict):
        pos = None
    return {
        "id": f"p{index}",
        "operation": op,
        "target_session_key": _text(raw.get("target_session_key")),
        "clause_no": _text(raw.get("clause_no")),
        "original_quote": _text(raw.get("original_quote")),
        "revised_clause": _text(raw.get("revised_clause")),
        "reason": _text(raw.get("reason")),
        "legal_basis": [str(x) for x in legal],
        "position": pos,
        "resolved": False,
        "resolved_by": "",
        "anchor_text": "",
        "blocking_reason": "",
    }


def _normalize_position(pos: dict | None, parsed_text: str) -> tuple[dict | None, str]:
    """规范化 add_clause 的插入位置（只允许后端 docx_reviser 能表达的形式）。

    返回 ``(position, blocking_reason)``；position 为 None 表示无法表达。
    """
    if not isinstance(pos, dict) or not pos:
        return None, "缺少插入位置"
    if pos.get("append"):
        return {"append": True, "hint": _text(pos.get("hint")) or "追加到合同末尾"}, ""
    anchor = _text(pos.get("anchor"))
    if not anchor:
        return None, "插入位置缺少条款编号锚点"
    if _cn_to_int(anchor) is None:
        return None, f"插入位置「{anchor}」无法识别（只支持「第X条之后」或「追加到末尾」）"
    hint = _text(pos.get("hint")) or f"第{anchor}条之后"
    return {"anchor": anchor, "hint": hint}, ""


def _resolve_item(db: Session, c: Contract, item: dict,
                  sessions_by_key: dict[str, dict]) -> dict:
    """确定性地为修改项定位（replace → 逐字原文锚点；add_clause → 插入位置）。

    定位全部复用审核/修订链路既有的 ``_locate_clause`` / ``_suggest_position``，
    **不新写第二套定位算法**，也不调用 LLM。
    """
    parsed = c.parsed_text or ""

    if item["operation"] == "add_clause":
        pos, why = _normalize_position(item.get("position"), parsed)
        if pos is None:
            suggested = _suggest_position(parsed)
            item["suggested_position"] = suggested
            item["resolved"] = False
            item["blocking_reason"] = f"{why}；需由用户确认插入位置后再确认该修改项"
            return item
        item["position"] = pos
        item["suggested_position"] = pos
        item["resolved"] = True
        item["resolved_by"] = "position"
        return item

    # replace：① 挂到已有专项会话 → 复用该会话的原文锚点（链条口径与 DOCX 导出一致）
    skey = item["target_session_key"]
    if skey and skey in sessions_by_key:
        found = sessions_by_key[skey]
        anchor = found.get("anchor_text") or ""
        if not anchor:
            # 专项会话还没有锚点：退回用它的"当前原文"现场定位（与 revise 的兜底同源）
            anchor = found.get("original_text") or ""
        if anchor:
            item["resolved"] = True
            item["resolved_by"] = "session"
            item["anchor_text"] = _effective_anchor(parsed, anchor)
            if not item["clause_no"]:
                item["clause_no"] = found.get("clause_no") or ""
            return item
        item["resolved"] = False
        item["blocking_reason"] = "目标会话尚未建立可靠原文定位，请先在对应专项会话中确认修改位置"
        return item
    if skey and skey not in sessions_by_key:
        # LLM 编造/写错了 session_key：不给它悄悄新建会话，按"无目标"继续用原文定位
        logger.warning("总体方案引用了不存在的 session_key=%s，按无目标项处理", skey)
        item["target_session_key"] = ""

    # ② 逐字原文定位（用户/AI 给出的 original_quote 必须真的出现在合同正文里）
    quote = item["original_quote"]
    if not quote:
        item["resolved"] = False
        item["blocking_reason"] = "缺少条款逐字原文，无法建立可靠定位，请指定要修改的条款位置"
        return item
    located = _locate_clause(parsed, quote) if parsed else None
    if located and _text(located.get("original_text")):
        item["resolved"] = True
        item["resolved_by"] = "quote"
        item["anchor_text"] = _effective_anchor(parsed, located.get("original_text"))
        if not item["clause_no"] and located.get("clause_no"):
            item["clause_no"] = str(located["clause_no"])
        return item

    # ③ 退化到条款编号定位（LLM 没给逐字原文但给了条号时）
    n = _cn_to_int(item["clause_no"]) if item["clause_no"] else None
    if n is not None and parsed:
        marker = f"第{_int_to_cn(n)}条"
        idx = parsed.find(marker)
        if idx >= 0:
            res = _locate_at(parsed, idx)
            item["resolved"] = True
            item["resolved_by"] = "clause_no"
            item["anchor_text"] = _effective_anchor(parsed, res.get("original_text"))
            item["clause_no"] = str(n)
            return item

    item["resolved"] = False
    item["blocking_reason"] = "无法在合同正文中定位该条款原文，请重新给出逐字原文或指定条款位置"
    return item


_HEADING_HEAD_RE = re.compile(
    r'^\s*((?:第\s*[一二三四五六七八九十百千\d]+\s*条)|(?:[一二三四五六七八九十百千\d]+\s*、))')


def _heading_head(text: str) -> str:
    """文本开头的条款编号（``第二条`` / ``五、``）；无则空串。"""
    m = _HEADING_HEAD_RE.match(text or "")
    return m.group(1) if m else ""


def _effective_anchor(parsed: str, anchor: str) -> str:
    """把锚点归一化成「DOCX 替换实际命中的文本」。

    导出是**段内子串替换**：若段落里锚点前面还紧贴着条款编号（``第二条 …``），
    被替换掉的其实是 ``第二条 + 锚点``。这里复现同一判据，让定位结果与真实替换范围一致，
    避免出现「第二条」被替换却不在影响范围内、或编号被写两遍。
    """
    anchor = _text(anchor)
    if not anchor or not parsed:
        return anchor
    idx = parsed.find(anchor)
    if idx < 0:
        return anchor
    if _heading_head(anchor):
        return anchor
    line_start = max(parsed.rfind("\n", 0, idx) + 1, 0)
    prefix = _heading_head(parsed[line_start:idx])
    return prefix + anchor if prefix else anchor


def _strip_duplicate_heading(revised: str, anchor: str) -> str:
    """修订文本与锚点都带条款编号时去重，避免落库后出现「第二条 第二条 …」。

    DOCX 替换是**段内子串替换**：锚点 ``第二条 验收标准与验收方式：…`` 被整体换成
    ``revised_clause``。用户/AI 若在 revised_clause 里又写了一遍条款编号（``第二条 …``），
    替换后正文会出现重复编号，且该文本一旦成为链条起点还会污染后续多轮修订。

    这里只在「锚点本身就带编号」且「修订文本以同一个编号开头」时剥掉修订文本的编号前缀；
    其余情况（锚点无编号、编号不同）一律不改写用户文本。
    """
    head = _heading_head(anchor)
    if not head:
        return revised
    if (revised or "").startswith(head):
        # 编号后紧跟的顿号/逗号/冒号等分隔符一并去掉，避免留下「第二条 、违约责任」
        return re.sub(r'^[\s、，,：:；;]+', '', revised[len(head):])
    return revised


def _proposal_payload(p: RevisionProposal) -> dict:
    items = list(p.items or [])
    confirmed = set(p.confirmed_ids or [])
    return {
        "proposal_id": p.id,
        "contract_id": p.contract_id,
        "status": p.status,
        "summary": p.summary or "",
        "instruction": p.instruction or "",
        "created_at": _iso(p.created_at),
        "items": items,
        "confirmed_ids": sorted(confirmed),
        "counts": {
            "total": len(items),
            "confirmed": len([i for i in items if i.get("id") in confirmed]),
            "replace": len([i for i in items if i.get("operation") == "replace"]),
            "add_clause": len([i for i in items if i.get("operation") == "add_clause"]),
            # 需要用户先确认位置的项（不可确认落库）
            "needs_location": len([i for i in items
                                   if i.get("id") not in confirmed and not i.get("resolved")]),
        },
    }


# ===========================================================================
# 端点
# ===========================================================================

class OverviewPlanRequest(BaseModel):
    instruction: str = ""
    # 可选：只针对这些专项会话做统筹（不传 = 全部专项会话）
    session_keys: list[str] = []


class OverviewConfirmItem(BaseModel):
    id: str
    # 用户在确认界面上可以微调这几项；锚点一律由后端重新确定性定位，前端无法伪造锚点
    revised_clause: str | None = None
    position: dict | None = None
    target_session_key: str | None = None
    # 「未自动定位」的项由用户指定位置：给出合同原文中的逐字片段（后端据此重新定位锚点）
    original_quote: str | None = None
    clause_no: str | None = None


class OverviewConfirmRequest(BaseModel):
    proposal_id: int
    items: list[OverviewConfirmItem] = []


def _get_contract_or_404(db: Session, user: User, contract_id: int) -> Contract:
    c = db.query(Contract).filter(Contract.id == contract_id).first()
    if not c or not _can_view_contract(user, c):
        raise HTTPException(status_code=404, detail="contract not found")
    return c


@router.get("/{contract_id}/overview")
def get_contract_overview(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """总体修改会话总览（只读，不调 LLM、不写库）。

    返回：总体会话自身状态 + **全部专项修改会话**的
    原文 / 当前最新修改结果 / 法律依据 / 剩余风险 / 定位状态 / 导出状态。
    """
    c = _get_contract_or_404(db, current_user, contract_id)
    return {"code": 0, "message": "ok", "data": _aggregate(db, c.id)}


@router.get("/{contract_id}/overview/proposals")
def list_overview_proposals(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """该合同历史生成过的综合修改方案（刷新后恢复用）。"""
    c = _get_contract_or_404(db, current_user, contract_id)
    rows = (
        db.query(RevisionProposal)
        .filter(RevisionProposal.contract_id == c.id)
        .order_by(RevisionProposal.id.desc())
        .all()
    )
    return {"code": 0, "message": "ok", "data": [_proposal_payload(p) for p in rows]}


@router.post("/{contract_id}/overview/plan")
def create_overview_plan(
    contract_id: int,
    body: OverviewPlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _llm_ready=Depends(require_llm_key_configured),
):
    """在总体会话中对整个合同提出修改要求 → AI 产出**结构化综合修改方案**。

    语义要点（与旧「总体修改」的区别）：
    - 不再把整份合同重写一遍存成 ``overview+replace`` 讨论稿；而是产出逐项修改清单；
    - 本端点**只写方案表**（revision_proposals），**不建立任何 ClauseRevision**；
    - 每个修改项都已由后端做确定性定位：``resolved=false`` 的项必须由用户先确认位置，
      才能进入确认落库（``POST /overview/confirm``）。
    """
    c = _get_contract_or_404(db, current_user, contract_id)
    if not (c.parsed_text or "").strip():
        raise HTTPException(status_code=400, detail="合同正文为空，无法生成整体修改方案")

    agg = _aggregate(db, c.id)
    sessions = agg["sessions"]
    sessions_by_key = {s["key"]: s for s in sessions}
    ctx = _build_plan_context(db, c, sessions, list(body.session_keys or []))

    instruction = body.instruction.strip() or "请从整份合同的角度统筹所有已发现的问题，给出统一的修改方案。"
    prompt = (
        f"{PROPOSAL_SYSTEM_PROMPT}\n\n"
        f"【合同类型】{c.contract_type or '未指定'}\n\n"
        f"{ctx['contract_block']}\n\n"
        f"【合同条款结构】\n{ctx['headings_block']}\n\n"
        f"【各专项修改会话的当前结果】\n{ctx['sessions_block']}\n"
    )
    if ctx["risks_block"]:
        prompt += f"\n【当前审核风险条目（合同正文过长，以此代替全文）】\n{ctx['risks_block']}\n"
    prompt += f"\n【用户本次的整体修改要求】\n{instruction}\n"

    try:
        raw = llm_client.chat(prompt=prompt, temperature=0.1)
    except Exception as e:
        logger.error("总体方案生成失败: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"生成整体修改方案失败：{e}",
        )
    data = extract_json_dict(raw)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="生成整体修改方案失败：AI 未返回可解析的修改项清单，请重试",
        )

    items = []
    for i, raw_item in enumerate(data["items"][:MAX_PROPOSAL_ITEMS], 1):
        if not isinstance(raw_item, dict):
            continue
        item = _normalize_item(raw_item, i)
        if not item["revised_clause"]:
            continue  # 没有修改内容的项直接丢弃，不占位
        items.append(_resolve_item(db, c, item, sessions_by_key))

    if not items:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="生成整体修改方案失败：AI 未给出任何有效的具体修改项，请补充要求后重试",
        )

    p = RevisionProposal(
        contract_id=c.id,
        user_id=current_user.id,
        instruction=instruction,
        status="draft",
        summary=_text(data.get("summary")),
        items=items,
        confirmed_ids=[],
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    payload = _proposal_payload(p)
    included = [s for s in sessions if not body.session_keys or s["key"] in set(body.session_keys)]
    payload["context"] = {
        "sessions_included": len(included),
        "contract_text_truncated": ctx["truncated"],
        # 本次方案纳入的专项会话（供前端把每个修改项对应回来源会话）
        "sessions": [{
            "key": s["key"], "title": s["title"], "kind": s["kind"],
            "operation": s["operation"], "located": s["located"],
            "clause_no": s["clause_no"], "original_text": s["original_text"],
        } for s in included],
    }
    payload["aggregate"] = {
        "counts": agg["counts"],
        "export": agg["export"],
    }
    return {"code": 0, "message": "ok", "data": payload}


@router.get("/{contract_id}/overview/proposals/{proposal_id}")
def get_overview_proposal(
    contract_id: int,
    proposal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取某一份综合修改方案（逐项查看，含定位与阻塞原因）。"""
    c = _get_contract_or_404(db, current_user, contract_id)
    p = (
        db.query(RevisionProposal)
        .filter(RevisionProposal.id == proposal_id, RevisionProposal.contract_id == c.id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="proposal not found")
    return {"code": 0, "message": "ok", "data": _proposal_payload(p)}


@router.post("/{contract_id}/overview/confirm")
def confirm_overview_plan(
    contract_id: int,
    body: OverviewConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """用户确认综合方案中的**具体修改项** → 逐项转换成现有安全的 clause/add_clause revision。

    安全边界：
    - 只接受 **已定位**（``resolved=true``）的项；未定位的项明确拒绝并说明原因，
      不让"定位不了"的修改静默进入导出链路；
    - 复用与 ``POST /{id}/revise`` 完全相同的字段与锚点规则
      （``clause_key`` / ``original_clause_text`` / ``operation`` / ``position``）；
    - **不调用 LLM**：方案内容已经是最终文本，确认环节只做确定性落库；
    - 单项失败不影响其它项（逐项事务），并把失败原因逐项返回给用户；
    - 本端点**不触碰** scope=overview 的既有记录，也不改 DOCX 导出取数口径。
    """
    c = _get_contract_or_404(db, current_user, contract_id)
    p = (
        db.query(RevisionProposal)
        .filter(RevisionProposal.id == body.proposal_id, RevisionProposal.contract_id == c.id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="proposal not found")
    if not body.items:
        raise HTTPException(status_code=400, detail="请至少选择一个修改项进行确认")

    parsed = c.parsed_text or ""
    items_by_id = {str(i.get("id")): dict(i) for i in (p.items or [])}
    confirmed = list(p.confirmed_ids or [])
    sessions_by_key = {s["key"]: s for s in _build_sessions(
        db, c.id,
        db.query(ClauseRevision).filter(ClauseRevision.contract_id == c.id)
        .order_by(ClauseRevision.id.asc()).all(),
    )}

    applied: list[dict] = []
    failed: list[dict] = []

    for req in body.items:
        item = items_by_id.get(str(req.id))
        if item is None:
            failed.append({"id": req.id, "reason": "该修改项不属于这份方案"})
            continue
        if str(req.id) in confirmed:
            failed.append({"id": req.id, "reason": "该修改项已确认落库，请勿重复确认"})
            continue

        # 用户可在确认时微调：仅这三项可改，锚点一律由后端重新确定性定位
        if req.revised_clause is not None:
            new_text = _text(req.revised_clause)
            if not new_text:
                failed.append({"id": req.id, "reason": "修改后的条款内容不能为空"})
                continue
            item["revised_clause"] = new_text
        if req.target_session_key is not None:
            item["target_session_key"] = _text(req.target_session_key)
        if req.position is not None:
            item["position"] = req.position
        if req.clause_no is not None:
            item["clause_no"] = _text(req.clause_no)
        if req.original_quote is not None:
            quote = _text(req.original_quote)
            if not quote:
                failed.append({"id": req.id, "reason": "指定的原文片段为空"})
                continue
            item["original_quote"] = quote
            if item.get("operation") == "replace":
                # 用户重新指定了原文 → 丢弃旧定位并从原文重新定位
                item["resolved"] = False
                item["anchor_text"] = ""
                item.pop("blocking_reason", None)
                fresh = _resolve_item(db, c, item, {})  # 空会话表：强制走"按原文定位"分支
                item["resolved"] = fresh["resolved"]
                item["resolved_by"] = fresh.get("resolved_by", "")
                item["anchor_text"] = fresh.get("anchor_text", "")
                item["clause_no"] = fresh.get("clause_no", item.get("clause_no", ""))
                if not fresh["resolved"]:
                    item["blocking_reason"] = fresh.get("blocking_reason", "")
        if item.get("operation") == "replace" and req.target_session_key is not None:
            # 目标会话改了 → 重新定位（已在上面的原文重定位分支处理过则跳过）
            if req.original_quote is None:
                item["resolved"] = False
                item["anchor_text"] = ""
        if not item.get("resolved"):
            # 未定位的项：给用户一次"就地确认位置"的机会（clause_no 或逐字原文）
            item = _resolve_item(db, c, item, sessions_by_key)

        if not item.get("resolved"):
            failed.append({
                "id": req.id,
                "reason": item.get("blocking_reason") or "该修改项尚未建立可靠定位，无法落库",
                "needs_location": True,
            })
            continue

        if item["operation"] == "add_clause":
            pos, why = _normalize_position(item.get("position"), parsed)
            if pos is None:
                failed.append({"id": req.id, "reason": why, "needs_location": True})
                continue
            rev = ClauseRevision(
                contract_id=c.id,
                scope="clause",
                operation="add_clause",
                position=pos,
                clause_key=_text(item.get("target_session_key")) or OVERVIEW_KEY,
                clause_no=_text(item.get("clause_no")) or None,
                clause_text="",
                original_clause_text=None,   # 新增条款没有原文锚点（与既有 add_clause 链路一致）
                instruction=f"【总体会话综合方案确认为新增条款】{item.get('reason') or p.instruction or ''}",
                revised_clause=item["revised_clause"],
                explanation=item.get("reason") or "",
                constraints=[],
                legal_basis=list(item.get("legal_basis") or []),
                remaining_risks=[],
            )
        else:
            # 落库前归一化：锚点必须等于 DOCX 实际替换的文本范围，且仍能在正文中复核
            anchor = _effective_anchor(parsed, _text(item.get("anchor_text")))
            if not anchor or (parsed and anchor not in parsed):
                failed.append({
                    "id": req.id,
                    "reason": "原文锚点无法在合同正文中复核，已拒绝落库（避免导出漏改/改错位置）",
                    "needs_location": True,
                })
                continue
            skey = _text(item.get("target_session_key"))
            if skey and skey in sessions_by_key:
                # 挂到已有专项会话：clause_text 必须延续该会话的链条（否则 DOCX 链式归并会错位）
                clause_text = sessions_by_key[skey].get("original_text") or anchor
            else:
                clause_text = anchor
            revised_text = _strip_duplicate_heading(item["revised_clause"], anchor)
            if not revised_text:
                failed.append({"id": req.id, "reason": "去掉重复的条款编号后内容为空，请补全条款正文"})
                continue
            rev = ClauseRevision(
                contract_id=c.id,
                scope="clause",
                operation="replace",
                position=None,
                clause_key=skey,
                clause_no=_text(item.get("clause_no")) or None,
                clause_text=clause_text,
                original_clause_text=anchor,
                instruction=f"【总体会话综合方案确认】{item.get('reason') or p.instruction or ''}",
                revised_clause=revised_text,
                explanation=item.get("reason") or "",
                constraints=[],
                legal_basis=list(item.get("legal_basis") or []),
                remaining_risks=[],
            )

        try:
            db.add(rev)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error("总体方案落库失败 proposal=%s item=%s: %s", p.id, req.id, e)
            failed.append({"id": req.id, "reason": "落库失败，请重试"})
            continue

        item["resolved"] = True
        item["anchor_text"] = rev.original_clause_text or item.get("anchor_text") or ""
        items_by_id[str(req.id)] = item
        items_rev = p.items or []
        for i, existing in enumerate(items_rev):
            if str(existing.get("id")) == str(req.id):
                items_rev[i] = item
                break
        p.items = items_rev
        confirmed.append(str(req.id))
        p.confirmed_ids = confirmed
        applied.append({
            "id": req.id,
            "revision_id": rev.id,
            "operation": rev.operation,
            "clause_key": rev.clause_key,
            "clause_no": rev.clause_no,
            "clause_text": rev.clause_text,
            "original_clause_text": rev.original_clause_text,
            "revised_clause": rev.revised_clause,
            "position": rev.position,
            "exportable": _rev_exportable(rev),
        })

    p.confirmed_ids = confirmed
    p.status = "applied" if len(confirmed) >= len(items_by_id) else ("partially_applied" if confirmed else "draft")
    db.add(p)
    db.commit()
    db.refresh(p)

    agg = _aggregate(db, c.id)
    return {"code": 0, "message": "ok", "data": {
        "proposal": _proposal_payload(p),
        "applied": applied,
        "failed": failed,
        "aggregate": {"counts": agg["counts"], "export": agg["export"]},
        "docx_hint": "修改项已写入既有条款修改链路，可在「修改合同」工作台下载修订版合同。",
    }}
