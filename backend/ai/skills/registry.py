"""ai.skills.registry — 声明式 Skill 注册表。

定位
----
一个**真正的注册表**：支持 `register()` / `get()` / `list()`，可枚举、可按名查找、
可重复注册保护。**没有任何 if/else 分派** —— 分派由查表完成。

为什么照抄 `ai/taxonomy.py` 的范式
--------------------------------
`taxonomy.py` 已在本仓库验证了"声明式登记表 + enabled 开关 + 枚举函数 + to_dict 暴露"
这一整套模式（其模块 docstring 自述："加新类别 = 在本模块登记 + 补该类数据，
代码逻辑零改动"）。Skill 注册表采用同一范式，因此不引入新的架构概念。

与 taxonomy 的一个刻意差异
--------------------------
`taxonomy` 的重复登记是静默覆盖（列表里出现两条同名项时后者生效）；
Skill 注册表**显式报错**（`SkillAlreadyRegisteredError`）。理由：`len(registry.list()) == 12`
是本轮验收标准之一，静默覆盖会让这个数字失去可信度。
"""
from __future__ import annotations

from typing import Iterator

from ai.skills.protocol import (
    Skill,
    SkillAlreadyRegisteredError,
    SkillDisabledError,
    SkillNotFoundError,
)

# 内部用 dict 存储 —— 自 Python 3.7 起 dict 保序，因此 `list()` 的顺序
# 就是 `register()` 的声明顺序（adapters.py 里按业务链路顺序登记）。
_skills: dict[str, Skill] = {}


def register(skill: Skill, *, replace: bool = False) -> Skill:
    """登记一个 Skill。

    Args:
        skill: 待登记的 Skill 描述。
        replace: 显式为 True 时允许覆盖同名 Skill；默认 False，重复即报错。

    Returns:
        登记后的 Skill 本身（便于 `SKILL = register(make_skill(...))` 链式书写）。
    """
    name = getattr(skill, "name", "")
    if not name:
        raise ValueError("Skill.name 不能为空")
    if name in _skills and not replace:
        raise SkillAlreadyRegisteredError(
            f"Skill {name!r} 已注册（如需覆盖请显式 replace=True）"
        )
    _skills[name] = skill
    return skill


def unregister(name: str) -> Skill:
    """移除一个 Skill（仅测试使用，生产不调用）。"""
    if name not in _skills:
        raise SkillNotFoundError(f"Skill {name!r} 未注册")
    return _skills.pop(name)


def get(name: str) -> Skill:
    """按名取 Skill；不存在抛 `SkillNotFoundError`。"""
    skill = _skills.get(name)
    if skill is None:
        raise SkillNotFoundError(f"Skill {name!r} 未注册")
    return skill


def has(name: str) -> bool:
    """该名是否已注册（不抛异常）。"""
    return name in _skills


def list_skills(*, enabled_only: bool = False, category: str | None = None) -> list[Skill]:
    """枚举已登记 Skill（按声明顺序）。

    Args:
        enabled_only: 只返回 `enabled=True` 的 Skill。
        category: 只返回指定分类的 Skill。
    """
    result = list(_skills.values())
    if enabled_only:
        result = [s for s in result if s.enabled]
    if category is not None:
        result = [s for s in result if s.category == category]
    return result


def names(*, enabled_only: bool = False) -> list[str]:
    """枚举 Skill 名（供冒烟脚本与测试做精确断言）。"""
    return [s.name for s in list_skills(enabled_only=enabled_only)]


def count() -> int:
    """已登记 Skill 总数（本轮验收标准 `list() == 12`）。"""
    return len(_skills)


def describe(*, enabled_only: bool = False, category: str | None = None) -> list[dict]:
    """枚举的 JSON 形态（GET /api/skills 直接返回它）。"""
    return [
        s.to_dict()
        for s in list_skills(enabled_only=enabled_only, category=category)
    ]


def invoke(name: str, payload: dict | None = None):
    """按名调用 Skill：查找 → 检查 enabled → 输入校验 → 转发底层真实函数。

    异常语义（由 `api/skills.py` 翻译为 HTTP 状态码）：
    * `SkillNotFoundError`  → 404
    * `SkillDisabledError`  → 409
    * `SkillInputError`     → 400
    * 其余异常原样向上传播（不吞、不包装），保证"底层真实报错"可见。
    """
    skill = get(name)
    if not skill.enabled:
        raise SkillDisabledError(f"Skill {name!r} 已注册但处于禁用状态，当前不可调用")
    return skill.invoke(payload)


def reset() -> None:
    """清空注册表（**仅测试使用**；生产代码不得调用）。"""
    _skills.clear()


def _iter_skills() -> Iterator[Skill]:
    return iter(_skills.values())


__all__ = [
    "count",
    "describe",
    "get",
    "has",
    "invoke",
    "list_skills",
    "names",
    "register",
    "reset",
    "unregister",
]
