"""
初始化 ChromaDB 知识库脚本
运行方式: cd backend && python -m ai.rag.init_chroma
"""
from ai.rag.vector_store import init_chroma, init_contract_templates

if __name__ == "__main__":
    print("正在初始化 ChromaDB 知识库...")
    stats = init_chroma()
    print("\n初始化完成:")
    for name, count in stats.items():
        print(f"  {name}: {count} 条")

    print("\n正在初始化「合同范本」分类向量库...")
    n = init_contract_templates()
    print(f"  contract_templates: {n} 条")

    print(f"\n数据存储在: backend/chroma_data/")
