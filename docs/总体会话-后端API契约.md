# 总体修改会话 · 后端 API 契约（前端对接用）

> 本文档由**只读核对实现**产出（`backend/api/overview.py`、`models/revision_proposal.py`、`api/contracts.py::download_revised_docx`）。
> 未修改任何代码。字段名、状态取值、判定条件均与实现逐条对齐；标注了实现细则与已知边界。
>
> 后端路由前缀：`/api`（`main.py` 中 `include_router(overview_router, prefix="/api")`）。
> 前端 `utils/request.js` 的响应拦截器**已经剥掉 HTTP 层**（`return response.data`），
> 因此下文 `res` 指**响应体**（`{code, message, data}`），业务数据取 `res.data`。

---

## 0. 产品语义（决定了前端怎么组织界面）

```
总体会话
  → 读取全部专项修改会话（GET /overview）
  → 形成综合上下文
  → AI 分析整份合同的修改关系（POST /overview/plan）
  → 形成结构化修改方案（逐项：replace / add_clause）
  → 用户查看每一项
  → 用户确认（POST /overview/confirm，逐项选择）
  → 每一项转换成现有安全的 clause / add_clause 修订
  → 复用现有定位 + 现有 DOCX 安全导出
```

前端必须体现的三件事：

1. **方案 ≠ 修改**。`/overview/plan` 只产出"待确认清单"，一条合同修改记录都不会生成。
2. **确认是逐项的**，不是整包提交。用户可以只勾选其中几项。
3. **未定位的项不能提交**，必须先让用户指出位置（原文片段或条款编号）。

---

## 1. 端点总表

| # | 方法 | 路径 | 是否调 LLM | 是否需要 LLM Key | 是否写库 |
|---|---|---|---|---|---|
| 1 | GET | `/api/contracts/{id}/overview` | 否 | 否 | 否（纯只读） |
| 2 | POST | `/api/contracts/{id}/overview/plan` | 是（1 次） | **是** | 只写方案表 |
| 3 | GET | `/api/contracts/{id}/overview/proposals` | 否 | 否 | 否 |
| 4 | GET | `/api/contracts/{id}/overview/proposals/{pid}` | 否 | 否 | 否 |
| 5 | POST | `/api/contracts/{id}/overview/confirm` | 否 | 否 | **写 ClauseRevision** |

依赖与错误码（5 个端点通用）：

| 状态码 | 触发条件 | `detail` |
|---|---|---|
| 401 | 未登录 / token 失效 | 前端拦截器自动跳登录页 |
| 404 | 合同不存在**或无权查看**（上传者只能看自己的；审核人/验收人/admin 可看全部；已删除合同不可见） | `"contract not found"` |
| 404 | （4、5）方案不存在，或方案不属于该合同 | `"proposal not found"` |
| 400 | （2）合同正文为空 | `"合同正文为空，无法生成整体修改方案"` |
| 400 | （2）无可用 DeepSeek Key（个人 Key 与 .env 默认 Key 都缺） | 提示去「个人信息」页配置个人 Key |
| 400 | （5）`items` 为空数组 | `"请至少选择一个修改项进行确认"` |
| 502 | （2）LLM 调用异常 / 返回不可解析 / 无任何有效修改项 | `"生成整体修改方案失败：…"` |

> 前端统一按现有拦截器行为处理即可：`detail` 原样展示，不要改写成"系统异常"。

---

## 2. `GET /api/contracts/{id}/overview`（① ② 总览）

### 请求

无 body、无 query。

### 响应（`res.data` 完整结构）

```jsonc
{
  "contract_id": 12,

  // ── 总体会话自身状态（不是"专项会话"）──
  "overview": {
    "key": "__overview__",
    "scope": "overview",
    "revision_count": 3,          // 该合同下 clause_key=__overview__ 的历史修订条数
    "last_revision_id": 88,       // 无记录时 null
    "last_instruction": "…",      // 最后一轮的用户要求；无记录 ""
    "last_result": "…",           // 最后一轮的修订文本；无记录 ""
    "updated_at": "2026-09-04T10:00:00Z",  // 无记录 null
    "writes_docx": false          // 恒为 false：总体会话自身的 replace 讨论稿永不写入 DOCX
  },

  // ── 全部专项修改会话（按 clause_key 首现顺序）──
  "sessions": [ /* 见 2.1 */ ],

  "counts": {
    "sessions": 25,               // 专项会话数
    "revise_sessions": 24,        // 其中"非新增条款"的会话数
    "by_kind": { "risk": 18, "cmp": 6, "add": 1, "history": 0 },
    "total_revisions": 41         // 该合同全部修订记录条数
  },

  // ── 导出就绪度（提示用；权威判定见第 7 节）──
  "export": {
    "exportable_count": 40,       // 会被 DOCX 端点取到的记录数
    "blocker_count": 1,           // 会阻断导出的记录数（无原文锚点的替换型）
    "ready": false                // = exportable_count > 0 且 blocker_count == 0
  }
}
```

### 2.1 `sessions[]` — 每个专项会话的字段

| 字段 | 类型 | 含义与来源 |
|---|---|---|
| `key` | string | 会话标识 = `ClauseRevision.clause_key`。风险来源是 `str(AuditRecord.id)`；比对来源是 `"__cmp__"+标题`；无 key 的历史脏数据是 `"__orphan__{id}"`。**前端不要自己解析语义，用 `kind`** |
| `kind` | `"risk"` \| `"cmp"` \| `"add"` \| `"history"` \| `"overview"` | 分类（优先级固定）：`__overview__` → overview；该会话全部记录都是 add_clause → add；key 以 `__cmp__` 开头 → cmp；key 命中该合同 AuditRecord.id → risk；其余 → history |
| `title` | string | 展示标题。risk → `"R01（high）"` 形式；cmp → `"条款比对·验收标准"`；add → `"新增条款会话"`；history → `"历史修改会话"`；overview → `"总体会话下的新增条款"` |
| `scope` | `"clause"` \| `"overview"` | 最后一轮记录的 scope |
| `operation` | `"replace"` \| `"add_clause"` | 最后一轮记录的操作 |
| `revision_count` | int | 该会话轮次数 |
| `last_revision_id` | int\|null | 最后一轮 `ClauseRevision.id` |
| `created_at` / `updated_at` | string\|null | ISO8601（UTC，带 `Z`）；首轮 / 末轮时间 |
| `clause_no` | string\|null | 条款编号（取该会话最后一条有值的）；无则 null |
| `position` | object\|null | 新增条款的插入位置（`{"anchor":"五","hint":"…"}` 或 `{"append":true,"hint":"…"}`） |
| **`original_text`** | string | **当前原文**（①）：已定位时为 DOCX 实际替换的逐字原文；未定位时退回首轮 `clause_text`；新增型为 `""` |
| **`original_source`** | `"anchor"` \| `"first"` \| `"none"` | 上面那个值的可信度：`anchor` = 有可导出锚点（可靠）；`first` = 仅首轮输入文本（**不可导出**）；`none` = 无原文 |
| **`revised_clause`** | string | **当前最新修改结果**（②） |
| `explanation` | string | 最新一轮的修订说明 |
| `instruction` | string | 最新一轮的完整 instruction（**可能含前端拼接的上下文块**，展示前需剥离，见 2.2） |
| **`legal_basis`** | string[] | 法律依据（③），空数组表示无 |
| **`remaining_risks`** | string[] | 剩余风险（④），空数组表示无 |
| `constraints` | string[] | 最新一轮的硬性约束 |
| **`located`** | bool | ⑤ 是否已建立可靠原文锚点 |
| `anchor_text` | string | 已建立的锚点原文；未定位为 `""` |
| **`location_state`** | `"located"` \| `"pending"` \| `"none"` | `located` 有锚点；`pending` 有文本但无锚点（**导出会被 400 拦下**）；`none` 无原文（缺失/新增型） |
| **`export`** | object | ⑤ 该会话的 DOCX 状态，见 2.3 |

### 2.2 `instruction` 的展示注意

后端**原样返回**入库时的 instruction。专项会话的 instruction 里可能含前端拼接的
`【当前修改依据】…【用户修改要求】…` 前缀块（由 `useContractWorkspace.js` 的
`withSessionContext` 生成）。前端若要展示"用户最终要求"，请复用已有的
`displayInstruction(text)`（`composables/workspaceLogic.js`），它按
`'【用户修改要求】'` 最后一次出现位置截断。**不要显示拼接块和 API 路径**。

总体会话确认落库的记录，其 instruction 前缀是：

- 替换型：`【总体会话综合方案确认】…`
- 新增型：`【总体会话综合方案确认为新增条款】…`

### 2.3 `sessions[].export` 字段

```jsonc
{
  "exportable": true,       // 该会话当前是否具备被写进修订版 DOCX 的条件
  "applied": 2,             // 已具备条件、会被写入的记录数
  "blocker": ""             // exportable=false 时的中文原因；否则 ""
}
```

判定口径（与导出端点镜像）：

- 只有 `scope=="clause"` 或 `scope=="overview" && operation=="add_clause"` 的记录会被取用；
- 替换型必须有 `original_clause_text`，否则计入 blocker；
- 新增型必须有可解析的 `position`，否则计入 blocker。

`blocker` 的三种文案：`"记录不参与修订版合同导出"` / `"有 N 条修改尚未建立可靠原文定位"`。

### 2.4 空态

- 该合同没有任何修订记录：`sessions = []`、`counts.sessions = 0`、`export.exportable_count = 0`、`export.ready = false`。
- 端点**永不返回 404 以外的失败**，可以安全重复调用（不读文件、不调 LLM、不写库）。

---

## 3. `POST /api/contracts/{id}/overview/plan`（③ ④ 生成方案）

### 请求体

```jsonc
{
  "instruction": "结合前面讨论过的问题，统一调整违约金与验收安排。",
  "session_keys": []           // 可选：只统筹这些会话；省略或 [] = 全部专项会话
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `instruction` | string | 否（默认 `""`） | 用户对整个合同的修改要求。**留空不是错误**，后端会替换为默认指令"请从整份合同的角度统筹所有已发现的问题，给出统一的修改方案。"；但前端应做非空校验并提示，避免用户以为自己的话被采纳 |
| `session_keys` | string[] | 否（默认 `[]`） | 只把这些 `sessions[].key` 纳入统筹。传了不存在的 key = 该 key 被忽略（空 focus 会变成"什么都排除"**不会**发生：非空 focus 时才过滤，全部不匹配则纳入 0 个会话、LLM 仍能基于整份合同产出） |

### 后端做的三件事（前端要知道的输入规模）

1. 组装上下文：整份合同正文（**超过 24000 字降级**为"标题结构纲要 + 当前审核风险条目"，并在 prompt 里显式标注）、合同标题结构、每个专项会话的原文/最新结果/法律依据/剩余风险/插入位置/导出阻塞原因。
2. 单次 LLM 调用（`temperature=0.1`），要求只输出**逐项修改清单**，明令禁止输出整份合同重写稿。
3. 对每一项做**确定性定位**（不调 LLM）：replace → 逐字原文锚点；add_clause → 插入位置。

### 响应（`res.data`）

```jsonc
{
  "proposal_id": 7,             // int，后续 confirm 用的就是这个
  "contract_id": 12,
  "status": "draft",            // 新方案恒为 draft
  "summary": "整体修改方案的思路与影响范围……",
  "instruction": "…",           // 后端实际使用的指令（可能是默认补的）
  "created_at": "2026-09-04T10:00:00Z",
  "items": [ /* 见第 4 节 */ ],
  "confirmed_ids": [],          // 新方案恒为空
  "counts": {
    "total": 6,
    "confirmed": 0,
    "replace": 5,
    "add_clause": 1,
    "needs_location": 1         // 未确认且 resolved=false 的项数（前端要提示用户先处理）
  },
  "context": {
    "sessions_included": 25,
    "contract_text_truncated": false,   // true = 合同过长，已降级为纲要
    "sessions": [                       // 本次纳入的会话摘要（把修改项对回来源会话用）
      { "key": "12", "title": "R01（high）", "kind": "risk",
        "operation": "replace", "located": true, "clause_no": "3",
        "original_text": "第三条 违约责任与违约金上限为合同总价的30%。" }
    ]
  },
  "aggregate": {
    "counts": { "sessions": 25, "revise_sessions": 24,
                "by_kind": { "risk": 18, "cmp": 6, "add": 1, "history": 0 },
                "total_revisions": 41 },
    "export": { "exportable_count": 40, "blocker_count": 1, "ready": false }
  }
}
```

### 重要语义

- **本端点不生成任何 `ClauseRevision`**。刷新页面后合同仍无新修改，必须走 confirm。
- 一次最多保留 **30 项**（超出部分被丢弃，不是报错）。
- LLM 返回里 `revised_clause` 为空的项会被**静默丢弃**（不占位）。
- 全部项都无效 → 502，前端提示用户"补充要求后重试"。
- **会调用 LLM，耗时较长**：前端请用 `OVERVIEW_PLAN_TIMEOUT`（180s，已在 `api/timeouts.js`）。响应等待期间必须给 loading 态，并禁用重复提交（该端点会写库，不要自动重试）。

---

## 4. `items[]` 的字段（贯穿 plan / proposals / confirm）

每个修改项在**三个端点里的字段集合完全相同**，只是取值会随确认进度变化。

| 字段 | 类型 | 阶段 | 含义 |
|---|---|---|---|
| `id` | string | 后端生成 | 项标识，形如 `"p1"`、`"p2"`（`p` + 顺序号）。**confirm 时用它** |
| `operation` | `"replace"` \| `"add_clause"` | LLM 输出 | 替换已有条款 / 新增条款 |
| `target_session_key` | string | LLM 输出 | 挂到哪个专项会话。`""` = 全新目标。已存在则复用该会话的锚点（同一链条只保留最终结果）。**LLM 编造的不存在的 key 会被后端重置为 `""`** |
| `clause_no` | string | LLM 输出，定位失败后可补 | 条款编号，如 `"3"`。无则 `""` |
| `original_quote` | string | LLM 输出，**resolved=false 时可由用户覆盖** | 用户在确认时提供的合同正文**逐字片段** |
| `revised_clause` | string | LLM 输出，**可覆盖** | 改后 / 新增的条款全文 |
| `reason` | string | LLM 输出 | 为什么这么改（会写进 `ClauseRevision.instruction` 与 `explanation`） |
| `legal_basis` | string[] | LLM 输出 | 依据的法条（会写进 `ClauseRevision.legal_basis`） |
| `position` | object\|null | LLM 输出，**可覆盖** | **仅 add_clause 有意义**，两种合法形态：`{"anchor":"五","hint":"第五条之后"}`（插在该编号条款之后）或 `{"append":true,"hint":"追加到合同末尾"}`。`anchor` 只接受能解析的中文/阿拉伯数字编号；**不支持"第X条之前"**（前端要翻译成"第 X-1 条之后"，第一条无法表达） |
| `resolved` | bool | 后端判定 | **前端最重要的字段**：是否已能可靠落地。false 的项不能被确认 |
| `resolved_by` | `"session"` \| `"quote"` \| `"clause_no"` \| `"position"` \| `""` | 后端判定 | 定位来源：复用专项会话锚点 / 逐字原文定位 / 条款编号定位 / 新增位置 |
| `anchor_text` | string | 后端判定 | 后端定位到的**逐字原文锚点**（replace 成功时）。前端**只用于展示**，不要自己算、也不要回传 |
| `blocking_reason` | string | 后端判定 | `resolved=false` 时的中文原因，直接展示给用户 |
| `suggested_position` | object\|null | 可能不存在 | **仅 add_clause 且缺位置时出现**，是 `_suggest_position` 的建议位置（插在"争议解决"之前一条 / "违约责任"之后 / 末尾）。**只是建议**，前端必须让用户确认后再提交，绝不默认追加到末尾 |

> ⚠️ 前端注意：`proposal_id` 是 **int**，`items[].id` 是 **string**。confirm 请求里 `items[].id` 必须是字符串（后端会做 `str()` 归一化，但别依赖它）。

---

## 5. `resolved=false` 前端应该怎么处理

这是本轮设计的核心安全边界：**AI 不能偷偷替用户决定改哪里；定位不了就明确拒绝，而不是猜一个位置写进合同。**

### 5.1 三种 `blocking_reason` 文案与应对

| `operation` | `blocking_reason` | 界面应对 |
|---|---|---|
| replace | `"缺少条款逐字原文，无法建立可靠定位，请指定要修改的条款位置"` | 提供"指定位置"入口：**推荐让用户在合同原文里选中片段**（回传 `original_quote`）；也可让用户填条款编号（回传 `clause_no`） |
| replace | `"无法在合同正文中定位该条款原文，请重新给出逐字原文或指定条款位置"` | 同上。说明 AI 给的原文片段在合同里找不到 |
| replace | `"目标会话尚未建立可靠原文定位，请先在对应专项会话中确认修改位置"` | 引导用户去对应专项会话先确认位置；或在总体会话里直接回传 `original_quote` |
| add_clause | `"缺少插入位置；需由用户确认插入位置后再确认该修改项"` | 用 `suggested_position` 作为**默认候选**（可高亮），让用户在下拉/选择器里确认，回传 `position` |

### 5.2 界面状态建议

- `resolved=true`：显示"已定位"+ 条款编号 + `anchor_text` 摘要，勾选框**默认可用**。
- `resolved=false`：显示 `blocking_reason`，勾选框**置灰并附"需先指定位置"**；提供 [指定修改位置] 按钮。**不要**允许用户直接勾选提交，否则会收到一条 `failed`。
- 全部项都 `resolved=false` 时，主按钮禁用并提示"请先为至少一项确认修改位置"。

---

## 6. 用户指定位置时需要传什么

`POST /api/contracts/{id}/overview/confirm` 的单项结构：

```jsonc
{
  "id": "p3",                    // 必填，来自 items[].id
  "revised_clause": "…",         // 可选，覆盖 AI 给的条款文本（非空）
  "original_quote": "…",         // 可选，replace 用：合同正文中的逐字片段
  "clause_no": "3",              // 可选，条款编号（中文/阿拉伯数字）
  "target_session_key": "12",    // 可选，改挂到哪个会话（"" = 不挂会话，新建一条）
  "position": { "anchor": "四", "hint": "第四条之后" }   // 可选，add_clause 用
}
```

**字段语义（都是"传了才覆盖，不传保留方案原值"）**：

| 需求 | 传什么 | 后端行为 |
|---|---|---|
| 直接确认（已定位） | 只传 `{"id":"p3"}` | 用方案里的锚点/位置落库 |
| 编辑条款文本后确认 | `{"id","revised_clause"}` | 用新文本；锚点仍由后端确定性定位 |
| **replace 定位失败 → 用户选中原文** | `{"id","original_quote":"<逐字原文>"}` | **丢弃旧定位**，用该片段重新定位（走 `_locate_clause`，与审核/修订链路同一算法），成功后 `clause_no` 会被自动补全 |
| replace 定位失败 → 用户只给条号 | `{"id","clause_no":"3"}` | 按"第X条"在正文中找，取该处窗口作为锚点 |
| **add_clause 定位失败 → 用户选插入位置** | `{"id","position":{"anchor":"四"}}` | 落库为新增条款修订，位置即所传 |
| add_clause → 追加到末尾 | `{"id","position":{"append":true,"hint":"追加到合同末尾"}}` | 追加（`append` 为真，`anchor` 被忽略） |
| 改挂到另一个专项会话 | `{"id","target_session_key":"12"}` | 该修订会并入会话 `12` 的链条（DOCX 链式归并只保留最终结果） |

**注意与已知边界：**

- `previous_position` 与 `position` 的区别：**用户必须显式传 `position` 才能改位置**；不传则沿用方案里的值。
- `original_quote` 传空串 = 直接失败（`"指定的原文片段为空"`），**不是**"清空定位"。
- ⚠️ **只改 `target_session_key` 时，用来定位的 `original_quote` 仍是方案里的旧值**。如果那个旧值本来就是 AI 改写过的（原本就定位失败），改挂会话后依然会失败。**前端在"改挂会话"的同时，最好一并回传 `original_quote`（或 `clause_no`）**，否则可能定位到别的条款。
- 锚点**永远由后端重新计算并在 `parsed_text` 里复核**，前端不需要也不应该回传 `anchor_text`。

### 6.1 请求体整体形态

```jsonc
{
  "proposal_id": 7,
  "items": [ {"id": "p1"}, {"id": "p3", "original_quote": "违约责任与违约金上限为合同总价的30%"} ]
}
```

- `items` 为空数组 → **400**（`"请至少选择一个修改项进行确认"`）。前端应提前禁用按钮。
- 多选提交是允许的：**逐项独立事务**，一项失败不影响其他项。

---

## 7. `POST /confirm` 响应：已确认 / 已应用 / 失败 三种状态

### 响应结构

```jsonc
{
  "proposal": { /* 与第 4 节同构的方案对象，反映确认后的最新状态 */ },
  "applied": [
    {
      "id": "p1",
      "revision_id": 91,                    // 新生成的 ClauseRevision.id
      "operation": "replace",
      "clause_key": "12",                   // 挂到的会话 key；"" = 未挂会话
      "clause_no": "3",
      "clause_text": "第三条 违约责任与违约金上限为合同总价的30%。",  // 链条根（重排放）
      "original_clause_text": "第三条 违约责任与违约金上限为合同总价的30%。", // DOCX 锚点
      "revised_clause": "违约责任与违约金上限为合同总价的10%。",
      "position": null,
      "exportable": true                    // 这条记录本身是否会被 DOCX 取用
    }
  ],
  "failed": [
    { "id": "p4", "reason": "该修改项已确认落库，请勿重复确认" },
    { "id": "p6", "reason": "无法在合同正文中定位该条款原文，请重新给出逐字原文或指定条款位置",
      "needs_location": true }              // 仅部分失败分支带此字段
  ],
  "aggregate": { "counts": {…}, "export": {"exportable_count":…, "blocker_count":…, "ready":…} },
  "docx_hint": "修改项已写入既有条款修改链路，可在「修改合同」工作台下载修订版合同。"
}
```

### 三个概念必须分清（前端不要混用）

| 概念 | 在哪里 | 含义 |
|---|---|---|
| **已确认** | `proposal.confirmed_ids`（string[]）与 `proposal.counts.confirmed` | 历史上**累计**已成功落库的项 id（含本次之前各次）。**不是本次结果** |
| **本次已应用** | 响应的 `applied[]` | **只看本次提交**成功落库的项。列表按请求顺序 |
| **本次失败** | 响应的 `failed[]` | 本次提交**没能落库**的项 + 原因。已确认项重复提交也会出现在这里 |

> 典型误用：拿 `applied.length` 判断"方案是否全做完"。正确做法是看
> `proposal.status` 或 `counts.confirmed / counts.total`。

### `proposal.status` 状态机

| 取值 | 条件 |
|---|---|
| `"draft"` | 新方案；**或**本次没有任何项被确认（全部失败） |
| `"partially_applied"` | 已确认项数 < 总项数 |
| `"applied"` | 已确认项数 >= 总项数 |

注意 `status` 只描述**方案**，不描述合同状态；合同是否可下载另看第 8 节。

### `failed[].reason` 全量清单

| `reason` | 带 `needs_location` | 前端应对 |
|---|---|---|
| `该修改项不属于这份方案` | 否 | 前端 bug（id 串了方案），刷新方案后重试 |
| `该修改项已确认落库，请勿重复确认` | 否 | 从待确认列表移除该项（说明确认成功了） |
| `修改后的条款内容不能为空` | 否 | 编辑框非空校验 |
| `指定的原文片段为空` | 否 | 用户清空了原文输入，要求重新选 |
| `缺少插入位置；需由用户确认插入位置后再确认该修改项` | 是 | 走位置选择器（`suggested_position` 作候选） |
| `缺少条款逐字原文，无法建立可靠定位，请指定要修改的条款位置` | 是 | 让用户在原文中选中 |
| `无法在合同正文中定位该条款原文，请重新给出逐字原文或指定条款位置` | 是 | 让用户重新选中/换位置 |
| `目标会话尚未建立可靠原文定位，请先在对应专项会话中确认修改位置` | 是 | 引导去专项会话，或直接回传 `original_quote` |
| `原文锚点无法在合同正文中复核，已拒绝落库（避免导出漏改/改错位置）` | 是 | 让用户重新指定位置（说明锚点与合同正文不一致） |
| `插入位置「X」无法识别（只支持「第X条之后」或「追加到末尾」）` | 是 | 位置选择器限定这两种 |
| `插入位置缺少条款编号锚点` | 是 | 同上 |
| `去掉重复的条款编号后内容为空，请补全条款正文` | 否 | 条款正文只有编号，要求补内容 |
| `落库失败，请重试` | 否 | 服务端偶发错误，可安全重试该项 |

### 提示文案建议

- `applied.length > 0`：提示"已应用 N 项修改，可下载修订版合同"（并引导去导出）。
- `failed.length > 0`：**逐项列出 `reason`**，原样展示（后端文案已经是面向用户的中文），不要吞掉、不要统一改成"部分失败"。
- 含 `needs_location: true` 的失败：滚动定位到那一项并展开"指定位置"面板。

---

## 8. DOCX 下载什么时候允许

下载走**既有端点**（本轮没有改动它）：`GET /api/contracts/{id}/revised-docx`（返回 Blob）。

### 后端硬条件（按顺序判定，任一不满足即失败并返回对应 detail）

| 顺序 | 条件 | 失败响应 |
|---|---|---|
| 1 | 合同存在且有权查看 | 404 `contract not found` |
| 2 | 源格式在**可导出白名单**内：`.docx` / `.pdf` / `.jpg` / `.jpeg` / `.png` / `.tiff` / `.tif` / `.bmp` | 400 `当前源文件格式暂不支持导出修订版（已支持 .docx / .pdf / 图片）` |
| 3 | 原文件在磁盘上存在 | 404 `原始合同文件不存在` |
| 4 | 存在可导出的修订记录：`scope=="clause"`，或 `scope=="overview" && operation=="add_clause"` | 400 `当前合同还没有任何条款修改，无法生成修订版` |
| 5 | **所有**替换型（scope=clause）记录中，凡有 `clause_text` 与 `revised_clause` 的，都必须有非空 `original_clause_text` | 400 `有 N 条修订无法在合同原文中定位，未写入修订版；请对该条款重新发起一次修改（保存时会重新定位原文锚点）后再下载` |
| 6 | 新增条款的插入位置能在文档中可靠定位 | 400，detail 为具体原因（如 `未找到插入位置「五」`） |
| 7 | 实际替换/插入成功数 > 0 | 400 `修订条款未能定位到原文，无法生成修订版` |

导出成功后返回文件，并在响应结束后删除临时文件；**原文件永不被覆盖**。

### 前端判断口径

- 用 `GET /overview` 的 `export.ready` + `export.blocker_count` 做**提示**（不调 LLM、便宜，页面加载时就能拿到）。
- 但**权威判定在下载端点**：`export.ready === true` 仍可能因条件 6/7（新增条款位置不可解析）返回 400。
- 因此：**不要把 `ready` 当作"下载按钮一定成功"的承诺**；按钮始终可点，失败时**原样展示后端 `detail`**（这是产品既定原则：宁可不能导出，也不能导出漏改的 DOCX）。
- 用户看到 400 时的正确引导：去出现问题的**专项会话**重新发起一次修改（保存时会重新定位锚点），或在总体会话里为对应的未定位项指定位置后确认。
- `confirm` 响应里的 `applied[].exportable` 只表示**那一条记录**会被取用，不表示整份合同现在就能导出（可能还有别的 blocker）。
- 非 `.docx` 合同（PDF / 图片）：**同样可以导出**——由 `parsed_text` 重建**中间 DOCX** 后再应用修订，最终统一输出 DOCX。只有**白名单之外**的源格式会被条件 2 拦下（400 `当前源文件格式暂不支持导出修订版（已支持 .docx / .pdf / 图片）`），UI 应提前明示，而不是等用户点了才报错。

---

## 9. 端点 3 / 4：方案查询

### `GET /api/contracts/{id}/overview/proposals`

返回该合同历史方案列表，**按 `proposal_id` 倒序**（最新在前）：

```jsonc
{ "code": 0, "message": "ok", "data": [ { /* 方案对象，见第 3 节响应，但不含 context/aggregate */ } ] }
```

用途：刷新页面后恢复"我生成过哪些方案、做到哪一步"。没有方案时 `data = []`（不是 404）。

### `GET /api/contracts/{id}/overview/proposals/{pid}`

```jsonc
{ "code": 0, "message": "ok", "data": { /* 单个方案对象 */ } }
```

- 方案不存在，或 `pid` 属于别的合同 → 404 `proposal not found`。
- 返回的方案对象与 `/plan` 响应**同构**（`proposal_id` / `status` / `summary` / `instruction` / `created_at` / `items` / `confirmed_ids` / `counts`），**不含** `context`（那是生成时的快照）与 `aggregate`（会过期，需要时重新 `GET /overview` 取实时值）。

### 推荐调用流程

```
进入「修改合同」总体会话
  ├─ GET /overview                     → 渲染左栏会话列表 + 每个会话的原文/结果/依据/风险/定位/导出状态
  └─ GET /overview/proposals           → 若有 draft / partially_applied 方案，恢复其 items（逐项展示）

用户输入整体要求 → POST /overview/plan → 渲染修改项列表
  ├─ 全部 resolved=true：直接勾选 → POST /overview/confirm
  └─ 有 resolved=false：对该项 [指定位置] → 收集 original_quote / clause_no / position
                       → POST /overview/confirm（只提交勾选项）
confirm 返回
  ├─ applied.length > 0 → 提示已应用，引导 GET /revised-docx（失败原样展示 detail）
  ├─ failed 含 needs_location → 回到该项继续指定位置
  └─ 重新 GET /overview → 刷新会话列表与 export.ready（confirm 响应的 aggregate 也可直接用）
```

---

## 10. 前端必须守住的产品边界（否则会误导用户）

1. **不要在总体会话里暗示"我改完总体会话，下载的 DOCX 就包含这些修改"**。
   方案必须经过"逐项确认"才会落到合同上；未确认的方案对合同零影响。
2. **展示 `overview.writes_docx === false` 的语义**：总体会话自身的历史 replace 讨论稿是
   "讨论记录"，永不写进文件。左栏若要展示这条语义，请用用户语言，不要写 `scope=overview`。
3. **未定位就如实说未定位**，不要灰掉了事，也不要给"直接替换"按钮。
4. **不要伪造位置**：`suggested_position` 只是候选，必须用户点确认。
5. **不要吞掉 `failed[]`**：这是用户唯一能看到"为什么这项没改成"的地方。
6. `instruction` 里可能带前端拼接块与总体会话确认前缀，展示前按 2.2 处理。
