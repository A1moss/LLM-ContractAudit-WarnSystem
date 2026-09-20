"""api.a2a — A2A 最小闭环对外的 HTTP 端点。

端点
----
    GET  /api/a2a/agents                     枚举 Agent 与它们真实的能力
    GET  /api/a2a/agents/{agent_name}        查看单个 Agent Card
    POST /api/a2a/tasks                      接收 A2A Task（Agent B 侧入口）
    POST /api/a2a/audit-to-law               演示：AuditAgent 经**真实 HTTP** 调 LawAgent

真实链路（POST /api/a2a/tasks）
-----------------------------
    HTTP Request
      ↓  Task validation                （缺字段/类型错/版本不符 → 400）
      ↓  Agent routing                  （非白名单 Agent → 404）
      ↓  LawAgent.handle_task
      ↓  Skill Registry（capability 必须是已注册 Skill）
      ↓  retrieve_knowledge → 现有真实函数 search_knowledge
      ↓  A2A Result
      ↓  HTTP Response

真实链路（POST /api/a2a/audit-to-law）
-------------------------------------
    AuditAgent ──httpx POST──▶ 本服务 /api/a2a/tasks ──▶ LawAgent ──▶ Skill
               ◀──A2AResult───                            （**真实 HTTP**，非同进程函数调用）

安全边界（沿用 Skill API 的口径，逐条落地）
------------------------------------------
1. **必须登录**：四个端点都要 `get_current_user`。
2. **Agent 名称白名单**：`to_agent` / `{agent_name}` 只能取 Agent Registry 中的名字。
3. **capability 白名单**：`capability` 必须是**已注册 Skill 名**，
   **绝不**把它当可动态 import 的 Python 路径（禁用任意函数调用 / 任意 import）。
4. **Skill 权限继续生效**：由 LawAgent 按 Skill 自己的 `permissions` 校验；
   拒绝时对端返回 `status=rejected` + `UNAUTHORIZED`，本层映射为 **HTTP 403**
   （不能让"被拒绝的任务"看起来像 200 成功）。
5. **禁用 Skill 不可调用**：`status=failed` + `SKILL_DISABLED` → 409。
6. **禁止任意 URL 请求**：出站传输层只允许本机回环 + 固定路径 `/api/a2a/tasks`
   （见 `ai/a2a/transport.py::validate_task_url`），防 SSRF。
7. **禁止任意文件读取**：本层不接触文件；涉及的 Skill 只有 `retrieve_knowledge`。
8. **不泄露 traceback**：错误只回稳定错误码 + 可读摘要。
9. **不回显密钥**：出站只透传调用方自己的 Bearer token（且仅用作出站请求头）。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ai.a2a.agents import SERVER_AGENTS, AuditAgent, server_agent
from ai.a2a.multi_agent import (
    STATUS_FAILED,
    MultiAgentOrchestrator,
    WorkflowStateError,
)
from ai.a2a.protocol import (
    ERR_TRANSPORT_ERROR,
    STATUS_COMPLETED,
    A2ATask,
    A2ATaskValidationError,
)
from ai.a2a.registry import agent_registry
from ai.a2a.transport import A2ATransportError, HttpA2ATransport
from api.deps import get_current_user
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/a2a", tags=["a2a"])


class A2ATaskRequest(BaseModel):
    """A2A Task 的 HTTP 入参（与 `A2ATask` 协议字段一一对应）。

    刻意**全部字段可选**：结构校验统一交给 `A2ATask.from_payload`，
    这样"缺字段 / 类型错"对调用方一律是 **400 + 明确原因**，
    而不是 FastAPI 默认的 422 —— 协议层错误码契约保持一致。
    """

    to_agent: str | None = Field(None, description="目标 Agent 名（必须在 Agent Registry 白名单内）。")
    capability: str | None = Field(None, description="能力名，必须是已注册的 Skill 名。")
    input: dict | None = Field(None, description="该能力的输入（符合 Skill 的 input_schema）。")
    from_agent: str | None = Field(None, description="发起方 Agent 名。")
    task_id: str | None = Field(None, description="任务 ID；缺省自动生成 uuid4。")
    trace_id: str | None = Field(None, description="链路追踪 ID；缺省自动生成 uuid4。")
    schema_version: str | None = Field(None, description="协议版本，默认 1.0。")
    timeout: float | None = Field(None, description="预留字段（本轮不做超时调度）。")

    def to_task_payload(self) -> dict:
        """转成 `A2ATask.from_payload` 需要的 dict（丢掉显式 None 的可选字段）。"""
        return {k: v for k, v in self.model_dump().items() if v is not None}


def _get_agent_card_or_404(agent_name: str):
    if not agent_registry.has(agent_name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_name!r} 不存在（可用 GET /api/a2a/agents 查看全部 Agent）",
        )
    return agent_registry.get(agent_name)


# 错误码 → HTTP 状态码（保持"被拒绝 ≠ 成功"）
_ERROR_HTTP_STATUS = {
    "UNAUTHORIZED": status.HTTP_403_FORBIDDEN,
    "UNKNOWN_CAPABILITY": status.HTTP_403_FORBIDDEN,
    "UNKNOWN_AGENT": status.HTTP_404_NOT_FOUND,
    "SKILL_DISABLED": status.HTTP_409_CONFLICT,
    "INVALID_TASK": status.HTTP_400_BAD_REQUEST,
}


@router.get("/agents")
def list_agents(
    capability: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """枚举已登记 Agent 及各自**真实**声明的能力。"""
    cards = agent_registry.describe(capability=capability)
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "total": len(cards),
            "registered_total": len(agent_registry.names()),
            "agents": cards,
        },
    }


@router.get("/agents/{agent_name}")
def get_agent(
    agent_name: str,
    current_user: User = Depends(get_current_user),
):
    """查看单个 Agent Card。"""
    card = _get_agent_card_or_404(agent_name)
    return {"code": 0, "message": "ok", "data": card.to_dict()}


@router.post("/tasks")
def submit_task(
    body: A2ATaskRequest,
    current_user: User = Depends(get_current_user),
):
    """接收 A2A Task：校验 → 路由到目标 Agent → 调 Skill → 返回 A2A Result。

    HTTP 状态码语义：
    * 400 —— Task 结构不合法（缺字段 / 类型错 / schema_version 不支持）
    * 404 —— `to_agent` 不在 Agent Registry（含"该 Agent 不接收任务"的情况）
    * 403 —— Agent 拒绝执行（能力未声明 / 权限不足 / 未注册能力）
    * 409 —— 能力对应的 Skill 处于禁用状态
    * 502 —— Skill 执行失败
    * 200 —— `status=completed`，且 `result.items` 是 Skill 的真实返回
    """
    try:
        task = A2ATask.from_payload(body.to_task_payload())
    except A2ATaskValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Agent 白名单 + "该 Agent 是否接收任务"
    card = _get_agent_card_or_404(task.to_agent)
    agent = server_agent(task.to_agent)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Agent {task.to_agent!r} 存在（{card.description}）但不接收 A2A 任务："
                f"它是客户端侧 Agent，可接收任务的是 {sorted(SERVER_AGENTS)}"
            ),
        )

    a2a_result = agent.handle_task(task, current_user)
    payload = a2a_result.to_payload()

    if a2a_result.status == STATUS_COMPLETED:
        return {"code": 0, "message": "ok", "data": {"a2a_result": payload}}

    error_code = (a2a_result.error or {}).get("code", "")
    logger.info("A2A Task 未成功: to=%s capability=%s status=%s code=%s",
                task.to_agent, task.capability, a2a_result.status, error_code)
    raise HTTPException(
        status_code=_ERROR_HTTP_STATUS.get(error_code, status.HTTP_502_BAD_GATEWAY),
        detail={"a2a_result": payload},
    )


@router.post("/audit-to-law")
async def audit_agent_to_law_agent(
    request: Request,
    body: A2ATaskRequest,
    current_user: User = Depends(get_current_user),
):
    """演示闭环：**AuditAgent 经真实 HTTP** 把任务发给 LawAgent。

    与 `POST /api/a2a/tasks` 的区别：这里站在 **Agent A（发起方）** 一侧 ——
    AuditAgent 用 httpx 向本服务的 `/api/a2a/tasks` 发真实 HTTP POST，
    由 LawAgent 处理后把 A2AResult 回传。

    安全：
    * 目标地址由本服务自身的 `request.base_url` 推导（**不信任**用户输入的 URL），
      并强制经 `validate_task_url` 校验（仅本机回环 + 固定路径），防 SSRF；
    * 只透传调用方自己的 Authorization 头，不读取也不回显任何密钥。
    """
    payload_in = body.input or {}
    query = str(payload_in.get("query") or "").strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="演示闭环需要 input.query（法规检索词）",
        )

    base_url = str(request.base_url).rstrip("/")
    try:
        transport = HttpA2ATransport(base_url, timeout=30.0)
    except A2ATransportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    auth_header = request.headers.get("Authorization") or ""
    bearer = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else None

    audit_agent = AuditAgent(transport)
    try:
        # 真实 HTTP 调用（httpx POST → /api/a2a/tasks → LawAgent → Skill）。
        # 必须用**异步**客户端：本端点自身就在事件循环里，阻塞式 HTTP 会让内层
        # 请求永远排不上队（自请求死锁，实测 ReadTimeout）。
        a2a_result = await audit_agent.request_law_retrieval_async(
            query,
            top_k=int(payload_in.get("top_k") or 3),
            collection_name=payload_in.get("collection_name"),
            bearer_token=bearer,
            to_agent=body.to_agent or "law_agent",
            trace_id=body.trace_id,
        )
    except A2ATransportError as exc:
        logger.warning("AuditAgent -> LawAgent 传输失败: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": ERR_TRANSPORT_ERROR, "message": str(exc)},
        )

    payload = a2a_result.to_payload()
    if a2a_result.status != STATUS_COMPLETED:
        code = (a2a_result.error or {}).get("code", "")
        raise HTTPException(
            status_code=_ERROR_HTTP_STATUS.get(code, status.HTTP_502_BAD_GATEWAY),
            detail={"a2a_result": payload},
        )

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "initiated_by": audit_agent.identity().name,
            "transport": "http",
            "target": transport.url,
            "a2a_result": payload,
        },
    }


class MultiAgentRunRequest(BaseModel):
    """Multi-Agent 闭环入参。

    刻意**不提供** `max_attempts` —— 循环次数由服务端固定（`MAX_ATTEMPTS_CEILING`），
    客户端无法把它改成无限，避免出现不可控的长循环。
    """

    query: str = Field(..., description="法规检索词（如「合同违约金过高如何处理」）。")
    top_k: int | None = Field(None, description="检索条数，默认 3（底层 Chroma 上限 10）。")
    collection_name: str | None = Field(None, description="知识库集合，默认 laws。")


@router.post("/multi-agent/run")
async def run_multi_agent(
    request: Request,
    body: MultiAgentRunRequest,
    current_user: User = Depends(get_current_user),
):
    """跑一次 **Multi-Agent 最小真实协作闭环**。

    真实执行路径（全程复用第二阶段的 A2A，未另造通信层）：

        AuditAgent ──A2A──▶ LawAgent ──retrieve_knowledge Skill──▶ 真实法规依据
                          │
                          └──A2A──▶ ReviewerAgent ──decision──┐
                                                             ├ approve → completed
                                                             └ revise  → 回到 LawAgent（最多 1 次）
                                                                         第二次仍 revise → rejected

    `ReviewerAgent` 的 decision 是**控制流输入**：approve 时 LawAgent 只被调用 1 次，
    revise 时被调用 2 次。响应里的 `steps` 与 `attempts` 可逐条核对。

    安全：登录必需；Agent/capability 走 Registry 白名单；Skill 权限与禁用检查由各
    Agent 复用第二阶段逻辑；出站仍限本机回环；`max_attempts` 服务端固定。
    """
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query 不能为空")

    top_k = body.top_k if body.top_k is not None else 3
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not (1 <= top_k <= 10):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="top_k 需为 1~10 的整数"
        )

    # A2A_SELF_BASE_URL（若配置）优先于 request.base_url：
    # 让测试可把出站目标指向它真正启动的 uvicorn 实例；两者都必须过回环白名单。
    base_url = (os.getenv("A2A_SELF_BASE_URL") or "").strip() or str(request.base_url).rstrip("/")
    try:
        transport = HttpA2ATransport(base_url, timeout=60.0)
    except A2ATransportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    auth_header = request.headers.get("Authorization") or ""
    bearer = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else None

    orchestrator = MultiAgentOrchestrator(transport, bearer_token=bearer)

    # Orchestrator 是同步实现；放在线程池里跑，避免阻塞事件循环
    # （同时保证它发出的真实 HTTP 自请求能被本 worker 正常处理）
    try:
        run = await run_in_threadpool(
            orchestrator.run,
            query,
            top_k=top_k,
            collection_name=body.collection_name,
        )
    except A2ATransportError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": ERR_TRANSPORT_ERROR, "message": str(exc)},
        )
    except WorkflowStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    # 传输层失败 → 502（编排器已把失败原因结构化放进 error，这里不掩盖）
    if run.status == STATUS_FAILED and (run.error or {}).get("code") == ERR_TRANSPORT_ERROR:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"a2a_result": {"workflow_id": run.workflow_id, **run.to_dict()}},
        )

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "transport": "http",
            "target": transport.url,
            "orchestrator": "MultiAgentOrchestrator",
            **run.to_dict(),
        },
    }


__all__ = ["router"]