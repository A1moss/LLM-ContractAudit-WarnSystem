"""api.router — Task-level Expert Router 的对外 HTTP 入口。

端点
----
    GET  /api/router/experts          枚举 3 个 Expert 及其能力与负责的任务类型
    POST /api/router/route            只做路由（返回选中哪个 Expert / 哪项能力）
    POST /api/router/execute          路由 + **真实执行**

真实执行链（POST /api/router/execute）
------------------------------------
    HTTP Request
      ↓  Task validation                 （缺 task_type / 类型错 → 400）
      ↓  Expert Router（确定性路由）
      ↓  Expert（选中的能力路径）
      ↓  Skill Registry                  （**唯一执行出口**，不直接调业务函数）
      ↓  现有真实函数
      ↓  HTTP Response

与 MoE 的区别（不得混淆）
-----------------------
本层路由的是**任务能力**，不是模型：不切换任何 LLM 模型，
`ai/llm_client.py` 完全未动。命名一律 `Expert Router` / `Task-level Expert Routing`。

安全边界（沿用前三阶段口径）
--------------------------
1. **必须登录**：三个端点都要 `get_current_user`。
2. **Expert / 能力白名单**：Expert 由 Expert Registry 校验，能力必须是已注册 Skill。
3. **Skill 权限继续生效**：执行前按 Skill 自己的 `permissions` 校验（与 `api/skills.py`、
   A2A 的 `LawAgent` 同一口径）；拒绝 → 403，**不让被拒绝的任务看起来像成功**。
4. **禁用 Skill / 禁用 Expert 不可执行**：分别为 409 与 400。
5. **LLM Key 前置检查**：仅当所选能力 `requires_llm` 时检查（与 Skill API 一致）。
6. **未知任务**：`unroutable`，HTTP 400（绝不"随便选一个 Expert"）。
7. **不泄露 traceback**：错误只回稳定错误码 + 可读摘要。
8. **不落库**：路由与执行都是内存态；不写任何表。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ai.llm_client import LLMConfigError
from ai.router.protocol import (
    KNOWN_TASK_TYPES,
    STATUS_ROUTED,
    STATUS_UNROUTABLE,
    TaskDescriptor,
    TaskValidationError,
)
from ai.router.registry import expert_registry
from ai.router.router import ExpertRouter
from ai.skills import registry as skill_registry
from ai.skills.protocol import (
    SkillDisabledError,
    SkillInputError,
    SkillNotFoundError,
    SkillPermissionError,
)
from api import deps
from api.deps import ROLE_ADMIN, get_current_user
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/router", tags=["router"])

_expert_router = ExpertRouter()


class RouteTaskRequest(BaseModel):
    """Expert Router 的任务入参（对应 `TaskDescriptor`）。

    字段刻意**不做类型约束**（`task_type: Any` 等）：结构校验统一交给
    `TaskDescriptor.from_payload`，这样"缺 task_type / 类型错"一律是
    **400 + 明确原因**，而不是 FastAPI/Pydantic 默认的 422 —— 协议层错误码契约保持一致。
    """

    task_type: Any = Field(None, description="任务类型：legal_retrieval / contract_analysis / clause_revision。")
    query: Any = Field(None, description="任务的自然语言描述（检索词 / 修改要求）。")
    contract_type: Any = Field(None, description="合同类型（contract_analysis 会用到）。")
    capability: Any = Field(None, description="可选：在所选 Expert 内指定用哪项能力。")
    context: Any = Field(None, description="任务上下文（如 full_text / clause_text / instruction / evidence）。")
    all_capabilities: bool | None = Field(
        None, description="execute 时是否执行该 Expert 的**全部**能力（默认只执行选中的一项）。"
    )

    def to_task_payload(self) -> dict:
        return {
            k: v
            for k, v in self.model_dump(exclude={"all_capabilities"}).items()
            if v is not None
        }


# ── 辅助：与 api/skills.py、ai/a2a/agents.py 完全同一口径的权限校验 ──
def _require_skill_permission(skill, current_user: User) -> None:
    allowed = tuple(skill.permissions or ())
    if not allowed:
        return
    if current_user.role == ROLE_ADMIN:
        return
    if current_user.role not in allowed:
        raise SkillPermissionError(
            f"当前角色（{current_user.role}）无权调用能力 {skill.name!r}"
        )


def _load_skill_or_error(name: str):
    try:
        return skill_registry.get(name)
    except SkillNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Expert 声明的能力 {name!r} 未在 Skill Registry 注册（注册状态异常）",
        )


def _unroutable_error(decision) -> HTTPException:
    """把 `unroutable` 决策映射为 HTTP 400（含稳定 reason + 可用任务类型）。"""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "status": STATUS_UNROUTABLE,
            "task_type": decision.task_type,
            "reason": decision.reason,
            "message": decision.detail,
            "known_task_types": sorted(KNOWN_TASK_TYPES),
        },
    )


@router.get("/experts")
def list_experts(
    task_type: str | None = None,
    enabled_only: bool = False,
    current_user: User = Depends(get_current_user),
):
    """枚举 Expert 及其**真实**能力（能力均来自 Skill Registry）。"""
    experts = _expert_router.describe_experts(enabled_only=enabled_only, task_type=task_type)
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "total": len(experts),
            "registered_total": len(expert_registry.names()),
            "experts": experts,
        },
    }


@router.post("/route")
def route_task(
    body: RouteTaskRequest,
    current_user: User = Depends(get_current_user),
):
    """只做路由：返回选中哪个 Expert、哪项能力，以及候选与优先级。

    * 可路由 → HTTP 200，`status="routed"`
    * 不可路由（未知任务 / Expert 全禁用）→ HTTP 200，`status="unroutable"` + `reason`

    刻意把"不可路由"也作为 200 返回：它是**路由决策的一种结果**，
    而不是协议错误。真正的错误由 `/execute` 用 4xx 表达。
    """
    try:
        task = TaskDescriptor.from_payload(body.to_task_payload())
    except TaskValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    decision = _expert_router.route(task)
    return {"code": 0, "message": "ok", "data": decision.to_dict()}


@router.post("/execute")
def execute_task(
    body: RouteTaskRequest,
    current_user: User = Depends(get_current_user),
):
    """路由 + **真实执行**：Expert → Skill Registry → 现有真实函数。

    默认只执行选中的那一项能力（保持"一个任务一次专家调用"的语义）；
    传 `all_capabilities=true` 时按 Expert 声明的顺序执行其全部能力
    （用于演示 `contract_analysis_expert` 的最小能力链）。

    返回结构：`{status, task_type, expert, capability, capabilities, priority, results[]}`，
    其中 `results[i].result` 是 Skill 的**原始返回值**，未做二次加工。
    """
    try:
        task = TaskDescriptor.from_payload(body.to_task_payload())
    except TaskValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    decision = _expert_router.route(task)

    # 不可路由：明确 400，绝不兜底执行
    if decision.status != STATUS_ROUTED:
        raise _unroutable_error(decision)

    expert = expert_registry.get(decision.expert)
    if expert is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"路由结果指向未注册的 Expert {decision.expert!r}（注册状态异常）",
        )

    targets = list(expert.capabilities) if body.all_capabilities else [decision.capability]

    # 能力链上下文：**只做字段搬运**，把上一项能力的真实输出接到下一项能力的输入上。
    # `contract_analysis_expert` 的 extract_evidence → adjudicate_risks 正是靠这一步串联
    # （否则 adjudicate_risks 会拿到空证据）。这不是"复制 _run_audit"：
    # 主链路 `_run_audit` 完全未动，这里只是旁路的极简能力链。
    chain_ctx = _seed_chain_context(task)

    results = []
    for name in targets:
        skill = _load_skill_or_error(name)

        # 权限（与 Skill API / A2A 同口径）
        try:
            _require_skill_permission(skill, current_user)
        except SkillPermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

        # 禁用 Skill 拒绝执行
        if not skill.enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"能力 {name!r} 已注册但处于禁用状态，当前不可执行",
            )

        # LLM Key 前置检查（只对需要 LLM 的能力）
        if skill.requires_llm:
            deps.require_llm_key_configured()

        # 真实执行：经 Skill Registry（**不**直接调业务函数）
        payload = _build_skill_payload(name, task, chain_ctx)
        try:
            value = skill_registry.invoke(name, payload)
        except SkillInputError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"能力 {name!r} 输入不合法：{exc}",
            )
        except LLMConfigError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except SkillDisabledError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 —— 真实失败必须可见，不伪装成功
            logger.exception("Router 执行失败: expert=%s capability=%s", expert.name, name)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"能力 {name!r} 执行失败：{type(exc).__name__}: {exc}",
            )

        results.append({
            "capability": name,
            "source": skill.source,
            "requires_llm": skill.requires_llm,
            "result": value,
        })
        _chain_forward(name, value, chain_ctx)

    logger.info("Router 执行成功: task_type=%s expert=%s capabilities=%s user_id=%s",
                task.task_type, expert.name, targets, current_user.id)

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "status": "completed",
            "task_type": task.task_type,
            "expert": expert.name,
            "capability": decision.capability,
            "capabilities": list(expert.capabilities),
            "priority": expert.priority,
            "executed": targets,
            "results": results,
        },
    }


def _seed_chain_context(task: TaskDescriptor) -> dict:
    """能力链的初始上下文（只做字段搬运，不做业务加工）。"""
    ctx = dict(task.context or {})
    text = str(ctx.get("full_text") or task.query or "")
    ctx.setdefault("full_text", text)
    ctx.setdefault("text", text)
    return ctx


def _chain_forward(capability: str, value, ctx: dict) -> None:
    """把上一项能力的**真实输出**接到下一项能力的输入上（能力链串联）。

    这是本层唯一的"编排"动作：只做主链路已有的字段名映射，
    不复制 `_run_audit`、不改任何业务逻辑。
    """
    if capability == "extract_evidence" and isinstance(value, dict):
        # evidence_extractor 返回状态信封；裁决只要其中的 evidence
        if isinstance(value.get("evidence"), dict):
            ctx["evidence"] = value["evidence"]
    elif capability == "extract_elements" and isinstance(value, dict):
        ctx["elements"] = value
    elif capability == "rule_scan" and isinstance(value, list):
        ctx["risks"] = value


def _build_skill_payload(capability: str, task: TaskDescriptor, ctx: dict) -> dict:
    """按能力真实需要，从任务 + 能力链上下文组装 Skill 输入。

    这是"路由层 → 能力层"的唯一适配点：只做字段搬运，**不做任何业务加工**。
    """
    query = task.query

    if capability == "retrieve_knowledge":
        return {"query": query, "top_k": int(ctx.get("top_k") or 3),
                **({"collection_name": ctx["collection_name"]} if ctx.get("collection_name") else {})}
    if capability == "retrieve_templates":
        return {"query": query, "top_k": int(ctx.get("top_k") or 3)}
    if capability == "extract_elements":
        return {"full_text": str(ctx.get("full_text") or query),
                "contract_type": task.contract_type or str(ctx.get("contract_type") or "")}
    if capability == "rule_scan":
        return {"text": str(ctx.get("text") or ctx.get("full_text") or query)}
    if capability == "extract_evidence":
        return {"full_text": str(ctx.get("full_text") or query)}
    if capability == "adjudicate_risks":
        evidence = ctx.get("evidence")
        return {"evidence": evidence if isinstance(evidence, dict) else {}}
    if capability == "revise_clause":
        return {
            "clause_text": str(ctx.get("clause_text") or ""),
            "instruction": str(ctx.get("instruction") or query),
            "contract_type": task.contract_type or str(ctx.get("contract_type") or ""),
            **({"history": ctx["history"]} if ctx.get("history") else {}),
            **({"rag_context": ctx["rag_context"]} if ctx.get("rag_context") else {}),
        }
    if capability == "draft_clause":
        return {
            "instruction": str(ctx.get("instruction") or query),
            "contract_type": task.contract_type or str(ctx.get("contract_type") or ""),
            **({"rag_context": ctx["rag_context"]} if ctx.get("rag_context") else {}),
            **({"position_hint": ctx["position_hint"]} if ctx.get("position_hint") else {}),
        }

    # 兜底：把上下文直接当作 Skill 输入（Expert 扩容时无需改这里）
    return dict(ctx)


__all__ = ["router"]
