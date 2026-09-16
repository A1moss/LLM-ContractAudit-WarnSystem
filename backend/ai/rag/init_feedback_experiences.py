"""初始化 / 重建 Feedback Experience 索引（Feedback RAG 第 ③ 类知识源）

运行方式（backend 目录下）：
    python -m ai.rag.init_feedback_experiences

它会：
1. 从数据库表 `feedback_experiences` 读取**全部 status='active'** 的经验；
2. **只删除并重建 `feedback_experiences` 一个集合**（绝不触碰 laws / standard_clauses /
   contract_templates，这三者的语义、数据源与生命周期完全不变）；
3. 打印重建条数与经验库版本。

适用场景：首次启用 Feedback RAG、索引损坏、或批量撤销/新增经验后需要对齐索引。
注意：批准/撤销经验时本来就会**增量**写入/精确删除，本脚本主要用于恢复与对齐。
"""
import logging

from ai.rag import feedback_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


if __name__ == "__main__":
    docs = feedback_store.load_active_experiences()
    print("当前可检索经验（status=active）：%d 条" % len(docs))
    for d in docs[:20]:
        print("  exp_id=%-5s kind=%-16s contract_type=%-10s risk_type=%-4s label=%s"
              % (d["id"], d["kind"], d["contract_type"], d["risk_type"], d["human_label"]))
    if len(docs) > 20:
        print("  … 共 %d 条" % len(docs))

    out = feedback_store.rebuild_index()
    if out.get("rebuilt"):
        print("\n重建完成：")
        print("  集合        : %s（只重建该集合）" % feedback_store.COLLECTION)
        print("  写入条数    : %s" % out["count"])
        print("  经验库版本  : %s" % out["index_version"])
        print("\n提示：Feedback RAG 默认关闭，需在 .env 设置 FEEDBACK_RAG_ENABLED=true 才会在生产审核中注入。")
    else:
        print("\n重建失败：%s" % out.get("error"))
        raise SystemExit(1)
