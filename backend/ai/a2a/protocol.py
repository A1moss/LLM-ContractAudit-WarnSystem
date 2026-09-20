"""ai.a2a.protocol — A2A（Agent-to-Agent）最小数据协议。

定位（本阶段事实边界）
--------------------
本模块只定义 **两个 Agent 之间的一次任务通信** 长什么样：

    Agent A ──A2ATask──▶ Agent B ──A2AResult──▶ Agent A

**它不是 Multi-Agent 编排**：没有 planner、没有 task graph、没有 agent 自主选择下一步、
没有 memory、没有消息队列、没有后台 worker。本轮只做"一次真实 HTTP 任务调用"。

与 Skill 层的关系（本阶段最重要的架构关系）
----------------------------------------
    A2A   → Agent 之间的**任务通信**（谁把什么任务交给谁）
    Skill → Agent 可以调用的**具体能力**（任务最终落到哪个真实函数）

因此 A2A **不重新实现任何业务逻辑**：`A2ATask.capability` 只允许是
`ai.skills.registry` 里**已经注册的 Skill 名**，最终由 Skill 适配器转发到现有真实函数。
禁止把 `capability` 当成"可动态 import 的 Python 路径"。

字段设计
-------
严格按省赛第二阶段约定：必要字段 `schema_version / task_id / from_agent / to_agent /
capability / input / trace_id`；预留 `auth_scheme / created_at / timeout`（**本轮不实现认证**，
`auth_scheme` 仅作为占位字段如实回显，不代表已具备认证能力）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

# 协议版本：Task / Result / AgentCard 三者共用，便于将来演进时做兼容判断
SCHEMA_VERSION = "1.0"

# Result 状态集合（刻意只保留三态，不引入 pending/running —— 本轮是同步 request/response）
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"
STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_REJECTED)

# 错误码（对外稳定契约；内部异常一律映射到这些码，不泄露 traceback）
ERR_INVALID_TASK = "INVALID_TASK"
ERR_UNKNOWN_AGENT = "UNKNOWN_AGENT"
ERR_UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
ERR_UNAUTHORIZED = "UNAUTHORIZED"
ERR_SKILL_DISABLED = "SKILL_DISABLED"
ERR_SKILL_ERROR = "SKILL_ERROR"
ERR_TRANSPORT_ERROR = "TRANSPORT_ERROR"


def new_id() -> str:
    """生成 task_id / trace_id（uuid4 字符串）。"""
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    """UTC 时间戳（秒级，带 Z 后缀），用于 created_at 等可读字段。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class A2AError(Exception):
    """A2A 协议层基类异常。"""

    code = ERR_INVALID_TASK

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class A2ATaskValidationError(A2AError):
    """Task 结构不合法（缺字段 / 类型错 / schema_version 不支持）。"""

    code = ERR_INVALID_TASK


class A2AAgentNotFoundError(A2AError):
    """`to_agent` 不在 Agent Registry 白名单内。"""

    code = ERR_UNKNOWN_AGENT


class A2ACapabilityError(A2AError):
    """`capability` 不是该 Agent 声明的能力，或不是已注册的 Skill。"""

    code = ERR_UNKNOWN_CAPABILITY


@dataclass
class A2ATask:
    """一次 Agent→Agent 的任务请求。

    `capability` **必须**是已注册 Skill 名（由 Agent 的 capabilities 声明约束），
    绝不是可执行路径 —— 这是本协议的安全底线。
    """

    to_agent: str
    capability: str
    input: dict
    from_agent: str = ""
    task_id: str = field(default_factory=new_id)
    trace_id: str = field(default_factory=new_id)
    schema_version: str = SCHEMA_VERSION
    # ── 预留位（本轮不实现认证；auth_scheme 仅如实回显）──
    auth_scheme: str = "none"
    created_at: str = field(default_factory=utc_now_iso)
    timeout: float | None = None

    # 协议要求的必要字段（用于校验与文档）
    REQUIRED_FIELDS = ("schema_version", "task_id", "from_agent", "to_agent",
                       "capability", "input", "trace_id")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "A2ATask":
        """从 JSON dict 构造 Task，并做严格校验（缺字段/类型错一律拒绝）。"""
        if not isinstance(payload, Mapping):
            raise A2ATaskValidationError("Task 必须是 JSON 对象")

        missing = [f for f in ("to_agent", "capability", "input") if f not in payload]
        if missing:
            raise A2ATaskValidationError(f"Task 缺少必要字段：{', '.join(missing)}")

        schema_version = payload.get("schema_version") or SCHEMA_VERSION
        if schema_version != SCHEMA_VERSION:
            raise A2ATaskValidationError(
                f"不支持的 schema_version={schema_version!r}（当前支持 {SCHEMA_VERSION!r}）"
            )

        for name in ("to_agent", "capability"):
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip():
                raise A2ATaskValidationError(f"字段 {name!r} 必须是非空字符串")

        input_payload = payload.get("input")
        if not isinstance(input_payload, Mapping):
            raise A2ATaskValidationError("字段 'input' 必须是 JSON 对象")

        task_id = payload.get("task_id") or new_id()
        trace_id = payload.get("trace_id") or new_id()
        for name, value in (("task_id", task_id), ("trace_id", trace_id)):
            if not isinstance(value, str) or not value.strip():
                raise A2ATaskValidationError(f"字段 {name!r} 必须是非空字符串")

        from_agent = payload.get("from_agent") or ""
        if not isinstance(from_agent, str):
            raise A2ATaskValidationError("字段 'from_agent' 必须是字符串")

        timeout = payload.get("timeout")
        if timeout is not None and not isinstance(timeout, (int, float)):
            raise A2ATaskValidationError("字段 'timeout' 必须是数字或省略")

        return cls(
            to_agent=payload["to_agent"].strip(),
            capability=payload["capability"].strip(),
            input=dict(input_payload),
            from_agent=from_agent.strip(),
            task_id=task_id,
            trace_id=trace_id,
            schema_version=schema_version,
            auth_scheme=str(payload.get("auth_scheme") or "none"),
            created_at=str(payload.get("created_at") or utc_now_iso()),
            timeout=float(timeout) if timeout is not None else None,
        )

    def to_payload(self) -> dict:
        """序列化为 JSON dict（用于真实 HTTP 传输）。"""
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "capability": self.capability,
            "input": self.input,
            "trace_id": self.trace_id,
            "auth_scheme": self.auth_scheme,
            "created_at": self.created_at,
            "timeout": self.timeout,
        }


@dataclass
class A2AResult:
    """一次 Agent→Agent 任务的响应。

    约定：`status == completed` 时 `result` 必为 dict；否则 `error` 必为 dict。
    绝不把 Python traceback 放进 `error.message`。
    """

    task_id: str
    from_agent: str
    to_agent: str
    status: str
    trace_id: str
    result: dict | None = None
    error: dict | None = None
    schema_version: str = SCHEMA_VERSION
    completed_at: str = field(default_factory=utc_now_iso)
    # 便于演示时如实展示"到底走了哪一段"（agent → capability → skill → 真实函数）
    capability: str = ""
    skill: str = ""
    skill_source: str = ""

    @classmethod
    def completed(
        cls,
        task: A2ATask,
        result: dict,
        *,
        from_agent: str,
        skill: str,
        skill_source: str = "",
    ) -> "A2AResult":
        return cls(
            task_id=task.task_id,
            from_agent=from_agent,
            to_agent=task.from_agent or "unknown",
            status=STATUS_COMPLETED,
            trace_id=task.trace_id,
            result=result,
            capability=task.capability,
            skill=skill,
            skill_source=skill_source,
        )

    @classmethod
    def failed(
        cls,
        task: A2ATask | None,
        *,
        code: str,
        message: str,
        from_agent: str = "unknown",
        to_agent: str = "unknown",
        task_id: str | None = None,
        trace_id: str | None = None,
    ) -> "A2AResult":
        """构造失败结果。`message` 必须是**已脱敏**的可读信息，不含 traceback。"""
        return cls(
            task_id=task_id or (task.task_id if task else new_id()),
            from_agent=from_agent or (task.from_agent if task else "unknown"),
            to_agent=to_agent or (task.to_agent if task else "unknown"),
            status=STATUS_FAILED if code != ERR_UNAUTHORIZED else STATUS_REJECTED,
            trace_id=trace_id or (task.trace_id if task else new_id()),
            error={"code": code, "message": message},
            capability=task.capability if task else "",
        )

    @classmethod
    def rejected(cls, task: A2ATask | None, *, code: str, message: str, **kwargs) -> "A2AResult":
        result = cls.failed(task, code=code, message=message, **kwargs)
        result.status = STATUS_REJECTED
        return result

    def to_payload(self) -> dict:
        """序列化为 JSON dict。`completed` 只带 result，`failed/rejected` 只带 error。"""
        payload = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "status": self.status,
            "trace_id": self.trace_id,
            "capability": self.capability,
            "skill": self.skill,
            "skill_source": self.skill_source,
            "completed_at": self.completed_at,
        }
        if self.status == STATUS_COMPLETED:
            payload["result"] = self.result if self.result is not None else {}
        else:
            payload["error"] = self.error or {"code": ERR_SKILL_ERROR, "message": "未知错误"}
        return payload


@dataclass
class AgentCard:
    """最小 Agent 描述。

    `capabilities` 里每一项都必须是**真实存在**的能力，由 `implementation` 明确其落地位置：

    * `implementation="skill"`（`kind="skill-backed"`）
      → 每一项能力必须是 `ai.skills.registry` 中已注册的 **Skill 名**，
        由 Skill 适配器转发到现有真实函数。
    * `implementation="method"`（`kind="a2a-client"`）
      → 每一项能力必须是该 Agent 类上**真实存在的方法名**，
        由 AgentRegistry 用 `hasattr` 校验。

    两种情况下都**绝不**允许声明"尚未实现的能力"。
    """

    name: str
    description: str
    capabilities: tuple[str, ...]
    version: str = "1.0"
    implementation: str = "skill"
    """能力的落地方式：`skill`（Skill Registry）或 `method`（Agent 自身方法）。"""

    kind: str = "skill-backed"
    """`skill-backed` = 能力落在 Skill 上；`a2a-client` = 客户端侧，能力落在方法上。"""

    transport: str = "http"
    endpoint: str = ""
    """对外接收 A2A Task 的 HTTP 端点（真实存在，非占位）。"""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "implementation": self.implementation,
            "kind": self.kind,
            "transport": self.transport,
            "endpoint": self.endpoint,
        }


__all__ = [
    "ERR_INVALID_TASK",
    "ERR_SKILL_DISABLED",
    "ERR_SKILL_ERROR",
    "ERR_TRANSPORT_ERROR",
    "ERR_UNAUTHORIZED",
    "ERR_UNKNOWN_AGENT",
    "ERR_UNKNOWN_CAPABILITY",
    "SCHEMA_VERSION",
    "STATUSES",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_REJECTED",
    "A2AError",
    "A2AAgentNotFoundError",
    "A2ACapabilityError",
    "A2AResult",
    "A2ATask",
    "A2ATaskValidationError",
    "AgentCard",
    "new_id",
    "utc_now_iso",
]
