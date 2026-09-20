"""ai.skills — 合同 AI 能力层（Skill Registry）。

省赛第一阶段成果：把系统**已经存在**的 12 个稳定能力统一登记、枚举、调用，
形成真实可运行的 Skill 能力层，作为后续 Multi-Agent / A2A / Expert Router 的能力底座。

本轮边界（务必如实对外表述）
--------------------------
* 只做 Skill 层。**没有**实现 Multi-Agent、A2A、MoE / Expert Router、欧盟法域。
* 12 个 Skill 全部是**适配器转发**到现有真实函数（见 `adapters.py` 的 `source` 字段），
  **未修改任何现有函数的函数体 / 签名 / 返回结构**。
* 主审核链路 `_run_audit` 未改动，Skill 层是**旁路暴露**，不参与生产流水线。

用法
----
    from ai.skills import registry

    registry.count()                 # -> 12
    registry.names()                 # -> ['parse_document', 'classify_contract', ...]
    registry.get("rule_scan")        # -> Skill 描述对象
    registry.describe()              # -> 可直接 JSON 化的清单（GET /api/skills）
    registry.invoke("rule_scan", {"text": "..."})   # 按名调用（校验 + 转发）

导入副作用说明
-------------
导入本包会执行 `adapters`，从而把 12 个 Skill 注册进 registry（声明式注册）。
`import ai.skills` 本身是轻量的：适配器内部对 openai / chromadb / torch 等
重依赖一律**延迟导入**，因此 `main.py` 在启动链上导入它不会拖慢冷启动。
"""
from ai.skills.protocol import (
    CATEGORIES,
    Skill,
    SkillAlreadyRegisteredError,
    SkillDisabledError,
    SkillError,
    SkillInputError,
    SkillNotFoundError,
    SkillPermissionError,
    make_skill,
    validate_against_schema,
)
from ai.skills import registry  # noqa: F401  （对外暴露注册表对象）
from ai.skills.registry import (  # noqa: F401
    count,
    describe,
    get,
    has,
    invoke,
    list_skills,
    names,
    register,
)

# 触发声明式注册（副作用导入：adapters 模块内逐个 register(...)）
from ai.skills import adapters  # noqa: F401,E402

__all__ = [
    "CATEGORIES",
    "Skill",
    "SkillAlreadyRegisteredError",
    "SkillDisabledError",
    "SkillError",
    "SkillInputError",
    "SkillNotFoundError",
    "SkillPermissionError",
    "adapters",
    "count",
    "describe",
    "get",
    "has",
    "invoke",
    "list_skills",
    "make_skill",
    "names",
    "register",
    "registry",
    "validate_against_schema",
]
