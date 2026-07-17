# LLM-ContractAudit-WarnSystem 代码审查报告 (Day 1)

**审查日期**: 2026-07-17  
**项目阶段**: Day 1 — 骨架代码 + 原型 (开发指南预期：Day 1 为"设计文档通读 + 骨架代码 + 原型草图")  
**审查范围**: 全部后端 Python 源码 (22 文件) + 全部前端 Vue.js 源码 (14 文件)  
**技术栈**: FastAPI + SQLAlchemy + SQLite (后端), Vue 3 + Element Plus + Vite (前端)

---

## 重要说明：Day 1 上下文

本报告与开发指南《A24-开工准备指南-v4.md》对照编写。指南明确规划了 15 天开发路线：
- **Day 0 (7.17)**: 环境搭建
- **Day 1 (7.18)**: 骨架代码 + 原型草图
- **Day 2-3 (7.19-20)**: 数据库建表 + 登录注册 + 上传/分类 + RAG/Dify
- **Day 4-5 (7.22-23)**: 审核引擎 + 审核报告 + 条款比对
- **Day 10+ (7.29+)**: Corex Review、BERT 训练等高级功能

**当前代码实际上已超前于 Day 1 规划**——已实现了审核引擎、Corex 编排器、分类器等 Day 3-5 的功能。以下将每个问题标注为"需要现在修复的真 bug"或"按计划后续实现的功能"。

---

## 总体评估

| 维度 | 评分 | Day 1 预期对比 |
|------|------|---------------|
| **后端架构** | 7.0/10 | 超前——AI 管道、解析器、分类器、Corex 已实现骨架，超出 Day 1 "骨架代码"规划 |
| **前端架构** | 6.0/10 | 正常——5 个页面骨架 + 路由 + API 层就绪，符合 Day 1 预期，甚至超前（图表和 PDF 已集成） |
| **整体** | **6.5/10** | 项目已超前日程，代码骨架质量好。重点是修 4 个真 bug + 统一代码模式 |

---

## 需要现在修复的真 Bug (共 4 个)

这些是不管开发第几天都该修的代码缺陷，与进度无关。

### Bug 1: 外键类型不匹配
**文件**: `backend/models/contract.py:13`, `backend/models/user.py:13`  
**问题**: `Contract.user_id` 是 `String(36)`, `User.id` 是 `Integer`。外键约束无法建立。  
**影响**: SQLite 可能静默接受，但切换到 MySQL 时建表失败。  
**修复**: 统一为相同类型。建议 `User.id` 也改为 `String(36)` + UUID，或者 `Contract.user_id` 改为 `Integer`。团队 C 在 Day 2 建模时应一并修复。  
**应修复时间**: Day 2（7.19，数据库建表日）

### Bug 2: 分类器键名不匹配
**文件**: `backend/api/contracts.py:56`  
**问题**: 分类器返回 `{"contract_type": "采购合同", ...}`，代码读 `cls_result.get("type", "other")`。所有上传合同的类型被存为 "other"。  
**影响**: AI 分类功能形同虚设。  
**修复**: 改为 `cls_result.get("contract_type", "other")`。  
**应修复时间**: Day 3（7.20，合同上传+分类器集成日）

### Bug 3: Self-QA 空结果逻辑反转
**文件**: `backend/ai/corex/orchestrator.py:85`  
**问题**: `if qa and not any(r.get("failed") for r in qa)` — 当 Self-QA 正确判定所有风险为假阳性返回空列表 `[]` 时，条件为 `False`，回退到未去重结果。  
**影响**: Self-QA 的去伪功能完全被破坏。  
**修复**: 改为 `if qa is not None and not any(r.get("failed") for r in qa):`  
**应修复时间**: Day 10（7.29，Corex Review 开发日）——但建议现在就修，因为这是个一行修改。

### Bug 4: pdf_parser.py context manager 退出后访问属性
**文件**: `backend/ai/parser/pdf_parser.py:32`  
**问题**: `pdf.pages` 在 `with pdfplumber.open(file_path) as pdf:` 块退出后访问。虽然 pdfplumber 内存缓存使其当前可用，但违反了 Python 资源管理惯例。  
**修复**: 将 `page_count = len(pdf.pages)` 移入 `with` 块内。  
**应修复时间**: 随时（一行移动即可）

---

## 代码模式问题 — 建议在 Day 2-3 统一修复

这些问题不是单个 bug，而是贯穿代码库的模式问题。越早统一，后面越省力。

### P1: `_extract_json` 重复 5 次 (Shotgun Surgery)
**出现位置**: `llm_auditor.py:42-69`, `classifier.py:22-42`, `extractor.py:41-59`, `orchestrator.py:12-32`, `rule_engine.py:43-47`  
**问题**: 相同的 LLM JSON 提取逻辑（处理 markdown 代码块、部分提取等）复制了 5 份。  
**修复**: 抽取到 `backend/ai/utils.py` 或 `backend/ai/llm_utils.py` 作为共享函数。  
**应修复时间**: Day 2-3（趁模块还在骨架阶段，改起来成本低）

### P2: 静默吞异常模式
**出现位置**: `contracts.py`, `classifier.py`, `extractor.py`, `llm_auditor.py`  
**问题**: 大量 `except Exception: pass` 将错误完全静默。开发阶段可以理解（快速跑通），但需要有计划地替换。  
**修复**: 至少加 `logger.warning(f"xxx failed: {e}")`。Day 5 开发指南明确要求 D 同学"规则引擎增加错误处理"。  
**应修复时间**: Day 4-5（审核引擎完善阶段）

### P3: 前端硬编码假数据
**出现位置**: `HomeView.vue`（统计卡片、图表数据、近期合同列表）, `ContractDetail.vue`（页级摘要引用特定 NDA 内容）, `AuditReport.vue`（固定加载 test.pdf）  
**评估**: 这**不是 bug**——开发指南 Day 1-2 明确写了"数据先写死，后面接真实数据"。Day 2 A 验收标准就是"首页 Dashboard（卡片+表格有数据）"，Day 3 开始接 API。  
**应修复时间**: Day 3-5（分别对接各页面 API）

### P4: 缺少路由守卫
**问题**: `router/index.js` 没有 `beforeEach` 守卫。  
**评估**: 开发指南 Day 1 A 明确写了"配好 5 条路由（含路由守卫，未登录跳 /login）"，Day 2 A 也写了"在 router/index.js 加路由守卫"。这是按计划 Day 1-2 完成的。  
**应修复时间**: Day 2（7.19）

---

## 按计划后续实现的功能 (非问题)

以下模块当前为空或未实现，但开发指南已规划了具体的开发日期：

| 模块 | 当前状态 | 计划开发日 | 开发指南引用 |
|------|---------|-----------|------------|
| RAG 向量检索 | 空目录 (`.gitkeep`) | Day 2-3 | "E — 知识库初始化 + ChromaDB 连通" |
| 标准条款比对 (matcher) | 空目录 (`.gitkeep`) | Day 5 | "E — 创建 matcher.py，双层对齐" |
| 报告生成 (reporter) | 空目录 (`.gitkeep`) | Day 5 | "E — 报告生成 + 条款比对" |
| BERT 本地分类器 | 未实现（仅有 LLM 分类） | Week 4 (8.4-8.10) | "BERT 微调训练（5 分类）" |
| OCR 解析器 | 已实现但 API 层未接入 | Day 12 (7.31) | "OCR 基础集成" |
| 审计操作日志 | 未实现 | Day 6+ | "反馈 API + 错误处理统一" |
| Dify 工作流集成 | 未实现 | Day 2 | "E — Dify 工作流搭建" |
| 反馈标注面板 | 未实现 | Day 6 | "A+B — 反馈标注面板" |

---

## 架构亮点 (值得保留的做法)

以下设计决策值得在后续开发中继续保持：

1. **AI 模块作为 Python 函数调用而非 HTTP 微服务**：`contracts.py` 直接 import AI 模块，避免了 RPC 网络开销和序列化问题。与开发指南的"协作边界约定"一致。

2. **多 Agent Corex 架构**：法务→合规→财务→Self-QA 四步流水线设计合理，能有效缓解单 LLM 视角偏差。

3. **统一解析器入口**：`detect_and_parse(filepath)` 根据扩展名分发的设计使得调用方无需关心文件类型。

4. **LLM JSON 提取的防御性设计**：处理 markdown 代码块、部分提取、正则回退等场景，说明团队对 LLM 输出不稳定性有认知。

5. **前端 lazy loading**：所有路由组件使用 `() => import(...)` 懒加载。

---

## 建议的开发节奏调整

基于当前代码已超前日程的实际情况：

| 优先级 | 事项 | 建议日期 |
|--------|------|---------|
| 今天 (Day 1) | 修复 Bug 4 (pdf_parser) + Bug 3 (Self-QA 一行修复) | 立刻 |
| Day 2 | 修复 Bug 1 (FK 类型) + P1 (_extract_json 抽取) | 数据库建模时统一 |
| Day 3 | 修复 Bug 2 (分类器键名) | 分类器集成时修复 |
| Day 4-5 | 替换裸 except (P2) + 去重逻辑 | 审核引擎完善时 |
| Day 10 | 前后端数据解包约定统一 (P3 潜在问题的根源) | Corex 开发时 |

---

## 给各角色的具体建议

### C (后端)
- **Day 2 建表时**：修复 FK 类型不匹配。建议全项目统一用 UUID 字符串作主键。
- **Day 4 写 audit_service 时**：确保每步异常单独捕获并记录，参考指南"每个步骤的异常单独捕获并记录，不阻塞后续步骤"。
- **现在就可以做**：把 `_extract_json` 抽取到 `backend/ai/utils.py`，然后让 D/E 同学引用。

### D (AI 引擎)
- **Day 2-3 完善分类器时**：统一与 C 的接口约定（`contract_type` 键名），避免前后不一致。
- **Day 4 规则引擎时**：抽出魔法数字到常量配置。
- **Day 10 Corex 开发前**：修复 Self-QA 空结果判断。

### E (AI 引擎)
- **Day 2-3 RAG 开发时**：设计好 `search()` 的接口，与 D 的 `audit_with_llm(rag_context=...)` 对接。
- **Day 5 条款比对时**：与 A/B 约定前端展示数据格式。

### A (前端)
- **Day 2 登录页 + 首页时**：注意 `res.data.token` vs `res.token` 的数据解包层级，建议与 C 约定统一的响应格式。
- **Day 3 合同上传时**：用指南中的 A 提示词"首页 AI 测试按钮"作为前后端联调入口。

### B (前端)
- **Day 1-2 图表 + PDF 时**：可以保留硬编码数据，但建议给假数据的变量名加上 `MOCK_` 前缀，方便后续搜索替换。
- **Day 4 审核结果页时**：注意审核时序——审核是异步的，需要轮询 status 而非立即取结果。
