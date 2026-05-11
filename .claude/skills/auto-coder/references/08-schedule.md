# 8. 项目排期

## 8.1 排期原则

严格对齐本 DEV_SPEC 的架构分层与 1.3.1 节目录结构。

1. **只按本文档设计落地**：以 1.3.1 节目录树为"交付清单"，每一步都要在文件系统上产生可见变化。
2. **先打通主闭环，再逐层增强**：优先做"可跑通的端到端路径（Ingestion → Retrieval → Agent 最小诊断链路 ①→①.5→②→③→④→⑤→⑩→⑪→⑫→⑬）"，再接入追问循环（⑥⑦）和检查循环（⑧⑨）。
3. **外部依赖可替换/可 Mock**：LLM（Qwen）/ Embedding（Qwen3-Embedding-8B）/ Reranker / Milvus / PostgreSQL 的真实调用在单元测试中一律用 Fake/Mock，集成测试再开真实后端。
4. **每个小阶段给出验收标准**：明确"完成"的定义，避免模糊交付。
5. **基础设施按需引入**：Docker Compose 在阶段 A 搭建基座，监控/缓存等在主链路跑通后再逐步接入。

## 8.2 阶段总览

| 阶段 | 名称 | 目的 |
|------|------|------|
| **A** | 工程骨架与基础设施基座 | 建立可运行、可配置、可测试的工程骨架；Docker Compose 拉起全部存储依赖 |
| **B** | 数据层与模型客户端 | 打通 PostgreSQL / Milvus 连接；封装 Qwen3-Embedding-8B、Reranker、Qwen LLM 推理客户端 |
| **C** | Ingestion Pipeline（MinerU → Chunk → Embedding → 存储） | 离线摄取链路跑通，样例文档写入 Milvus + PostgreSQL（含 raw_documents 表存 MinerU 产物），支持幂等与增量 |
| **D** | 术语库与 Entity Linking | 构建 terms_collection，实现口语→标准术语映射，为 Retrieval 术语扩展和 Agent 症状预处理提供基础 |
| **E** | Retrieval（Dense + Sparse + RRF + Rerank） | 在线查询链路跑通，得到 Top-K chunks（含引用信息），具备稳定回退策略 |
| **F** | Agent 工作流（LangGraph StateGraph） | 按 4.1 节设计落地 16 节点 + 2 条件路由，实现基于信息增益收敛的迭代式诊断工作流 |
| **G** | API 层与权限系统 | FastAPI 入口服务、JWT 认证、角色权限、限流，暴露问诊接口 |
| **H** | 基础设施增强（监控、缓存、日志） | Prometheus + Grafana 指标监控，Loki 日志采集，Redis 缓存客户端与缓存层 |
| **I** | 评估体系 | 离线评估（RAG + Agent）、在线追踪、LLM Judge |
| **J** | 端到端验收与文档收口 | 真实环境（非 Mock）全链路 E2E 冒烟测试，README 完善，确保开箱即用 |

## 8.3 详细排期

---

### 阶段 A：工程骨架与基础设施基座

**目的**：建立可运行、可配置、可测试的工程骨架；Docker Compose 拉起全部存储依赖（PostgreSQL、Milvus、Redis），后续所有模块都能以 TDD 方式落地。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| A1 | 初始化目录树与最小可运行入口 | 1.3.1 节完整目录结构、`pyproject.toml`、`src/__init__.py` 等 | `python -m src` 不报错；目录结构与 1.3.1 节一致 |
| A2 | Docker Compose 搭建存储基座 | `docker-compose.yml`、`infra/docker/` | `docker compose up -d` 可拉起 PostgreSQL + Milvus + Redis，各服务健康检查通过 |
| A3 | 配置加载与校验 | `config/settings.py`、`.env.example` | 从 `.env` 加载配置，缺失必填项时抛明确错误；**必须包含 §9.7 定义的 `AgentLimitsSettings` 段**（7 个常量：`MAX_FOLLOWUP_ROUNDS` / `MAX_EXAM_ROUNDS` / `MAX_FOLLOWUP_QUESTIONS` / `RETRIEVE_TOP_N` / `ASKABLE_GAIN_THRESHOLD` / `ENTITY_LINKING_TIER2_THRESHOLD` / `RERANKER_CUTOFF_LAYERS`），以 `settings.agent_limits` 嵌套属性暴露；单元测试：默认值与 §9.7 初始值一致；`.env` 覆盖 `AGENT_MAX_FOLLOWUP_ROUNDS=10` 能生效；缺失 LLM API_KEY 等必填项报错 |
| A4 | pytest 测试基座 | `tests/`、`pyproject.toml [tool.pytest]` | `pytest` 可运行，冒烟测试通过 |
| A5 | 公共工具模块 | `src/common/normalize.py`、`hashing.py`、`metrics.py` | normalize + SHA256 哈希函数单元测试通过；与 3.1.4.2 定义一致 |
| A6 | Prompt 模板骨架 | `src/prompts/ingestion.py`、`agent.py`、`evaluation.py` | 三个模块均可 `from src.prompts.xxx import yyy` 导入；函数签名与第 7 节设计一致；具体 prompt 内容随对应业务阶段（C/F/I）落地时填充。查询处理 prompt 统一归入 `agent.py` 的 `build_query_construction_prompt`，不再单列 `retrieval.py` |

---

### 阶段 B：数据层与模型客户端

**目的**：打通两层存储连接（PostgreSQL / Milvus），封装三个模型推理客户端（Qwen3-Embedding-8B / Reranker / Qwen LLM），使上层业务代码可通过统一接口调用。Redis 缓存客户端延至阶段 H 与缓存业务对接一并落地。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| B1 | PostgreSQL 连接池 + ORM 模型 | `src/db/postgres/connection.py`、`models.py` | 连接池可用；sources / raw_documents / chunks / users / patients / conversations 等表 ORM 模型与 2.4 定义一致 |
| B2 | PostgreSQL 迁移脚本 | `src/db/postgres/migrations/` | Alembic `upgrade head` 可创建全部表结构与索引（与 2.4.2、2.4.3、2.4.5 一致） |
| B3 | Milvus 连接管理 + docs_collection | `src/db/milvus/connection.py`、`docs_collection.py`、`config/milvus_schema.py` | Collection Schema 与 2.4.1 定义一致；upsert + search 集成测试通过 |
| B4 | Milvus terms_collection | `src/db/milvus/terms_collection.py` | Schema 与 2.4.6 一致；upsert + 向量检索集成测试通过 |
| B5 | PostgreSQL raw_documents 表（MinerU 产物存储） | `src/db/postgres/models.py`（raw_documents ORM 类）、`src/db/postgres/migrations/0001_raw_documents.sql` | raw_documents 表结构与 2.4.4 一致（jsonb + text + GIN 索引）；上游与 sources 表 1:1 外键约束生效；upsert 集成测试通过 |
| B6 | Qwen3-Embedding-8B 客户端 | `src/models/embedding_model.py` | GPU 推理（INT8）；单条/批量编码接口；输出 4096 维 Dense 向量（Sparse 由 Milvus BM25 承担）；单元测试（Mock）+ 集成测试 |
| B7 | BGE-Reranker-v2-minicpm-layerwise 客户端 | `src/models/reranker_model.py` | GPU 推理（与 Embedding 共享显卡）；layerwise 推理与 cutoff_layers 配置；输入 [query, doc] 对，输出相关性分数；超时回退机制；单元测试 |
| B8 | LLM 推理客户端（DashScope） | `src/models/llm_client.py` | 对接 DashScope OpenAI-compatible API；支持流式/非流式输出；单元测试（Mock） |

---

### 阶段 C：Ingestion Pipeline（MinerU → Chunk → Embedding → 存储）

**目的**：离线摄取链路跑通，能把样例 PDF 文档经 MinerU 解析 → 切分 → 增强 → 向量化 → 写入 Milvus + PostgreSQL（含 raw_documents 表存 MinerU 原始产物），支持幂等写入与增量更新。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| C1 | MinerU 产物加载器 | `src/rag/ingestion/mineru_loader.py` | 读取 `mineru_output/{name}/{backend}_auto/` 下的 `.md` + `content_list_v2.json`(v2 页级嵌套,见 2.4.4)；写入 PostgreSQL `raw_documents` 表；单元测试 |
| C2 | Chunking(父子分块 + 表格双粒度) | `src/rag/ingestion/chunking.py` + 单本书 anchor 配置 | **非表格内容**:按 §3.1.2 4 步流程切分 — Step 1 目录权威清单提取、Step 2 正文节边界匹配 + REAL_START 选取、Step 3 全书层面截断 + 节内参考文献丢弃、Step 4 父块构建(节本身或节内三遍切【】+(一)+1.,严格层级合并)、Step 5 子块构建(size 驱动,目标 600 字,父块 ≤ 1200 字直接当 child)。父块 `embedding_status='skip'` 不向量化,子块多向量化。**表格内容**(识别 MinerU `content_list` 中的 table 类型块):产出两类 chunk —— ① 整表摘要 chunk(LLM 一句概括) ② 逐行 chunk(parse HTML 转自然语言);共享 `parent_chunk_id` 指向所在节父块。**非表格图像类**:只留图名/bbox 到 `raw_documents.content_list`,不进 chunks 表(见 §3.1.1 末)。**单元测试**:目录字典提取 / REAL_START 选取规则 / 三遍切阈值 / 严格层级合并 / 子块 size 累积算法(含 force-add 边界) / 父子覆盖完整性 mismatch=0。**12 本书验证**:每本按 §3.1.2 切分主流程跑 + 抽样人审 + 验 mismatch=0 |
| C3 | 幂等性工具 | `src/rag/ingestion/idempotency.py` | source_id / heading_path_id / chunk_id / content_hash 生成逻辑与 3.1.4 一致；父块 chunk_id 固定使用 `relative_chunk_index="parent"` 参与哈希（见 3.1.4.2）；单元测试覆盖 normalize + 多级哈希 + 父块 ID 与子块 ID 不冲突验证 |
| C4 | LLM 语义增强 | `src/rag/ingestion/enrichment.py`、`src/agent/schemas/ingestion.py`（`ChunkEnrichmentOutput`，定义与 §9.5 一致） | 单次 LLM 调用生成 title / summary / hypothetical_questions（`tags` 字段保留兼容但不再生成,见 §3.1.3.2 末尾决策）；Prompt 来自 `src/prompts/ingestion.py`；单元测试（Mock LLM） |
| C5 | 多向量 Embedding | `src/rag/ingestion/embedding.py` | 对每个 Chunk 生成 1 original + 1 summary + 2~3 question 向量记录；全部仅含 Dense 向量（Sparse 检索由 Milvus BM25 基于 original_content 文本字段实现）；批处理；content_hash 增量判断；单元测试 |
| C6 | 三层存储写入 + 僵尸清理 | `src/rag/ingestion/storage.py` | PostgreSQL chunks 表 upsert（父块 `embedding_status='skip'` 直写，跳过 Milvus）+ 子块 Milvus 向量 upsert + 僵尸 chunk 差集清理（先删子块含 Milvus，再删父块仅 PostgreSQL，见 3.1.4.3）；集成测试覆盖父块/子块删除顺序与 foreign key 约束 |
| C7 | Pipeline 编排 | `src/rag/ingestion/pipeline.py` | 串联 C1~C6；`python scripts/ingest.py <pdf_path>` 可完整摄取一份文档；集成测试 |
| C8 | 摄取入口脚本 | `scripts/ingest.py`、`scripts/init_db.py`、`scripts/init_milvus.py` | CLI 可用；支持单文件/批量摄取；初始化脚本可创建表结构和 Collection |

---

### 阶段 D：术语库与 Entity Linking

**目的**：构建 `terms_collection`（2.4.6），导入 ICD-10-CN + CMeSH 标准术语数据，Layer 1 PROJECT 层别名先以医师整理 + 上线后回流方式补充，实现口语→标准术语的向量检索映射，为阶段 E 查询预处理的术语扩展和阶段 F Agent 节点 ② build_query 的 Entity Linking 提供基础。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| D1 | 术语数据整理与清洗 | `terms/icd10_cn/`、`terms/cmesh/` | 数据源格式统一为 `{concept_id, preferred_term, alias, source_vocab, icd10, category}` |
| D2 | 术语库构建脚本 | `terms/build_icd10.py` | 对 alias 文本做 Qwen3-Embedding-8B Dense 编码 → upsert 到 terms_collection；幂等；集成测试 |
| D3 | 术语检索接口 | `src/db/milvus/terms_collection.py` 扩展 | 输入口语文本，返回 Top-5 候选术语（含 concept_id / preferred_term / icd10）；按 category 可过滤；集成测试 |

---

### 阶段 E：Retrieval（Dense + Sparse + RRF + Rerank）

**目的**：在线查询链路跑通，输入用户 query，经预处理 → 双路召回 → 单阶段多路 RRF 融合 → 多向量聚合(sum-aggregate + vector_hits 副载荷) → 精排，输出 Top-K chunks（含 original_content + heading_path 引用信息），具备稳定回退策略。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| E1 | 查询预处理（分路构建） | `src/rag/retrieval/query_processing.py` | 关键词提取 → 术语扩展（查 terms_collection）→ 生成 `sparse_queries`（每个症状维度一个词袋）；Dense Query 整合改写（LLM）→ 生成单一 `dense_query`；LLM 调用与 prompt 由 Agent ② `build_query` 节点直接持有（`src/prompts/agent.py` 的 `build_query_construction_prompt`），本模块只暴露确定性的关键词/术语扩展工具函数供 ② 调用；单元测试 |
| E2 | Sparse Retriever（Milvus BM25） | `src/rag/retrieval/sparse_retriever.py` | 对 `sparse_queries` 中每个维度词袋分别查询 Milvus 内置 BM25，N 个维度 = N 次查询；各自返回 Top-N；单元测试 |
| E3 | Dense Retriever（单次 ANN） | `src/rag/retrieval/dense_retriever.py` | 对 `dense_query` 做 Qwen3-Embedding-8B 编码 → Milvus ANN 向量检索，返回 Top-N；单元测试 |
| E4 | 单阶段多路 RRF 融合 + 多向量聚合 | `src/rag/retrieval/fusion.py` | Dense（1 路）+ Sparse 各维度（各 1 路）→ 单阶段多路 RRF → 按 source_chunk_id 聚合(各 vector_type 命中分数求和 + 携带 `vector_hits` 副载荷) → Top-M;单元测试覆盖 sum-aggregate 公式与 vector_hits 形态(matched_text 三类取值规则) |
| E5 | Reranker 精排 + 回退 | `src/rag/retrieval/reranker.py` | Cross-Encoder 在 `diagnose` ⑩ 前置调用，对收敛后候选精排截断 Top-K；超时/不可用时回退至 `candidate_chunks` 原序（3.2.3 策略）；单元测试 |
| E6 | 元数据过滤（Pre-filter + Post-filter） | `src/rag/retrieval/` 各文件内 | Pre-filter：source_id / tags 在 Milvus 检索时过滤；Post-filter：在 Rerank 前兜底过滤；missing → include 宽松策略 |

---

### 阶段 F：Agent 工作流（LangGraph StateGraph）

**目的**：按 4.1 节设计，使用 LangGraph StateGraph 实现完整诊断工作流（16 节点 + 2 条件路由）。先落地最小可用路径（① → ①.5 → ② → ③ → ④ → ⑤ → ⑩ → ⑪ → ⑫ → ⑬），再接入追问循环（⑥a→⑥b→⑦）和检查循环（⑧a→⑧b→⑨）。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| F1 | MedicalState 定义 + 初始化工厂 | `src/agent/state.py` | Pydantic `BaseModel`(见 §4.1.1 实现形态注)包含 messages / patient_id / patient_input / chief_complaint / present_illness / present_illness_slots / medical_history / exam_reports / report_findings / standardized_entities / dense_query / sparse_queries / candidate_chunks / extracted_symptoms / confirmed_symptoms / denied_symptoms / uncertain_symptoms / followup_round / last_nlu_round / followup_question / followup_answer / followup_questions / unaskable_symptoms / info_gain / exam_round / pending_exam_results / diagnosis_result / safety_constraints / recommended_tests / medication_advice / risk_warnings / final_response / last_reranked_chunks / session_token_usage / session_latency_ms / last_diagnose_prompt / last_diagnose_raw_output 全部字段（无 `followup_capped` 旗标，追问上限兜底由 Node ⑩ 直读 `followup_round` 判断）；`present_illness_slots` 包含 13 个维度槽位（onset_time/onset_mode/trigger/location/nature/severity/duration_pattern/aggravating/relieving/associated_symptoms/progression/treatment_tried/treatment_response），初始值为 None/空列表；实现 `create_initial_state(patient_id, patient_input) -> MedicalState` 工厂函数（初始值与 4.1.1a 节一致）；与 4.1 节定义一致；单元测试 |
| F2 | 节点 ①：info_collect | `src/agent/nodes/info_collect.py`、`src/agent/schemas/info_collect.py`（`InfoCollectOutput`，定义与 §9.5 一致） | Step 1: LLM 从 patient_input 提取 chief_complaint + present_illness + present_illness_slots（13 个维度槽位同步结构化填充，未提及维度保持 None/空）；Step 2: 以 patient_id 查 PostgreSQL 加载 medical_history；Step 3: 加载 exam_reports；Prompt 来自 `src/prompts/agent.py`；单元测试（Mock LLM + Mock DB）；验证：完整输入无空槽、简短输入多空槽 |
| F2.5 | 节点 ①.5：analyze_initial_reports | `src/agent/nodes/analyze_initial_reports.py`、`src/agent/utils/report_parser.py`、`src/agent/schemas/report_parser.py`（`ReportFinding` / `ReportFindings`，定义与 §9.5 一致） | exam_reports 非空时执行；多模态 LLM 直读报告（图片 jpg/png / PDF 直传）→ 提取 report_type / report_date / abnormal_values / impressions / positive_findings / negative_findings；输出 report_findings；exam_reports 为空时透传；报告本身已是标准术语，无需 Entity Linking；Prompt 来自 `src/prompts/agent.py`；单元测试（Mock 多模态 LLM） |
| F3 | 节点 ②：build_query | `src/agent/nodes/build_query.py`、`src/agent/schemas/ner.py`（`NEREntity` / `NERResult`）、`src/agent/schemas/entity_linking.py`（`EntityLinkingMatch` / `EntityLinkingResult`）、`src/agent/schemas/query_construction.py`（`QueryConstructionOutput`），三个 Schema 定义均与 §9.5 一致 | 四步流程：Step 1 LLM NER 实体抽取（首轮对 `chief_complaint` + `present_illness`；后续轮仅对本轮新增 `followup_answer`）→ Step 2 Entity Linking（查 terms_collection Top-5 → LLM 选择 Top-1，新实体按 `preferred_term` 去重追加到 `standardized_entities`）→ Step 3 术语扩展（concept_id 查全部别名 → OR 表达式）→ Step 4 Query 构建/改写（Dense query 整合 preferred_term + `present_illness_slots` 已填维度 + `report_findings` 的 positive_findings/impressions；Sparse 用术语扩展 OR 表达式；`abnormal_values` 原始数值、`negative_findings`、`denied_symptoms` 均不进 query）；单元测试（Mock LLM + Mock terms_collection） |
| F4 | 节点 ③：retrieve | `src/agent/nodes/retrieve.py` | 用改写后的 query 对 Milvus 做混合检索（Dense + Sparse 双路 → RRF 融合 → Top-N 截断），覆盖写入 `candidate_chunks`；单元测试 |
| F5 | 节点 ④：extract_symptoms | `src/agent/nodes/extract_symptoms.py` | 两阶段零 LLM：阶段一 TF-IDF/KeyBERT 提取表面症状关键词 → 阶段二 分层术语归一化（Tier 1 精确/别名匹配 → Tier 2 向量检索 + 阈值截断 → Tier 3 保留原文标记 linked=False，送 Node ⑤ 软比对兜底）；输出去重症状列表，每项含 `text`/`preferred_term`/`linked`；单元测试 |
| F6 | 节点 ⑤：select_discriminative_symptom | `src/agent/nodes/select_symptom.py`、`src/agent/schemas/symptom_selection.py`（`DimensionSelection` / `AskabilityJudgment`，定义与 §9.5 一致） | **维度缺口优先（配额制）**：读取 `present_illness_slots` 空槽，LLM 选 1~2 个最有鉴别价值的维度占用 `settings.agent_limits.MAX_FOLLOWUP_QUESTIONS` 名额（标记 `type: "dimension"`），空槽填满后跳过；**症状级（剩余名额）**：在未问症状中计算信息增益（二元熵），按增益降序遍历，循环内 LLM 做可问性评估：可问 → `followup_questions`（标记 `type: "symptom"`），不可问 → `unaskable_symptoms`（附增益值）；遍历结束后若可问症状最高增益 < `settings.agent_limits.ASKABLE_GAIN_THRESHOLD` 则清空症状级 `followup_questions`；`info_gain` 仅由症状级决定（维度不影响收敛）；**所有阈值/上限常量来源见 §9.7，禁止 hardcode**；单元测试：有空槽→混合输出、无空槽→纯症状输出 |
| F7 | 条件路由：should_continue | `src/agent/routers/should_continue.py` | **纯函数路由**（不修改 State）：优先级：`followup_round >= settings.agent_limits.MAX_FOLLOWUP_ROUNDS`（常量来源 §9.7）→ 返回 diagnose（硬性兜底，防收敛失效无限循环；兜底 insufficient 产出由 Node ⑩ Step -1 完成）；`followup_questions` 非空 → followup；否则 → diagnose；所有自然收敛过滤逻辑（可问性、增益阈值）已内聚在 Node ⑤；单元测试覆盖三分支 + 验证路由函数调用前后 State 字段未被修改 |
| F8 | 节点 ⑥⑦：追问循环 | `src/agent/nodes/generate_followup.py`、`wait_followup_answer.py`、`process_followup.py`、`src/agent/schemas/followup.py`（`FollowupParseResult`，定义与 §9.5 一致） | ⑥a LLM 将混合类型追问项（维度级 `type: "dimension"` + 症状级 `type: "symptom"`）转为患者可理解的流畅追问，写入 `followup_question`；⑥b `wait_followup_answer` 调用 interrupt() 等待用户回答（与 LLM 调用分离，避免恢复时重复生成）；⑦ LLM 解析回答：症状级 → 确认/否认/不确定三类分流更新 confirmed_symptoms / denied_symptoms / uncertain_symptoms；维度级 → 回填 `present_illness_slots` 对应槽位 + 追加 `present_illness` 自由文本；同时提取新增症状信息 → followup_round += 1 → 回到 build_query；Prompt 来自 `src/prompts/agent.py`；单元测试 |
| F9 | 节点 ⑧⑨：检查循环 | `src/agent/nodes/recommend_exam.py`、`wait_exam_report.py`、`process_exam_result.py` | ⑧a LLM 根据候选疾病推断所需检查（体格检查 + 辅助检查），按优先级排序；对与 report_findings 有交集的检查项，LLM 额外输出复用评估说明（含报告日期、采集条件判断），不静默删除，写入 `recommended_tests`；⑧b `wait_exam_report` 调用 interrupt() 等待结果回传（与 LLM 调用分离，避免恢复时重复生成）；⑨ 调用 report_parser.py 共享解析函数（与 ①.5 复用）→ 追加到 exam_reports 和 report_findings → 回到 build_query；`exam_round += 1`，上限 `settings.agent_limits.MAX_EXAM_ROUNDS`（常量来源 §9.7，禁止 hardcode）；单元测试 |
| F10 | 节点 ⑩：diagnose | `src/agent/nodes/diagnose.py`、`src/agent/schemas/diagnosis.py`（`HistoryFactor` / `SlotRelevance` / `ReportEvidence` / `CandidateEvidence` / `EvidenceSheet` / `RankedDisease` / `DiagnosisRanking` / `DiagnosisOutput`，8 个 Schema 定义均与 §9.5 一致） | **Step -1 兜底短路**：入口直读 `state["followup_round"] >= settings.agent_limits.MAX_FOLLOWUP_ROUNDS`（常量来源 §9.7）时跳过所有 LLM，直接产出 insufficient 结果且 `failure_reason="followup_round_capped"`；正常路径：三步分阶段 LLM 推理：Step 0 Cross-Encoder 前置截断（可插拔，3.2.3）→ Step 1 证据归集（LLM #1 从 reranked_chunks + confirmed/denied symptoms + present_illness_slots + 病史摘要 + report_findings 归集 `EvidenceSheet`）→ Step 2 鉴别诊断排序（LLM #2 基于证据表做临床决策排序 + unaskable 条件推理，输出 `DiagnosisRanking`）→ Step 3 置信度校准（LLM #3 用原始事实交叉验证，防幻觉 + 校准概率与标签一致性）；**整链路兜底 + 错误原因记录**：Step 1/2/3 任一步最多尝试 3 次仍失败 → try/except 捕获后立即停止并返回 insufficient 结果，`failure_reason="step_{n}_structured_output_failed: <ExcType>: <msg>"`，不向下一步喂空/不完整中间结果；同时 `logger.error(..., exc_info=True)` 记录完整堆栈；输出 diagnosis_result（含 disease / probability / evidence_chain / differentiation_type / unaskable_impact / failure_reason）；中间 Schema：`EvidenceSheet`、`DiagnosisRanking`；Prompt 来自 `src/prompts/agent.py`（三个独立 prompt 函数）；**单元测试覆盖 5 条路径**：① followup_round 触顶 → `failure_reason == "followup_round_capped"` ② 正常三步成功 → `failure_reason is None` ③ Step 1 失败 → `failure_reason.startswith("step_1_structured_output_failed")` ④ Step 2 失败 → `failure_reason.startswith("step_2_structured_output_failed")` ⑤ Step 3 失败 → `failure_reason.startswith("step_3_structured_output_failed")`；③④⑤ 断言 `differentiation_type == "insufficient"` 且 `probability == 0.0` 且 downstream 节点（⑪⑫⑬）仍能正常运行 |
| F11 | 条件路由：diagnose_router | `src/agent/routers/diagnose_router.py` | `need_exam` 且 `exam_round < settings.agent_limits.MAX_EXAM_ROUNDS` → recommend_exam；`confirmed` / `insufficient` / `exam_round >= settings.agent_limits.MAX_EXAM_ROUNDS` → safety_gate（常量来源 §9.7，禁止 hardcode）；单元测试 |
| F12 | 节点 ⑪：safety_gate | `src/agent/nodes/safety_gate.py`、`src/agent/schemas/safety_gate.py`（`SafetyGateOutput`，定义与 §9.5 一致） | 规则过滤：从 medical_history 提取过敏药物/当前用药/妊娠状态 → 匹配药物-过敏对（含同类药排除）+ 配伍禁忌表 + FDA 妊娠分级（D/X 禁用）；LLM 兜底：交叉过敏、罕见药物相互作用、肝肾功能剂量调整；输出 safety_constraints（banned_drugs / interaction_warnings / contraindication_flags）；单元测试 |
| F13 | 节点 ⑫⑬：建议与输出 | `src/agent/nodes/generate_advice.py`、`format_response.py`、`src/agent/schemas/advice.py`（`AdviceOutput`，定义与 §9.5 一致） | ⑫ 在 safety_constraints 约束内：confirmed → 用药建议 + 注意事项 + 复查建议；insufficient → 线下检查建议；need_exam 达上限 → 诚实告知局限；**读取 `diagnosis_result[0].failure_reason`**：`"followup_round_capped"` → risk_warnings 追加"问诊轮次较多仍未收敛"提示；`"step_N_structured_output_failed..."` → risk_warnings 追加"系统分析出现技术问题，结果不可作为依据"提示（不暴露异常细节）；高危提示优先级最高；⑬ LLM 组织自然语言回复 + 免责声明，failure_reason 非 None 时免责声明补一句"本次诊断因系统原因未能完整推理"；单元测试覆盖 failure_reason 的三种取值（None / followup_round_capped / step_N_... ）对 risk_warnings 和 final_response 的影响 |
| F14 | StateGraph 编排 | `src/agent/graph.py` | 注册 16 节点 + 2 条件边；顺序边 ①→①.5→②→③→④→⑤；条件边 ⑤→⑥a/⑩（两路：追问或诊断）；追问循环 ⑥a→⑥b→⑦→②；检查循环 ⑧a→⑧b→⑨→②；诊断后路由 ⑩→⑧a/⑪；安全门控 ⑪→⑫；输出链 ⑫→⑬→END；集成测试 |
| F15 | 全工作流集成测试 | `tests/integration/test_agent_workflow.py` | Mock 存储 + Mock LLM，验证：正常路径（信息充足直接诊断）/ 追问循环（多轮追问后收敛）/ 检查循环（建议检查→结果回传→重新诊断）/ 信息不足路径 四条典型路径；安全门控过滤验证（过敏药物排除、配伍禁忌拦截） |

---

### 阶段 G：API 层与权限系统

**目的**：搭建 FastAPI 入口服务，实现 JWT 认证与角色权限（admin / patient），暴露问诊、患者信息、知识库管理等 RESTful 接口，接入限流保护。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| G1 | FastAPI 应用骨架 | `src/api/app.py`、`src/api/routes/__init__.py` | 应用可启动并挂载路由集合；注册 `prometheus-fastapi-instrumentator` 并暴露 `/metrics` 端点（**此时端点存在但无业务指标，业务埋点在 H2 完成，HTTP 层指标由 instrumentator 自动采集**）；**健康检查端点 `/healthz` + `/readyz` 在 H8 实现**，本任务不实现，不占用 `/health` 命名；启动冒烟测试 |
| G2 | JWT 认证中间件 | `src/api/middleware/auth_middleware.py`、`src/api/routes/auth.py` | 注册 / 登录 → JWT 签发；token 校验 + 角色提取；过期 / 无效 token 返回 401；单元测试 |
| G3 | 限流中间件 | `src/api/middleware/rate_limiter.py` | 基于内存的滑动窗口限流；超限返回 429；单元测试（进程内有效，多实例部署时由 H6 切换为 Redis 后端以共享配额） |
| G4 | 问诊接口 | `src/api/routes/diagnosis.py`、`src/api/schemas/diagnosis_schema.py` | `POST /diagnose`：调用 Agent graph → 返回诊断结果；支持追问交互（session_id 关联）；**按 §9.6 规则在返回响应前写入一条 `rag_trace` 记录**（从 final State 组装 15 字段，含 `last_reranked_chunks` / `last_diagnose_prompt` / `last_diagnose_raw_output` / `session_token_usage` / `session_latency_ms`）；集成测试（含"`POST /diagnose` 完成后对应会话在 `rag_trace` 表有一条完整记录"断言） |
| G5 | 患者信息接口 | `src/api/routes/patient.py`、`src/api/schemas/patient_schema.py` | 患者 CRUD（仅 patient 角色可操作自己的数据）；集成测试 |
| G6 | 管理员接口 | `src/api/routes/admin.py` | 知识库上传（触发 Ingestion Pipeline）/ 系统配置修改 / 用户管理；仅 admin 角色；集成测试 |
| G7 | Nginx 反向代理 | `infra/docker/nginx.conf`、`docker-compose.yml` 更新 | Nginx 代理 FastAPI；HTTPS（可选）；健康检查 |

---

### 阶段 H：基础设施增强（监控、缓存、日志）

**目的**：接入 Prometheus + Grafana 指标监控，Promtail + Loki 日志采集，落地 Redis 缓存客户端并与业务层对接，完善 config/logging_config.py。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| H1 | Redis 缓存客户端 | `src/db/redis/cache.py` | **仅实现配置缓存**（`config:<key_name>`，TTL 60s）读写测试通过；**不实现 RAG 响应级缓存**（原因见 §5.1"为何不做 RAG 响应级缓存"——Agentic 工作流下同一 query 在不同 State 应产出不同结果，query 字符串做 key 会串话）；Redis 不可用时降级（回源 PG）测试通过 |
| H2 | Prometheus 指标埋点 | `src/common/metrics.py` 完善、`infra/prometheus/prometheus.yml` | **基础指标**：向量检索耗时 / LLM 调用耗时 / Token 统计 / QPS / 错误率；**上下文/会话级指标**（对应 4.2.7 上下文表）：`context_tokens_per_llm_call` / `context_structured_fields_size` / `context_messages_count` / `context_loop_iterations`；**结构化输出健康度**（对应 4.2.7 结构化输出表，**调 prompt/Schema 的核心可观测面**）：`structured_output_attempt_total` / `retry_total` / `failure_total` / `fallback_triggered_total` / `diagnose_failure_reason_total` / `latency_seconds`，按 `node` + `schema` 标签分桶（`failure_total` 额外按 `exception_type`、`fallback_triggered_total` 额外按 `fallback_type`、`diagnose_failure_reason_total` 按 `reason_kind`）；**实现方式（明确约束）**：① `src/common/metrics.py` 仅做**模块级指标对象声明**（6 个指标作为单例，import 使用）+ 定义 `RetryObserver(BaseCallbackHandler)` 用于捕获 `with_retry` 内部重试事件；② **禁止**封装装饰器 / helper 函数 / 上下文管理器；③ 各 LLM 调用点按 §9.1 伪代码模板**裸写** `try/except/finally` 手动 `.inc()` / `.observe()`，调用时通过 `config={"callbacks": [retry_observer], "metadata": {...}}` 传入 RetryObserver；Prometheus 可抓取；**本任务只负责 Prometheus 聚合时序指标，与 G4 的 `rag_trace` per-session DB 记录是两个独立系统（见 §9.6），数据不重复**；**MVP 基线（本任务内完成）**：④ 接入 `prometheus-fastapi-instrumentator` 暴露 HTTP 层指标（`http_request_duration_seconds` 等，§5.2.1 ②），一行代码注册；⑤ 依赖层指标（§5.2.1 ③）——SQLAlchemy 事件订阅（`src/db/postgres/metrics.py`）、Redis Histogram wrapper（≤ 20 行）、Milvus client wrapper（`src/db/milvus/client.py`）；⑥ `/metrics` 端点排除 `/healthz`/`/readyz`/`/metrics` 自身避免自污染；⑦ **演进路径指标不在本任务范围**（见 §5.2.5），严禁提前埋点；单元测试：对一个典型调用点断言成功/重试/失败/兜底四种路径下对应指标的增量正确；对 `RetryObserver` 单独断言 `on_retry` 触发时 `_retries` 正确累加；HTTP/依赖指标通过 `GET /metrics` 断言能看到对应 metric family |
| H3 | Grafana 仪表盘 | `infra/grafana/dashboards/` | 应用性能仪表盘 + 硬件资源仪表盘；导入即可用 |
| H4 | 日志采集（Promtail → Loki） | `config/logging_config.py`、`infra/promtail/promtail-config.yml`、`infra/loki/loki-config.yml` | 诊断日志 / 错误日志 / 访问日志 写入 Loki（**JSON 结构化格式**，强制字段见 §5.2.1.1：`trace_id` / `session_id` / `patient_id` / `node` / `level` / `message` / `exc_info` / `timestamp`）；`config/logging_config.py` 用 `python-json-logger` 的 `JsonFormatter` + `contextvars.ContextVar` 在 FastAPI middleware 入口注入 `trace_id`，与 G4 写 `rag_trace` 用同一个 UUID 打通审计↔日志；审计日志写入 PostgreSQL；Grafana 可通过 `trace_id` label 在 Loki 中检索并关联到 PG `rag_trace` 详情 |
| H5 | Node Exporter 硬件监控 | `docker-compose.yml` 更新 | CPU / 内存 / 磁盘 / 网络指标采集；Grafana 仪表盘可视化 |
| H5b | DCGM Exporter GPU 监控 | `docker-compose.yml` 更新 | GPU 使用率 / 显存 / 温度 / 功耗指标采集（NVIDIA DCGM Exporter :9400）；Grafana GPU 仪表盘 |
| H6 | Redis 缓存与业务层对接 | `src/db/redis/cache.py` 与业务层对接、`src/api/middleware/rate_limiter.py` 改造 | 动态配置缓存（60s TTL）生效；冷启动首次读取回源 PG 并写缓存；**将 G3 的内存滑动窗口限流替换为 Redis 后端**（多实例共享配额，SCRIPT `INCR` + `EXPIRE` 原子操作）；集成测试（**不含 RAG 响应缓存**，见 H1） |
| H7 | 动态配置管理 | `src/db/postgres/` 中 system_config 表 | system_config 表存储 Top-K / 温度 / 阈值等；服务定时读取（经 Redis 缓存）；admin 可通过 API 修改 |
| H8 | 健康检查端点 | `src/api/routes/health.py` | 实现 `GET /healthz`（liveness，零依赖，固定 200）+ `GET /readyz`（readiness，并发探测 PG `SELECT 1` + Milvus ping，2s 超时，任一失败返 503 且响应体列出 `failing`；Redis 不可用仍算 ready，对齐 §5.1 降级模式）；两个端点不经过 JWT/限流/审计；详见 §5.2.4；单元测试覆盖所有依赖正常 / PG 失败 / Milvus 失败 / 同时失败 4 条路径 |

---

### 阶段 I：评估体系

**目的**：实现离线评估（RAG 检索质量 + Agent 决策质量）、在线追踪（端到端延时 + Token 统计）、LLM Judge 评分，建立回归基线。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| I1 | RAG 离线评估 | `evaluation/offline/rag_evaluator.py`、`evaluation/datasets/rag_eval.jsonl`、`src/agent/schemas/evaluation.py`（首次创建，含 `FaithfulnessScore` / `ClaimJudgment` / `RelevanceScore` / `HallucinationReport`，定义与 §9.5 一致；I2/I3 继续追加 Schema 到本文件） | 召回率 / 准确率 / MRR 指标计算；Golden Test Set 构建；可复现执行 |
| I2 | Agent 离线评估（L1~L5 梯度） | `evaluation/offline/agent_evaluator.py`、`evaluation/datasets/agent_eval.jsonl`、追加到 `src/agent/schemas/evaluation.py`（`DecisionTraceScore`，定义与 §9.5 一致） | Mock 检索结果 → 评估 Agent 决策链；覆盖 L1（完整信息）~ L5（矛盾信息）五个梯度；工具选择准确率 / 自我纠错能力 / 幻觉决策 等维度 |
| I3 | LLM Judge | `evaluation/offline/llm_judge.py`、追加到 `src/agent/schemas/evaluation.py`（`ResponseQualityScore` / `AdviceCompletenessScore`，定义与 §9.5 一致） | Prompt 来自 `src/prompts/evaluation.py`；评估响应质量 + 轨迹合理性；输出评测报告 |
| I4 | 在线追踪 | `evaluation/online/tracing.py` | 端到端延时 / 每步 Token 统计 / 每次运行上报；阈值告警 |
| I5 | 评估脚本入口 | `scripts/` 或 `evaluation/` 中的 runner | `python -m evaluation.offline.rag_evaluator` / `agent_evaluator` 一键执行 |

---

### 阶段 J：端到端验收与文档收口

**目的**：在真实环境（非 Mock）下跑通全链路 E2E 冒烟测试——与前面各阶段使用 Mock 存储 / Mock LLM 的集成测试不同，本阶段使用真实运行的 PostgreSQL、Milvus、LLM 推理后端，验证数据真正在各层之间流转。完善 README，确保"开箱即用 + 可复现"。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| J1 | E2E：Ingestion 全链路 | `tests/e2e/test_ingestion_e2e.py`（与 C7 的 `tests/integration/test_ingestion_pipeline.py` 区分：C7 是 Mock 依赖的集成测试，J1 是真实 Milvus/PG 的端到端冒烟） | 样例 PDF → MinerU 产物 → 完整摄取 → Milvus + PostgreSQL（含 raw_documents 表）数据验证 |
| J2 | E2E：Retrieval 全链路 | `tests/e2e/test_retrieval_e2e.py`（走真实 Milvus + 真实 Embedding/Reranker） | 真实 query → 双路召回 → RRF → Rerank → Top-K 结果校验 |
| J3 | E2E：Agent 全链路 | `tests/e2e/test_agent_workflow_e2e.py`（**与 F15 的 `tests/integration/test_agent_workflow.py` 区分**：F15 走 Mock LLM + Mock DB 的集成测试，J3 走真实 DashScope + 真实 Milvus/PG 的端到端冒烟） | 模拟患者输入 → 完整工作流 → 诊断输出（覆盖正常/高危/追问/低置信度路径） |
| J4 | E2E：API 接口 | `tests/e2e/test_api_e2e.py`（真实 FastAPI + 真实后端） | 注册 → 登录 → 问诊 → 追问 → 获取结果 完整交互链路 |
| J5 | README 完善 | `README.md` | 项目介绍 / 快速开始 / 环境要求 / Docker 部署 / 配置说明 / API 文档 / 评估运行 |
| J6 | 清理与一致性检查 | 全项目 | 无未使用的 import / 无空实现桩 / 类型注解完整 / 全部测试通过 |

---

## 8.4 进度跟踪表

> 状态说明：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成
>
> 更新时间：每完成一个子任务后更新对应状态

### 阶段 A：工程骨架与基础设施基座

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| A1 | 初始化目录树与最小可运行入口 | [x] | 2026-05-01 | §1.3.1 目录树修订(删过时 `data/` + 简化 `terms/`);`src/__init__.py` + `src/__main__.py` 就绪,`python -m src` 通过 |
| A2 | Docker Compose 搭建存储基座 | [~] | | 已:compose 配齐(13 服务,Mongo 已删)、Milvus etcd/minio/standalone 三件套 healthy、端口绑 127.0.0.1 防外网;待:PG/Redis 实启验证 |
| A3 | 配置加载与校验 | [x] | 2026-05-01 | `config/settings.py` 实现(§9.7 + `LLM_API_KEY` 必填),5 测试 PASS;spec 同步删除冗余的 `config/model_config.py` |
| A4 | pytest 测试基座 | [~] | | 已:tests/ 双层(unit 5 + integration 5)、`test_settings.py` 4/4 PASS、`test_terms_retrieval_smoke.py` collect OK(等 mineru 跑完 GPU 释放再实跑)、CLAUDE.md 加测试位置规则;待:conftest 设计、`test_reranker_smoke` / `test_terms_retrieval_smoke` 真跑过 |
| A5 | 公共工具模块 | [ ] | | |
| A6 | Prompt 模板骨架 | [ ] | | |

### 阶段 B：数据层与模型客户端

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| B1 | PostgreSQL 连接池 + ORM 模型 | [~] | | 已:`connection.py`(engine + session_scope)+ `models.py` Source/RawDocument/Chunk + 配套 upsert/bulk_upsert 接口(7 unit + 5 integration PASS,含父子 FK + 部分索引验证 + TEXT[] roundtrip);待:users/patients/sessions/conversations/audit 等 G/F 阶段才用的表 ORM |
| B2 | PostgreSQL 迁移脚本 | [~] | | 已:`0001_raw_documents.sql`(sources + raw_documents + GIN)、`0002_chunks.sql`(chunks + 5 索引,含两个 partial index);待:其他表迁移、Alembic 接入(2 个迁移阶段手动 psql 即可,Alembic 推迟到表数 ≥ 5 才接) |
| B3 | Milvus 连接管理 + docs_collection | [x] | 2026-05-01 | `config/milvus_schema.py` 9 字段 schema(spec 8 + BM25 派生 sparse)+ HNSW dense / BM25 sparse(中文 analyzer)/ 3 scalar 索引;`src/db/milvus/docs_collection.py` ensure/upsert/search_dense/search_sparse_bm25/count/drop;9 unit + 5 integration PASS(中文 BM25 命中"胆囊炎"验证) |
| B4 | Milvus terms_collection | [x] | 2026-04-30 | schema(8 字段)+ HNSW + INVERTED 索引 + ensure/upsert/search/count/drop 接口齐;`config/milvus_schema.py` + `src/db/milvus/terms_collection.py` |
| B5 | PostgreSQL raw_documents 表（MinerU 产物存储） | [x] | 2026-05-02 | `connection.py`(engine + session_scope)+ `models.py`(Source/RawDocument ORM + `upsert_source`/`upsert_raw_document` 幂等接口,ON CONFLICT DO UPDATE);4 个 JSONB 字段全 NOT NULL(spec §2.4.4 修订版);11 测试 PASS(6 unit schema 锁 + 5 integration:upsert/幂等/级联删/GIN jsonpath 查 type/FK 违反) |
| B6 | Qwen3-Embedding-8B 客户端 | [~] | | 已:8bit 加载链路验证(BitsAndBytesConfig + device_map='auto'),scripts 内已实战调用,显存 9.3GB;待:封装到 `src/models/embedding_model.py` |
| B7 | BGE-Reranker-v2-minicpm-layerwise 客户端 | [~] | | 已:`src/rag/retrieval/reranker.py` 封装 LayerWiseFlagLLMReranker(lazy load + cutoff_layer=28)+ 冒烟测试 `tests/integration/test_reranker_smoke.py`;待:真跑过测试 |
| B8 | Qwen LLM 推理客户端 | [x] | 2026-05-01 | `src/models/llm_client.py::get_llm()` 工厂(lru_cache,薄层不封装 retry/metrics 按 §9.1);7 测试 PASS(5 unit + 2 integration smoke 真调 DashScope qwen3.5-122b-a10b 通,流式 + 非流式) |

### 阶段 C：Ingestion Pipeline

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| C1 | MinerU 产物加载器 | [x] | 2026-05-02 | `src/rag/ingestion/mineru_loader.py::load_mineru_output()`(177 行)读 4 文件 + **双清洗 image VLM 幻觉**(v2 `block.content.content` 删除 + 用同段文本作指纹精确 substring 删 markdown,短指纹 < 20 字符跳过防误删,清洗后 grep 自检 unclean 报 warning)+ source_id 走 C3 + upsert sources/raw_documents + 返回 stats dict(预留 H2/§5.2.3 埋点接口);保留 image_caption / image_footnote / bbox / `![](images/...)` 占位符 / table.html / chart.content / page_header 等(过滤归 C2);11 unit + 3 integration PASS;`scripts/load_mineru.py` 批量入口(单本/--all);**13 本教材全部灌入 PG**(13912 页 / 264948 block / 删 7426 image content / 0 指纹遗漏 / 22.6s,raw_documents 表占 273MB);顺手删 0 行僵尸文件 `image_caption.py` |
| C2 | Chunking(父子分块 + 表格双粒度) | [~] | | 已:**step1 title.level 重建**(已弃案,见 §3.1.1 限制 2 / §3.1.2,改用目录权威清单);**step2 block extractor**(`extract_chunkable_text` 已实现,15 unit PASS);**step5 12 本书逐本 POC 全部完成**(2026-05-03 至 2026-05-06,12 本累计 ~12000 父块 / ~25000 子块,**全部 mismatch=0**);通用 SOP 沉淀至 [scripts/METHODOLOGY.md](scripts/METHODOLOGY.md)(~1200 行 + §11 27 条决策来源)+ 12 本 specific BOOK_NOTES.md;**chunks 表 schema 升级到位**(§3.1.2:chunk_type / linked_chunk_id / image_path / sub_type 字段 + relative_chunk_index 改 TEXT + embedding_status 加 bm25_only,为 step4 table+chart 双粒度铺路);**step4 chart/figure manifest+heading+多面板合并**(2026-05-08):`scripts/extract_figures.py`(4026 条 manifest)+ `scripts/derive_figure_heading_paths.py`(figure 块按 (pg_start, head 前缀) 反查 POC parent → 关联 heading_path,3891 hit / 135 孤儿)+ `scripts/merge_multipanel_figures.py`(方案 A':同 page + 同 heading_path + 严格相邻 + caption 必含「图 N-Y」模式 → 27 anchor 组吸纳 33 sibling,chunk_kind / sub_type 不做硬约束);**step4 table 跨页冗余去重**(2026-05-10~11):`scripts/merge_crosspage_tables.py`(识别 mineru 跨页冗余转录 sibling:同 head + 紧邻页 + sibling 空 cap + 表头一致 → 89 anchor 组吸纳 91 duplicate;新增 `merged_html_extension` 字段,sibling 真有新行时(loose-norm 全位置匹配)anchor 拿合并 html → 1 个 anchor 触发,实测 mineru 97% sibling 是冗余;`resolve_anchor_for_dup` 加 cluster 排除 dup 修穿透 bug,per-sibling 解析最近 anchor)+ `scripts/reroute_figure_in_table.py`(把 caption 写「图 N-X」但 chunk_kind=table 的 16 条改回 figure 走 vision LLM,html `<img src=` ≥5 张兜底);待:**step3 把 POC port 到 production** `chunking.py` 主流程 / **table 双粒度** chunks 表落库 + 逐行 chunk |
| C2.5 | 用药指南专用处理(待定) | [ ] | | **背景**:《中国医师药师临床用药指南》是药典/reference book(每药品名独立 title,30289 条),通用"篇/章/节"chunking 策略不适用(C2-step1 验证 fallback 99.9%)。**候选方案**:A 药品级 chunker(每药品 → 1 条完整 chunk 含【适应症】【用法】【禁忌】) / B 改 PG `drug_reference` 表 + terms_collection alias linking(更贴药典 reference 本质,绕开 Milvus 模糊检索的 overkill)。**当前**:raw_documents 已灌(source_id `189905989d350dd2`),C2 主流程通过 exclude 列表跳过它,C5/C6 同样跳过,本 RAG 主线不阻塞。**决策时机**:C2 主流程 + 12 本 chunking 跑通后,根据实际检索召回率与产品场景独立 PR |
| C3 | 幂等性工具 | [x] | 2026-05-02 | `src/rag/ingestion/idempotency.py` 6 个纯函数(normalize / source_id / heading_path_id / chunk_id / parent_chunk_id / content_hash);全部按 §3.1.4 规则,无 IO 无状态;30 unit PASS(覆盖 normalize 6 个、source_id 5、heading_path 5、chunk_id 3、parent 3、content_hash 4 + 综合 4) |
| C4 | LLM 语义增强 | [~] | | 已:**child chunk enrichment 22287 条全跑完**(a44ca9cf,`scripts/enrichment.py` deepseek-v4-pro 16 并发,disk-first jsonl);**chart/figure summary 单步 4 字段 enrichment 1023/1023 完成**(2026-05-08~10,`scripts/figure_enrichment_generation.py` qwen3.5-plus 多模态 12 并发 + 16 条 reroute 走 vision,产出 medical_statement/title/summary/hypothetical_questions 一次到位 — 替代原 spec §3.1.3.2 的"两步"设计:vision LLM 看图直出 4 字段避免 Stage 2 enrichment 看不到图导致的视觉幻觉扩散),**0 fail** / 33 figure-multipanel sibling 合并到 anchor;**table summary 单步 4 字段 enrichment 2744/2744 完成**(2026-05-10~11,`scripts/table_enrichment_generation.py` deepseek-v4-pro 16 并发 ~2h,**0 fail**;91 跨页 duplicate skip;1 anchor 拿 49 char merged_html_extension 跑合并 html);新增 schema `FigureSummaryEnrichmentOutput`(§9.5)+ prompt 共享 `_SHARED_4FIELD_TAIL`(child 对齐:questions 临床/口语分布、英中文混排、空字段非空兜底、caption 杂质识别)+ **footnote 必传**(table/figure user template 都加,system prompt 强调"必须用 footnote 解读缩写/图例/单位定义,但不写参考文献条目入 ms");deepseek 用 `method="json_mode"` 避 BadRequest(json_schema 不支持);待:**child / figure / table enrichment 进 PG**(C5 多向量 + C6 三层存储) |
| C5 | 多向量 Embedding | [ ] | | |
| C6 | 三层存储写入 + 僵尸清理 | [ ] | | |
| C7 | Pipeline 编排 | [ ] | | |
| C8 | 摄取入口脚本 | [ ] | | |

### 阶段 D：术语库与 Entity Linking

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| D1 | 术语数据整理与清洗 | [~] | | 已:ICD-10 北京临床版 v601 下载就位(/data/medical-resources/ICD10/,40k 行)+ build_icd10.py 内嵌清洗(去重、空值过滤、类型推导);待:CMeSH 等其他来源(YAGNI 暂不做) |
| D2 | 术语库构建脚本 | [x] | 2026-04-30 | `terms/build_icd10.py` 灌入 40474 条 ICD-10 北京临床版;主键 `{icd_code}_{SHA256(alias)[:16]}` + Milvus upsert 保证幂等;categorize:R 段→symptom,其他→disease |
| D3 | 术语检索接口 | [x] | 2026-04-30 | `search_aliases` 候选池放大 + 按 `preferred_term` 去重(score 同则取 concept_id 更短/字母序更小);确定性 tie-break 保证幂等;冒烟测试 11 条 query Top-1 命中率 95%、Top-K 信息密度 5/5 |

### 阶段 E：Retrieval

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| E1 | 查询预处理（分路构建） | [ ] | | |
| E2 | Sparse Retriever（Milvus BM25） | [ ] | | |
| E3 | Dense Retriever（单次 ANN） | [ ] | | |
| E4 | 单阶段多路 RRF 融合 + 多向量聚合 | [ ] | | |
| E5 | Reranker 精排 + 回退（diagnose ⑩ 前置） | [ ] | | |
| E6 | 元数据过滤 | [ ] | | |

### 阶段 F：Agent 工作流

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| F1 | MedicalState 定义 + 初始化工厂 | [x] | 2026-05-01 | `src/agent/state.py` 实现(§4.1.1 37 字段 **Pydantic BaseModel** + 嵌套 `PresentIllnessSlots` / `SessionTokenUsage` / `SessionLatencyMs` + `create_initial_state`),8 测试 PASS(字段清单 + 类型校验 + 老数据反序列化默认值 + 多 session 不共享 + §9.2 演化规则) |
| F2 | 节点 ①：info_collect | [ ] | | |
| F2.5 | 节点 ①.5：analyze_initial_reports | [ ] | | |
| F3 | 节点 ②：build_query | [ ] | | |
| F4 | 节点 ③：retrieve | [ ] | | |
| F5 | 节点 ④：extract_symptoms | [ ] | | |
| F6 | 节点 ⑤：select_discriminative_symptom | [ ] | | |
| F7 | 条件路由：should_continue | [ ] | | |
| F8 | 节点 ⑥⑦：追问循环 | [ ] | | |
| F9 | 节点 ⑧⑨：检查循环 | [ ] | | |
| F10 | 节点 ⑩：diagnose | [ ] | | |
| F11 | 条件路由：diagnose_router | [ ] | | |
| F12 | 节点 ⑪：safety_gate | [ ] | | |
| F13 | 节点 ⑫⑬：建议与输出 | [ ] | | |
| F14 | StateGraph 编排 | [ ] | | |
| F15 | 全工作流集成测试 | [ ] | | |

### 阶段 G：API 层与权限系统

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| G1 | FastAPI 应用骨架 | [ ] | | |
| G2 | JWT 认证中间件 | [ ] | | |
| G3 | 限流中间件 | [ ] | | |
| G4 | 问诊接口 | [ ] | | |
| G5 | 患者信息接口 | [ ] | | |
| G6 | 管理员接口 | [ ] | | |
| G7 | Nginx 反向代理 | [ ] | | |

### 阶段 H：基础设施增强

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| H1 | Redis 缓存客户端 | [ ] | | |
| H2 | Prometheus 指标埋点 | [ ] | | |
| H3 | Grafana 仪表盘 | [ ] | | |
| H4 | 日志采集（Promtail → Loki） | [ ] | | |
| H5 | Node Exporter 硬件监控 | [ ] | | |
| H5b | DCGM Exporter GPU 监控 | [ ] | | |
| H6 | Redis 缓存与业务层对接 | [ ] | | |
| H7 | 动态配置管理 | [ ] | | |
| H8 | 健康检查端点 | [ ] | | |

### 阶段 I：评估体系

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| I1 | RAG 离线评估 | [ ] | | |
| I2 | Agent 离线评估（L1~L5 梯度） | [ ] | | |
| I3 | LLM Judge | [ ] | | |
| I4 | 在线追踪 | [ ] | | |
| I5 | 评估脚本入口 | [ ] | | |

### 阶段 J：端到端验收与文档收口

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| J1 | E2E：Ingestion 全链路 | [ ] | | |
| J2 | E2E：Retrieval 全链路 | [ ] | | |
| J3 | E2E：Agent 全链路 | [ ] | | |
| J4 | E2E：API 接口 | [ ] | | |
| J5 | README 完善 | [ ] | | |
| J6 | 清理与一致性检查 | [ ] | | |






