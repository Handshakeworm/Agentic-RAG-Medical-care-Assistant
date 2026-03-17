# 1. 项目总览
## 作者的电脑配置为9800x3d，rtx5070ti (16GB)，RAM：48GB
## 项目亮点：

项目带有SKILL libarary，为众多流程创建其专属SKILL，形成可复用，方便管理，方便调试，避免上下文堆积，节省tokens的优点

于此同时，该项目在开发中也高度依赖SKILL进行自动开发、测试


根据业务场景进行优化，听取了经验丰富主任医师的意见

## 目录
```
1. 项目总览
   1.1 总体架构
   1.2 技术选型（模型选型 + 数据存储选型）
   
2. RAG系统pipeline
   2.1 数据摄取（MinerU）
   2.2 Chunking
   2.3 Transform & Enrichment
   2.4 幂等性设计
   2.5 Embedding
   2.6 索引存储

3. Agent 设计（整合目前分散的 Agent 内容）
   3.1 工作流（LangGraph StateGraph）
   3.2 检索策略（两步走粗排+精排）
   3.3 上下文管理

4. 基础设施（监控、缓存、权限等）

5. 系统性能评估（RAG 评估 + Agent 评估统一放这里）

6. 项目目录结构

7. 项目排期
   7.1 排期原则
   7.2 阶段总览
   7.3 详细排期
   7.4 进度跟踪表
```

## 1.1 总体架构
### 1.1.1 项目文件目录结构
```
Agentic-RAG-Medical-care-Assistant/
│
├── docker-compose.yml                  # 容器编排：PostgreSQL, Milvus, MongoDB, Redis, Prometheus, Grafana, Loki
├── .env.example                        # 环境变量模板（不提交 .env）
├── .gitignore
├── pyproject.toml                      # 项目依赖与构建配置
├── README.md
├── DEV_SPEC.md                         # 技术文档
│
├── config/                             # 静态配置文件
│   ├── settings.py                     # 全局配置（从环境变量/文件加载）
│   ├── model_config.py                 # 模型配置：BGE-M3、Reranker、Qwen 参数
│   ├── milvus_schema.py                # Milvus Collection Schema 定义（docs_collection + terms_collection）
│   └── logging_config.py               # 日志格式与 Promtail 适配
│
├── src/
│   ├── __init__.py
│   │
│   ├── api/                            # API 网关 / 入口服务
│   │   ├── __init__.py
│   │   ├── app.py                      # FastAPI 应用入口
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── diagnosis.py            # 问诊接口（POST /diagnose, 追问交互）
│   │   │   ├── auth.py                 # 登录注册（JWT）
│   │   │   ├── patient.py              # 患者信息 CRUD
│   │   │   ├── admin.py                # 管理员：知识库上传、配置修改
│   │   │   └── health.py               # 健康检查 & Prometheus /metrics
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth_middleware.py       # JWT 验证 + 角色判断（admin/patient）
│   │   │   └── rate_limiter.py         # 限流，保护本地资源
│   │   └── schemas/                    # Pydantic 请求/响应模型
│   │       ├── __init__.py
│   │       ├── diagnosis_schema.py
│   │       └── patient_schema.py
│   │
│   ├── agent/                          # Agent 编排层（LangGraph StateGraph）
│   │   ├── __init__.py
│   │   ├── graph.py                    # StateGraph 定义：节点注册、边与条件边连接
│   │   ├── state.py                    # DiagnosisState Schema（TypedDict）
│   │   ├── nodes/                      # 各节点实现
│   │   │   ├── __init__.py
│   │   │   ├── symptom_preprocess.py   # 节点 0a：LLM NER + Entity Linking
│   │   │   ├── symptom_clarification.py# 节点 0b：追问引导（interrupt()）
│   │   │   ├── emergency_referral.py   # 节点 0c：高危转诊
│   │   │   ├── symptom_analysis.py     # 节点 1a：症状初步分析 + 科室分布
│   │   │   ├── dept_filtered_recall.py # 节点 1b：科室精细召回
│   │   │   ├── cross_dept_recall.py    # 节点 1c：跨科室全库召回
│   │   │   ├── history_analysis.py     # 节点 2：患者历史分析
│   │   │   ├── diagnostic_reasoning.py # 节点 3：诊断推理
│   │   │   ├── uncertainty_report.py   # 节点 4a：低置信度输出
│   │   │   ├── diagnosis_report.py     # 节点 4b：高置信度输出
│   │   │   └── medication_safety.py    # 节点 5：用药安全检查
│   │   └── routers/                    # 条件边路由逻辑
│   │       ├── __init__.py
│   │       ├── risk_router.py          # 高危信号筛查
│   │       ├── completeness_router.py  # 追问循环判断
│   │       ├── dept_router.py          # 科室集中度路由
│   │       └── confidence_router.py    # 置信度路由
│   │
│   ├── rag/                            # RAG 系统 Pipeline
│   │   ├── __init__.py
│   │   ├── ingestion/                  # 2.1 数据摄取
│   │   │   ├── __init__.py
│   │   │   ├── mineru_loader.py        # 2.1.1 MinerU 产物加载（读取 markdown + content_list）
│   │   │   ├── chunking.py             # 2.1.2 RecursiveCharacterTextSplitter 切分
│   │   │   ├── enrichment.py           # 2.1.3 LLM 增强（title/summary/tags/questions）
│   │   │   ├── image_caption.py        # 2.1.3.2 图像 Caption 关联绑定
│   │   │   ├── idempotency.py          # 2.1.4 幂等性：source_id / heading_path_id / content_hash
│   │   │   ├── embedding.py            # 2.1.5 多向量 Embedding（Dense + Sparse，BGE-M3）
│   │   │   ├── storage.py              # 2.1.6 写入 PostgreSQL + Milvus（含僵尸清理）
│   │   │   └── pipeline.py             # 完整摄取 Pipeline 编排（串联以上步骤）
│   │   │
│   │   ├── retrieval/                  # 2.2 召回策略
│   │   │   ├── __init__.py
│   │   │   ├── query_processing.py     # 2.2.1 查询预处理（指代消歧、关键词提取、术语扩展、多角度改写）
│   │   │   ├── sparse_retriever.py     # 2.2.2 Sparse Route（BM25）
│   │   │   ├── dense_retriever.py      # 2.2.2 Dense Route（MultiQuery + 内层 RRF）
│   │   │   ├── fusion.py               # 2.2.2 外层 RRF 融合 + 多向量去重
│   │   │   └── reranker.py             # 2.2.3 精排（BGE-Reranker-v2-m3 / 回退策略）
│   │   │
│   │   └── context/                    # Agent 上下文管理（3.2）
│   │       ├── __init__.py
│   │       ├── compressor.py           # 上下文压缩（多轮对话摘要）
│   │       └── selector.py             # 上下文选择（历史筛选/截断）
│   │
│   ├── models/                         # 模型推理层
│   │   ├── __init__.py
│   │   ├── llm_client.py              # LLM 推理客户端（Qwen，llama.cpp/vLLM 后端）
│   │   ├── embedding_model.py         # BGE-M3 Embedding（CPU 推理）
│   │   └── reranker_model.py          # BGE-Reranker-v2-m3（CPU 推理）
│   │
│   ├── db/                            # 数据与缓存层
│   │   ├── __init__.py
│   │   ├── postgres/
│   │   │   ├── __init__.py
│   │   │   ├── connection.py           # PostgreSQL 连接池
│   │   │   ├── models.py               # ORM 模型（sources, chunks, users, patients, conversations 等）
│   │   │   └── migrations/             # 数据库迁移脚本（Alembic）
│   │   │       └── ...
│   │   ├── milvus/
│   │   │   ├── __init__.py
│   │   │   ├── connection.py           # Milvus 连接管理
│   │   │   ├── docs_collection.py      # 医学文献向量库操作（1.2.4.1）
│   │   │   └── terms_collection.py     # 术语向量库操作（1.2.4.6）
│   │   ├── mongo/
│   │   │   ├── __init__.py
│   │   │   ├── connection.py           # MongoDB 连接管理
│   │   │   └── raw_documents.py        # raw_documents Collection 操作（1.2.4.4）
│   │   └── redis/
│   │       ├── __init__.py
│   │       └── cache.py                # Redis 缓存（配置缓存 + RAG 响应缓存）
│   │
│   └── common/                        # 公共工具
│       ├── __init__.py
│       ├── normalize.py               # 文本规范化函数（全角转半角、NFC 等，见 2.1.4.2）
│       ├── hashing.py                 # SHA256 工具（chunk_id、content_hash、heading_path_id）
│       └── metrics.py                 # Prometheus 指标埋点
│
├── skills/                            # SKILL Library（见项目亮点）
│   ├── core/                          # Skill 引擎
│   │   ├── __init__.py
│   │   ├── loader.py
│   │   ├── registry.py
│   │   └── executor.py
│   ├── chunk-enrichment/              # Chunk 语义增强 Skill
│   │   ├── SKILL.md
│   │   ├── references/
│   │   ├── scripts/
│   │   └── assets/
│   ├── diagnostic-reasoning/          # 诊断推理 Skill
│   │   ├── references/
│   │   └── scripts/
│   ├── query-disambiguation/          # 查询消歧 Skill
│   │   ├── references/
│   │   └── scripts/
│   ├── symptom-standardize/           # 症状标准化 Skill
│   │   ├── references/
│   │   ├── scripts/
│   │   └── assets/
│   ├── followup-generation/           # 追问生成 Skill
│   │   └── references/
│   └── llm-judge/                     # LLM 评估 Skill
│       ├── references/
│       ├── scripts/
│       └── assets/
│
├── terms/                             # 术语词典数据（1.2.4.6 数据来源）
│   ├── chip_cblue/                    # CHIP/CBLUE 口语→标准术语数据集
│   ├── icd10_cn/                      # ICD-10-CN 国家医保局临床版
│   ├── cmesh/                         # CMeSH 中国医学主题词表
│   └── build_terms.py                 # 术语库构建脚本（→ terms_collection）
│
├── data/                              # 数据目录（.gitignore 排除）
│   ├── raw_pdfs/                      # 原始 PDF 指南/教材
│   └── mineru_output/                 # MinerU 解析产物
│       └── {document_name}/auto/
│           ├── images/
│           ├── {document_name}.md
│           ├── {document_name}_content_list.json
│           ├── {document_name}_middle.json
│           └── {document_name}_model.json
│
├── evaluation/                        # 5. 评估系统
│   ├── __init__.py
│   ├── datasets/                      # 测试集（JSON/JSONL）
│   │   ├── rag_eval.jsonl             # RAG 检索质量测试集
│   │   └── agent_eval.jsonl           # Agent 决策测试集（L1-L5 梯度）
│   ├── offline/
│   │   ├── rag_evaluator.py           # RAG 离线评估（召回率、准确率）
│   │   ├── agent_evaluator.py         # Agent 离线评估（轨迹、工具调用、容错）
│   │   └── llm_judge.py               # LLM Judge 评分
│   └── online/
│       └── tracing.py                 # 在线追踪（端到端延时、Token 统计）
│
├── infra/                             # 基础设施配置
│   ├── docker/
│   │   ├── Dockerfile.api             # API 服务镜像
│   │   ├── Dockerfile.llm             # LLM 推理服务镜像
│   │   └── nginx.conf                 # Nginx 反向代理配置
│   ├── prometheus/
│   │   └── prometheus.yml             # Prometheus 采集配置
│   ├── grafana/
│   │   └── dashboards/               # Grafana 仪表盘 JSON
│   ├── loki/
│   │   └── loki-config.yml
│   └── promtail/
│       └── promtail-config.yml
│
├── scripts/                           # 运维脚本
│   ├── init_db.py                     # 初始化 PostgreSQL 表结构 + 索引
│   ├── init_milvus.py                 # 初始化 Milvus Collection + 索引
│   ├── ingest.py                      # 文档摄取入口（调用 rag.ingestion.pipeline）
│   └── seed_terms.py                  # 术语库初始导入
│
└── tests/
    ├── unit/
    │   ├── test_normalize.py
    │   ├── test_hashing.py
    │   ├── test_chunking.py
    │   └── test_fusion.py
    └── integration/
        ├── test_ingestion_pipeline.py
        ├── test_retrieval.py
        └── test_agent_workflow.py
```
### 目录与文档章节对应关系

| DEV_SPEC 章节 | 对应目录 |
|---|---|
| 1.2.1 BGE-M3 Embedding 模型 | `src/models/embedding_model.py` |
| 1.2.2 Qwen LLM 推理模型 | `src/models/llm_client.py` |
| 1.2.3 BGE-Reranker 精排模型 | `src/models/reranker_model.py` |
| 1.2.4.1 Milvus 医学文献向量库 | `src/db/milvus/docs_collection.py` |
| 1.2.4.2 PostgreSQL 元数据存储 | `src/db/postgres/` |
| 1.2.4.3 PostgreSQL 对话记录 | `src/db/postgres/models.py` → conversations |
| 1.2.4.4 MongoDB 原始文档存储 | `src/db/mongo/raw_documents.py` |
| 1.2.4.5 PostgreSQL 病人信息 | `src/db/postgres/models.py` → patients 等 |
| 1.2.4.6 Milvus 术语向量库 | `src/db/milvus/terms_collection.py` + `terms/` |
| 2.1.1 MinerU 数据加载 | `src/rag/ingestion/mineru_loader.py` |
| 2.1.2 Chunking | `src/rag/ingestion/chunking.py` |
| 2.1.3 Transform & Enrichment | `src/rag/ingestion/enrichment.py` + `image_caption.py` |
| 2.1.4 幂等性设计 | `src/rag/ingestion/idempotency.py` + `src/common/hashing.py` |
| 2.1.5 Embedding | `src/rag/ingestion/embedding.py` |
| 2.1.6 索引存储 | `src/rag/ingestion/storage.py` |
| 2.2.1 查询预处理 | `src/rag/retrieval/query_processing.py` |
| 2.2.2 召回（Dense + Sparse + RRF） | `src/rag/retrieval/` |
| 2.2.3 精排与重排 | `src/rag/retrieval/reranker.py` |
| 3.1 Agent 工作流 | `src/agent/graph.py` + `nodes/` + `routers/` |
| 3.2 上下文管理 | `src/rag/context/` |
| 4.1 Redis 缓存 | `src/db/redis/cache.py` |
| 4.2 监控层 | `infra/prometheus/` + `infra/grafana/` + `infra/loki/` |
| 4.3 权限与配置 | `src/api/middleware/` + `src/db/postgres/` |
| 5. 评估系统 | `evaluation/` |
| SKILL Library | `skills/` |

### 1.1.2 项目层级
**客户端层**

- Nginx 反向代理（`infra/docker/nginx.conf`），暴露 REST 接口
- 认证中间件（`src/api/middleware/auth_middleware.py`）与限流中间件（`src/api/middleware/rate_limiter.py`），确保本地资源稳定

**API 服务层**

- FastAPI 应用（`src/api/app.py`），提供诊断、患者管理、健康检查、管理等路由
- 请求/响应 Schema 校验（`src/api/schemas/`）

**Agent 编排层（LangGraph StateGraph）**

- 状态图驱动的多步诊断流程（`src/agent/graph.py`）
- 节点：症状预处理、症状分析、病史分析、科室检索、跨科召回、诊断推理、用药安全、急诊转诊、诊断报告、不确定性报告、症状澄清（`src/agent/nodes/`）
- 路由器：完整性路由、置信度路由、科室路由、风险路由（`src/agent/routers/`）

**RAG 服务层**

- 数据摄取 Pipeline：MinerU 文档解析 → Chunking → LLM 增强（摘要/问题生成/图片描述） → 幂等写入 → Embedding → 向量存储（`src/rag/ingestion/`）
- 检索 Pipeline：查询处理 → Dense/Sparse 双路检索 → RRF 融合 → Reranker 精排（`src/rag/retrieval/`）
- 上下文管理：上下文筛选与压缩（`src/rag/context/`）

**模型推理层（本地部署）**

- LLM 推理：Qwen3.5 系列，通过 llama.cpp/Ollama 部署于本地 GPU（RTX 5070 Ti 16GB）（`src/models/llm_client.py`）
- Embedding：BGE-M3，部署于 CPU（`src/models/embedding_model.py`）
- Reranker：BGE-Reranker-v2-m3，部署于 CPU（`src/models/reranker_model.py`）

**数据与缓存层**

- 向量存储：Milvus（Dense + Sparse 向量，容器化部署）（`src/db/milvus/`）
- 元数据存储：PostgreSQL（Chunk 元数据、来源文档、医学术语等）（`src/db/postgres/`）
- 原始文档存储：MongoDB（MinerU 解析后的原始文档）（`src/db/mongo/`）
- 缓存：Redis（FAQ、热点查询等）（`src/db/redis/`）

**日志与监控层**

- 指标采集与告警：Prometheus（`infra/prometheus/`）
- 可视化面板：Grafana（`infra/grafana/`）
- 日志收集：Loki + Promtail（`infra/loki/`、`infra/promtail/`）
- 应用指标埋点（`src/common/metrics.py`）

**基础设施层（本地部署）**

- 容器编排：Docker Compose（`docker-compose.yml`）
- 容器镜像：API 服务与 LLM 推理服务分离（`infra/docker/Dockerfile.api`、`infra/docker/Dockerfile.llm`）
- 存储：本地磁盘
- 密钥管理：环境变量配置（`.env.example`）
