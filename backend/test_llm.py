"""
同学 D 的 AI 模块完整自测脚本
运行方式：cd backend && python test_llm.py
"""
import sys
import os

TEST_CONTRACT = """
杭州科技有限公司（以下简称甲方）与上海软件有限公司（以下简称乙方）经友好协商，
就甲方向乙方采购企业管理系统软件事宜达成如下协议。
合同总金额为人民币伍拾万元整（￥500,000）。
本合同自2024年3月15日起生效，履行期限至2025年3月15日。
任何一方违约需支付合同金额30%作为违约金。
乙方对任何原因造成的全部损失承担无限赔偿责任。
因本合同引起的争议，提交北京仲裁委员会仲裁。
知识产权归乙方所有。
"""

passed = 0
failed = 0


def test(func):
    def wrapper():
        global passed, failed
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  ❌ {func.__name__} 失败: {e}")
            failed += 1
    return wrapper


@test
def test_01_llm_connect():
    from ai.llm_client import llm_client
    res = llm_client.chat("回复'OK'，只回复这两个字母")
    assert len(res) > 0, "LLM 返回为空"
    print(f"  ✅ LLM 连通: {res[:80]}...")


@test
def test_02_classifier():
    from ai.classifier import classify_contract
    result = classify_contract(TEST_CONTRACT)
    assert "contract_type" in result and "confidence" in result
    assert 0.0 <= result["confidence"] <= 1.0
    print(f"  ✅ 分类: {result['contract_type']} (置信度 {result['confidence']})")


@test
def test_03_extractor():
    from ai.extractor import extract_elements
    result = extract_elements(TEST_CONTRACT, "采购合同")
    assert "parties" in result and "amount" in result
    print(f"  ✅ 要素: 甲方={result['parties'].get('甲方','?')}, 金额={result['amount']}")


@test
def test_04_rule_engine_detect():
    from ai.auditor.rule_engine import run_rules
    types = [r["risk_type"] for r in run_rules(TEST_CONTRACT)]
    assert "R01" in types, f"应检出R01，实际: {types}"
    assert "R02" in types, f"应检出R02，实际: {types}"
    print(f"  ✅ 规则引擎检出: {types}")


@test
def test_05_rule_engine_missing():
    from ai.auditor.rule_engine import run_rules
    types = [r["risk_type"] for r in run_rules("甲乙双方协商一致签订本合同。")]
    assert "R08" in types and "R09" in types, f"应检出R08+R09，实际: {types}"
    print(f"  ✅ 缺失检测: {types}")


@test
def test_06_safe_patterns():
    from ai.auditor.rule_engine import run_rules
    types = [r["risk_type"] for r in run_rules("违约金不超过合同金额的10%。保密期限为合同终止后3年。")]
    assert "R01" not in types, f"安全表述不应触发R01: {types}"
    print(f"  ✅ 白名单过滤, 仅检出: {types}")


@test
def test_07_llm_auditor():
    from ai.auditor.llm_auditor import audit_with_llm
    results = audit_with_llm(TEST_CONTRACT)
    assert isinstance(results, list)
    if results:
        print(f"  ✅ LLM审核: {len(results)} 条 (第一条: {results[0]['risk_type']} [{results[0]['level']}])")
    else:
        print(f"  ⚠️ LLM审核返回空列表")


@test
def test_08_parser():
    from ai.parser import detect_and_parse
    for f in ["test.docx", "test.pdf"]:
        if os.path.exists(f):
            r = detect_and_parse(f)
            assert "full_text" in r and "paragraphs" in r
            print(f"  ✅ parser({f}): {len(r['paragraphs'])} 段落, {len(r['full_text'])} 字符")
            return
    print(f"  ⚠️ 未找到测试文件")


@test
def test_09_ocr_import():
    from ai.parser.ocr_parser import parse_image, _ensure_ocr, _ocr_available
    _ensure_ocr()
    print(f"  ✅ OCR模块已加载 (PaddleOCR={'可用' if _ocr_available else '未安装'})")


@test
def test_10_knowledge():
    import json
    for f in ["risk_cases.json", "laws.json", "standard_clauses.json"]:
        path = os.path.join(os.path.dirname(__file__), "ai", "knowledge", f)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            print(f"  ✅ {f}: {len(data)} 条")
        else:
            print(f"  ⚠️ {f} 不存在")


@test
def test_11_corex():
    from ai.corex import run_review
    from ai.auditor.rule_engine import run_rules
    initial = run_rules(TEST_CONTRACT)
    result = run_review(TEST_CONTRACT[:2000], initial)
    assert "risks" in result and "agent_logs" in result
    print(f"  ✅ Corex: {result['completed_agents']}/{result['total_agents']} Agent, {len(result['risks'])} 条终审")


@test
def test_12_chunking():
    from ai.chunker import split_chunks
    from ai.auditor.rule_engine import run_rules
    # 构造超长合同：头部无风险，尾部才有"违约金30%"，验证分块/全文扫描不漏尾部
    head = "本合同各方本着平等自愿原则达成协议。\n" * 400
    tail = "\n第20条 违约责任\n若一方违约，应向守约方支付违约金为30%的合同总金额。\n"
    long_text = head + tail
    assert len(long_text) > 10000, "测试文本应超过旧截断阈值"
    # 分块：无丢失、每块不超上限
    chunks = split_chunks(long_text, 4000)
    assert len(chunks) > 1
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks) == long_text.replace("\n", "")
    # 规则引擎全文扫描：尾部 R01 不被截断漏检
    types = {r["risk_type"] for r in run_rules(long_text)}
    assert "R01" in types, "尾部违约金风险应被检出"
    print(f"  ✅ 分块: {len(long_text)} 字→{len(chunks)} 块，尾部 R01 风险已检出")


if __name__ == "__main__":
    print("=" * 60)
    print("同学 D — AI 模块完整自测")
    print("=" * 60)

    test_01_llm_connect()
    test_02_classifier()
    test_03_extractor()
    test_04_rule_engine_detect()
    test_05_rule_engine_missing()
    test_06_safe_patterns()
    test_07_llm_auditor()
    test_08_parser()
    test_09_ocr_import()
    test_10_knowledge()
    test_11_corex()
    test_12_chunking()

    print("=" * 60)
    print(f"结果: {passed} 通过 / {failed} 失败 / {passed + failed} 总计")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
