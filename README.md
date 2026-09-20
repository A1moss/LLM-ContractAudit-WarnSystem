# LLM-ContractAudit-WarnSystem

> A24 · 基于大模型的企业合同智能审核与风险预警系统（命题企业：网新恒天）
> 团队：海底喵喵队

面向企业法务与风控部门，覆盖「合同上传 → 解析/OCR → 分类 → 要素抽取 → 风险识别 → 修改建议 → 条款比对 → 审核报告 → 条款修改 → 修订版 DOCX 导出」的完整闭环。核心理念是**人机协同审核**：机器负责可重复的证据提取与确定性判定，人负责最终定性、位置确认与版本采纳。

---

## 零、启动方式（只有两种）

本项目**对外只提供两种官方启动方式**，请二选一，不要再使用其它入口：

| 方式 | 适用场景 | 一条命令 |
|---|---|---|
| **方式 A：Windows 本地启动** | 开发 / 现场演示 / 没有 Docker | 首次先跑 `prepare.bat`，之后双击 `start.bat` |
| **方式 B：Docker 部署** | 评委机器 / 环境干净 / 不想装 Python+Node | `docker compose up -d` |

Windows 本地方式是**两个脚本、职责严格分离**，请按顺序使用：

| 脚本 | 何时用 | 职责 |
|---|---|---|
| `prepare.bat` | **首次部署只跑一次** | 准备 embedding 模型 → 建立/重建向量库 → **真实校验** RAG 是否就绪 |
| `start.bat` | 之后每次启动 | 只做「检查 → 启动 → 报错」；不安装、不下载、不重建 |

### 方式 A：Windows 本地启动

#### A1. 一次性准备（`prepare.bat`）

交付小包（≤50MB）**不含** embedding 模型与向量库，因此首次部署必须先准备：

```bat
prepare.bat                :: 缺什么补什么（模型缺失时联网下载，约 390MB）
prepare.bat --rebuild      :: 强制重建向量库
```

它依次做三件事，任一步失败都会**明确报错并中止**（绝不静默降级）：

1. **准备 embedding 模型**（`scripts/prepare_model.py`）：缺失则下载
   `shibing624/text2vec-base-chinese` 到 `models/hf-cache`；先试官方端点，失败自动改用国内镜像；
2. **准备向量库**：`backend/chroma_data` 缺失（或带 `--rebuild`）时，用
   `backend/ai/rag/resources/contract_templates_source.json` 重建；
3. **真实校验**（`scripts/check_rag_ready.py`）：真实加载模型 + 真实检索一次 +
   真实跑一次生产分类，要求 `method=rag` 且 `fallback=false`。

> 为什么必须"真实校验"：RAG 失效是**静默**的 —— 向量库或模型缺失时
> `search_similar_templates()` 只返回空列表并打一条 warning，分类随即退化为
> `rag-fallback-llm`，接口仍返回 200。因此「目录存在」不能作为准备完成的判据。

若你拿到的是**离线完整包**（已内置模型与向量库），`prepare.bat` 会跳过前两步，
只做第 3 步校验。

#### A2. 启动（`start.bat`）

`start.bat` 是**唯一对外启动入口**，职责只有「**检查 → 启动 → 报错**」：

1. 检查项目结构（`backend/main.py`、`frontend/package.json`、内部启动器）；
2. 检查 `.env` 与 `SECRET_KEY`（缺失或仍是占位串 → 打印生成命令并中止）；
3. 检查 RAG 资源（预构建向量库 `backend/chroma_data`，或建库源 `backend/ai/rag/resources/`；
   以及交付包携带的 `models/hf-cache` embedding 模型）；
4. 检查后端运行环境（优先 `backend/venv`，其次 PATH 上的 `python`，并试 `import fastapi,uvicorn,sqlalchemy`）；
5. 检查前端运行环境（`node` + `frontend/node_modules`）；
6. 分别调用内部启动器 `scripts/start_backend.bat` 与 `scripts/start_frontend.bat`，
   各自打开一个窗口；
7. 打印两个访问地址；**关闭这两个窗口即停止服务**。

> **`start.bat` 明确不做**（避免"一键脚本变成安装器"）：不 `pip install`、不 `npm install`、
> 不下载 embedding 模型、不重建向量库、不改数据库、不改任何项目文件。
> 依赖安装与向量库重建请按下面各节单独执行。

**环境准备（只需一次）**：

```bat
:: 1) 后端依赖（推荐用项目内 venv，start.bat 会优先使用它）
python -m venv backend\venv
backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt

:: 2) 前端依赖
cd frontend
npm install
cd ..

:: 3) 配置（必填 SECRET_KEY）
copy .env.example .env
::    生成并填入 SECRET_KEY：python -c "import secrets;print(secrets.token_urlsafe(32))"
```

内部启动器（`scripts/start_backend.bat`、`scripts/start_frontend.bat`）**不是独立启动方式**，
仅供 `start.bat` 调用；直接运行它们会先提示"请通过 start.bat 启动"。

### 向量库维护（与启动器职责分离）

```bat
prepare.bat --rebuild               :: 推荐：重建并顺带真实校验 RAG 是否就绪

cd backend
python -m ai.rag.init_chroma        :: 底层工具：用 backend/ai/rag/resources/contract_templates_source.json 重建向量库
```

### 交付包：双轨打包（维护者用）

比赛有两条提交轨道，体积上限不同，因此提供两个打包脚本：

| 轨道 | 脚本 | 内容 | 体积 |
|---|---|---|---|
| **校赛邮件提交** | `scripts/build_school_package.ps1` | 源码 + 建库源 + Docker 文件 + 文档 + prepare 工具 | **约 3.6MB（硬上限 50MB）** |
| **省赛网评 / 现场答辩** | `scripts/build_offline_package.ps1` | 上述全部 **+ `models/hf-cache` + `backend/chroma_data`** | 约 435MB |

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_school_package.ps1
powershell -ExecutionPolicy Bypass -File scripts\build_offline_package.ps1
```

两个脚本都会：只装该装的内容（按**路径**排除，绝不按裸目录名，以免误删
`backend/models` 这类源码包）→ 校验「必须包含的都在、绝不能带的一个没带」→
做匿名性检查 → 生成 ZIP → **回读 ZIP 条目名逐个比对**（防中文文件名被压坏）；
校赛脚本还会**强制校验 ZIP ≤ 50MB，超限直接失败**，且不允许靠删源码/删文档来压体积。

- **校赛上限 50MB** 是硬约束：模型（390MB）与向量库（45MB）因此**不能**进校赛包，
  首次部署必须先跑 `prepare.bat`；
- **省赛参赛指南没有总包体积上限**（仅 S4B 演示视频限 150MB），所以离线完整包可以带全资源，
  解压后直接 `start.bat` 即可离线运行生产 RAG。
- **命名注意**：校赛通知要求压缩包以「参赛学院+作品名称」命名，但省赛参赛指南明确
  「打包文件中的文件名或目录名」不得出现参赛学校名称，否则取消资格。两者冲突，
  故脚本默认使用中性名，需要时由你自行改名（脚本末尾会打印改名命令）。

---

### 方式 B：Docker 部署（`docker compose up -d`）

> ⚠️ **交付状态声明（如实说明）**
> Docker 交付文件（`Dockerfile.api` / `Dockerfile.web` / `docker-compose.yml` / `.dockerignore`）
> **部署配置已完成，但当前开发机无 Docker 环境，尚未进行实际容器构建与运行验证**
> （未执行过 `docker build` / `docker compose up`，也未做干净环境验收）。
> 正式提交前必须在具备 Docker 的环境完成一次构建与验收。
> 本文档中不包含「已验证 / 已构建成功 / 可直接运行」之类的结论。

##### 0.1 你会得到什么

> ⚠️ **注意**：以下描述的是**镜像成功构建之后**的状态。由于当前尚未执行过 `docker build`，
> **交付包内并不存在"已构建好的镜像 tar"**；要走 Docker 路线，请先按 **0.8 节**自行构建。
> 构建完成后，评委不需要安装 Python、Node.js、torch、OCR 引擎、embedding 模型或生产向量库
> —— 这些都会固化在 API 镜像里：

| 构建后将内置在镜像中 | 说明 |
|---|---|
| Python 3.11 + 全部后端依赖 | 含 FastAPI / SQLAlchemy / uvicorn |
| **embedding 模型** `shibing624/text2vec-base-chinese` | 390 MB；`Dockerfile.api` 已配置为在构建期写入镜像（运行期离线可用）。**因尚未构建，此项未实测** |
| **生产向量库** `backend/chroma_data` | 预构建，含 `contract_templates` 363 条范本（分类 RAG 依赖） |
| **RapidOCR 3.9.2 + ONNX Runtime** | OCR 模型随 wheel 自带，无需联网下载 |
| SQLite | 文件型数据库，无 DB 服务端 |
| reportlab | PDF 审核报告导出（中文走内置 CID 字体） |
| pdfplumber / pypdfium2 / python-docx | PDF 读取栅格化、DOCX 修订与导出 |
| 前端构建产物 | 由 web 镜像静态托管，无需 `npm install` |


#### 0.2 你需要做的（约 5 步）

> ⚠️ **前提**：交付包内**没有**预构建的镜像 tar（从未执行过 `docker build`）。
> 请先按 **0.8 节**构建镜像；第 3 步的 `docker load` 仅在你确实拿到了导出 tar 时才需要，
> 否则直接跳过（镜像已在本地）。

```bash
# 1) 安装 Docker Desktop（Windows 需启用 WSL2；仅需一次）
#    下载：https://www.docker.com/products/docker-desktop/

# 2) 解压交付包，进入解压后的目录
cd a24-contract-audit

# 3) 【可选】仅当你已按 0.8 节导出过镜像 tar 时才执行；否则跳过
docker load -i images/a24-api.tar
docker load -i images/a24-web.tar

# 4) 创建并填写 .env（必做，见 0.3）
cp .env.example .env        # Windows: copy .env.example .env

# 5) 启动
docker compose up -d
```

启动后浏览器打开：**http://localhost:5173**

#### 0.3 `.env` 必填项

| 变量 | 是否必填 | 说明 |
|---|---|---|
| `SECRET_KEY` | **必填** | JWT 签名 + 用户个人 Key 加密密钥。**`.env.example` 中故意留空**；为空或写成占位串时后端启动会直接失败。生成方法：<br>`python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `DEEPSEEK_API_KEY` | 建议填写 | 系统默认 Key（供未配置个人 Key 的账号共用）。留空也可启动，但上传/审核会提示需要 Key |
| `BOOTSTRAP_ADMIN_USERNAME` | 首次可选 | 见 0.5 |
| `DATABASE_URL` | 不用改 | Docker 下由 `docker-compose.yml` 覆盖为 `sqlite:////data/contract.db`（落在持久卷上） |
| `CORS_ORIGINS` | 不用改 | 默认 `http://localhost:5173`，与前端写死的地址一致 |

> **DeepSeek API Key 的两种配法**：① 在 `.env` 填一个系统默认 Key（所有未配个人 Key 的用户共用）；
> ② 留空，各用户登录后到「个人信息」页配置自己的 Key（服务端 Fernet 加密落库，接口只回
> 「是否已配置」，不回显）。生效优先级：**个人 Key > `.env` 系统默认 Key**。

#### 0.4 启动后如何确认正常

```bash
docker compose ps                 # 两个容器应为 running（api 会显示 healthy）
docker compose logs -f api        # 观察启动日志
```

浏览器打开健康检查：**http://localhost:8080/api/health**

```json
{"status":"ok","warmup":{"rag":"ready","ocr":"idle","rag_seconds":...,"ocr_seconds":null}}
```

- `status: "ok"` → API 已就绪；
- **`warmup.rag == "ready"`** → 后台已完成 embedding 模型加载与向量库预热，此时再上传合同最快；
  若仍是 `"warming"` 请稍等十几秒；若为 `"failed"`，说明镜像内资源异常（此时上传仍可用，
  但分类会退化，请查看 `docker compose logs api`）。
- `GET /` 返回标题与版本，也可作为最快探活。

> `warmup` 是「静启动」机制：后端在后台线程预热 torch / 向量模型 / 向量库，**不阻塞接口**，
> 启动瞬间即可访问。首次上传不会再付冷启动成本。

#### 0.5 首次使用的账号与权限

系统为四级角色：`uploader`（上传者）/ `reviewer`（审核人）/ `approver`（验收人）/ `admin`（管理员）。

1. **公开注册只能得到 `uploader`** —— 打开 http://localhost:5173 → 注册 → 登录；
2. 系统里一个 `admin` 都没有时，谁也无法分配角色，所以提供了**一次性 bootstrap**：
   - 先把上面那个已注册的用户名填进 `.env` 的 `BOOTSTRAP_ADMIN_USERNAME=你的用户名`；
   - 再 `docker compose restart api`；
   - 启动时若系统里**一个 admin 都没有**，且该账号**已存在**，就把它提升为 `admin`
     （不会自动创建账号、不改密码、已有 admin 时完全不动）；
3. 之后用该 admin 登录 →「用户管理」页分配 `reviewer` / `approver` / `uploader`。

#### 0.6 停止与重置

```bash
docker compose stop          # 停止（保留数据）
docker compose down          # 停止并删除容器（named volume 数据保留）
docker compose down -v       # ⚠️ 连卷一起删除：数据库/上传件/向量库全部重置
                             #    再次 up 时向量库会从镜像内容重新播种（RAG 恢复正常）
```

> 三个 named volume（`a24_db` / `a24_uploads` / `a24_chroma`）分别持久化 SQLite、上传合同、
> 向量库。**请勿把它们改成 bind mount**：空目录 bind mount 会遮蔽镜像内预置的向量库，
> 导致分类 RAG 静默退化（详见 `docker-compose.yml` 末尾注释）。

#### 0.7 网络依赖（如实说明）

- **DeepSeek API 必须联网**（`https://api.deepseek.com`）：合同分类、要素抽取、证据抽取、
  建议生成、条款比对、条款修改都由它完成。**完全断网时无法完成核心审核流程**
  （上传与审核接口会返回明确错误提示）。
- 除此之外全部离线自足：依赖、OCR 模型、embedding 模型、向量库、前端产物均在镜像内。

#### 0.8 维护者：如何重新构建镜像

> 本节只写给需要重新打包镜像的人，**评委无需执行**。

```bash
# API 镜像（无任何仓库外依赖：建库源已随源码在 backend/ai/rag/resources/ 内）
docker build -f Dockerfile.api -t a24-api:1.0 .

# Web 镜像
docker build -f Dockerfile.web -t a24-web:1.0 .

# 导出交付用 tar
docker save -o images/a24-api.tar a24-api:1.0
docker save -o images/a24-web.tar a24-web:1.0
```

`Dockerfile.api` 在**构建期**内置了 4 项自检（向量库集合存在、embedding 模型可离线加载、
RapidOCR 可初始化、reportlab 中文 CID 字体可注册），任一不满足会**直接构建失败**，
避免产出「能 build 但运行期才炸」的镜像。

> 关于交付资源：`models/hf-cache`（embedding 模型，约 390 MB）与 `backend/chroma_data`
> （预构建向量库，约 45 MB）**不进 Git**（见 `.gitignore` 末尾说明），但**必须随最终交付 ZIP 携带**。
> Docker 方案下二者分别由构建期下载与 `COPY backend/` 进入镜像，不依赖这两个目录存在于 ZIP 中；
> 本地启动方案（`start.bat`）则直接使用它们。

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
| 文档解析 | `pdfplumber`（PDF 文本层）、`python-docx`（DOCX）、**RapidOCR 3.9.2 + ONNX Runtime 1.23.2**（图片 / 扫描 PDF；**单引擎，模型随 wheel 自带、无需联网下载**） |
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
| 人工反馈闭环（审核结果 → 用户标注 → feedback_logs → reviewer 审 → admin 批 → feedback_experiences → 独立 Feedback RAG 检索注入 → learning_context 留痕 → 可撤销回退） | `api/feedback.py`、`services/feedback_experience.py`、`ai/rag/feedback_store.py` |
| 多用户角色与权限（uploader / reviewer / approver / admin） | `api/deps.py`、`services/role_bootstrap.py` |
| 静启动预热 | `services/warmup.py`、`GET /api/health` |

---

## 五、目录结构

```
backend/    FastAPI 后端 + AI 引擎（api/ services/ models/ ai/ evaluate/ tests/）
frontend/   Vue 3 前端（views/ views/contract-detail/ composables/ constants/）
docs/       项目文档（含 总体会话-后端API契约.md）
prepare.bat 首次部署一次性准备（模型 + 向量库 + 真实校验），见 零·方式 A1
start.bat   唯一对外启动入口（检查 → 启动 → 报错），见 零·方式 A2
scripts/    prepare_model.py / check_rag_ready.py  准备与就绪性校验
            start_backend.bat / start_frontend.bat 内部启动器（仅供 start.bat 调用）
            build_school_package.ps1               校赛小包（≤50MB，不含模型与向量库）
            build_offline_package.ps1              离线完整包（含模型与向量库）
            docker_acceptance.ps1                  维护者用 Docker 自动化验收
backend/data/        上传合同存储（运行时生成，git 忽略）
backend/chroma_data/ ★ 预构建生产向量库（约 45MB；git 忽略，由离线完整包携带）
backend/ai/rag/resources/contract_templates_source.json  ★ 向量库建库源（已随源码入库，两个包都带）
models/hf-cache/      ★ embedding 模型（约 390MB；git 忽略，仅离线完整包携带）
```

> 完整目录职责（含各文件一句话说明）见 [docs/项目目录说明.md](docs/项目目录说明.md)。

---

## 六、DeepSeek API Key 配置

> 启动方式见本文档开头的 **[零、启动方式（只有两种）](#零启动方式只有两种)**。
> 本节只说明 Key 的配置，不重复启动步骤。

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
# 后端（unittest，36 个测试文件 / 463 个测试方法）
cd backend && python -m unittest discover -s tests -t .

# 前端（node --test，8 个文件 / 108 个测试）
cd frontend && node --test
```

---

## 八、文档索引

| 文档 | 说明 |
|---|---|
| [docs/系统架构.md](docs/系统架构.md) | 分层架构、技术边界、技术栈、数据模型、权限体系、状态机、已废弃组件、真实局限 |
| [docs/审核流程.md](docs/审核流程.md) | 上传 → 解析 → 分类 → 要素 → 规则 → 证据 → 裁决 → 建议 → 比对 → 报告 的逐步说明 |
| [docs/系统功能说明.md](docs/系统功能说明.md) | 页面与路由、各 Tab 功能、**API 一览（52 个操作 / 46 条唯一路径）**、交互约定、功能边界 |
| [docs/风险规则说明.md](docs/风险规则说明.md) | R01–R13 名称/等级/触发条件/硬阈值/法条映射 + Gold 覆盖情况 |
| [docs/条款修改与修订机制.md](docs/条款修改与修订机制.md) | 两种修改范围、多轮会话、**采用态语义**、DOCX 修订版导出、R09 补充 |
| [docs/审核报告与可视化.md](docs/审核报告与可视化.md) | 审核报告 Tab、独立完整审核报告、13 类规则扫描、条款完整性、导航与位置记忆 |
| [docs/数据集说明.md](docs/数据集说明.md) | 分类/风险数据集规模与来源结构、Gold 规模与分布、脱敏、泄漏排查 |
| [docs/评测方法.md](docs/评测方法.md) | 三套正式评测的脚本/公式/冻结指标 + 非正式脚本说明 |
| [docs/风险标注与Gold规范.md](docs/风险标注与Gold规范.md) | 标注原则、边界规则、三条上位规则、质控（含如实的方法学说明） |
| [docs/项目目录说明.md](docs/项目目录说明.md) | 代码目录职责、已删除/归档组件、前端注意事项 |
| [docs/部署与复现.md](docs/部署与复现.md) | 环境、依赖、`.env`、启动、静启动、测试、评测复现、FAQ |
| [docs/参考论文与技术依据.md](docs/参考论文与技术依据.md) | 可核验的公开文献与技术点映射 |
| [docs/变更记录.md](docs/变更记录.md) | 变更台账 + 文档影响速查 + 待办 |
| [docs/总体会话-后端API契约.md](docs/总体会话-后端API契约.md) | 总体修改会话的字段级接口契约（前端对接用） |
| `A24-详细设计文档-v4.0.md` | 详细设计文档（申报交付物） |

---

## 九、当前系统的限制（如实列出）

1. **修订版统一导出 DOCX**：DOCX / 普通 PDF / 扫描 PDF / JPG / PNG / TIFF / BMP 都能「审核 → 修改 → 导出修订版 DOCX」（非 DOCX 输入由 `parsed_text` 重建**中间 DOCX** 后再应用修改，中间件不出现在用户侧）。系统**不提供** PDF→PDF 或图片→图片导出。
2. **修订版不生成 Word 修订痕迹（Track Changes）**，也不做完整版本管理。
3. **审核报告可导出正式 PDF**（GET /api/contracts/{id}/audit-report/pdf，reportlab 纯 Python 排版；**没有 Word 报告导出**），报告页另支持浏览器打印；报告不显示审核人 / 报告编号 / 报告版本号 / 审批状态（这些字段后端不存在）。
4. **R05 无 Gold、R13 不入 F1、R08/R09 Gold 覆盖有结构性限制**（见第一节）。
5. **SQLite 单写者**并发限制（缓解而非根治）；高并发需切 MySQL（非默认）。
6. **`risk_cases.json` 为空**（0 条），风险案例库仅预留占位。它与人工反馈经验**不是一回事**：`risk_cases.json` 是预留的规则知识库占位，反馈经验的实际沉淀载体是 `feedback_experiences`（见下一条）。
7. **反馈经验闭环尚无真实数据**：`feedback_logs = 0`、`feedback_experiences = 0`（正式库实测）。**功能链路已实现并经过测试，但当前尚无真实用户反馈数据沉淀**；Feedback RAG 默认关闭（`FEEDBACK_RAG_ENABLED=False`）。该闭环**不是模型参数微调**，而是基于人工反馈经验的 RAG 辅助优化，风险最终裁决仍由确定性 adjudicator 完成（详见 [docs/系统架构.md](docs/系统架构.md) 第 6.5 节）。
8. **评测数据为项目人工参考标准，非外部权威金标准**：84 份风险评测样本来自公开渠道采集 → 公告/完整合同筛选 → 必要时正文还原 → 标注；其中**只有第 3 批 10 份为真正的双人独立盲标**，项目统一采用「双人交叉复核 + 分歧仲裁」表述并只报告可核查的复核一致率，**不宣称全量 κ**（详见 [docs/数据集说明.md](docs/数据集说明.md) 与 [docs/评测方法.md](docs/评测方法.md)）。

> 更完整的评测过程、FP 归因与设计演进记录在仓库外的 `../02_项目文档/`。
