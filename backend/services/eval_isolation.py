"""services.eval_isolation — 官方评测集隔离闸（防止测试样本进入学习闭环）

## 为什么需要这道闸

项目里存在三份**官方评测语料**，它们绝不允许以任何形式进入 Feedback RAG，
否则就是"用测试样本喂知识库、再去测同一个测试样本"的数据泄漏：

| 语料 | 作用 | 正文字段 | 规模 |
| --- | --- | --- | --- |
| `backend/evaluate/realtest.json` | 风险评测 gold（P/R/F1） | `content` | 84 条 |
| `backend/evaluate/classification_test.json` | 分类正式评测集 | `content` | 147 条 |
| `03_数据集/测试集/testset.json` | 范本建库源 / 分类对照 | `text` | 363 条 |

## 做法

对三份语料的正文做**归一化（去掉所有空白字符）后取 sha256**，构成"评测样本指纹集"；
经验入库前，用待入库合同正文同样归一化取 sha256 比对，**命中即拒绝进入学习**。

这是与"官方评测路径不启用 Feedback RAG"（`FEEDBACK_RAG_ENABLED` 只作用于生产审核）
并列的**第二道闸**，属于 defense in depth：即使有人误开开关，测试样本也无法沉淀成经验。

## 边界

- 本模块**只读**评测语料，从不写入、从不修改；
- 语料文件缺失时只告警跳过（不阻断），但会把"实际检查了哪些来源"记录下来；
- 通过环境变量 `EVAL_CORPUS_PATHS` 可覆盖语料路径（分号分隔），便于测试与部署调整。
"""
import hashlib
import json
import logging
import os
import threading

from pathlib import Path

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SERVICE_DIR = _BACKEND_DIR.parent.parent

# 默认语料（顺序即优先级；缺失的会跳过并告警）
DEFAULT_CORPUS_PATHS = [
    _BACKEND_DIR / "evaluate" / "realtest.json",
    _BACKEND_DIR / "evaluate" / "classification_test.json",
    _SERVICE_DIR / "03_数据集" / "测试集" / "testset.json",
]

# 每份语料里承载正文的字段名（按出现顺序探测）
_TEXT_FIELDS = ("content", "text")

_lock = threading.Lock()
_cache: dict = {"loaded": False, "digests": frozenset(), "sources": []}


def _corpus_paths() -> list[Path]:
    """语料路径：环境变量优先（分号分隔），否则用默认列表。"""
    raw = (os.getenv("EVAL_CORPUS_PATHS") or "").strip()
    if raw:
        return [Path(p.strip()) for p in raw.split(";") if p.strip()]
    return list(DEFAULT_CORPUS_PATHS)


def normalize_text(text) -> str:
    """归一化：去掉**所有空白字符**（含全角空格/换行/制表符）。

    LLM 审核会把合同正文原样写入 `Contract.parsed_text`，与评测语料里的正文
    可能存在空白差异，因此比对前必须做同样的归一化，否则闸门形同虚设。
    """
    if not text:
        return ""
    s = str(text)
    for ch in ("\n", "\r", "\t", " ", "\u3000", "\u00a0", "\u200b"):
        s = s.replace(ch, "")
    return s


def text_digest(text) -> str:
    """归一化正文的 sha256（十六进制）。空文本返回空串。"""
    norm = normalize_text(text)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _digests_from_file(path: Path) -> tuple[set, str | None]:
    """读一份语料，返回 (指纹集合, 错误说明)。只读，不抛异常。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return set(), "not_found"
    except Exception as e:  # 解析失败一律降级，不阻断主流程
        return set(), f"load_error: {e}"

    items = data if isinstance(data, list) else []
    out = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in _TEXT_FIELDS:
            d = text_digest(item.get(field))
            if d:
                out.add(d)
                break
    return out, None


def eval_corpus_digests() -> tuple[frozenset, list[dict]]:
    """返回 (评测样本指纹集合, 来源明细)；结果进程内缓存（只读语料，可安全缓存）。"""
    if _cache["loaded"]:
        return _cache["digests"], _cache["sources"]

    with _lock:
        if _cache["loaded"]:
            return _cache["digests"], _cache["sources"]

        digests: set = set()
        sources: list[dict] = []
        for path in _corpus_paths():
            ds, err = _digests_from_file(path)
            digests |= ds
            sources.append({"path": str(path), "digests": len(ds), "error": err})
            if err:
                logger.warning("评测语料隔离闸：跳过 %s（%s）", path, err)
            else:
                logger.info("评测语料隔离闸：%s 载入 %d 条样本指纹", path.name, len(ds))

        if not digests:
            logger.error(
                "评测语料隔离闸：**没有任何评测语料可用**，本轮无法校验测试集重叠。"
                "请检查 EVAL_CORPUS_PATHS / 语料文件是否存在；官方评测前必须保证可用。"
            )

        _cache.update({"loaded": True, "digests": frozenset(digests), "sources": sources})
        return _cache["digests"], _cache["sources"]


def reset_cache() -> None:
    """清空缓存（测试与运维重载语料时用）。"""
    with _lock:
        _cache.update({"loaded": False, "digests": frozenset(), "sources": []})


def is_eval_sample(text) -> bool:
    """该正文是否命中任一官方评测语料（命中即禁止进入学习）。"""
    digest = text_digest(text)
    if not digest:
        return False
    digests, _sources = eval_corpus_digests()
    return digest in digests


def check_not_eval_sample(text) -> str:
    """校验正文不属于评测集；命中则抛 ValueError（调用方转成 4xx）。

    Returns:
        校验结论摘要（供调用方写入日志/审计）：`checked:N` 表示与 N 条评测样本比对过。
    """
    digests, sources = eval_corpus_digests()
    if digests and text_digest(text) in digests:
        raise ValueError(
            "该合同正文与官方评测语料完全一致，禁止将测试样本沉淀为学习经验（数据泄漏防护）。"
        )
    return f"checked:{len(digests)}/sources:{len(sources)}"
