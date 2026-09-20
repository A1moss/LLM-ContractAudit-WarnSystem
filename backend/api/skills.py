"""api.skills — Skill 能力层对外的三个只读/受控端点。

     GET  /api/skills              → 枚举（12 个 Skill 的描述）
     GET  /api/skills/{name}       → 单个 Skill 详情
     POST /api/skills/{name}/invoke → 受控执行

安全边界（重要，不得放宽）
------------------------
题面要求："不要让这个通用执行接口绕过现有权限、数据隔离或安全边界"。本模块的处理：

1. **必须登录**：三个端点都要 `get_current_user`。未登录一律 401 —— 能力清单本身
   不对外公开（与既有 `GET /api/contract-types` 需登录的口径一致）。
2. **按 Skill 粒度做角色校验**：每个 Skill 在自己的 `permissions` 里声明允许的角色，
   `require_skill_permission` 据此检查（`admin` 恒通过）。Skill 层不预设"人人都能调"。
3. **LLM Key 前置检查**：`requires_llm=True` 的 Skill，在**执行前**用既有的
   `require_llm_key_configured` 检查（个人 Key 或 .env 默认 Key），
   避免"看似成功、实际全链路降级"。不需要 LLM 的 Skill 不受影响。
4. **`parse_document` 收窄为 admin + 限定目录**：它是唯一会读磁盘的 Skill，
   适配器内已把路径限制在 `backend/data/` 内（见 `adapters._resolve_data_file`），
   并把角色收紧为 `admin`，防止统一 invoke 变成任意文件读取原语。
5. **`enabled=False` 的 Skill 拒绝执行**（409），不做静默跳过。
6. **不吞异常、不包装返回**：Skill 输出与直接调用底层函数逐字一致；
   底层异常原样向上抛（除少数可映射的输入/配置错误转成 4xx），
   保证"真实报错可见"，也保证本接口不会伪装成功。

关于数据库写操作
--------------
现有 12 个 Skill **全部无数据库写副作用**（写入发生在 `_run_audit` 与各修订端点里，
不在这些能力函数里）。因此统一 invoke 不构成"任意写库"原语。后续若新增带副作用的
Skill，必须先在此处显式加白名单校验，不得默认放开。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ai.llm_client import LLMConfigError
from ai.skills import registry
from ai.skills.protocol import (
    Skill,
    SkillDisabledError,
    SkillInputError,
    SkillNotFoundError,
    SkillPermissionError,
)
from api import deps
from api.deps import (
    ROLE_ADMIN,
    get_current_user,
)
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillInvokeRequest(BaseModel):
    """统一调用入参：`payload` 是该 Skill 自己的 input_schema 所描述的对象。"""

    payload: dict = Field(default_factory=dict, description="符合该 Skill input_schema 的输入对象。")


def _require_skill_permission(skill: Skill, current_user: User) -> None:
    """按 Skill 的 `permissions` 声明做角色校验（admin 恒通过；声明为空则仅需登录）。"""
    allowed = tuple(skill.permissions or ())
    if not allowed:
        return
    if current_user.role == ROLE_ADMIN:
        return
    if current_user.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"当前角色（{current_user.role}）无权调用 Skill {skill.name!r}",
        )


def _get_skill_or_404(name: str) -> Skill:
    try:
        return registry.get(name)
    except SkillNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {name!r} 不存在（可用 GET /api/skills 查看全部 Skill）",
        )


@router.get("")
def list_skills(
    category: str | None = Query(None, description="按分类过滤（解析/分类/抽取/审核/检索/修订）。"),
    enabled_only: bool = Query(False, description="只返回 enabled=True 的 Skill。"),
    current_user: User = Depends(get_current_user),
):
    """枚举全部已注册 Skill 的名称、描述、分类与输入/输出契约。"""
    skills = registry.describe(enabled_only=enabled_only, category=category)
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "total": len(skills),
            "registered_total": registry.count(),
            "skills": skills,
        },
    }


@router.get("/{name}")
def get_skill(
    name: str,
    current_user: User = Depends(get_current_user),
):
    """查看单个 Skill 的完整描述（含 input_schema / output_schema / source / notes）。"""
    skill = _get_skill_or_404(name)
    return {"code": 0, "message": "ok", "data": skill.to_dict()}


@router.post("/{name}/invoke")
def invoke_skill(
    name: str,
    body: SkillInvokeRequest,
    current_user: User = Depends(get_current_user),
):
    """受控执行一个 Skill：404 / 403 / 409 / 400 / 502 各有明确语义。

    返回结构：`{code, message, data: {skill, result}}`。
    `result` 是底层真实函数的原始返回值，**未做任何包装或改写**。
    """
    skill = _get_skill_or_404(name)

    # 已注册但禁用 → 409（明确拒绝，不静默跳过）
    if not skill.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill {name!r} 已注册但处于禁用状态，当前不可调用",
        )

    # 权限：按 Skill 自己的 permissions 声明校验
    _require_skill_permission(skill, current_user)

    # LLM Key 前置检查：只对真正需要 LLM 的 Skill 生效（调用既有依赖函数，语义与 api/deps.py 一致）
    if skill.requires_llm:
        deps.require_llm_key_configured()

    try:
        result = registry.invoke(name, body.payload)
    except SkillNotFoundError:
        # 理论上不可达（上面已 404）；保留以防注册表在请求期间被改动
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill {name!r} 不存在")
    except SkillDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except SkillPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except SkillInputError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"输入不合法：{exc}")
    except LLMConfigError as exc:
        # 复用既有语义：未配置 Key 时给 400 + 明确指引（与 api/deps.py 一致）
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 —— 底层真实异常必须可见，不伪装成功
        logger.exception("Skill 执行失败: name=%s", name)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Skill {name!r} 执行失败：{type(exc).__name__}: {exc}",
        )

    # 只读留痕：记录"哪个用户调了哪个能力"，不记录 payload 内容（避免把合同正文写进日志）
    logger.info("Skill 调用成功: name=%s user_id=%s", name, current_user.id)

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "skill": name,
            "category": skill.category,
            "requires_llm": skill.requires_llm,
            "mutates_input": skill.mutates_input,
            "result": result,
        },
    }
