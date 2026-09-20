"""ai.router.registry — Expert Registry（声明式）。

职责
----
登记"有哪些专家、每个专家由哪些能力组成、负责哪些任务类型"。

与 Skill Registry 的关系（单向、无循环）
------------------------------------
    Skill Registry  → 定义"系统有什么能力"
    Expert Registry → 定义"哪些能力组成专家"（只读 Skill Registry 做校验）
    Expert Router   → 定义"当前任务该找哪个专家"

**依赖方向严格单向**：本模块只 import `ai.skills.registry` 做"能力是否真实存在"的校验，
Skill 层**不** import 本模块（无循环依赖）。本层也**不** import
`ai.rag` / `ai.auditor` / `ai.reviser` / `ai.matcher` 等业务实现 —— 路由层只负责路由。
"""
from __future__ import annotations

import logging

from ai.router.protocol import (
    REASON_DISABLED,
    REASON_NO_ENABLED_EXPERT,
    REASON_NO_EXPERT_FOR_TASK,
    REASON_UNKNOWN_TASK_TYPE,
    STATUS_ROUTED,
    STATUS_UNROUTABLE,
    TASK_CLAUSE_REVISION,
    TASK_CONTRACT_ANALYSIS,
    TASK_LEGAL_RETRIEVAL,
    KNOWN_TASK_TYPES,
    ExpertDescriptor,
    ExpertRegistrationError,
    RouteDecision,
    TaskDescriptor,
)
from ai.skills import registry as skill_registry

logger = logging.getLogger(__name__)


class ExpertRegistry:
    """进程内 Expert 注册表（声明式，无 if/else 分派）。"""

    def __init__(self) -> None:
        self._experts: dict[str, ExpertDescriptor] = {}

    # ── 注册 ──
    def register(self, expert: ExpertDescriptor, *, replace: bool = False) -> ExpertDescriptor:
        if not expert.name or not expert.name.strip():
            raise ExpertRegistrationError("ExpertDescriptor.name 不能为空")
        if expert.name in self._experts and not replace:
            raise ExpertRegistrationError(f"Expert {expert.name!r} 已注册（如需覆盖请显式 replace=True）")
        if not expert.capabilities:
            raise ExpertRegistrationError(f"Expert {expert.name!r} 必须至少声明一个能力")
        if not expert.task_types:
            raise ExpertRegistrationError(f"Expert {expert.name!r} 必须至少声明一个 task_type")

        # 硬约束①：能力必须是**已注册的真实 Skill**（不允许凭空声明能力）
        for capability in expert.capabilities:
            if not skill_registry.has(capability):
                raise ExpertRegistrationError(
                    f"Expert {expert.name!r} 声明的能力 {capability!r} 不是已注册的 Skill —— "
                    f"Expert 只能聚合真实存在的能力"
                )
        # 硬约束②：task_type 必须是本阶段已知的任务类型（避免拼写错误静默失配）
        unknown = [t for t in expert.task_types if t not in KNOWN_TASK_TYPES]
        if unknown:
            raise ExpertRegistrationError(
                f"Expert {expert.name!r} 声明了未知 task_type：{unknown}（已知：{list(KNOWN_TASK_TYPES)}）"
            )

        self._experts[expert.name] = expert
        return expert

    # ── 查询 ──
    def has(self, name: str) -> bool:
        return name in self._experts

    def get(self, name: str) -> ExpertDescriptor | None:
        return self._experts.get(name)

    def names(self) -> list[str]:
        return list(self._experts)

    def list_experts(self, *, enabled_only: bool = False,
                     task_type: str | None = None) -> list[ExpertDescriptor]:
        experts = list(self._experts.values())
        if enabled_only:
            experts = [e for e in experts if e.enabled]
        if task_type is not None:
            experts = [e for e in experts if e.supports_task(task_type)]
        return experts

    def describe(self, *, enabled_only: bool = False, task_type: str | None = None) -> list[dict]:
        return [e.to_dict() for e in self.list_experts(enabled_only=enabled_only, task_type=task_type)]

    def candidates_for(self, task_type: str, *, enabled_only: bool = True) -> list[ExpertDescriptor]:
        """命中该任务类型的 Expert，按**确定性顺序**排列。

        排序键：`(-priority, 注册顺序)` —— 优先级高者优先；同优先级按注册先后。
        因此"相同输入 + 相同注册状态 ⇒ 相同结果"（无随机、无时间、无外部状态）。
        """
        order = {name: i for i, name in enumerate(self._experts)}
        hits = [e for e in self._experts.values() if e.supports_task(task_type)]
        if enabled_only:
            hits = [e for e in hits if e.enabled]
        return sorted(hits, key=lambda e: (-e.priority, order[e.name]))

    def reset(self) -> None:
        """清空（**仅测试使用**）。"""
        self._experts.clear()


# 进程内单例
expert_registry = ExpertRegistry()


# ══════════════════════════════════════════════════════════════════════
# 本阶段三个 Expert（能力划分以 Skill 层真实 category 为依据）
# ══════════════════════════════════════════════════════════════════════

def register_experts() -> None:
    """登记三个 Expert（声明式；能力真实性由 registry.register 校验）。"""

    # ① 法规检索专家：把"查法条"与"查范本"两条检索路径收成一个专家
    expert_registry.register(ExpertDescriptor(
        name="legal_retrieval_expert",
        description="法规检索专家：为合同问题检索法条与同类范本依据（检索类能力）。",
        capabilities=("retrieve_knowledge", "retrieve_templates"),
        task_types=(TASK_LEGAL_RETRIEVAL,),
        priority=100,
        tags=("rag", "no-llm"),
        agent="law_agent",
    ))

    # ② 合同分析专家：抽取 → 规则 → 证据 → 确定性裁决（**不复制 _run_audit**，只做最小能力链）
    expert_registry.register(ExpertDescriptor(
        name="contract_analysis_expert",
        description="合同分析专家：对合同文本做要素抽取、规则初筛、证据抽取与确定性风险裁决。",
        capabilities=("extract_elements", "rule_scan", "extract_evidence", "adjudicate_risks"),
        task_types=(TASK_CONTRACT_ANALYSIS,),
        priority=100,
        tags=("analysis", "deterministic-adjudication"),
    ))

    # ③ 条款修订专家：替换式修订 + 新增条款起草
    expert_registry.register(ExpertDescriptor(
        name="clause_revision_expert",
        description="条款修订专家：按指令修订既有条款，或起草新增条款。",
        capabilities=("revise_clause", "draft_clause"),
        task_types=(TASK_CLAUSE_REVISION,),
        priority=100,
        tags=("revision", "llm"),
    ))


__all__ = [
    "ExpertRegistry",
    "expert_registry",
    "register_experts",
]
