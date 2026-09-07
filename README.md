# LLM-ContractAudit-WarnSystem

> A24 · 基于大模型的企业合同智能审核与风险预警系统（命题企业：网新恒天）
> 团队：海底汪汪队

面向企业法务与风控部门的 AI 原生合同审核平台，实现「合同上传 → 智能分类 → 要素抽取 → 风险识别 → 修改建议 → 条款比对 → 审核报告」全流程闭环，核心理念是**人机协同审核**。

---

## 一、成绩单（三大硬指标，实测数据）

| 赛题硬指标 | 门槛 | 实测 | 达标 |
|---|---|---|---|
| 法理分类准确率 | ≥85% | **98.81%**（83/84） | ✅ 超额 13.8pp |
| 要素抽取 F1 | ≥80% | **90.7%**（宏平均） | ✅ 超额 10.7pp |
| 风险识别精准率 | ≥75% | **76.6%** | ✅ |
| 风险识别召回率 | ≥75% | **80.1%** | ✅ |

> 测试集 84 份真实合同 / 152 条 gold 风险标注，双人盲标 + ChatGPT 法理仲裁。详见 [docs/测试集说明.md](docs/测试集说明.md)。

---

## 二、技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + SQLAlchemy + SQLite（开发）/ MySQL（生产） |
| AI 引擎 | DeepSeek（`deepseek-chat`，temperature=0） |
| 前端 | Vue 3 + Element Plus + ECharts + Vite |
| 向量库 | ChromaDB（RAG 法律知识检索） |

---

## 三、系统架构（三层递进）

风险识别的核心架构是 **「LLM 只抽事实证据 + 确定性规则裁决」**，把「有没有风险」和「怎么改」彻底解耦：

1. **规则召回**（`rule_engine.py`）——确定性正则召回，`fast` 基线。
2. **证据抽取 + 确定性裁决**（`evidence_extractor.py` → `evidence_adjudicator.py`）——LLM 只抽取条款事实证据，Python 按 v1.2.4 口径的硬阈值裁决 R01–R12，**风险判定不做任何 LLM 自由发挥**。
3. **建议层**（`recommendation_engine.py`）——按法条编号精确取条文（`laws.json`）→ LLM 生成修改建议 → grounding 校验，只负责「怎么改」，不反向影响风险判定。

完整流水线见 [docs/审核流程.md](docs/审核流程.md)。

---

## 四、目录结构

```
backend/
  ai/
    parser/       文档解析（docx / pdf / ocr）
    classifier/   法理分类（11 类 + is_outsourcing）
    extractor/    要素抽取（双方/金额/期限/争议解决）
    auditor/      规则引擎 + 证据抽取 + 确定性裁决 + 建议层（核心）
    knowledge/    法条库 laws.json / 风险案例 / 标准条款
    matcher/      标准条款比对
    reporter/     报告生成 + 热力图数据
    rag/          ChromaDB / BM25 检索
  api/            FastAPI 路由（含后台审核流水线 _run_audit）
  models/         SQLAlchemy 模型
  evaluate/       评测脚本 + 测试集 realtest.json + gold
frontend/         Vue 3 前端
docs/             说明文档（测试集 / 审核流程 / 风险标注口径）
data/             上传合同存储（运行时生成，git 忽略）
```

---

## 五、快速启动

```bash
# 后端（端口 8080，API 文档 http://localhost:8080/docs）
start-backend.bat

# 前端（端口 5173）
start-frontend.bat
```

环境变量见 `.env.example`：`DEEPSEEK_API_KEY`、`DIFY_API_KEY`、`DATABASE_URL`、`SECRET_KEY`、`CORS_ORIGINS`。

---

## 六、文档索引

| 文档 | 说明 |
|---|---|
| `README.md`（本文件） | 项目总览 |
| `A24-详细设计文档-v3.0.md` | 详细设计文档（申报交付物） |
| [docs/测试集说明.md](docs/测试集说明.md) | 测试集构成、字段结构、标注口径、三大硬指标、复现方式 |
| [docs/审核流程.md](docs/审核流程.md) | 审核流水线逐步说明（含对应代码文件） |
| [docs/风险标注口径.md](docs/风险标注口径.md) | R01–R13 风险标注口径（v1.2.4，权威盲标标准） |

> 更完整的评测过程、FP 归因、设计演进记录在仓库外的 `../02_项目文档/`（`盲标与gold/`、`评测与FP归因/`、`设计文档/`、`风险标注口径/` 等子目录）。
