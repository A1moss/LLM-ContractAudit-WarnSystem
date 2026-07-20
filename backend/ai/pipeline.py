"""
ai.pipeline — 合同审核端到端流水线

完整链路：合同上传 → 解析 → 分类 → 要素抽取 → RAG检索 → 规则审核 → LLM审核 → 条款比对 → 报告生成

用法:
    from ai.pipeline import run_pipeline
    result = run_pipeline("path/to/contract.docx", audit_mode="precise")
"""
import os
import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

PIPELINE_STEPS = [
    "parse",
    "classify",
    "extract",
    "rag_retrieve",
    "rule_audit",
    "llm_audit",
    "matcher_compare",
    "generate_report",
]


def run_pipeline(
    file_path: str,
    contract_type_hint: str | None = None,
    audit_mode: str = "precise",
) -> dict[str, Any]:
    """运行完整的合同审核流水线。

    Args:
        file_path: 合同文件路径 (.pdf / .docx)
        contract_type_hint: 合同类型提示（可选，跳过分类）
        audit_mode: "fast"（仅规则）| "precise"（规则+RAG+LLM+比对）

    Returns:
        {
            "status": "success" | "partial",
            "steps": {"parse": {...}, "classify": {...}, ...},
            "report_html": "...",
            "summary": {"risk_score": int, "high": int, "medium": int, "low": int, "coverage_rate": float},
            "timing": {"total_s": float, "steps": {...}},
        }
    """
    t_start = datetime.now()
    steps: dict[str, dict] = {}
    step_times: dict[str, float] = {}

    # ── Step 0: 确保 ChromaDB 可用 ──
    _step_start = datetime.now()
    try:
        from ai.rag import init_chroma
        from ai.rag.vector_store import CHROMA_DIR
        chroma_exists = os.path.exists(os.path.join(CHROMA_DIR, "chroma.sqlite3"))
        if not chroma_exists:
            init_chroma()
            steps["chroma_init"] = {"status": "initialized", "note": "首次初始化 ChromaDB 知识库"}
        else:
            steps["chroma_init"] = {"status": "ready", "note": "ChromaDB 知识库已就绪"}
    except Exception as e:
        steps["chroma_init"] = {"status": "error", "error": str(e)}
    step_times["chroma_init"] = (datetime.now() - _step_start).total_seconds()

    # ── Step 1: 解析 ──
    _step_start = datetime.now()
    try:
        from ai.parser import detect_and_parse
        parsed = detect_and_parse(file_path)
        full_text = parsed.get("full_text", "")
        word_count = len(full_text)
        steps["parse"] = {
            "status": "ok",
            "format": parsed.get("format", "unknown"),
            "word_count": word_count,
            "paragraphs_count": len(parsed.get("paragraphs", [])),
        }
    except Exception as e:
        steps["parse"] = {"status": "error", "error": str(e)}
        return _fail("parse", steps, step_times, t_start)
    step_times["parse"] = (datetime.now() - _step_start).total_seconds()

    if not full_text.strip():
        steps["parse"]["status"] = "error"
        steps["parse"]["error"] = "合同文本为空"
        return _fail("parse", steps, step_times, t_start)

    # ── Step 2: 分类 ──
    _step_start = datetime.now()
    contract_type = contract_type_hint
    type_confidence = 1.0
    if contract_type_hint:
        steps["classify"] = {
            "status": "ok",
            "contract_type": contract_type_hint,
            "confidence": 1.0,
            "method": "user_hint",
            "fallback": False,
        }
    else:
        try:
            from ai.classifier import classify_contract
            cls_result = classify_contract(full_text)
            contract_type = cls_result.get("contract_type", "其他合同")
            type_confidence = cls_result.get("confidence", 0.0)
            steps["classify"] = {
                "status": "ok",
                "contract_type": contract_type,
                "confidence": type_confidence,
                "method": cls_result.get("method", "llm"),
                "fallback": cls_result.get("fallback", False),
            }
        except Exception as e:
            contract_type = "其他合同"
            type_confidence = 0.0
            steps["classify"] = {
                "status": "warning",
                "contract_type": contract_type,
                "confidence": 0.0,
                "method": "fallback",
                "error": str(e),
            }
    step_times["classify"] = (datetime.now() - _step_start).total_seconds()

    # ── Step 3: 要素抽取 ──
    _step_start = datetime.now()
    try:
        from ai.extractor import extract_elements
        elements = extract_elements(full_text, contract_type)
        steps["extract"] = {
            "status": "ok",
            "has_parties": bool(elements.get("parties")),
            "has_amount": bool(elements.get("amount")),
            "has_sign_date": bool(elements.get("sign_date")),
            "fallback": elements.get("fallback", False),
        }
    except Exception as e:
        elements = {}
        steps["extract"] = {"status": "warning", "error": str(e)}
    step_times["extract"] = (datetime.now() - _step_start).total_seconds()

    # ── Step 4: RAG 检索 ──
    _step_start = datetime.now()
    rag_context = None
    try:
        from ai.rag import retrieve_for_audit
        rag_context = retrieve_for_audit(full_text, top_k=5)
        steps["rag_retrieve"] = {
            "status": "ok",
            "results_count": len(rag_context),
            "top_scores": [r.get("score", 0) for r in rag_context[:3]],
        }
    except Exception as e:
        steps["rag_retrieve"] = {"status": "warning", "error": str(e)}
    step_times["rag_retrieve"] = (datetime.now() - _step_start).total_seconds()

    # ── Step 5: 规则引擎审核 ──
    _step_start = datetime.now()
    try:
        from ai.auditor import run_rules
        rule_results = run_rules(full_text)
        steps["rule_audit"] = {
            "status": "ok",
            "findings": len(rule_results),
            "high": sum(1 for r in rule_results if r.get("level") == "high"),
            "medium": sum(1 for r in rule_results if r.get("level") == "medium"),
            "low": sum(1 for r in rule_results if r.get("level") == "low"),
        }
    except Exception as e:
        rule_results = []
        steps["rule_audit"] = {"status": "error", "error": str(e)}
    step_times["rule_audit"] = (datetime.now() - _step_start).total_seconds()

    all_risks = list(rule_results)

    # ── Step 5.5: 风险卡片匹配（规则检出 → 风险卡片库 RAG 丰富）──
    _step_start = datetime.now()
    enriched_count = 0
    try:
        from ai.rag import search_risk_cards
        for r in all_risks:
            risk_code = r.get("risk_type", "")
            if risk_code and risk_code.startswith("R"):
                cards = search_risk_cards(r.get("clause_text", ""), risk_code=risk_code, top_k=1)
                if cards:
                    card = cards[0]
                    r["card_id"] = card.get("title", "")
                    r["card_detection"] = card.get("detection", "")
                    r["card_law_basis"] = card.get("law_basis", "")
                    r["card_suggestion"] = card.get("suggestion", "")
                    if card.get("law_basis"):
                        r["reason"] = f"{r.get('reason', '')}（风险卡片匹配：{card.get('title', '')}）"
                    enriched_count += 1
        steps["risk_card_match"] = {"status": "ok", "enriched": enriched_count, "total": len(all_risks)}
    except Exception as e:
        steps["risk_card_match"] = {"status": "warning", "error": str(e)}
    step_times["risk_card_match"] = (datetime.now() - _step_start).total_seconds()

    # ── Step 6: LLM 审核（仅 precise 模式）──
    _step_start = datetime.now()
    if audit_mode == "precise":
        try:
            from ai.auditor import audit_with_llm
            llm_results = audit_with_llm(full_text, rag_context if rag_context else None)
            for r in llm_results:
                r["detection_method"] = "rag"
            all_risks.extend(llm_results)
            steps["llm_audit"] = {
                "status": "ok",
                "findings": len(llm_results),
                "rag_injected": bool(rag_context),
            }
        except Exception as e:
            steps["llm_audit"] = {"status": "warning", "error": str(e)}
    else:
        steps["llm_audit"] = {"status": "skipped", "note": f"模式={audit_mode}，仅规则引擎"}
    step_times["llm_audit"] = (datetime.now() - _step_start).total_seconds()

    # ── Step 7: 条款比对 ──
    _step_start = datetime.now()
    compare_result = None
    try:
        from ai.matcher import compare_clauses, detect_missing
        compare_result = compare_clauses(full_text, contract_type)
        missing_findings = detect_missing(full_text, contract_type)
        # 将 matcher 发现的缺失条款合并到风险列表
        all_risks.extend(missing_findings)
        steps["matcher_compare"] = {
            "status": "ok",
            "summary": compare_result.get("summary", {}),
            "missing_critical": compare_result.get("missing_critical", []),
        }
    except Exception as e:
        steps["matcher_compare"] = {"status": "warning", "error": str(e)}
    step_times["matcher_compare"] = (datetime.now() - _step_start).total_seconds()

    # ── Step 8: 生成报告 ──
    _step_start = datetime.now()
    try:
        from ai.reporter import generate_report, compute_heatmap

        contract_info = {
            "contract_type": contract_type,
            "confidence": type_confidence,
            "quality": "good" if len(full_text) > 500 else "low",
            "word_count": len(full_text),
            "elements_json": json.dumps(elements, ensure_ascii=False, indent=2),
            "audit_mode": audit_mode,
        }

        # 转换为 reporter 期望的格式
        audit_records = []
        for i, r in enumerate(all_risks):
            audit_records.append({
                "risk_id": r.get("risk_type", f"R{i:02d}"),
                "risk_name": r.get("name", r.get("risk_type", "?")),
                "risk_level": r.get("level", "medium"),
                "clause_text": r.get("clause_text", ""),
                "reason": r.get("reason", ""),
                "suggestion": r.get("suggestion", ""),
                "confidence": r.get("confidence", 0.7),
                "detection_method": r.get("detection_method", "rule"),
                "source": r.get("detection_method", "rule"),
                "low_confidence": r.get("confidence", 0.7) < 0.7,
            })

        heatmap = compute_heatmap(audit_records, paragraphs_count=len(parsed.get("paragraphs", [])))
        report_html = generate_report(
            contract_info=contract_info,
            audit_records=audit_records,
            compare_result=compare_result,
            heatmap_data=heatmap,
            rule_findings_raw=rule_results,
        )
        steps["generate_report"] = {"status": "ok", "html_length": len(report_html)}
    except Exception as e:
        report_html = f"<html><body><h2>报告生成失败</h2><p>{e}</p></body></html>"
        steps["generate_report"] = {"status": "error", "error": str(e)}
    step_times["generate_report"] = (datetime.now() - _step_start).total_seconds()

    # ── 汇总 ──
    total_s = (datetime.now() - t_start).total_seconds()
    high = sum(1 for r in all_risks if r.get("level") == "high")
    medium = sum(1 for r in all_risks if r.get("level") == "medium")
    low = sum(1 for r in all_risks if r.get("level") == "low")
    risk_score = min(100, high * 30 + medium * 15 + low * 5)

    failed_steps = [k for k, v in steps.items() if v.get("status") == "error"]

    return {
        "status": "partial" if failed_steps else "success",
        "contract_type": contract_type,
        "type_confidence": type_confidence,
        "audit_mode": audit_mode,
        "steps": steps,
        "report_html": report_html,
        "all_risks": all_risks,
        "compare_result": compare_result,
        "summary": {
            "risk_score": risk_score,
            "high": high,
            "medium": medium,
            "low": low,
            "total_risks": len(all_risks),
            "coverage_rate": compare_result["summary"]["coverage_rate"] if compare_result else 0,
        },
        "timing": {
            "total_s": round(total_s, 2),
            "steps": {k: round(v, 2) for k, v in step_times.items()},
        },
    }


def _fail(step: str, steps: dict, step_times: dict, t_start: datetime) -> dict:
    total_s = (datetime.now() - t_start).total_seconds()
    return {
        "status": "error",
        "steps": steps,
        "error": f"流水线在 {step} 步骤失败",
        "timing": {"total_s": round(total_s, 2), "steps": step_times},
    }
