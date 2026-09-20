"""ai.a2a.registry — Agent Registry（最小）。

职责
----
登记"有哪些 Agent、它们各自声明了什么能力"，并提供按名/按能力的查询。
**不是** Multi-Agent 的编排器：没有 planner、没有 task graph、没有调度循环。

与 Skill Registry 的强制绑定
---------------------------
注册时**立即校验**：`AgentCard.capabilities` 中每一项都必须是
`ai.skills.registry` 里已注册的 Skill 名。这是"能力必须真实存在"的硬约束 ——
既防止写一堆尚未实现的能力，也防止 `capability` 被当成可动态 import 的 Python 路径。
"""
from __future__ import annotations

import logging

from ai.a2a.protocol import AgentCard
from ai.skills import registry as skill_registry

logger = logging.getLogger(__name__)

# 本阶段允许的 Agent（第三阶段：3 个 Agent，不做更多）
AUDIT_AGENT = "audit_agent"
LAW_AGENT = "law_agent"
REVIEWER_AGENT = "reviewer_agent"


class AgentRegistrationError(RuntimeError):
    """Agent 注册失败（重名、能力未落地为真实 Skill、字段非法）。"""


class _AgentRegistry:
    """进程内 Agent 注册表（声明式，无 if/else 分派）。"""

    def __init__(self) -> None:
        self._cards: dict[str, AgentCard] = {}

    # ── 注册 ──
    def register(self, card: AgentCard, *, owner: type | None = None, replace: bool = False) -> AgentCard:
        """登记一个 Agent Card。

        Args:
            card: Agent 名片。
            owner: `implementation="method"` 时的宿主类，用于 `hasattr` 校验能力真实存在。
            replace: 允许覆盖同名 Agent（默认不允许）。
        """
        if not card.name or not card.name.strip():
            raise AgentRegistrationError("AgentCard.name 不能为空")
        if card.name in self._cards and not replace:
            raise AgentRegistrationError(f"Agent {card.name!r} 已注册")

        if not card.capabilities:
            raise AgentRegistrationError(f"Agent {card.name!r} 必须至少声明一个能力")

        # 硬约束①：skill-backed 的能力必须是**已注册的真实 Skill**
        if card.implementation == "skill":
            for capability in card.capabilities:
                if not skill_registry.has(capability):
                    raise AgentRegistrationError(
                        f"Agent {card.name!r} 声明的能力 {capability!r} 不是已注册的 Skill —— "
                        f"能力必须对应真实存在的 Skill，不得声明尚未实现的能力"
                    )
        # 硬约束②：method 型的能力必须是宿主类上**真实存在的方法**
        elif card.implementation == "method":
            if owner is None:
                raise AgentRegistrationError(
                    f"Agent {card.name!r} 的 implementation='method'，注册时必须提供 owner 类以校验能力真实性"
                )
            missing = [c for c in card.capabilities if not callable(getattr(owner, c, None))]
            if missing:
                raise AgentRegistrationError(
                    f"Agent {card.name!r} 声明的方法型能力在 {owner.__name__} 上不存在或不可调用：{missing}"
                )
        else:
            raise AgentRegistrationError(
                f"Agent {card.name!r} 的 implementation 必须是 'skill' 或 'method'，收到 {card.implementation!r}"
            )

        self._cards[card.name] = card
        return card

    # ── 查询 ──
    def has(self, name: str) -> bool:
        return name in self._cards

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)

    def names(self) -> list[str]:
        return list(self._cards)

    def list_cards(self, *, capability: str | None = None) -> list[AgentCard]:
        """枚举 Agent Card（可按 capability 过滤）。"""
        cards = list(self._cards.values())
        if capability is not None:
            cards = [c for c in cards if capability in c.capabilities]
        return cards

    def describe(self, *, capability: str | None = None) -> list[dict]:
        """枚举的 JSON 形态（GET /api/a2a/agents 直接返回它）。"""
        return [c.to_dict() for c in self.list_cards(capability=capability)]

    def find_by_capability(self, capability: str) -> list[AgentCard]:
        """哪些 Agent 声明了该能力（可能多个；本阶段各能力只有一个宿主）。"""
        return [c for c in self._cards.values() if capability in c.capabilities]

    def reset(self) -> None:
        """清空（**仅测试使用**）。"""
        self._cards.clear()


# 进程内单例：Agent 的"名片簿"
agent_registry = _AgentRegistry()


__all__ = [
    "AUDIT_AGENT",
    "LAW_AGENT",
    "REVIEWER_AGENT",
    "AgentRegistrationError",
    "agent_registry",
]
