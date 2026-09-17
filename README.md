# LLM-ContractAudit-WarnSystem

> A24 · 基于大模型的企业合同智能审核与风险预警系统（命题企业：网新恒天）
> 团队：海底汪汪队

面向企业法务与风控部门，覆盖「合同上传 → 解析/OCR → 分类 → 要素抽取 → 风险识别 → 修改建议 → 条款比对 → 审核报告 → 条款修改 → 修订版 DOCX 导出」的完整闭环。核心理念是**人机协同审核**：机器负责可重复的证据提取与确定性判定，人负责最终定性、位置确认与版本采纳。

---

## 一、成绩单（三大硬指标）

| 赛题硬指标 | 门槛 | 正式口径 | 达标 |
|---|---|---|---|
| 法理分类准确率 | ≥85% | **98.6%**（145/147） | ✅ 超额 13.6 pp |
| 要素抽取 F1 | ≥80% | **93.7%**（P 94.8% / R 92.7%） | ✅ 超额 13.7 pp |
| 风险识别精准率 | ≥75% | **76.6%** | ✅ |
| 风险识别召回率 | ≥75% | **80.1%** | ✅ |
| 风险识别 F1（参考） | — | **78.3%**（TP 121 / FP 37 / FN 30） | — |

**测试集口径（三个数据集严格区分，不得混用）：**

| 评测 | 数据集 | 规模 |
|---|---|---|
| 分类 | `classification_test.json` | **147 条** = 第一批 84 + 第二批 55 + 人工构造困难边界样本 8 |
| 要素 | `realtest.json` | **84 份**（47 公开披露合同 + 32 真实采购合同 + 5 人工构造合同） |
| 风险 | `realtest.json` + `evidence.json` | **84 份 / 152 条 Gold 风险标签**（其中进入 R01–R12 严格 F1 的为 **151 条**） |

**必须同时说明的四条口径边界：**

1. 风险 F1 覆盖 **R01–R12 中实际有 Gold 的风险**；
2. **R13（疑似名实不符）不纳入当前 F1**（规则引擎示警项，定性交人工）；
3. **R05（保密期间不合理）没有任何 Gold 标注**，因此不存在 R05 的 P/R/F1；
4. **R08 / R09 的 Gold 覆盖存在结构性限制**：它们是缺失型风险，Gold 中相当部分来自公开披露合同的**构造补全条款**（缺失型共 53 条，占严格 Gold 的 35.1%）。

分类历史口径 97.62%（82/84）保留作版本对照。分类使用 temperature=0.1，存在 145↔147 的轻微波动，**正式口径冻结在 98.6%（145/147）**。
详见 [docs/评测方法.md](docs/评测方法.md) 与 [docs/数据集说明.md](docs/数据集说明.md)。

---

## 二、技术栈（当前默认实际运行）

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + SQLAlchemy 2.x + Uvicorn（端口 **8080**） |
| 数据库 | **SQLite**（`backend/contract.db`，WAL + `busy_timeout=30s`） |
| 数据库（备选，**非默认**） | MySQL 8.0 + PyMySQL（换 `DATABASE_URL` 即可切换） |
| AI 引擎 | DeepSeek `deepseek-chat`（唯一出口 `ai/llm_client.py`；分类/比对/改条款 temperature=0.1，要素/证据/建议 temperature=0.0） |
| 向量库 | ChromaDB **嵌入式持久化**（`backend/chroma_data`）+ BM25 + RRF 融合（**不需要 Docker**） |
| 嵌入模型 | `shibing624/text2vec-base-chinese` |
| 文档解析 | `pdfplumber`（PDF）、`python-docx`（DOCX）、PaddleOCR（图片/扫描件，可选） |
| 前端 | Vue 3 + Element Plus + ECharts + Vite（端口 **5173**） |
| 鉴权 | PyJWT（HS256，7 天）+ bcrypt；个人 API Key 用 Fernet 加密落库 |

> **部署口径**：当前 SQLite 部署存在单写者并发限制（已用 WAL + `busy_timeout` + 缩短写事务窗口缓解）；高并发场景建议切 MySQL，但**这不是默认部署方式**。
> 仓库根的 `docker-compose.yml` **只有 1 个 chromadb 服务**，而应用使用的是嵌入式向量库，因此**该容器当前未被使用**；`deploy/` 目录为空。**本项目不是 Docker 一键部署。**

---

## 三、核心架构：LLM 抽证据，Python 做裁决

系统的技术边界是**结构性的**，不是修辞：

> **LLM 负责理解、抽取证据、生成建议；Python 负责最终风险裁决。**

| 环节 | 承担者 | 代码 |
|---|---|---|
| 1. 规则召回（`fast` 基线，始终先跑） | 确定性正则 + 缺失型规则 | `ai/auditor/rule_engine.py`（**R01–R13**） |
| 2. 证据抽取 | **LLM 只抽客观事实**（prompt 明令禁止输出风险结论） | `ai/auditor/evidence_extractor.py` |
| 3. 确定性裁决 | **纯 Python 硬阈值** | `ai/auditor/evidence_adjudicator.py`（**R01–R12**） |
| 4. 建议层 | 法条精确取条文 → LLM 生成 → grounding 校验 | `ai/auditor/recommendation_engine.py` |

因此本系统**不是**「把合同丢给大模型让它判断有没有风险」。完整流水线见 [docs/审核流程.md](docs/审核流程.md)。

**审核模式**：`precise`（默认，完整链路）/ `fast`（仅规则引擎初筛）。

---

## 四、主要能力

| 能力 | 落地位置 |
|---|---|
| 合同上传 / 解析 / OCR | `backend/data/` + `ai/parser/` |
| 合同类型分类（11 类 + 服务外包业务标签） | `ai/classifier/rag_classifier.py`（RAG 少样本） |
| 要素抽取（双方/金额/日期/期限/争议解决/适用法律） | `ai/extractor/extractor.py` |
| 风险识别（R01–R13） | `ai/auditor/` |
| 条款比对（覆盖率 / 缺失 / 偏离 / 跨条款图分析） | `ai/matcher/matcher.py` |
| 审核报告（Tab 驾驶舱 + 独立完整报告 + 打印） | `frontend/src/views/contract-detail/ReportPanel.vue`、`views/AuditReportDetail.vue` |
| 条款修改（多轮会话 / 版本历史 / **确认采用此版**） | `api/contracts.py`、`models/clause_revision.py`、`views/contract-detail/RevisionWorkbench.vue` |
| 总体修改会话（结构化综合修改方案） | `api/overview.py`、`models/revision_proposal.py` |
| R09 缺失条款补充（建议位置 + 用户确认） | `POST /contracts/{id}/add-clause-suggestion` + `InlinePositionPicker.vue` |
| **DOCX 修订版导出**（真实原文锚点替换 + 新增条款插入 + 编号顺延） | `services/docx_reviser.py`、`GET /contracts/{id}/revised-docx` |
| 人工反馈闭环（审核 → 批准 → 经验库 → Feedback RAG，默认关闭） | `api/feedback.py`、`ai/rag/feedback_store.py` |
| 多用户角色与权限（uploader / reviewer / approver / admin） | `api/deps.py`、`services/role_bootstrap.py` |
| 静启动预热 | `services/warmup.py`、`GET /api/health` |

---

## 五、目录结构

```
backend/    FastAPI 后端 + AI 引擎（api/ services/ models/ ai/ evaluate/ tests/）
frontend/   Vue 3 前端（views/ views/contract-detail/ composables/ constants/）
docs/       项目文档
02_项目文档/总体会话-后端API契约.md   总体修改会话的字段级接口契约
backend/data/        上传合同存储（运行时生成，git 忽略）
backend/chroma_data/ 向量库持久化目录（运行时生成）
```

> 完整目录职责（含各文件一句话说明）见 [docs/项目目录说明.md](docs/项目目录说明.md)。

---

## 六、快速启动

```bash
# 一键启动（后端 + 前端）
start.bat

# 或分别启动
start-backend.bat      # 后端 http://localhost:8080/docs
start-frontend.bat     # 前端 http://localhost:5173
```

### 前置：配置 DeepSeek API Key

本项目**不携带任何作者 API Key**。Key 有两条来源，**优先级：个人 Key > 系统默认 Key**：

| 来源 | 配置位置 | 说明 |
|---|---|---|
| **系统默认 Key**（可选） | 项目根目录 `.env` 的 `DEEPSEEK_API_KEY` | 部署方配置，供**所有未配个人 Key 的用户**共用 |
| **用户个人 Key** | 登录后进「个人信息」页填写 | 只用于**该账号自己**的请求 |

**部署方**：

1. 到 [DeepSeek 开放平台](https://platform.deepseek.com) 申请 API Key；
2. 复制 `.env.example` 为**项目根目录**下的 `.env`；
3. 填写 `DEEPSEEK_API_KEY=sk-你的key`（**可留空** —— 留空时用户必须各自在「个人信息」页配自己的 Key，否则调用 LLM 会给出明确报错）；
4. **不要把真实 Key 提交到 Git** —— `.env` 已被 `.gitignore` 忽略，仓库只保留 Key 项留空的 `.env.example` 模板；
5. **必须配置 `SECRET_KEY`**（随机长字符串），否则后端启动即失败：

   ```bash
   python -c "import secrets;print(secrets.token_urlsafe(32))"
   ```

**使用者**：登录 → 右上角头像 →「个人信息」→ 填写自己的 DeepSeek API Key → 保存。
保存后该 Key 用于本账号调用 DeepSeek；删除后自动回退系统默认 Key。

> 个人 Key 由服务器**加密存储**（Fernet），接口**只回「是否已配置」**，不回显 Key 本身；
> 其他用户、管理员都看不到你的 Key；Key 也不会写入日志、不会下发到浏览器。
> 其余变量（`DATABASE_URL`、`CORS_ORIGINS`、`BOOTSTRAP_ADMIN_USERNAME`、静启动开关、`FEEDBACK_RAG_ENABLED`）见 `.env.example` 注释与 [docs/部署与复现.md](docs/部署与复现.md)。

**首次部署产生第一个管理员**（可选，一次性）：公开注册只能得到 `uploader`；把已注册的账号名填入 `.env` 的 `BOOTSTRAP_ADMIN_USERNAME`，重启后端即完成提升（**不会自动创建账号**），之后在「用户管理」页分配其余角色。

---

## 七、测试

```bash
# 后端（unittest，29 个测试文件 / 288 个测试方法）
cd backend && python -m unittest discover -s tests -t .

# 前端（node --test，6 个文件 / 95 个测试）
cd frontend && node --test src/
```

---

## 八、文档索引

| 文档 | 说明 |
|---|---|
| [docs/系统架构.md](docs/系统架构.md) | 分层架构、技术边界、技术栈、数据模型、权限体系、状态机、已废弃组件、真实局限 |
| [docs/审核流程.md](docs/审核流程.md) | 上传 → 解析 → 分类 → 要素 → 规则 → 证据 → 裁决 → 建议 → 比对 → 报告 的逐步说明 |
| [docs/系统功能说明.md](docs/系统功能说明.md) | 页面与路由、各 Tab 功能、**API 一览（51 个端点）**、交互约定、功能边界 |
| [docs/风险规则说明.md](docs/风险规则说明.md) | R01–R13 名称/等级/触发条件/硬阈值/法条映射 + Gold 覆盖情况 |
| [docs/条款修改与修订机制.md](docs/条款修改与修订机制.md) | 两种修改范围、多轮会话、**采用态语义**、DOCX 修订版导出、R09 补充 |
| [docs/审核报告与可视化.md](docs/审核报告与可视化.md) | 审核报告 Tab、独立完整审核报告、13 类规则扫描、条款完整性、导航与位置记忆 |
| [docs/数据集说明.md](docs/数据集说明.md) | 分类/风险数据集规模与来源结构、Gold 规模与分布、脱敏、泄漏排查 |
| [docs/评测方法.md](docs/评测方法.md) | 三套正式评测的脚本/公式/冻结指标 + 非正式脚本说明 |
| [docs/风险标注与Gold规范.md](docs/风险标注与Gold规范.md) | 标注原则、边界规则、三条上位规则、质控（含如实的方法学说明） |
| [docs/项目目录说明.md](docs/项目目录说明.md) | 代码目录职责、已删除/归档组件、已知前端缺陷 |
| [docs/部署与复现.md](docs/部署与复现.md) | 环境、依赖、`.env`、启动、静启动、测试、评测复现、FAQ |
| [docs/参考论文与技术依据.md](docs/参考论文与技术依据.md) | 可核验的公开文献与技术点映射 |
| [docs/变更记录.md](docs/变更记录.md) | 变更台账 + 文档影响速查 + 待办 |
| [02_项目文档/总体会话-后端API契约.md](02_项目文档/总体会话-后端API契约.md) | 总体修改会话的字段级接口契约（前端对接用） |
| `A24-详细设计文档-v4.0.md` | 详细设计文档（申报交付物） |

---

## 九、当前系统的限制（如实列出）

1. **DOCX 修订版导出只支持 `.docx` 原始合同**；PDF / 图片合同可审核、可建立修改会话，但不能导出修订版文件。
2. **修订版不生成 Word 修订痕迹（Track Changes）**，也不做完整版本管理。
3. **审核报告没有正式导出接口**（无 PDF / Word 报告导出 API），只支持浏览器打印；报告页也不显示审核人 / 报告编号 / 报告版本号 / 审批状态（这些字段后端不存在）。
4. **R05 无 Gold、R13 不入 F1、R08/R09 Gold 覆盖有结构性限制**（见第一节）。
5. **SQLite 单写者**并发限制（缓解而非根治）；高并发需切 MySQL（非默认）。
6. **`risk_cases.json` 为空**（0 条），风险案例库仅预留。
7. **前端已知缺陷**：新增条款的对话框路径（`AddClauseWizard.vue`）因未定义的 `mode` 变量在运行时不渲染，可用路径是「修改合同」Tab 右栏的内联流程；模板「历史版本」按钮因缺少导入而表现为空。**本轮为文档重构，未修改代码。**
8. **总体修改会话的 UI 确认尚未接入**：「纳入方案」仅为前端状态，`POST /overview/confirm` 未被当前界面调用。

> 更完整的评测过程、FP 归因与设计演进记录在仓库外的 `../02_项目文档/`。
