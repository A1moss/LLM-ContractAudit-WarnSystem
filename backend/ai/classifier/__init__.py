"""ai.classifier — 合同类型分类（法理维度）+ 业务标签（服务外包）

两条实现（**都是分类器**，不是"分类失败兜底"）：
    classify_by_rag     —— 生产主链路（`ai/classifier/rag_classifier.py`），
                           RAG 检索同类范本做 few-shot；检索无结果时降级零样本；
    classify_contract   —— 零样本分块投票（`ai/classifier/classifier.py`），
                           降级路径 + 官方分类评测基线（evaluate/evaluate_classifier.py）。

⚠️「无名合同」与「分类失败」是**两件事**（BUG-1 收口，不得混用）
------------------------------------------------------------------
* `contract_type == "无名合同"` 表示**模型真的判断**该合同不属于任何有名合同
  （民法典第467条意义上的无名合同，如战略合作框架协议、培训/养老/电商/医美服务等），
  它是 `ai.taxonomy.ENABLED_TYPES` 里的正式类别，置信度正常（实测 0.9+）；
* `contract_type is None` + `fallback=True` 表示**分类没有跑成功**
  （LLM 调用超时/限流/返回非法 JSON，且有界重试后仍失败）。
  此时**绝不返回任何看似正常的合同类型**：调用方（`api/contracts.py`）把合同落成
  「待分类」（`contract_type` 留空 + `classification_status="failed"`），
  前端显示「待分类」，不再显示成「无名合同」。

历史背景：`classify_*` 曾在失败时返回字符串 `"其他合同"`（一个**不在 taxonomy 里**的伪类型），
前端 `frontend/src/constants/contractTypes.js` 又把 `"其他合同"` / `"other"` 归一显示为
「无名合同」，于是"同一份合同一会儿是建设工程合同、一会儿是无名合同"——实际是**失败被伪装成了类别**。
现在失败态有独立语义，且前端不再把失败态画成任何法理类别。
"""
from .rag_classifier import classify_by_rag as classify_contract
from .classifier import CONTRACT_TYPES

__all__ = ["classify_contract", "CONTRACT_TYPES"]
