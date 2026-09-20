"""ai.skills.protocol — Skill 描述协议（省赛 Skill Registry 的地基）。

定位
----
本模块**只定义"一个能力长什么样"**，不包含任何业务逻辑，也不调用任何 LLM。

设计原则（与仓库既有风格保持一致）
----------------------------------
1. **零重写**：Skill 不重新实现业务，只描述并转发到 `ai/` 下已存在的真实函数
   （见 `ai/skills/adapters.py`）。因此本模块刻意保持极薄。
2. **描述性优先于约束性**：`output_schema` 是**描述**而非校验器 —— 现有 12 个能力的
   返回键就是事实 schema，登记时逐字抄录，不新增字段、不改返回结构。
3. **不引入抽象基类**：与仓库现状一致（全仓无 ABC / Protocol 使用），
   统一入口通过 `Skill.__call__` 提供，适配器用普适的 `handler(input) -> output` 闭包。
4. **schema 方言**：刻意采用 JSON-Schema **子集**（`type` / `properties` / `required` /
   `items` / `enum`），与 workflow 工具链、OpenAPI 生成器天然兼容，但解析器只有
   本模块这 40 行，不引入 `jsonschema` 依赖。

命名说明
--------
`api/skills.py` 暴露的 `/api/skills/{name}` 里 `name` 指的是**技能名**（如
`rule_scan`），与仓库既有的 `api/contracts.py` 中"合同名"字段无关，不要混淆。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

# ── 分类常量（供前端分组展示；不加新分类，逐个 Skill 从这 5 类里选）──
CATEGORY_PARSE = "解析"
CATEGORY_CLASSIFY = "分类"
CATEGORY_EXTRACT = "抽取"
CATEGORY_AUDIT = "审核"
CATEGORY_RETRIEVE = "检索"
CATEGORY_REVISE = "修订"

CATEGORIES = (
    CATEGORY_PARSE,
    CATEGORY_CLASSIFY,
    CATEGORY_EXTRACT,
    CATEGORY_AUDIT,
    CATEGORY_RETRIEVE,
    CATEGORY_REVISE,
)

# ── schema 类型标记 → Python 类型 ──
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
    "null": (type(None),),
}


class SkillError(Exception):
    """Skill 层基类异常。适配器/Registry 抛出，由 API 层翻译为 HTTP 状态码。"""


class SkillNotFoundError(SkillError):
    """请求的 Skill 未注册。"""


class SkillDisabledError(SkillError):
    """Skill 已注册但 `enabled=False`，当前不可调用。"""


class SkillAlreadyRegisteredError(SkillError):
    """同名 Skill 重复注册（显式报错，绝不静默覆盖 —— 静默覆盖会让"注册了几条"失去可信度）。"""


class SkillInputError(SkillError):
    """调用输入不满足 `input_schema`（缺必填字段 / 类型不符 / 枚举越界）。"""


class SkillPermissionError(SkillError):
    """当前调用者不满足该 Skill 的 `permissions`。"""


def validate_against_schema(value: Any, schema: Mapping[str, Any], *, path: str = "input") -> None:
    """按 schema **子集**校验一个值；不通过则抛 `SkillInputError`。

    支持的关键字：`type` / `properties` / `required` / `items` / `enum`。
    未识别的关键字一律忽略（保持宽松，避免把描述性 schema 变成硬约束）。

    约定：`required` 只要求 **键存在且值不为 None**。既有能力的可选字段大量使用
    `None` 表达"未提供"（如 `contract_type=None`、`rag_context=None`），
    因此把 `None` 视为"未提供"更贴合本仓库语义。
    """
    if not isinstance(schema, Mapping):
        return

    expected = schema.get("type")
    if isinstance(expected, str):
        py_types = _TYPE_MAP.get(expected)
        if py_types is not None:
            ok = isinstance(value, py_types)
            # bool 是 int 的子类：`"type": "integer"` 不应接受 True/False
            if ok and expected in ("integer", "number") and isinstance(value, bool):
                ok = False
            if not ok:
                raise SkillInputError(
                    f"{path}：期望 {expected}，实际 {type(value).__name__}"
                )
        if value is None:
            return  # Optional 语义：None 跳过后续结构校验

    enum = schema.get("enum")
    if isinstance(enum, (list, tuple)) and enum and value not in enum:
        raise SkillInputError(f"{path}：取值 {value!r} 不在允许集合 {list(enum)!r} 内")

    if expected == "object" or isinstance(schema.get("properties"), Mapping):
        if isinstance(value, Mapping):
            for key in schema.get("required") or ():
                if key not in value or value.get(key) is None:
                    raise SkillInputError(f"{path}：缺少必填字段 {key!r}")
            properties = schema.get("properties")
            if isinstance(properties, Mapping):
                for key, sub_schema in properties.items():
                    if key in value and value.get(key) is not None:
                        validate_against_schema(value[key], sub_schema, path=f"{path}.{key}")

    if expected == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                validate_against_schema(item, item_schema, path=f"{path}[{index}]")


def coerce_int(
    value: Any,
    *,
    default: int,
    minimum: int | None = None,
    maximum: int | None = None,
    field: str = "value",
) -> int:
    """把可选输入稳妥转成 int（供适配器屏蔽可变参数用）。

    非法值抛 `SkillInputError` 而不是静默用默认值 —— 静默会让"传了参数却没生效"
    变成难以排查的问题。
    """
    if value is None:
        result = default
    else:
        if isinstance(value, bool):
            raise SkillInputError(f"{field}：需要整数，收到布尔值")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise SkillInputError(f"{field}：需要整数，收到 {value!r}") from exc
    if minimum is not None and result < minimum:
        raise SkillInputError(f"{field}：需 >= {minimum}，收到 {result}")
    if maximum is not None and result > maximum:
        raise SkillInputError(f"{field}：需 <= {maximum}，收到 {result}")
    return result


@dataclass
class Skill:
    """一个可发现、可枚举、可调用的能力单元。

    基础字段（本轮全部实现并对外暴露）
    ----------------------------------
    name / description / category / input_schema / output_schema /
    handler / enabled / mutates_input / requires_llm

    扩展字段（**本轮只作占位，不实现复杂逻辑**，见省赛规划"保留架构扩展位"）
    --------------------------------------------------------------------
    version / dependencies / permissions / timeout / tags
    * `permissions` 例外：本轮**真的生效**（API 层据此做角色校验），
      因为"统一 invoke 不得绕过现有权限"是硬性安全要求。
    """

    name: str
    description: str
    category: str
    input_schema: dict
    output_schema: dict
    handler: Callable[[dict], Any]
    enabled: bool = True
    mutates_input: bool = False
    requires_llm: bool = False

    # ── 扩展位（本轮不实现其管理逻辑）──
    version: str = "0.1.0"
    dependencies: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    timeout: float | None = None
    tags: tuple[str, ...] = ()
    source: str = ""
    """底层真实函数位置（`module.function`），供审计"Skill ≠ 重新实现"这一事实。"""

    notes: str = ""
    """补充说明（例如 advisory-only、原地修改、法域限制等已知边界）。"""

    def __call__(self, payload: Mapping[str, Any] | None = None) -> Any:
        """统一调用入口：校验输入 → 调用适配器 → 返回底层真实结果。

        刻意**不做返回值转换**，保证 Skill 输出与现有函数输出逐字一致。
        """
        data = dict(payload or {})
        validate_against_schema(data, self.input_schema)
        return self.handler(data)

    def invoke(self, payload: Mapping[str, Any] | None = None) -> Any:
        """`__call__` 的显式别名（可读性更好；语义完全相同）。"""
        return self(payload)

    def to_dict(self) -> dict:
        """对外描述（GET /api/skills 的单项结构）。

        `handler` 不参与序列化 —— 它不可 JSON 化，也不应暴露给调用方。
        """
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "enabled": self.enabled,
            "mutates_input": self.mutates_input,
            "requires_llm": self.requires_llm,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "version": self.version,
            "tags": list(self.tags),
            "source": self.source,
            "notes": self.notes,
        }


def make_skill(
    *,
    name: str,
    description: str,
    category: str,
    source: str,
    input_schema: dict,
    output_schema: dict,
    handler: Callable[[dict], Any],
    enabled: bool = True,
    mutates_input: bool = False,
    requires_llm: bool = False,
    permissions: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    notes: str = "",
) -> Skill:
    """构造 Skill 的便捷函数（让 adapters.py 保持声明式、一屏一个能力）。"""
    return Skill(
        name=name,
        description=description,
        category=category,
        input_schema=input_schema,
        output_schema=output_schema,
        handler=handler,
        enabled=enabled,
        mutates_input=mutates_input,
        requires_llm=requires_llm,
        permissions=permissions,
        tags=tags,
        notes=notes,
        source=source,
    )


__all__ = [
    "CATEGORIES",
    "CATEGORY_AUDIT",
    "CATEGORY_CLASSIFY",
    "CATEGORY_EXTRACT",
    "CATEGORY_PARSE",
    "CATEGORY_RETRIEVE",
    "CATEGORY_REVISE",
    "Skill",
    "SkillAlreadyRegisteredError",
    "SkillDisabledError",
    "SkillError",
    "SkillInputError",
    "SkillNotFoundError",
    "SkillPermissionError",
    "coerce_int",
    "make_skill",
    "validate_against_schema",
]
