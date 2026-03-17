## 1.2 技术选型：
本项目选型使用大模型的时间为2026/3/6
### 1.2.1 embedding模型选型

Embedding 模型负责将文本转换为向量，用于粗排召回

#### 选型结论：BGE-M3（BAAI/bge-m3），部署于 CPU

##### 选型理由

1. **单模型覆盖双路编码需求**：BGE-M3 是目前唯一一个单模型同时输出 Dense、Sparse、ColBERT 三种向量的开源模型。本项目的混合检索架构（见 2.1.5）需要 Dense + Sparse 双路编码，BGE-M3 一个模型即可覆盖，无需额外维护独立的 BM25/SPLADE 稀疏编码器，显著降低部署与维护成本。

2. **中文医疗场景能力强**：由智源（BAAI）开发，中文语料训练充分。本项目涉及大量中文医学术语（ICD-10、SNOMED CT 等），BGE-M3 的中文理解能力在开源 Embedding 模型中属于第一梯队（C-MTEB 榜单前列）。同时支持中英混合，可覆盖英文医学文献检索需求。

3. **长上下文支持（8192 tokens）**：医疗指南的 Chunk 可能较长，8192 token 的上下文窗口远优于多数 Embedding 模型的 512 token 限制，能更完整地编码 Chunk 语义。

4. **与 Milvus 原生集成**：Milvus 官方文档和示例直接支持 BGE-M3 的 Dense + Sparse 输出格式，本项目的 Collection Schema（见 1.2.4.1）中的 `dense_vector` + `sparse_vector` 字段可无缝对接。

##### 部署策略：CPU 推理，不占用 GPU

模型参数量约 568M（~2.2GB），部署于 CPU（本机 48GB RAM 充裕）。理由如下：

- **离线 Embedding（文档入库）**：批量任务，可与 LLM 推理错开时间执行（如夜间跑 pipeline），CPU 批处理即可满足吞吐需求。
- **在线 Query Embedding（实时查询）**：Query 通常很短（几十个 token），CPU 编码延迟仅 10~30ms，用户无感知。
- **显存全部留给推理模型**：Qwen3.5-9B AWQ 4-bit 推理时峰值显存约 10~12GB（含 KV Cache），RTX 5070 Ti 仅 16GB。若 Embedding 模型也上 GPU，会压缩 KV Cache 空间，直接限制可处理的上下文长度，在多轮医疗问诊场景下不可接受。

##### 不追求更大 Embedding 模型的原因

Embedding 的职责是粗排召回，本项目已通过以下多层机制弥补单一 Embedding 精度的不足，无需为 Embedding 环节投入更多资源：

- 多向量表示（original + summary + question）大幅提升召回率（见 2.1.5）
- 混合检索（Dense + Sparse）关键词与语义互补（见 2.2.2）
- Reranker 精排才是决定最终检索精度的关键环节（见 2.2.3）
- Agent 两步检索策略可根据首次结果动态调整召回范围（见 2.2 召回策略）

精度提升的投入应优先放在 Reranker 选型和 Prompt 质量优化上，而非 Embedding 模型本身。

### 1.2.2 agent以及rag系统模型（本地部署）选型
#### 1.2.2.1 理论选型

**模型族系选择：Qwen 系列**

在当前主流开源大模型中，Qwen3.5 / Qwen3 系列是同时满足"参数量小（单卡可部署）"与"中文能力强"两项约束的少数选择之一。其中文语料覆盖广泛，对医学术语与临床文本的理解能力在同量级开源模型中属于第一梯队，是本项目的首选族系。

**验证方式：Ollama（llama.cpp 后端）**

作者使用 Ollama 对各候选模型进行快速原型验证，主要观测指标为显存占用与推理速度（eval rate）。Ollama 底层使用 llama.cpp，在消费级硬件上的端侧推理优化极为成熟——尤其是在显存不足需要 CPU Offload 时，llama.cpp 的资源调度与稳定性明显优于 vLLM（详见候选模型 3 的说明）。在初步筛选后，作者后续使用llama.cpp/vLLM进行后续调优和验证对比（小模型无offload，使用vLLM）。

> 涉及复杂医疗推理时，参数量更大的模型在推理质量上通常更具优势。小参数模型（9B）基本无性能压力，Ollama 快速验证的核心目的是评估较大的模型（14B 及以上）在本机是否可用。

---

#### 候选模型评估与选型策略

**选型优先级**
模型之间的能力明显35B-A3B高于其余二者
```
首选：35B-A3B（MoE） → 调试通过则直接采用
备选：14B / 9B（需实际业务测评后择优）
放弃：27B Dense（推理速度不可接受）
```

---

**🥇 首选：需调试验证（需 CPU Offload）**

**`unsloth/Qwen3.5-35B-A3B-Q4_K_M.gguf`**

该模型为 MoE 架构（Mixture of Experts），模型名称中的 A3B 表示每次前向推理实际激活的参数量约为 3B，总参数虽达 35B，但推理时的实际显存压力远低于同量级的 Dense 模型。由于单卡 16GB 显存不足以容纳全部权重，需启用 CPU Offload。

在 CPU Offload 场景下，llama.cpp 的表现优于 vLLM。vLLM 本身为高并发、大显存、超长上下文的服务端场景设计，即便使用 `--offload-experts-only` 专门避免路由层与注意力层的跨设备调度，也依然无法弥补其架构上对 Offload 场景的天然劣势——Offload 在 vLLM 中本就是兜底功能；而 llama.cpp 对端侧资源（CPU 多线程、极限场景内存管理）有极致优化，在单卡 Offload 场景下更稳定、吞吐更高。

Ollama 实测（显存基本跑满，prompt：`你是谁`）：

| 指标 | 数值 |
|---|---|
| total duration | 3.67s |
| prompt eval rate | 173.72 tokens/s |
| **eval rate** | **19.29 tokens/s** |

eval rate 约 19 tokens/s，体验略慢但基本可接受，仍有调参空间（如调整 `n_gpu_layers`、`--ctx-size` 等）。**只要调试通过、业务场景下推理速度可接受，即优先采用此模型。**

---

**🥈 备选：需业务场景实测对比**

若 35B-A3B 调试后仍无法满足推理速度要求，则在以下两个模型中择优：

1. `Qwen/Qwen3-14B-AWQ`（官方 AWQ 4-bit 量化版本）
2. `cyankiwi/Qwen3.5-9B-AWQ-4bit`

两者均可在显存范围内流畅运行，但参数量与推理质量之间存在权衡。不能仅凭显存占用或速度指标判断优劣，需通过实际业务测评决定（见 1.2.2.2）。

---

**❌ 已放弃（推理速度不可接受）**

**`unsloth/Qwen3.5-27B-Q4_K_M.gguf`**

Dense 架构全量 27B 参数，在单卡显存不足时 CPU Offload 比例更高，推理速度极慢。

Ollama 实测（显存基本跑满，prompt：`你是谁`）：

| 指标 | 数值 |
|---|---|
| total duration | 13.78s |
| prompt eval rate | 26.56 tokens/s |
| **eval rate** | **4.98 tokens/s** |

eval rate 仅约 5 tokens/s，与 35B-A3B（MoE，~19 tokens/s）相差近 4 倍，且无继续优化的空间，**放弃该模型**。

> **对比启示**：在单卡 CPU Offload 场景下，MoE 架构（实际激活参数少）的推理速度远优于同等标称参数量的 Dense 架构。选型时不应只看总参数量，应关注实际激活参数量与显存占用的比例。

---

#### 1.2.2.2 选型测评

1.2.2.1 的评估均基于简单 prompt 的速度测试，尚未反映真实业务场景的推理质量。若 35B-A3B 调试通过则直接采用，无需测评；若需在 14B 与 9B 之间抉择，则需对二者在以下维度进行对比测试：准确率、鲁棒性、推理成本、推理速度。


# 在本项目中，作者没有高质量的数据，也没有医学基础标注，微调需要考虑，目前没有这样的打算
# 厂商做过DPO和RLHF，但如果出现持续输出某类有害回答/不利行为，则可以做DPO对齐



### 1.2.3 reranker模型选型
Rerank 模型则是对召回的候选文档做精排。通常是 cross-encoder 架构——将 query 和 document 拼接在一起输入模型，直接输出一个相关性分数。因为 query 和 document 之间有充分的交互注意力，所以精度更高，但计算成本也更大，不适合直接用于全量检索，只适合对少量候选（比如 top-20 到 top-100）重新排序。

#### 选型结论：BGE-Reranker-v2-m3（BAAI/bge-reranker-v2-m3），部署于 CPU

##### 选型理由

1. **与 Embedding 模型同源，语义对齐**：BGE-Reranker-v2-m3 与本项目 Embedding 模型 BGE-M3 均出自智源（BAAI），对中文医疗术语的理解能力一致。避免出现 Embedding 召回了正确文档但 Reranker 因语言理解差异将其排低的问题，粗排→精排的语义衔接更稳定。

2. **中文医疗场景能力强**：智源中文语料训练充分，对 ICD-10、SNOMED CT 等医学术语有良好的理解能力。同时支持中英双语，可覆盖英文医学文献的精排需求。

3. **长上下文支持（8192 tokens）**：输入长度上限与 BGE-M3 一致，不会因 Chunk 过长被截断而丢失精排信息。医疗指南的 Chunk 可能较长，8192 token 窗口确保 query-document 对的完整交互。

4. **参数量适中，CPU 推理可行**：模型参数量约 568M（~2.2GB），与 BGE-M3 相当。本项目精排候选量为 M=20 左右，CPU 上对 20 个 query-doc pair 做 cross-encoder 打分，延迟约 100~300ms，用户可接受。

##### 部署策略：CPU 推理，不占用 GPU

与 BGE-M3 共享 CPU 推理资源（本机 48GB RAM 充裕），理由如下：

- **二者不会同时高负载**：Embedding 在文档入库时批量执行，Reranker 在用户查询时实时执行，负载天然错峰。
- **显存全部留给推理模型**：与 Embedding 选型逻辑一致，RTX 5070 Ti 16GB 显存全部分配给 Qwen3.5-9B / Qwen3-14B 推理，Reranker 上 GPU 会压缩 KV Cache 空间，在多轮问诊场景下不可接受。
- **候选量有限，CPU 即可满足**：精排仅处理 RRF 融合后的 Top-20 候选（见 2.2.3），不涉及大批量计算。

##### 备选方案与排除理由

| 备选模型 | 排除原因 |
|---------|---------|
| **Cohere Rerank** | 闭源 API 调用，本项目为本地部署架构，引入外部依赖违背设计原则；医疗数据不宜外传，存在合规风险 |
| **LLM Rerank（Qwen 自身做精排）** | 会抢占推理模型的 GPU 资源和推理队列，增加端到端延迟；结构化输出不如 cross-encoder 稳定；成本高于专用 Reranker |
| **BGE-Reranker-large（v1）** | 旧版本，中文能力和长上下文支持不如 v2-m3，最大输入仅 512 tokens，无法覆盖本项目的长 Chunk 场景 |
| **BGE-Reranker-v2-gemma** | 基于 Gemma 2B，参数量约 2B，CPU 推理延迟显著增加（约为 v2-m3 的 3~4 倍），精排精度提升有限，性价比不如 v2-m3 |

##### 与系统架构的衔接

- **输入**：RRF 融合 + 多向量去重后的 Top-M 候选（见 2.2.2），每条候选为 [query, original_content] 对
- **输出**：相关性分数排序后的 Top-K 结果，传给 LLM 生成诊断
- **回退机制**：Reranker 超时或不可用时，直接返回 RRF Top-K，确保系统可用性（见 2.2.3 回退策略）

### 1.2.4 数据存储选型及具体设计：
#### 1.2.4.1. 原始文档向量化的向量库：Milvus

每个 Chunk 在 Milvus 中对应 4~5 条向量记录（1 original + 1 summary + 2~3 question）：

| vector_type | id 规则 | Dense | Sparse | 说明 |
|-------------|---------|:-----:|:------:|------|
| `original` | `{chunk_id}` | ✅ | ✅ | 原文向量，支持语义检索与关键词检索 |
| `summary` | `{chunk_id}_summary` | ✅ | ❌ | 摘要向量，提升对模糊 query 的匹配能力 |
| `question` | `{chunk_id}_q{n}` | ✅ | ❌ | 问题向量，弥合患者口语与临床文本的语义鸿沟 |

summary / question 记录不生成 Sparse Vector——关键词匹配应基于原文，而非 LLM 改写文本，避免语义漂移。

**Milvus Collection Schema**：

```
{
    "id":               str,             # 本条记录唯一 ID（见上表）
    "source_chunk_id":  str,             # 所属原始 chunk_id（original 记录与 id 相同）
    "vector_type":      str,             # "original" | "summary" | "question"
    "dense_vector":     List[float],     # 语义向量（所有记录均有）
    "sparse_vector":    Dict[int,float], # 稀疏向量（仅 original 有，summary/question 存空字典 {}）
    "original_content": str,             # 原始 chunk 文本，冗余存储，命中后无需回查 PostgreSQL
    "source_id":        str,             # Pre-filter 字段：按来源文档过滤（见 1.2.4.2 sources 表）
    "tags":             List[str]        # Pre-filter 字段：按主题过滤
}
```

`title`、`heading_path` 等展示字段不在 Milvus 冗余，检索命中后以 `source_chunk_id` 回查 PostgreSQL `chunks` 表获取。


#### 1.2.4.2. 元数据存储：PostgreSQL

PostgreSQL 负责存储所有 Chunk 的结构性元数据与增强元数据，支撑幂等写入、僵尸清理、增量 Embedding 判断及检索结果的上下文还原。向量数据本身存储于 Milvus，PostgreSQL 不存储向量。

**sources 表**（来源文档注册表，source_id 的权威来源）

```sql
sources (
  source_id    TEXT PRIMARY KEY,          -- 文档唯一 ID（见 2.1.4.1）
  file_name    TEXT NOT NULL,             -- 原始文件名
  file_path    TEXT,                      -- 文件存储路径
  doc_type     VARCHAR(50),               -- 文档类型，如 guideline / textbook / protocol
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

**chunks 表**（Chunk 元数据核心表）

```sql
chunks (
  -- 幂等性字段（见 2.1.4）
  chunk_id              TEXT PRIMARY KEY,   -- SHA256(source_id:heading_path_id:relative_chunk_index)
  source_id             TEXT NOT NULL REFERENCES sources(source_id),
  heading_path_id       TEXT NOT NULL,      -- SHA256(H1_id:H2_id:...) 标题路径哈希
  heading_path          TEXT NOT NULL,      -- 人类可读标题路径，如 "第2章 > 2.1 > 2.1.4"，用于检索结果展示
  relative_chunk_index  INT  NOT NULL,      -- 同标题路径下的块序号（从 0 开始），用于顺序还原
  chunk_raw_text        TEXT NOT NULL,      -- Chunk 原始文本
  content_hash          TEXT NOT NULL,      -- SHA256(chunk_raw_text)，变动检测信号（见 2.1.4.3）

  -- LLM 增强字段（见 2.1.3）
  title                 TEXT,              -- LLM 生成的精准小标题
  summary               TEXT,             -- LLM 生成的内容摘要，同时作为摘要向量文本来源（见 2.1.5）
  tags                  TEXT[],           -- LLM 生成的主题标签数组
  hypothetical_questions TEXT[],          -- LLM 生成的假设性问题数组（2~3 条，见 2.1.5）
  image_captions        TEXT,             -- 多模态增强产出的图像/表格描述（无图时为 NULL）

  -- 运维状态字段
  embedding_status      VARCHAR(20) NOT NULL DEFAULT 'pending',
                                          -- pending / done / failed，用于追踪 Embedding 计算状态
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

**索引**

```sql
CREATE INDEX idx_chunks_source_id        ON chunks (source_id);           -- 僵尸清理差集查询
CREATE INDEX idx_chunks_heading_path_id  ON chunks (heading_path_id);     -- 按标题路径聚合查询
CREATE INDEX idx_chunks_content_hash     ON chunks (content_hash);        -- 跨文档内容去重
CREATE INDEX idx_chunks_embedding_status ON chunks (embedding_status)     -- 增量 Embedding 任务扫描
  WHERE embedding_status != 'done';
```

> `heading_path`（明文）与 `heading_path_id`（哈希）同时存储：前者用于 chunk_id 推导，后者用于检索结果展示来源标题，职责不同，不可合并。

#### 1.2.4.3. 对话记录：PostgreSQL

```sql
-- 对话记录表（功能用途）
conversations (
  id UUID PRIMARY KEY,
  session_id,
  user_id,
  user_input TEXT,
  llm_output TEXT,
  rag_context JSONB,
  created_at TIMESTAMP
)
```
#### 1.2.4.4. 原始指南/教材文档存储：MongoDB

MongoDB 负责存储 MinerU 解析后的所有原始产物，以 `source_id` 为主键聚合，与 PostgreSQL `sources` 表一一对应。

**存储动机**：MinerU 输出物既有深度嵌套的 JSON（`content_list`、`middle`），又有长文本 Markdown，结构异构且以"写一次、按需读"为主要访问模式，MongoDB 文档模型比 PostgreSQL JSONB 更自然，无需预定义 Schema 即可容纳 MinerU 各版本输出格式的差异。

**MongoDB Collection：`raw_documents`**

```json
{
  "source_id":       "string",    // 主键，与 PostgreSQL sources.source_id 完全对应
  "file_name":       "string",    // 原始文件名，如 "2024心力衰竭指南.pdf"
  "stored_at":       "ISODate",   // 本条记录写入时间

  // ── MinerU 文本产物 ──────────────────────────────────────────────
  "markdown_content": "string",   // target_document.md 全文，供 chunking pipeline 直接读取

  // ── MinerU JSON 产物（原样存入，不做二次解析）────────────────────
  "content_list":    [...],       // target_document_content_list.json
                                  // 含每个内容块的类型、文本、页码、坐标 bbox
                                  // Pipeline 用此字段做图像 caption 与 chunk 的位置匹配（见 2.1.3.2）
  "middle":          {...},       // target_document_middle.json
                                  // 含版面分析结构，排查解析异常时使用
  "model":           {...},       // target_document_model.json（可选）
                                  // 体积较大，仅在需要重新调试解析结果时写入，默认 null

  // ── 原始文件引用 ─────────────────────────────────────────────────
  "pdf_path":        "string"     // 原始 PDF 在本地磁盘的绝对路径，文件本身不入库
}
```

**索引**

```
db.raw_documents.createIndex({ "source_id": 1 }, { unique: true })
```

**字段说明**

| 字段 | 来源 | 主要用途 |
|------|------|---------|
| `markdown_content` | `target_document.md` | Chunking pipeline 的直接输入（见 2.1.2） |
| `content_list` | `content_list.json` | 图像/表格 bbox 坐标，支撑 caption 与 chunk 的位置关联（见 2.1.3.2） |
| `middle` | `middle.json` | 版面结构存档，供解析异常排查使用 |
| `model` | `model.json` | 模型推理细节，默认不写入，按需存储 |
| `pdf_path` | 文件系统 | 原始 PDF 路径引用，PDF 本体存本地磁盘 |

**不存入 MongoDB 的内容**

- 原始 PDF 文件本体：体积大，存本地磁盘，MongoDB 只记路径
- `target_document_span.pdf` / `target_document_layout.pdf`：MinerU 调试用中间产物，不纳入系统存储

#### 1.2.4.6. 术语向量库：Milvus（terms_collection）

`terms_collection` 是独立于医学文献向量库（1.2.4.1）的专用术语检索库，服务于节点 0a 的 Entity Linking 和 2.2.1 的术语扩展，两者均直接复用本库，不重复调用 LLM。

**数据来源（三层叠加，优先级从高到低）**：

| 层级 | 来源 | 内容 | 获取方式 |
|------|------|------|---------|
| Layer 1 PROJECT | 项目自建口语词典 | 患者口语、俗称 → 标准术语映射（如"肚子疼"→腹痛） | CHIP/CBLUE 数据集整理 + 医师意见，持续补充 |
| Layer 2 ICD-10-CN | 国家医保局临床版 | 中国医院实际使用的疾病编码，含中文标准名称和部分别名 | 国家医保局官网免费下载 |
| Layer 3 CMeSH | 中国医学主题词表 | 症状/解剖术语的中文规范名称与同义词，由中国医学科学院维护 | 官网免费申请 |

CHIP/CBLUE 医学实体标准化数据集（GitHub 开源）专为中文患者口语 → 标准术语设计，直接提供大量口语别名标注对，作为 Layer 1 的主要数据来源，大幅减少人工整理工作量。

**核心设计原则**：一条记录对应一个别名（alias），多别名同属一个 concept_id，向量化 alias 文本而非 preferred_term，使口语/缩写/英文专业术语均可通过向量检索命中标准术语。

**Milvus Collection Schema（terms_collection）**：

```
{
    "id":             str,          # 记录唯一 ID：{concept_id}_{alias_index}
    "concept_id":     str,          # 概念唯一 ID：优先用 ICD-10-CN 编码（如 "R10.4"）；
                                    # 无 ICD-10-CN 编码时用 CMeSH ID；
                                    # 两者均无时用项目自赋 ID（PROJECT_{hash}）
    "preferred_term": str,          # 该概念的标准首选术语，如"腹痛"
    "alias":          str,          # 本条记录的别名文本，如"肚子疼"/"腹部疼痛"/"abdominal pain"
    "source_vocab":   str,          # 别名来源：PROJECT / ICD10CN / CMESH / CHIP
    "icd10":          str,          # ICD-10-CN 编码，如 "R10.4"（无映射时为空）
    "category":       str,          # 概念类型：symptom / disease / anatomy / drug
    "dense_vector":   List[float]   # alias 文本的 BGE-M3 向量（仅 Dense，不需要 Sparse）
}
```

**与 1.2.4.1 的区别**：

| | 医学文献向量库（1.2.4.1） | 术语向量库（terms_collection） |
|---|---|---|
| 内容 | 医学指南/教材 Chunk | 术语别名条目 |
| 向量文本 | 原文/摘要/假设问题 | alias 字符串 |
| 检索目的 | 召回诊疗依据 | 实体归一化编码 |
| Sparse 向量 | ✅ original 有 | ❌ 不需要 |
| 更新频率 | 随文档导入更新 | 随 ICD-10-CN/CMeSH 版本更新，PROJECT 层持续补充 |

**索引**：

```
db.terms_collection.createIndex({ "concept_id": 1 })  # 按 concept_id 查所有别名（用于术语扩展）
db.terms_collection.createIndex({ "category": 1 })    # 按类型过滤（仅查 symptom 等）
```

#### 1.2.4.5. 病人信息：PostgreSQL

```
users (账号系统)
  └── patients (1:1，补充医疗信息)
        ├── medical_history (1:N，可以有多条病史)
        ├── allergies       (1:N，可以有多条过敏)
        ├── current_medications (1:N，可以有多条用药)
        └── family_history  (1:N，可以有多个亲属)
```

具体设计如下

```sql
-- 用户认证表
users (
  id UUID PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,       -- 存储哈希后的密码
  role VARCHAR(20) NOT NULL     -- patient / doctor / admin 等
)
```
```sql
-- 患者基本信息（关联 users 表）
patients (
  id UUID PRIMARY KEY REFERENCES users(id),
  name TEXT,
  gender VARCHAR(10),
  birth_date DATE,
  blood_type VARCHAR(5),        -- 血型，急诊相关
  height_cm INT,
  weight_kg DECIMAL(5,1),
  phone TEXT,
  emergency_contact TEXT        -- 紧急联系人
)
```
```sql
-- 既往病史（一对多）
medical_history (
  id UUID PRIMARY KEY,
  patient_id UUID REFERENCES patients(id),
  condition TEXT,               -- 疾病名称，如"2型糖尿病"
  diagnosed_at DATE,
  resolved_at DATE,             -- NULL表示持续中
  notes TEXT
)

-- 过敏史
allergies (
  id UUID PRIMARY KEY,
  patient_id UUID REFERENCES patients(id),
  allergen TEXT,                -- 过敏原，如"青霉素"
  allergen_type VARCHAR(20),    -- drug/food/other
  reaction TEXT,                -- 过敏反应描述
  severity VARCHAR(10)          -- mild/moderate/severe
)

-- 当前用药
current_medications (
  id UUID PRIMARY KEY,
  patient_id UUID REFERENCES patients(id),
  drug_name TEXT,
  dosage TEXT,                  -- "500mg"
  frequency TEXT,               -- "每日两次"
  started_at DATE,
  prescribed_by TEXT            -- 开药来源备注
)

-- 家族史
family_history (
  id UUID PRIMARY KEY,
  patient_id UUID REFERENCES patients(id),
  relation VARCHAR(20),         -- father/mother/sibling等
  condition TEXT,               -- 疾病名称
  notes TEXT
)
```


