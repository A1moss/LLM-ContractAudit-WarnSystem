# -*- coding: utf-8 -*-
"""RAG 交付资源就绪性检查（供 prepare.bat 调用，也可单独运行）。

为什么需要它
------------
RAG 链路的失效是**静默**的：`search_similar_templates()` 在向量库或 embedding 模型缺失时
只返回空列表并打一条 warning，`classify_by_rag()` 随即退化为 `method="rag-fallback-llm"`，
接口仍然 200、功能看起来"正常"。因此"目录存在"绝不能作为准备完成的判据，必须**真实检索一次**
并检查生产分类返回的 `method` / `fallback`。

检查项与判定
------------
硬失败（exit 1，prepare 必须中止）：
  1. 仓库内 `models/hf-cache` 的 embedding 模型无法加载；
  2. `backend/chroma_data` 无法打开；
  3. 必需集合 contract_templates / laws / standard_clauses 缺失或为空；
  4. 用真实合同做检索，`search_similar_templates()` 或 `search_knowledge()` 返回空；
  5. 生产分类实际跑通但 `fallback=True` 或 `method != "rag"`（即已退化）。

警告（不阻断，但会明确打印，绝不伪装成通过）：
  · risk_cases 集合为空（交付库中本就为 0，属预期）；
  · 集合条数与文档记载不一致；
  · 未配置 DeepSeek API Key → LLM 段无法验证，明确标注"未验证"。

用法：
    cd backend && python ..\scripts\check_rag_ready.py
    python scripts/check_rag_ready.py --strict     # 未配置 Key 也算失败
"""
import argparse
import os
import sys
import traceback

# 控制台编码：Windows cmd 下配合 chcp 65001，避免中文输出变成乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# ── 路径定位（不依赖 cwd）─────────────────────────────────────────────
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS_DIR)
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

MODEL_CACHE = os.path.join(ROOT, "models", "hf-cache")
CHROMA_SQLITE = os.path.join(BACKEND, "chroma_data", "chroma.sqlite3")
TESTSET_SOURCE = os.path.join(BACKEND, "ai", "rag", "resources", "contract_templates_source.json")

# 必需非空的集合 → 文档记载的预期条数（不一致只告警，不失败）
REQUIRED = {
    "contract_templates": 363,
    "laws": 161,
    "standard_clauses": 227,
}
OPTIONAL = {"risk_cases": 0}

failures: list[str] = []
warnings: list[str] = []


def ok(msg: str) -> None:
    print("  [PASS] " + msg)


def bad(msg: str, why: str = "") -> None:
    failures.append(msg)
    print("  [FAIL] " + msg)
    if why:
        print("         " + why)


def warn(msg: str) -> None:
    warnings.append(msg)
    print("  [警告] " + msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="未配置 API Key 时也判定为失败（默认仅警告）")
    ap.add_argument("--top-k", type=int, default=3)
    args = ap.parse_args()

    print("=" * 68)
    print("  RAG 交付资源就绪性检查")
    print("=" * 68)
    print("  仓库根   :", ROOT)

    # ── 0) 离线化：证明交付资源自足，不依赖联网 ──────────────────────
    if os.path.isdir(MODEL_CACHE):
        os.environ["HF_HOME"] = MODEL_CACHE
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        print("  HF_HOME  :", MODEL_CACHE, "(已强制离线模式)")
    else:
        print("  HF_HOME  : 未找到 models/hf-cache，将使用系统 HuggingFace 缓存")
    print()

    print("[1/5] 交付资源是否存在")
    if os.path.isfile(CHROMA_SQLITE):
        ok("向量库 %s (%.2f MB)" % (
            os.path.relpath(CHROMA_SQLITE, ROOT),
            os.path.getsize(CHROMA_SQLITE) / 1024 / 1024))
    else:
        bad("向量库缺失: " + os.path.relpath(CHROMA_SQLITE, ROOT),
            "请先执行 prepare.bat 或 cd backend && python -m ai.rag.init_chroma")
    if os.path.isfile(TESTSET_SOURCE):
        ok("建库源 %s (%.2f MB)" % (
            os.path.relpath(TESTSET_SOURCE, ROOT),
            os.path.getsize(TESTSET_SOURCE) / 1024 / 1024))
    else:
        bad("建库源缺失: " + os.path.relpath(TESTSET_SOURCE, ROOT),
            "该文件应随源码交付；缺失则向量库无法重建")
    print()

    print("[2/5] embedding 模型能否离线加载")
    dim = None
    try:
        from ai.rag.vector_store import _get_embedder
        emb = _get_embedder()
        # 兼容新旧 sentence-transformers：新版本把该方法改名为 get_embedding_dimension
        try:
            dim = emb.get_embedding_dimension()
        except AttributeError:
            dim = emb.get_sentence_embedding_dimension()
        ok("模型已加载: shibing624/text2vec-base-chinese, 维度 = %s, max_seq_length = %s"
           % (dim, getattr(emb, "max_seq_length", "?")))
        if dim != 768:
            warn("维度为 %s，与文档记载的 768 不一致" % dim)
        # 真实编码一次，确认前向计算可用
        vec = emb.encode(["合同审查就绪性自检"], normalize_embeddings=True)
        ok("前向编码成功，输出形状 = %s" % (tuple(vec.shape),))
    except Exception as e:  # noqa: BLE001
        bad("embedding 模型无法加载: %s: %s" % (type(e).__name__, e),
            "离线模式下加载失败说明 models/hf-cache 不完整；"
            "请重新执行 prepare.bat 准备模型")
        traceback.print_exc()
    print()

    print("[3/5] 向量库集合条数")
    counts: dict[str, int] = {}
    try:
        from ai.rag.vector_store import _get_client
        client = _get_client()
        for c in client.list_collections():
            try:
                counts[c.name] = c.count()
            except Exception:  # noqa: BLE001
                counts[c.name] = -1
        for name, expect in REQUIRED.items():
            n = counts.get(name)
            if n is None:
                bad("必需集合缺失: %s" % name)
            elif n <= 0:
                bad("必需集合为空: %s (count=0)" % name,
                    "检索必然返回空 → 分类会静默退化为 rag-fallback-llm")
            else:
                ok("%-19s %5d 条" % (name, n))
                if n != expect:
                    warn("%s 条数 %d 与文档记载的 %d 不一致（向量库与建库源可能不同步）"
                         % (name, n, expect))
        for name, expect in OPTIONAL.items():
            n = counts.get(name)
            if n is None:
                warn("可选集合缺失: %s" % name)
            elif n == 0:
                ok("%-19s %5d 条（交付库中本就为 0，属预期）" % (name, n))
            else:
                ok("%-19s %5d 条" % (name, n))
    except Exception as e:  # noqa: BLE001
        bad("向量库无法打开: %s: %s" % (type(e).__name__, e))
        traceback.print_exc()
    print()

    print("[4/5] 用真实合同做一次真实检索")
    real_text = ""
    try:
        import json
        with open(os.path.join(BACKEND, "evaluate", "realtest.json"), encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            t = (r.get("content") or r.get("text") or "").strip()
            if len(t) > 200:
                real_text = t
                break
        if real_text:
            ok("取自 backend/evaluate/realtest.json 的真实合同，长度 %d 字" % len(real_text))
        else:
            warn("未能从 realtest.json 取到可用正文，改用内置探测文本")
    except Exception as e:  # noqa: BLE001
        warn("读取 realtest.json 失败（%s），改用内置探测文本" % e)
    if not real_text:
        real_text = "本合同由甲方委托乙方提供软件开发与运维服务，双方就服务范围、付款方式、知识产权归属及违约责任达成如下约定。"

    try:
        from ai.rag.vector_store import search_similar_templates, search_knowledge
        hits = search_similar_templates(real_text[:2000], args.top_k)
        if hits:
            ok("search_similar_templates 命中 %d 条，Top1 类型 = %s (score=%.4f)"
               % (len(hits), hits[0].get("type"), float(hits[0].get("score") or 0)))
        else:
            bad("search_similar_templates 返回空",
                "这正是导致分类静默退化到 rag-fallback-llm 的直接原因")
        for coll in ("laws", "standard_clauses"):
            h = search_knowledge(real_text[:2000], collection_name=coll, top_k=args.top_k)
            if h:
                ok("search_knowledge(%s) 命中 %d 条" % (coll, len(h)))
            else:
                bad("search_knowledge(%s) 返回空" % coll)
    except Exception as e:  # noqa: BLE001
        bad("检索调用异常: %s: %s" % (type(e).__name__, e))
        traceback.print_exc()
    print()

    print("[5/5] 生产分类实际走的是不是正式 RAG 链路")
    try:
        from ai.llm_client import resolve_api_key
        key, source = resolve_api_key()
    except Exception as e:  # noqa: BLE001
        key, source = None, "none"
        warn("解析 API Key 失败: %s" % e)

    if not key:
        msg = ("未配置 DeepSeek API Key → 无法验证生产分类的 method/fallback，"
               "该段【未验证】（资源就绪性与它无关）")
        if args.strict:
            bad(msg)
        else:
            warn(msg)
    else:
        # 先单独尝试导入生产分类模块：导入失败通常与 RAG 资源无关
        # （例如 .env 未配置导致 config.py 抛错），因此算"未验证"而非"资源未就绪"。
        classify_by_rag = None
        try:
            from ai.classifier.rag_classifier import classify_by_rag
        except Exception as e:  # noqa: BLE001
            warn("无法导入生产分类模块（%s: %s）→ 该段【未验证】"
                 % (type(e).__name__, e))
        if classify_by_rag is not None:
            try:
                r = classify_by_rag(real_text[:4000], top_k=args.top_k)
                method = r.get("method")
                fb = r.get("fallback")
                print("  分类结果: contract_type=%s confidence=%s method=%s fallback=%s"
                      % (r.get("contract_type"), r.get("confidence"), method, fb))
                if method == "rag" and fb is False:
                    ok("生产分类走正式 RAG 链路：method=rag, fallback=false "
                       "(Key 来源=%s)" % source)
                else:
                    bad("生产分类未走正式 RAG 链路: method=%s, fallback=%s" % (method, fb),
                        "fallback=true 或 method=rag-fallback-llm 说明 RAG 已退化，"
                        "不得当作正常交付")
            except Exception as e:  # noqa: BLE001
                bad("生产分类调用异常: %s: %s" % (type(e).__name__, e))
                traceback.print_exc()
    print()

    print("=" * 68)
    if failures:
        print("  结论: 未就绪 —— %d 项硬失败" % len(failures))
        for f in failures:
            print("    · " + f)
        print("=" * 68)
        return 1
    print("  结论: RAG 交付资源就绪（%d 项警告）" % len(warnings))
    for w in warnings:
        print("    · " + w)
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
