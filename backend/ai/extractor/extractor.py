from ai.chunker import split_chunks
from ai.llm_client import llm_client
from ai.llm_context import submit_with_context
from ai import perf as perf   # 临时链路耗时诊断（BUG-2）
from ai.utils import extract_json_dict
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# 单块要素抽取文本上限（字符）。超长合同分块抽取后合并，尾部要素不再丢失。
MAX_EXTRACT_CHARS = 4000

# 长合同最多采样几块做要素抽取（首块 + 尾块必含，中间按需补 1 块）。
# 【BUG-2】为什么要采样：本任务只抽 6 个字段（当事人/金额/签署日/履行期限/争议解决/适用法律），
# 全部集中在**首部当事人**与**尾部签名、争议解决**；原实现把整份合同切块后**每块都发一次 LLM**
# （19k 字 → 6 次并发调用），实测墙钟 ≈ 各调用之和（API 对同 Key 并发会排队），是"上传很慢"的主因。
# 首尾采样把调用数降到 2~3 次，值与耗时实测等价或更优（见 _sample_chunks 注释）。
MAX_EXTRACT_CHUNKS = 3

SYSTEM_PROMPT_EXTRACT = """你是一个法律信息抽取专家。请从合同文本中抽取以下关键结构化信息。

只输出 JSON（不要加任何前缀或后缀）：
{
  "parties": {"甲方": "公司全称", "乙方": "公司全称"},
  "amount": {"value": 数字, "currency": "CNY/USD/EUR", "text": "大写金额原文"},
  "sign_date": "YYYY-MM-DD",
  "performance_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "dispute_resolution": "争议解决方式",
  "governing_law": "适用法律"
}

规则：
1. 双方信息从合同首部提取。不同合同的双方称谓不同——甲方/乙方、采购人/供应商、买方/卖方、
   委托方/受托方、发包方/承包方、出租方/承租方、用人单位/劳动者、披露方/接收方、需方/供方等，
   一律归一化到「甲方」「乙方」两栏（首部先出现/承担主要给付义务的一方为甲方，另一方为乙方）。
   若正文已脱敏（〔甲方〕/〔乙方〕/<公司>/〈公司〉等占位符），原样输出该占位符，
   表示已识别到该方存在，不得填「未知」或 null。
2. 金额取合同总金额，数字类型，注意区分含税/不含税；正文金额被脱敏为「<金额>」等占位符时填 null
3. 日期统一 YYYY-MM-DD，如"自签署之日"需结合签署日期推算
4. 争议解决提取仲裁机构或管辖法院全称
5. 未出现的字段填 null"""

FEWSHOT_EXAMPLE = """
合同片段：
"杭州科技有限公司（以下简称甲方）与上海软件有限公司（以下简称乙方）经友好协商，就甲方向乙方采购企业管理系统软件事宜达成如下协议。合同总金额为人民币伍拾万元整（¥500,000）。本合同自2024年3月15日起生效，履行期限至2025年3月15日。因本合同引起的争议，提交北京仲裁委员会仲裁。"

正确输出：
{
  "parties": {"甲方": "杭州科技有限公司", "乙方": "上海软件有限公司"},
  "amount": {"value": 500000, "currency": "CNY", "text": "人民币伍拾万元整"},
  "sign_date": "2024-03-15",
  "performance_period": {"start": "2024-03-15", "end": "2025-03-15"},
  "dispute_resolution": "北京仲裁委员会",
  "governing_law": null
}"""


def _merge_elements(results: list[dict]) -> dict:
    """合并多块抽取结果（BUG-007 收口：通用嵌套 dict 逐字段非空优先，不依赖块顺序）。

    合并规则（显式声明，不靠隐式行为）：
    1. 顶层标量字段（sign_date/dispute_resolution/governing_law）：首个非 None 值生效，
       后续非 None 不覆盖（first-wins，空不覆盖非空）。
    2. 嵌套 dict（parties/amount/performance_period）：逐字段合并，同样「空不覆盖非空」——
       某字段在任一块出现非 None 值即生效，其它块该字段为 None 不覆盖。
    3. "空"仅指 None（抽取约定缺失字段填 null）；""/0/False 视为已有值，不被覆盖。
    4. 非 dict 的块结果（None / 异常产物）跳过。
    5. 顺序无关性：对「空 vs 非空」这一真实场景，交换块顺序合并结果完全一致；
       仅当两块的同一字段都给出「非 None 且不一致」的值时由先出现者决定
       （同一合同分块覆盖不同段落，不会出现同字段冲突，属确定性兜底）。

    修复 BUG-007：原实现只对 parties 做内层合并，amount/performance_period 等嵌套 dict
    首块返回全 null 的 dict 时会挡住后续块的正确值（长合同金额/期限显示"—"）。
    """
    keys = ["parties", "amount", "sign_date", "performance_period", "dispute_resolution", "governing_law"]
    merged = {k: None for k in keys}
    for r in results:
        if not isinstance(r, dict):
            continue
        for key in keys:
            val = r.get(key)
            if merged[key] is None:
                merged[key] = val
            elif isinstance(val, dict) and isinstance(merged[key], dict):
                # 嵌套 dict（parties/amount/performance_period）：逐字段非空优先合并
                for kk, vv in val.items():
                    if merged[key].get(kk) is None and vv is not None:
                        merged[key][kk] = vv
    return merged


def _extract_chunk(chunk: str, contract_type: str):
    """抽取单个文本块的要素，失败返回 None。"""
    prompt = (
        f"{SYSTEM_PROMPT_EXTRACT}\n\n"
        f"这是一个{contract_type}。\n\n"
        f"{FEWSHOT_EXAMPLE}\n\n"
        f"现在请从以下合同中抽取要素：\n{chunk}"
    )
    try:
        with perf.stage("elements_llm"):
            response = llm_client.chat(prompt=prompt, temperature=0.0)
        result = extract_json_dict(response)
        if result:
            return {
                "parties": result.get("parties"),
                "amount": result.get("amount"),
                "sign_date": result.get("sign_date"),
                "performance_period": result.get("performance_period"),
                "dispute_resolution": result.get("dispute_resolution"),
                "governing_law": result.get("governing_law"),
            }
    except Exception as e:
        logger.warning(f"LLM 要素抽取失败: {e}")
    return None


def _sample_chunks(chunks: list[str], max_n: int = MAX_EXTRACT_CHUNKS) -> list[str]:
    """要素抽取的取块策略：首块 + 尾块必含，块数够时中间再补 1 块，覆盖全文首/中/尾。

    与「全量分块」相比（实测 evaluate/realtest.json 的长合同样本，同一份文本上对比）：
      4.4k 字 2 块 ：全量 vs【首尾】   提取值**完全一致**，墙钟 1.68s → 0.86s
      13k 字 4 块  ：全量 vs【首中尾】 提取值**完全一致**，墙钟 1.09s → 0.87s
    19k 字 6 块  ：6 次并发被 API 排队 → 单次耗时被放大（上传阶段主要瓶颈）
    即"少发几次并发调用"在不损失要素值的前提下直接省时间；块数不超过 max_n 时与全量一致。
    """
    n = len(chunks)
    if n <= max_n:
        return chunks
    idxs = [0]
    if max_n >= 3:
        idxs.append(n // 2)          # 中间块：兜住"金额/期限只写在正文中部"的长合同
    idxs.append(n - 1)               # 尾块：签署日/争议解决/适用法律多在这里
    return [chunks[i] for i in sorted(set(idxs))]


def extract_elements(full_text: str, contract_type: str) -> dict:
    chunks = split_chunks(full_text, MAX_EXTRACT_CHARS)
    sampled = _sample_chunks(chunks)
    if len(chunks) > 1:
        logger.info("要素抽取：合同 %d 字分为 %d 块，采样 %d 块（首/中/尾）",
                    len(full_text), len(chunks), len(sampled))

    # 并行抽取各块（LLM 调用并发，缩短上传等待）
    if len(sampled) > 1:
        with ThreadPoolExecutor(max_workers=min(len(sampled), 6)) as ex:
            # 复制上下文后提交：保证工作线程能读到用户个人 DeepSeek Key（见 ai/llm_context.py）
            futs = [perf.submit_labeled_ctx(ex, _extract_chunk, c, contract_type) for c in sampled]
            results = [r for r in (f.result() for f in futs) if r]
    else:
        results = [r for r in [_extract_chunk(sampled[0], contract_type)] if r]

    if results:
        merged = _merge_elements(results)
        return {**merged, "fallback": False}

    return {
        "parties": {"甲方": "未知", "乙方": "未知"},
        "amount": None,
        "sign_date": None,
        "performance_period": None,
        "dispute_resolution": None,
        "governing_law": None,
        "fallback": True,
    }
