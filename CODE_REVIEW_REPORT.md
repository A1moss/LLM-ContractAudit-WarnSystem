# LLM-ContractAudit-WarnSystem 代码审查报告 (v2)

**审查日期**: 2026-07-18
**审查范围**: 全部后端 Python 源码 + 全部前端 Vue.js 源码
**技术栈**: FastAPI + SQLAlchemy + SQLite (后端), Vue 3 + Element Plus + Vite (前端)

---

## 总体评估

| 维度 | 评分 | 说明 |
|------|------|------|
| **后端架构** | 7.0/10 | AI 管道、Corex 编排器、解析器、分类器骨架完整，但 RAG/matcher/reporter 三个模块只有 .gitkeep |
| **前端架构** | 6.5/10 | 10 个页面功能完整，路由设计合理，但存在 2 个 HIGH 级 bug + 多处静默吞异常 |
| **整体** | **6.5/10** | 核心业务闭环能跑通（上传→审核→结果→报告），工程化补全（测试/文档/Docker/降级）几乎空白 |

---

## 自上次审查以来的变化

| 事项 | 状态 |
|------|:--:|
| 路由守卫 (beforeEach) | ✅ 已修复 |
| pdfjs-dist v6 destroy() API | ✅ 已修复 (ContractDetail.vue + AuditReportDetail.vue) |
| 审核结果/报告页重构为列表页 + 详情子路由 | ✅ 已完成 |
| ContractList goToResult 跳转修复 | ✅ 已修复 |
| ContractUpload 上传后触发审核 → 跳转列表页 | ✅ 已完成 |
| AuditReport 列表页文件名/操作列跳转修复 | ✅ 已修复 |
| 上次报告的 Bug 1-4 | ❌ 四个仍在 |

---

## Bug — 需要修复 (共 12 个)

### HIGH (3 个)

#### H1: AuditReportDetail — 路由参数变化时图表和 PDF 不更新 · B
**文件**: `AuditReportDetail.vue:120-128, 219-224`

`watch(contractId, ...)` 在参数变化后只调了 `fetchReport()`，更新了文本数据，但 `initCharts()` 和 `renderPdf()` 只在 `onMounted` 中调用。从 `/audit/report/1` 跳转到 `/audit/report/2` 时，饼图、柱状图和 PDF 画布仍显示合同 1 的内容。此外 PDF 始终加载的是硬编码的 `/test.pdf`，与合同 ID 无关。

**修复**: watcher 回调中在 `fetchReport()` 完成后调用 `await nextTick(); initCharts(); renderPdf()`。`renderPdf` 需接受合同 ID 参数替换 `/test.pdf`。

#### H2: 分类器键名不匹配 (旧 Bug 2) · C
**文件**: `backend/api/contracts.py:56`

`classify_contract()` 返回 `{"contract_type": "采购合同", ...}`，但代码读取 `cls_result.get("type", "other")`。键名不匹配导致所有自动分类结果被丢弃，合同类型始终为 "other"。

**修复**: 改为 `cls_result.get("contract_type", "other")`。

#### H3: Self-QA 空结果逻辑反转 (旧 Bug 3) · D
**文件**: `backend/ai/corex/orchestrator.py:85`

```python
final = qa if qa and not any(r.get("failed") for r in qa) else all_risks
```

当 QA Agent 正确判断所有风险为假阳性返回 `[]` 时，空列表为 falsy，条件回退到未去重的 `all_risks`，去伪功能完全失效。

**修复**: 改为 `if qa is not None and not any(r.get("failed") for r in qa)`。

### MEDIUM (5 个)

#### M1: AuditResult — 分页 size-change 处理器入参错误 · B
**文件**: `AuditResult.vue:123-124`

```html
@size-change="fetchList"
```

Element Plus 的 size-change 事件传入新 page_size（如 20），`fetchList(page)` 将其赋值给 `pagination.page`，导致页码被覆盖为 size 值。页面会先发一次错误请求，再被 current-change 纠正。

**修复**: 改为 `@size-change="() => { fetchList(1) }"`。

#### M2: AuditReport — handleSelect 竞态条件 · B
**文件**: `AuditReport.vue:223-250`

用户快速点击两个合同时，两个 `handleSelect` 并发执行。如果先点击的请求后返回，最终展示的报告数据和图表属于先点的合同，但高亮选中的是后点的合同。

**修复**: 使用递增序号或 AbortController 实现"最后请求胜出"模式。

#### M3: ContractUpload — 审核触发静默失败 · A
**文件**: `ContractUpload.vue:185-186`

```js
triggerAudit(contractId).catch(() => {})
ElMessage.success('合同上传成功，审核已触发')
```

`triggerAudit` 失败时错误被吞掉，但成功消息无条件弹出。用户看到"审核已触发"实际审核可能根本没跑。

**修复**: 至少捕获错误后改为 `ElMessage.warning`。

#### M4: 外键类型不匹配 (旧 Bug 1) · C
**文件**: `backend/models/contract.py:13`, `backend/models/user.py:13`

`User.id` 是 `Integer`，`Contract.user_id` 是 `String(36)`。SQLite 静默接受，但切换到 MySQL 时建表失败。

**修复**: 统一类型，建议全局改用 UUID 字符串。

#### M5: pdf_parser context manager 退出后访问属性 (旧 Bug 4) · D
**文件**: `backend/ai/parser/pdf_parser.py:32`

`len(pdf.pages)` 在 `with pdfplumber.open(file_path) as pdf:` 块外执行。文件句柄已关闭，依赖 pdfplumber 的内存缓存可能在未来版本中失效。

**修复**: 将 `page_count` 移入 `with` 块内。

### LOW (4 个)

#### L1: AuditResult — N+1 API 调用 · B + C
**文件**: `AuditResult.vue:174-186`

`fetchList` 对列表中的每个合同调用 `getAuditResult(c.id)` 预取风险计数。每页 10 个合同 = 11 次串行 API 调用才能渲染列表。

**修复**: 后端提供批量风险计数端点，或将风险计数合并到合同列表 API 的响应中。

#### L2: ContractUpload — 上传失败不弹错误提示 · A
**文件**: `ContractUpload.vue:189-194`

catch 块只设置了进度条状态，未调用 `ElMessage.error`。用户如果滚动过了进度条区域，不会知道上传失败。

#### L3: ContractList — fetchList 静默吞异常 · A
**文件**: `ContractList.vue:185-188`

API 失败时只清空列表，不调用 `ElMessage.error`。用户看到空表格但不知道是没数据还是接口挂了。

#### L4: AuditReportDetail — PDF canvas 缺少空值守卫 · B
**文件**: `AuditReportDetail.vue:200-201`

`pdfCanvasRef.value.parentElement` 在 `await` 后 DOM 可能已卸载时为 null，直接访问 `.clientWidth` 会抛出 TypeError。

---

## 代码模式问题

### P1: `_extract_json` 重复 5 次 (仍存在) · D
**出现位置**: `llm_auditor.py`, `classifier.py`, `extractor.py`, `orchestrator.py`, `rule_engine.py`

相同的 LLM JSON 提取逻辑复制了 5 份。建议抽取到 `backend/ai/utils.py`。

### P2: 静默吞异常 (仍存在) · C + D
**出现位置**: `contracts.py`, `classifier.py`, `extractor.py`, `llm_auditor.py`

大量 `except Exception: pass` 导致问题难以排查。建议至少加 `logger.warning`。

### P3: 前端硬编码假数据 (部分改善) · A + B
- HomeView.vue 统计卡片和图表数据仍为硬编码
- ContractDetail.vue 仍引用特定 NDA 的页级摘要
- AuditReport.vue / AuditReportDetail.vue 固定加载 `/test.pdf`

### P4: 缺失的模块
| 模块 | 状态 | 负责人 |
|------|------|:--:|
| RAG 向量检索 (ai/rag/) | 只有 .gitkeep | E |
| 条款比对 (ai/matcher/) | 只有 .gitkeep | E |
| 报告生成 (ai/reporter/) | 只有 .gitkeep | E |
| 审核调度独立服务 (services/audit_service.py) | 不存在，逻辑在 contracts.py 里 | C |
| Dify 接口封装 (services/dify_client.py) | 不存在 | C + E |
| Dockerfile | 不存在 | C |
| schema.sql | 不存在 | C |
| 测试目录 (backend/tests/) | 不存在 | C + D + E |
| 反馈 API (backend/api/feedback_router.py) | 不存在，但 FeedbackPanel 组件和 FeedbackLog 模型已有 | C |

---

## 已修复 (自上次审查)

| 事项 | 文件 |
|------|------|
| 路由守卫 beforeEach | router/index.js |
| pdfjs-dist v6 destroy() 调用错误 | ContractDetail.vue, AuditReportDetail.vue |
| 审核结果按钮跳转到错误路由 | ContractList.vue goToResult |
| 审核报告页文件名/操作列跳转 | AuditReport.vue |
| 上传页进度条第二阶段无意义 | ContractUpload.vue |

---

## 建议修复优先级

| 优先级 | Bug | 理由 | 负责人 |
|--------|-----|------|:--:|
| P0 | H2 分类器键名 (旧 Bug 2) | 一行修复，AI 分类功能当前形同虚设 | C |
| P0 | H3 Self-QA 空结果 (旧 Bug 3) | 一行修复，Corex 去伪功能失效 | D |
| P0 | M3 审核触发静默失败 | 刚改的代码，用户可能被误导 | A |
| P1 | H1 AuditReportDetail 参数变化不刷新 | 审核报告核心页面的展示 bug | B |
| P1 | M1 AuditResult 分页错误 | 每次切换 page_size 都会触发 | B |
| P2 | M4 FK 类型 (旧 Bug 1) | SQLite 下不暴露，MySQL 部署时致命 | C |
| P2 | M5 pdf_parser (旧 Bug 4) | 当前不触发但违反规范 | D |
| P3 | L1-L4 + M2 | 体验问题和边界 case | A + B + C |

---

## 按负责人汇总

| 负责人 | Bug | 模式问题 | 缺失模块 |
|:------:|------|---------|---------|
| **A** | M3, L2, L3 | P3 | — |
| **B** | H1, M1, M2, L1, L4 | P3 | — |
| **C** | H2, M4, L1 | P2 | audit_service, dify_client, Dockerfile, schema.sql, feedback_router, tests |
| **D** | H3, M5 | P1, P2 | tests |
| **E** | — | — | rag, matcher, reporter, dify_client, tests |

> L1 同时涉及 B 和 C，需后端配合新增批量接口。
