from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, utcnow_naive


class FeedbackExperience(Base):
    """Feedback Experience 学习层：**只保存经人工批准、允许进入学习的经验**。

    与 FeedbackLog 的关系（不是第二套反馈系统）：
    - 唯一来源是 `feedback_logs`（`source_feedback_id` 强约束外键）；
    - **没有独立录入入口**，只能由「批准」动作派生；
    - 撤销/重建全部回到同一条反馈做状态判断。
    它等价于"知识库的物化视图"，而不是平行系统。

    检索可见性：`ai/rag/feedback_store.py` **只加载 status == 'active' 的行**。
    因此 `status` 是"能否进入 Feedback RAG"的唯一开关：
        active   → 可被检索
        revoked  → 从检索中排除（并精确删除 Chroma 文档）

    第一阶段只使用 `kind == 'risk_judgement'`；`clause_revision` /
    `comparison_confirmation` 的字段（clause_title / ai_final_clause /
    human_final_clause）已预留但尚未启用。
    """

    __tablename__ = "feedback_experiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 溯源：这条经验从哪条反馈来（"这条经验为什么进入知识库"的唯一答案）
    source_feedback_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("feedback_logs.id"), nullable=False, index=True, unique=True
    )
    # risk_judgement（第一阶段）| clause_revision（预留）| comparison_confirmation（预留）
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="risk_judgement")
    # active | revoked
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active", index=True
    )

    # ── 检索 / 过滤键（写入 Chroma metadata，必须是标量）──
    contract_type: Mapped[str] = mapped_column(String(50), nullable=True, default=None, index=True)
    risk_type: Mapped[str] = mapped_column(String(10), nullable=True, default=None, index=True)
    human_label: Mapped[str] = mapped_column(String(20), nullable=False)  # confirmed | false_positive
    feedback_reason: Mapped[str] = mapped_column(String(30), nullable=True, default=None)
    clause_title: Mapped[str] = mapped_column(String(200), nullable=True, default=None)  # 预留（比对类）

    # ── 语料正文 ──
    # query_text：用于命中"相似历史情况"（条款原文/风险涉条款）
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    # experience_text：给 LLM 看的经验摘要 —— **只允许写"应重点核查什么"**，
    # 绝不允许写"本次应该判定什么风险"（构造规则见 services/feedback_experience.py）
    experience_text: Mapped[str] = mapped_column(Text, nullable=False)

    # ── 原始上下文（防 AuditRecord 被 superseded / 合同被删除后经验失去上下文）──
    original_text: Mapped[str] = mapped_column(Text, nullable=True, default=None)   # 锚点逐字原文
    contract_name: Mapped[str] = mapped_column(String(500), nullable=True, default=None)
    model_evidence: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    model_result: Mapped[dict] = mapped_column(JSON, nullable=True, default=None)
    human_comment: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    # 预留：条款修改经验
    ai_final_clause: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    human_final_clause: Mapped[str] = mapped_column(Text, nullable=True, default=None)

    # ── 版本与索引同步 ──
    approved_by: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    revoked_by: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    revoked_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, default=None)
    revoke_reason: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    collection_name: Mapped[str] = mapped_column(String(50), nullable=True, default=None)
    # Chroma 文档 id：撤销时据此**精确删除**（不依赖文档内容反查）
    doc_id: Mapped[str] = mapped_column(String(120), nullable=True, default=None, index=True)
    # 入库时的经验库版本（回答"这次审核用的是哪一版经验库"）
    index_version: Mapped[str] = mapped_column(String(64), nullable=True, default=None)
