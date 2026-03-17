# 7. 项目排期

## 7.1 排期原则

严格对齐本 DEV_SPEC 的架构分层与第 6 节目录结构。

1. **只按本文档设计落地**：以第 6 节目录树为"交付清单"，每一步都要在文件系统上产生可见变化。
2. **先打通主闭环，再逐层增强**：优先做"可跑通的端到端路径（Ingestion → Retrieval → Agent 单轮诊断）"，再补齐追问循环、多步检索、用药安全等增强节点。
3. **外部依赖可替换/可 Mock**：LLM（Qwen）/ Embedding（BGE-M3）/ Reranker / Milvus / PostgreSQL / MongoDB 的真实调用在单元测试中一律用 Fake/Mock，集成测试再开真实后端。
4. **每个小阶段给出验收标准**：明确"完成"的定义，避免模糊交付。
5. **基础设施按需引入**：Docker Compose 在阶段 A 搭建基座，监控/缓存等在主链路跑通后再逐步接入。

## 7.2 阶段总览

| 阶段 | 名称 | 目的 |
|------|------|------|
| **A** | 工程骨架与基础设施基座 | 建立可运行、可配置、可测试的工程骨架；Docker Compose 拉起全部存储依赖 |
| **B** | 数据层与模型客户端 | 打通 PostgreSQL / Milvus / MongoDB / Redis 连接；封装 BGE-M3、Reranker、Qwen 推理客户端 |
| **C** | Ingestion Pipeline（MinerU → Chunk → Embedding → 存储） | 离线摄取链路跑通，样例文档写入 Milvus + PostgreSQL + MongoDB，支持幂等与增量 |
| **D** | Retrieval（Dense + Sparse + RRF + Rerank） | 在线查询链路跑通，得到 Top-K chunks（含引用信息），具备稳定回退策略 |
| **E** | 术语库与 Entity Linking | 构建 terms_collection，实现口语→标准术语映射，为 Agent 症状预处理提供基础 |
| **F** | Agent 工作流（LangGraph StateGraph） | 按 3.1 节设计落地全部节点与条件边，实现完整诊断工作流 |
| **G** | API 层与权限系统 | FastAPI 入口服务、JWT 认证、角色权限、限流，暴露问诊接口 |
| **H** | 基础设施增强（监控、缓存、日志） | Prometheus + Grafana 指标监控，Loki 日志采集，Redis 缓存层 |
| **I** | 评估体系 | 离线评估（RAG + Agent）、在线追踪、LLM Judge |
| **J** | 端到端验收与文档收口 | 全链路 E2E 测试，README 完善，确保开箱即用 |

## 7.3 详细排期

---

### 阶段 A：工程骨架与基础设施基座

**目的**：建立可运行、可配置、可测试的工程骨架；Docker Compose 拉起全部存储依赖（PostgreSQL、Milvus、MongoDB、Redis），后续所有模块都能以 TDD 方式落地。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| A1 | 初始化目录树与最小可运行入口 | 第 6 节完整目录结构、`pyproject.toml`、`src/__init__.py` 等 | `python -m src` 不报错；目录结构与第 6 节一致 |
| A2 | Docker Compose 搭建存储基座 | `docker-compose.yml`、`infra/docker/` | `docker compose up -d` 可拉起 PostgreSQL + Milvus + MongoDB + Redis，各服务健康检查通过 |
| A3 | 配置加载与校验 | `config/settings.py`、`config/model_config.py`、`.env.example` | 从 `.env` 加载配置，缺失必填项时抛明确错误；单元测试覆盖 |
| A4 | pytest 测试基座 | `tests/`、`pyproject.toml [tool.pytest]` | `pytest` 可运行，冒烟测试通过 |
| A5 | 公共工具模块 | `src/common/normalize.py`、`hashing.py`、`metrics.py` | normalize + SHA256 哈希函数单元测试通过；与 2.1.4.2 定义一致 |

---

### 阶段 B：数据层与模型客户端

**目的**：打通四层存储连接（PostgreSQL / Milvus / MongoDB / Redis），封装三个模型推理客户端（BGE-M3 / Reranker / Qwen），使上层业务代码可通过统一接口调用。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| B1 | PostgreSQL 连接池 + ORM 模型 | `src/db/postgres/connection.py`、`models.py` | 连接池可用；sources / chunks / users / patients / conversations 等表 ORM 模型与 1.2.4 定义一致 |
| B2 | PostgreSQL 迁移脚本 | `src/db/postgres/migrations/` | Alembic `upgrade head` 可创建全部表结构与索引（与 1.2.4.2、1.2.4.3、1.2.4.5 一致） |
| B3 | Milvus 连接管理 + docs_collection | `src/db/milvus/connection.py`、`docs_collection.py`、`config/milvus_schema.py` | Collection Schema 与 1.2.4.1 定义一致；upsert + search 集成测试通过 |
| B4 | Milvus terms_collection | `src/db/milvus/terms_collection.py` | Schema 与 1.2.4.6 一致；upsert + 向量检索集成测试通过 |
| B5 | MongoDB 连接管理 + raw_documents | `src/db/mongo/connection.py`、`raw_documents.py` | raw_documents Collection 操作与 1.2.4.4 一致；upsert 集成测试通过 |
| B6 | Redis 缓存客户端 | `src/db/redis/cache.py` | 配置缓存（TTL 60s）+ RAG 响应缓存（TTL 1h）读写测试通过 |
| B7 | BGE-M3 Embedding 客户端 | `src/models/embedding_model.py` | CPU 推理；单条/批量编码接口；Dense + Sparse 双路输出；单元测试（Mock）+ 集成测试 |
| B8 | BGE-Reranker-v2-m3 客户端 | `src/models/reranker_model.py` | CPU 推理；输入 [query, doc] 对，输出相关性分数；超时回退机制；单元测试 |
| B9 | Qwen LLM 推理客户端 | `src/models/llm_client.py` | 对接 llama.cpp / vLLM 后端；支持流式/非流式输出；单元测试（Mock） |

---

### 阶段 C：Ingestion Pipeline（MinerU → Chunk → Embedding → 存储）

**目的**：离线摄取链路跑通，能把样例 PDF 文档经 MinerU 解析 → 切分 → 增强 → 向量化 → 写入 Milvus + PostgreSQL + MongoDB，支持幂等写入与增量更新。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| C1 | MinerU 产物加载器 | `src/rag/ingestion/mineru_loader.py` | 读取 `mineru_output/` 下的 `.md` + `content_list.json`；写入 MongoDB `raw_documents`；单元测试 |
| C2 | Chunking（Markdown 切分） | `src/rag/ingestion/chunking.py` | 基于 `RecursiveCharacterTextSplitter`；输出携带 heading_path、relative_chunk_index；单元测试 |
| C3 | 幂等性工具 | `src/rag/ingestion/idempotency.py` | source_id / heading_path_id / chunk_id / content_hash 生成逻辑与 2.1.4 一致；单元测试覆盖 normalize + 多级哈希 |
| C4 | LLM 语义增强 | `src/rag/ingestion/enrichment.py` | 单次 LLM 调用生成 title / summary / tags / hypothetical_questions；Prompt 复用 `skills/chunk-enrichment/`；单元测试（Mock LLM） |
| C5 | 图像 Caption 关联 | `src/rag/ingestion/image_caption.py` | 基于 content_list 中的 bbox 坐标与 Chunk offset 范围匹配；填充 image_captions 字段；单元测试 |
| C6 | 多向量 Embedding | `src/rag/ingestion/embedding.py` | 对每个 Chunk 生成 1 original + 1 summary + 2~3 question 向量记录；original 含 Dense + Sparse，其余仅 Dense；批处理；content_hash 增量判断；单元测试 |
| C7 | 三层存储写入 + 僵尸清理 | `src/rag/ingestion/storage.py` | PostgreSQL chunks 表 upsert + Milvus 向量 upsert + 僵尸 chunk 差集清理（2.1.4.3 三步逻辑）；集成测试 |
| C8 | Pipeline 编排 | `src/rag/ingestion/pipeline.py` | 串联 C1~C7；`python scripts/ingest.py <pdf_path>` 可完整摄取一份文档；集成测试 |
| C9 | 摄取入口脚本 | `scripts/ingest.py`、`scripts/init_db.py`、`scripts/init_milvus.py` | CLI 可用；支持单文件/批量摄取；初始化脚本可创建表结构和 Collection |

---

### 阶段 D：Retrieval（Dense + Sparse + RRF + Rerank）

**目的**：在线查询链路跑通，输入用户 query，经预处理 → 双路召回 → RRF 融合 → 多向量去重 → 精排，输出 Top-K chunks（含 original_content + heading_path 引用信息），具备稳定回退策略。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| D1 | 查询预处理（共享 + 分路） | `src/rag/retrieval/query_processing.py` | 指代消歧（LLM）→ 关键词提取 → 术语扩展（查 terms_collection）→ 上下文补全（多轮）→ MultiQuery 改写；单元测试（Mock LLM） |
| D2 | Sparse Retriever（BM25） | `src/rag/retrieval/sparse_retriever.py` | 以关键词 + 同义词 OR 表达式查询 Milvus Sparse 向量；返回 Top-N；单元测试 |
| D3 | Dense Retriever（MultiQuery + 内层 RRF） | `src/rag/retrieval/dense_retriever.py` | 多个语义变体独立检索 → 内层 RRF 合并（按排名倒数加权）→ 输出 Dense Top-N；与 2.2.2 设计一致；单元测试 |
| D4 | 外层 RRF 融合 + 多向量去重 | `src/rag/retrieval/fusion.py` | Dense Top-N + Sparse Top-N → 外层 RRF → 按 source_chunk_id 去重（保留最高分）→ Top-M；单元测试 |
| D5 | Reranker 精排 + 回退 | `src/rag/retrieval/reranker.py` | Cross-Encoder 精排 Top-M → Top-K；超时/不可用时回退至 RRF Top-K（2.2.3 策略）；单元测试 |
| D6 | 元数据过滤（Pre-filter + Post-filter） | `src/rag/retrieval/` 各文件内 | Pre-filter：source_id / tags 在 Milvus 检索时过滤；Post-filter：在 Rerank 前兜底过滤；missing → include 宽松策略 |

---

### 阶段 E：术语库与 Entity Linking

**目的**：构建 `terms_collection`（1.2.4.6），导入 CHIP/CBLUE + ICD-10-CN + CMeSH 三层术语数据，实现口语→标准术语的向量检索映射，为 Agent 节点 0a 的 Entity Linking 提供基础。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| E1 | 术语数据整理与清洗 | `terms/chip_cblue/`、`terms/icd10_cn/`、`terms/cmesh/` | 三层数据源格式统一为 `{concept_id, preferred_term, alias, source_vocab, icd10, category}` |
| E2 | 术语库构建脚本 | `terms/build_terms.py`、`scripts/seed_terms.py` | 对 alias 文本做 BGE-M3 Dense 编码 → upsert 到 terms_collection；幂等；集成测试 |
| E3 | 术语检索接口 | `src/db/milvus/terms_collection.py` 扩展 | 输入口语文本，返回 Top-5 候选术语（含 concept_id / preferred_term / icd10）；按 category 可过滤；集成测试 |

---

### 阶段 F：Agent 工作流（LangGraph StateGraph）

**目的**：按 3.1 节设计，使用 LangGraph StateGraph 实现完整诊断工作流。先落地最小可用路径（0a → 3 → 4b），再逐步补齐追问循环、高危转诊、并行分支、用药安全等节点。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| F1 | DiagnosisState 定义 | `src/agent/state.py` | TypedDict 包含 raw_input / symptom_profile / risk_flags / symptom_completeness / candidate_diseases / dept_dist / risk_factors / final_diagnosis / output / medication_plan / clarification_round / emergency_referral / symptom_confidence 等全部字段 |
| F2 | 节点 0a：symptom_preprocess | `src/agent/nodes/symptom_preprocess.py` | Stage 1（LLM NER：实体抽取 + 否定 + 时态 + 数值）+ Stage 2（Entity Linking：查 terms_collection Top-5 → LLM 选择）；歧义实体标记 ambiguous；写入 state；复用 `skills/symptom-standardize/`；单元测试 |
| F3 | 条件边：risk_router | `src/agent/routers/risk_router.py` | 基于 state.risk_flags 检测高危信号（胸痛放射、突发剧烈头痛等）；检测到 → 0c，否则 → completeness_router；单元测试 |
| F4 | 节点 0c：emergency_referral | `src/agent/nodes/emergency_referral.py` | 标注高危症状 + 紧急程度分级（120 / 急诊 / 当日就诊）；不输出诊断；终止工作流；单元测试 |
| F5 | 条件边：completeness_router | `src/agent/routers/completeness_router.py` | 完整度达标且无 ambiguous → 进入并行节点；未达标且 < 3 轮 → 0b；已满 3 轮 → 强制进入（confidence 下调）；单元测试 |
| F6 | 节点 0b：symptom_clarification | `src/agent/nodes/symptom_clarification.py` | 选取区分度最高的缺失维度/歧义项生成追问（≤ 3 个问题）；`interrupt()` 等待患者回答；回答合并入 raw_input → 回到 0a；复用 `skills/followup-generation/`；单元测试 |
| F7 | 节点 1a：symptom_analysis | `src/agent/nodes/symptom_analysis.py` | 基于 symptom_profile 查询 Milvus 全库检索（doc_type = 教材）；初步匹配候选疾病（带概率）；统计科室分布 → dept_dist；降级策略；单元测试 |
| F8 | 条件边：dept_router | `src/agent/routers/dept_router.py` | top 科室占比 ≥ 70% → 1b；否则 → 1c；单元测试 |
| F9 | 节点 1b + 1c：召回节点 | `src/agent/nodes/dept_filtered_recall.py`、`cross_dept_recall.py` | 1b 加科室 filter 精细召回；1c 全库扩大召回；更新 candidate_diseases；单元测试 |
| F10 | 节点 2：history_analysis | `src/agent/nodes/history_analysis.py` | 查 PostgreSQL 获取病史 / 家族史 / 当前用药 → risk_factors；DB 不可用时空历史降级；单元测试 |
| F11 | StateGraph 编排（并行分支） | `src/agent/graph.py` | 1a 与 2 通过 Send API 并行触发；1b/1c 在 1a 后串行；fan-in 等待全部完成；集成测试 |
| F12 | 节点 3：diagnostic_reasoning | `src/agent/nodes/diagnostic_reasoning.py` | 读取 candidate_diseases + risk_factors + 检查报告 → 检索诊疗指南（调用 Retrieval）→ LLM 推理 → final_diagnosis（含置信度 + 推理链）；复用 `skills/diagnostic-reasoning/`；单元测试 |
| F13 | 条件边：confidence_router | `src/agent/routers/confidence_router.py` | 高危疾病 < 80% → 4a；普通 < 60% → 4a；否则 → 4b；阈值从 system_config 读取；单元测试 |
| F14 | 节点 4a + 4b：诊断输出 | `src/agent/nodes/uncertainty_report.py`、`diagnosis_report.py` | 4a：候选疾病 + 概率 + 推理链 + 建议检查项 + 免责声明，终止；4b：最终诊断 + 用药方向，进入节点 5；单元测试 |
| F15 | 节点 5：medication_safety | `src/agent/nodes/medication_safety.py` | 查 PostgreSQL 过敏史 + 当前用药 → 检索药物相互作用文献 → 检查禁忌症 → medication_plan；单元测试 |
| F16 | 全工作流集成测试 | `tests/integration/test_agent_workflow.py` | Mock 存储 + Mock LLM，验证正常路径 / 高危转诊 / 追问循环 / 低置信度 四条典型路径 |

---

### 阶段 G：API 层与权限系统

**目的**：搭建 FastAPI 入口服务，实现 JWT 认证与角色权限（admin / patient），暴露问诊、患者信息、知识库管理等 RESTful 接口，接入限流保护。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| G1 | FastAPI 应用骨架 | `src/api/app.py`、`src/api/routes/__init__.py` | 应用可启动；`/health` 返回 200；Prometheus `/metrics` 端点可用 |
| G2 | JWT 认证中间件 | `src/api/middleware/auth_middleware.py`、`src/api/routes/auth.py` | 注册 / 登录 → JWT 签发；token 校验 + 角色提取；过期 / 无效 token 返回 401；单元测试 |
| G3 | 限流中间件 | `src/api/middleware/rate_limiter.py` | 基于 Redis 的滑动窗口限流；超限返回 429；单元测试 |
| G4 | 问诊接口 | `src/api/routes/diagnosis.py`、`src/api/schemas/diagnosis_schema.py` | `POST /diagnose`：调用 Agent graph → 返回诊断结果；支持追问交互（session_id 关联）；集成测试 |
| G5 | 患者信息接口 | `src/api/routes/patient.py`、`src/api/schemas/patient_schema.py` | 患者 CRUD（仅 patient 角色可操作自己的数据）；集成测试 |
| G6 | 管理员接口 | `src/api/routes/admin.py` | 知识库上传（触发 Ingestion Pipeline）/ 系统配置修改 / 用户管理；仅 admin 角色；集成测试 |
| G7 | Nginx 反向代理 | `infra/docker/nginx.conf`、`docker-compose.yml` 更新 | Nginx 代理 FastAPI；HTTPS（可选）；健康检查 |

---

### 阶段 H：基础设施增强（监控、缓存、日志）

**目的**：接入 Prometheus + Grafana 指标监控，Promtail + Loki 日志采集，Redis 缓存层生效，完善 config/logging_config.py。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| H1 | Prometheus 指标埋点 | `src/common/metrics.py` 完善、`infra/prometheus/prometheus.yml` | 向量检索耗时 / LLM 调用耗时 / Token 统计 / QPS / 错误率 等指标上报；Prometheus 可抓取 |
| H2 | Grafana 仪表盘 | `infra/grafana/dashboards/` | 应用性能仪表盘 + 硬件资源仪表盘；导入即可用 |
| H3 | 日志采集（Promtail → Loki） | `config/logging_config.py`、`infra/promtail/promtail-config.yml`、`infra/loki/loki-config.yml` | 诊断日志 / 错误日志 / 访问日志 写入 Loki；审计日志写入 PostgreSQL；Grafana 可查询 |
| H4 | Node Exporter 硬件监控 | `docker-compose.yml` 更新 | CPU / 内存 / 磁盘 / 网络 / GPU 指标采集；Grafana 仪表盘可视化 |
| H5 | Redis 缓存生效 | `src/db/redis/cache.py` 与业务层对接 | 动态配置缓存（60s TTL）+ RAG 响应缓存（1h TTL）生效；冷启动自动预热；集成测试 |
| H6 | 动态配置管理 | `src/db/postgres/` 中 system_config 表 | system_config 表存储 Top-K / 温度 / 阈值等；服务定时读取（经 Redis 缓存）；admin 可通过 API 修改 |

---

### 阶段 I：评估体系

**目的**：实现离线评估（RAG 检索质量 + Agent 决策质量）、在线追踪（端到端延时 + Token 统计）、LLM Judge 评分，建立回归基线。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| I1 | RAG 离线评估 | `evaluation/offline/rag_evaluator.py`、`evaluation/datasets/rag_eval.jsonl` | 召回率 / 准确率 / MRR 指标计算；Golden Test Set 构建；可复现执行 |
| I2 | Agent 离线评估（L1~L5 梯度） | `evaluation/offline/agent_evaluator.py`、`evaluation/datasets/agent_eval.jsonl` | Mock 检索结果 → 评估 Agent 决策链；覆盖 L1（完整信息）~ L5（矛盾信息）五个梯度；工具选择准确率 / 自我纠错能力 / 幻觉决策 等维度 |
| I3 | LLM Judge | `evaluation/offline/llm_judge.py` | 复用 `skills/llm-judge/`；评估响应质量 + 轨迹合理性；输出评测报告 |
| I4 | 在线追踪 | `evaluation/online/tracing.py` | 端到端延时 / 每步 Token 统计 / 每次运行上报；阈值告警 |
| I5 | 评估脚本入口 | `scripts/` 或 `evaluation/` 中的 runner | `python -m evaluation.offline.rag_evaluator` / `agent_evaluator` 一键执行 |

---

### 阶段 J：端到端验收与文档收口

**目的**：补齐 E2E 测试，完善 README，全链路验收，确保"开箱即用 + 可复现"。

| 编号 | 任务 | 产出文件 | 验收标准 |
|------|------|---------|---------|
| J1 | E2E：Ingestion 全链路 | `tests/integration/test_ingestion_pipeline.py` | 样例 PDF → MinerU 产物 → 完整摄取 → Milvus + PostgreSQL + MongoDB 数据验证 |
| J2 | E2E：Retrieval 全链路 | `tests/integration/test_retrieval.py` | 真实 query → 双路召回 → RRF → Rerank → Top-K 结果校验 |
| J3 | E2E：Agent 全链路 | `tests/integration/test_agent_workflow.py` | 模拟患者输入 → 完整工作流 → 诊断输出（覆盖正常/高危/追问/低置信度路径） |
| J4 | E2E：API 接口 | 新增 `tests/integration/test_api_e2e.py` | 注册 → 登录 → 问诊 → 追问 → 获取结果 完整交互链路 |
| J5 | README 完善 | `README.md` | 项目介绍 / 快速开始 / 环境要求 / Docker 部署 / 配置说明 / API 文档 / 评估运行 |
| J6 | 清理与一致性检查 | 全项目 | 无未使用的 import / 无空实现桩 / 类型注解完整 / 全部测试通过 |

---

## 7.4 进度跟踪表

> 状态说明：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成
>
> 更新时间：每完成一个子任务后更新对应状态

### 阶段 A：工程骨架与基础设施基座

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| A1 | 初始化目录树与最小可运行入口 | [ ] | | |
| A2 | Docker Compose 搭建存储基座 | [ ] | | |
| A3 | 配置加载与校验 | [ ] | | |
| A4 | pytest 测试基座 | [ ] | | |
| A5 | 公共工具模块 | [ ] | | |

### 阶段 B：数据层与模型客户端

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| B1 | PostgreSQL 连接池 + ORM 模型 | [ ] | | |
| B2 | PostgreSQL 迁移脚本 | [ ] | | |
| B3 | Milvus 连接管理 + docs_collection | [ ] | | |
| B4 | Milvus terms_collection | [ ] | | |
| B5 | MongoDB 连接管理 + raw_documents | [ ] | | |
| B6 | Redis 缓存客户端 | [ ] | | |
| B7 | BGE-M3 Embedding 客户端 | [ ] | | |
| B8 | BGE-Reranker-v2-m3 客户端 | [ ] | | |
| B9 | Qwen LLM 推理客户端 | [ ] | | |

### 阶段 C：Ingestion Pipeline

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| C1 | MinerU 产物加载器 | [ ] | | |
| C2 | Chunking（Markdown 切分） | [ ] | | |
| C3 | 幂等性工具 | [ ] | | |
| C4 | LLM 语义增强 | [ ] | | |
| C5 | 图像 Caption 关联 | [ ] | | |
| C6 | 多向量 Embedding | [ ] | | |
| C7 | 三层存储写入 + 僵尸清理 | [ ] | | |
| C8 | Pipeline 编排 | [ ] | | |
| C9 | 摄取入口脚本 | [ ] | | |

### 阶段 D：Retrieval

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| D1 | 查询预处理（共享 + 分路） | [ ] | | |
| D2 | Sparse Retriever（BM25） | [ ] | | |
| D3 | Dense Retriever（MultiQuery + 内层 RRF） | [ ] | | |
| D4 | 外层 RRF 融合 + 多向量去重 | [ ] | | |
| D5 | Reranker 精排 + 回退 | [ ] | | |
| D6 | 元数据过滤 | [ ] | | |

### 阶段 E：术语库与 Entity Linking

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| E1 | 术语数据整理与清洗 | [ ] | | |
| E2 | 术语库构建脚本 | [ ] | | |
| E3 | 术语检索接口 | [ ] | | |

### 阶段 F：Agent 工作流

| 编号 | 任务名称 | 状态 | 完成日期 | 备注 |
|------|---------|------|---------|------|
| F1 | DiagnosisState 定义 | [ ] | | |
| F2 | 节点 0a：symptom_preprocess | [ ] | | |
| F3 | 条件边：risk_router | [ ] | | |
| F4 | 节点 0c：emergency_referral | [ ] | | |
| F5 | 条件边：completeness_router | [ ] | | |
| F6 | 节点 0b：symptom_clarification | [ ] | | |
| F7 | 节点 1a：symptom_analysis | [ ] | | |
| F8 | 条件边：dept_router | [ ] | | |
| F9 | 节点 1b + 1c：召回节点 | [ ] | | |
| F10 | 节点 2：history_analysis | [ ] | | |
| F11 | StateGraph 编排（并行分支） | [ ] | | |
| F12 | 节点 3：diagnostic_reasoning | [ ] | | |
| F13 | 条件边：confidence_router | [ ] | | |
| F14 | 节点 4a + 4b：诊断输出 | [ ] | | |
| F15 | 节点 5：medication_safety | [ ] | | |
| F16 | 全工作流集成测试 | [ ] | | |

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
| H1 | Prometheus 指标埋点 | [ ] | | |
| H2 | Grafana 仪表盘 | [ ] | | |
| H3 | 日志采集（Promtail → Loki） | [ ] | | |
| H4 | Node Exporter 硬件监控 | [ ] | | |
| H5 | Redis 缓存生效 | [ ] | | |
| H6 | 动态配置管理 | [ ] | | |

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