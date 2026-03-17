# 2. RAG系统pipeline：
## 2.1 数据摄取：
### 2.1.1 数据加载及处理

使用 MinerU 作为文档解析器，以下为选型原因：
医疗场景的文档具有高度复杂性与专业性，对解析精度要求极高，MinerU 在以下几个关键维度上具备明显优势：
1. 扫描件与影像报告支持：医院文档大量以扫描 PDF 形式存在（如检验报告、病历归档），MinerU 内置高精度 OCR 引擎
2. 复杂表格的高精度还原：检验报告、用药记录、手术记录等文档均包含大量结构化表格。MinerU 基于深度学习的表格识别模型可准确还原行列结构，确保表格数据进入向量库后语义完整，避免因解析错乱导致的检索错误。
3. 医学公式与专业符号识别：医学文献、药品说明书中包含大量计量单位、化学式及统计公式，MinerU 支持 LaTeX 格式的公式输出，保障专业内容的准确提取。
4. 图文混排文档处理能力：影像科报告、图解等文档普遍存在图文混排，MinerU 具备多模态解析能力，可对图表进行结构化处理，而非直接丢弃。

由MinerU直接用命令行运行并解析后，将会在项目文件夹下出现如下文档
/project_folder/mineru_output/target_document/auto
- images/
- target_document_content_list.json
- target_document_origin.pdf
- target_document_middle.json
- target_document_model.json
- target_document_span.pdf
- target_document_layout.pdf
- target_document.md



项目使用的数据源资料中，存在大量：照片或简笔画示意图、表格、坐标图、思维导图，
对于照片或简笔画示意图的处理

对于表格的处理

对于坐标图的处理

对于思维导图的处理：


可观测、可视化管理、集成评估





## 2.1.2 chunking（LangChain 负责切分；独立、可控）

实现方案：使用 LangChain 的 `RecursiveCharacterTextSplitter` 进行切分。
优势：该方法对 Markdown 文档的结构（标题、段落、列表、代码块）有天然的适配性，能够通过配置语义断点（Separators）实现高质量、语义完整的切块。
输入：Loader 产出的 Markdown Document。
输出：若干 Chunk（或 Document-like chunks），每个 chunk 必须携带稳定的定位信息与来源信息：source, chunk_index, start_offset/end_offset（或等价定位字段）。


## 2.1.3 Transform & Enrichment（结构转换与深度增强）

### 2.1.3.1 结构转换

`RecursiveCharacterTextSplitter` 的输出为 `List[Document]`，每个 `Document` 对象包含 `page_content`（`str`）与基础 `metadata`（`dict`）。本步骤将 `page_content` 与各阶段元数据整合，写入 `chunks` 表（字段定义详见 1.2 数据存储设计 → chunks 表）。

### 2.1.3.2 增强策略

**语义元数据注入 (Semantic Metadata Enrichment)**：

策略：在基础元数据之上，利用 LLM 提取高维语义特征。
产出：为每个 Chunk 通过**单次 LLM 调用**统一生成以下字段，注入到 Metadata 中：
- **Title**（精准小标题）
- **Summary**（内容摘要）：同时作为摘要向量的文本来源（见 2.1.5）
- **Tags**（主题标签）
- **Hypothetical Questions**（假设性问题）：以患者口语视角，针对本 Chunk 内容生成 2~3 个患者可能提出的问题（见 2.1.5）。医疗场景中患者 query 多为口语症状描述，知识库内容多为临床陈述，该字段用于弥合二者之间的语义鸿沟，提升召回率。

**图像 Caption 注入 (Image Caption Injection)**：

> 注：Vision LLM 对图像的实际理解与 Caption 生成在 **2.1.1** 阶段按文档粒度统一完成，避免在 Chunk 级别重复调用。本步骤仅负责将已生成的 Caption 关联绑定到对应 Chunk，填充 `image_captions` 字段。

关联逻辑：依据 2.1.1 阶段解析出的图像位置（页码或字符偏移量）与 Chunk 的 `start_offset`/`end_offset` 进行范围匹配，将落在该 Chunk 范围内的图像 Caption 注入，实现”搜文出图”能力。

**工程特性**：Transform 步骤为原子化操作，每个 Chunk 独立处理，失败时仅需重试该 Chunk，不影响其他已完成的 Chunk。


## 2.1.4 幂等性设计(Idempotency)

**核心机制**：

三层存储均通过 Upsert 保证幂等写入，同一文档无论被处理多少次，均不产生重复数据：

| 存储层 | Upsert 主键 | 说明 |
|--------|------------|------|
| PostgreSQL `sources` 表 | `source_id` | 同一文档重复导入时直接覆盖，不新增记录（详见 2.1.4.1） |
| PostgreSQL `chunks` 表 | `chunk_id` | 配合 `content_hash` 实现增量更新，内容未变则跳过 Embedding（详见 2.1.4.2、2.1.4.3） |
| Milvus 向量记录 | 派生 ID | 由 `chunk_id` 确定性派生，如 `{chunk_id}_summary`（详见 2.1.6） |

**原子性保证**：Upsert 以 Batch 为单位进行事务性写入。若批次内某条写入失败，整批回滚，不产生部分写入的脏数据，下次重试时整批重新处理即可。


### 2.1.4.1 source_id

`source_id` 是来源文档的唯一标识符，source_id 生成规则详见（TODO：补充链接）。

**幂等写入**：每次文档摄取时，以 `source_id` 为主键对 `sources` 表执行 Upsert，更新 `updated_at` 等可变字段，不重复插入记录。


### 2.1.4.2 heading_path_id 的构建


**设计动机**：避免使用绝对位置编码——若使用绝对位置，文档中任意一处修改都会导致其后所有 Chunk 的位置编码全部失效。改用标题路径作为定位锚点，则只有标题本身变更才会影响对应的 `chunk_id`。

**构建步骤**：

**`normalize` 函数定义**

对标题文本执行以下操作（顺序执行）：

1. **Unicode 规范化**：转换为 NFC 形式，统一字符的组合方式
2. **全角转半角**：将全角字母、数字、空格转为对应半角字符（如 `Ａ→A`、`１→1`、`　→ `）
3. **大小写统一**：所有拉丁字母转为小写
4. **去除首尾空白**：trim 前后的空格、制表符
5. **合并内部空白**：将连续的空白字符（空格、制表符）压缩为单个空格

```python
import unicodedata
import re

def normalize(title: str) -> str:
    # 1. Unicode NFC 规范化
    s = unicodedata.normalize("NFC", title)
    # 2. 全角转半角
    s = s.translate(str.maketrans(
        "　！＂＃＄％＆＇（）＊＋，－．／０１２３４５６７８９：；＜＝＞？"
        "＠ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ［＼］＾＿"
        "｀ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ｛｜｝～",
        " !\"#$%&'()*+,-.//0123456789:;<=>?"
        "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_"
        "`abcdefghijklmnopqrstuvwxyz{|}~"
    ))
    # 3. 转小写
    s = s.lower()
    # 4. 去除首尾空白
    s = s.strip()
    # 5. 合并内部连续空白
    s = re.sub(r'\s+', ' ', s)
    return s
```

**设计说明**：
- 中文字符不做额外处理，NFC 已保证其规范性
- 不去除标点符号——标题中的标点（如冒号、括号）可能是有意义的区分因素
- 不做 stemming 或同义词处理，保持哈希的确定性和可复现性

---

**步骤 1：标准化各级标题，生成层级哈希**

将每个层级标题映射成一个稳定标识符（对标题文本规范化后取哈希）：

```
H1_id = hash(normalize(title_level1))
H2_id = hash(normalize(title_level2))
H3_id = hash(normalize(title_level3))
...
更深层的标题以此类推
```

结果为一个层级哈希序列，如 `[H1_id, H2_id, H3_id]`。

**步骤 2：拼接层级哈希，生成 heading_path_id**

将层级哈希按顺序拼接（冒号分隔），再整体哈希一次，得到固定长度的十六进制字符串。**只拼接实际存在的层级**，不补空位：

```
# 两级标题
heading_path_id = SHA256( H1_id + ":" + H2_id )

# 三级标题
heading_path_id = SHA256( H1_id + ":" + H2_id + ":" + H3_id )

# 通用形式
heading_path_id = SHA256( join(":", [H1_id, H2_id, ..., Hn_id]) )
```

**步骤 3：结合相对块索引，生成 chunk_id**

`relative_chunk_index` 为同一标题路径下的 Chunk 顺序编号（从 0 开始），确保同标题下多个 Chunk 各有唯一 ID，代入最终公式即得 `chunk_id`。

**最终公式**：

```
chunk_id = SHA256( source_id + ":" + heading_path_id + ":" + relative_chunk_index )
```



### 2.1.4.3 content_hash

**作用**：`content_hash` 是变动检测信号字段，与 `chunk_id` 分离，单独存储。

**生成方式**：

```
content_hash = SHA256( chunk_raw_text )
```

**职责边界**：

| 字段 | 职责 | 是否作为主键 |
|---|---|---|
| `chunk_id` | 结构定位（标题路径 + 块序号），稳定不变 | 是 |
| `content_hash` | 内容变动信号，触发更新 | 否 |

**更新逻辑**：

- Upsert 时，以 `chunk_id` 为主键进行匹配。
- 若 `content_hash` 与数据库中已有值相同 → 跳过 Embedding 计算，复用已有向量（注意：此"跳过"仅针对 Embedding 步骤，chunk_id 的遍历生成始终在全文档范围内完整执行）。
- 若 `content_hash` 不同 → 内容已变更，覆盖写入并重新触发 Embedding 计算。

这样即使文档局部修改，`chunk_id` 保持稳定（结构未变），仅通过 `content_hash` 的差异驱动增量更新，避免全量重建。

**注意：**修改标题时，`chunk_id` 会跟随变化，原标题下的旧 chunk 记录不会被自动覆盖，形成僵尸数据。需在每次文档处理流程中执行以下三步清理：

**文档处理的三步分层逻辑：**

1. **完整遍历（轻量）**：对整篇文档执行完整解析，生成当前版本所有 chunk 的 `chunk_id` 和 `content_hash`，此步骤仅涉及哈希计算，开销极低。
2. **僵尸清理**：以 `source_id` 为范围，从数据库中查出该文档所有已有 `chunk_id`（旧集合），与本次遍历生成的全量 `chunk_id`（新集合）做差集：
   ```
   待删除 = 旧集合 - 新集合   # 在旧集合中存在、但新集合中不存在的记录
   ```
   删除差集中的所有记录，消除僵尸 chunk。
3. **按需重算 Embedding**：对新集合中 `content_hash` 发生变化（或为全新）的 chunk，触发 Embedding 计算；`content_hash` 未变的 chunk 直接复用已有向量。


## 2.1.5 Embedding (多向量化)
差量计算 (Incremental Embedding / Cost Optimization)：
策略：在调用昂贵的 Embedding API 之前，计算 Chunk 的内容哈希（Content Hash）。仅针对数据库中不存在的新内容哈希执行向量化计算，对于文件名变更但内容未变的片段，直接复用已有向量，显著降低 API 调用成本。

**混合检索双路编码（Dense + Sparse）：**
为了支持高精度的混合检索（Hybrid Search），系统对每个 Chunk 并行执行双路编码计算：
- Dense Embeddings（语义向量）：调用 Embedding 模型（如 BGE）生成高维浮点向量，捕捉文本的深层语义关联，解决”词不同意同”的检索难题。
- Sparse Embeddings（稀疏向量）：利用 BM25 编码器或 SPLADE 模型生成稀疏向量（Keyword Weights），捕捉精确的关键词匹配信息，解决专有名词查找问题。

**多向量表示（文本多向量，Multi-Vector Representation）：**
为进一步提升召回率，系统对每个 Chunk 生成多条向量记录，均指向同一份原始 Chunk 内容。各向量记录携带 `vector_type` 字段加以区分：

| vector_type | 文本来源 | 作用 |
|---|---|---|
| `original` | Chunk 原文 | 主向量，捕捉原始语义 |
| `summary` | 2.1.3 生成的 Summary | 摘要向量，提升对模糊 query 的匹配能力 |
| `question` | 2.1.3 生成的 Hypothetical Questions | 问题向量，弥合患者口语描述与临床文本之间的语义鸿沟 |

每个 Chunk 产出 1 条 `original` + 1 条 `summary` + 2~3 条 `question` 向量记录，各条记录均通过 `source_chunk_id` 指向原始 Chunk，检索命中补充向量后统一回溯取原始内容（见 2.1.6、2.2.2）。

批处理优化：所有计算均采用 batch_size 驱动的批处理模式，最大化 CPU 利用率并减少网络 RTT。


## 2.1.6 Storage（索引存储）

TODO：补充 PostgreSQL 与 Milvus 的写入顺序设计——先写哪个、Milvus 写入失败时 PostgreSQL 状态是否回滚、是否支持并行写入。

## 2.2 召回策略
采用双路混合检索策略，并行执行稀疏与稠密两条召回路径：
### 2.2.1 内容查询预处理 (Query Processing)

各步骤按以下顺序执行，并分别产出稀疏/稠密两路的检索输入：

**共享前置预处理（两路均依赖）**
1. 指代消歧 (Disambiguation)：使用 LLM 对原始 Query 进行专业化改写，消除用词不专业、语焉不详及指代不明的问题，产出清洁 Query。

**Sparse Route 专用处理**
2. 关键词识别 (Keyword Extraction)：利用 NLP 工具从清洁 Query 中提取关键实体与动词（去停用词），生成 Token 列表。
3. 术语扩展 (Synonym Expansion)：直接复用节点 0a Stage 2 产出的 Entity Linking 结果——以已规范化术语的 `concept_id` 为主键，从 `terms_collection`（见 1.2.4.6）查出该概念下的全部别名（含口语、缩写、英文），合并为 OR 查询表达式（原始关键词可赋予更高权重以抑制语义漂移）。无需重复调用 LLM，0a 阶段已完成实体归一化。

**Dense Route 专用处理**
4. 上下文补全 (Context Completion)：在多轮对话中，使用 LLM 将历史问题与当前问题合并总结，产出语境完整的语义 Query。
5. 多角度 Query 改写 (MultiQueryRetriever)：使用 LangChain 集成的 MultiQueryRetriever，基于语义 Query 生成多个语义变体（通常 3 个），各自独立生成 Embedding 后检索，路由内部合并后产出 Dense 路的单一 Top-N 候选列表，参与外层 Dense+Sparse RRF 融合（合并设计详见 2.2.2）。

### 2.2.2 召回
注意，召回前可以使用元数据提前过滤，缩小候选集、降低成本。

**并行召回 (Parallel Execution)：**
检索范围：两条路径均在 Milvus **全量记录**上执行，不区分 `vector_type`，`original` / `summary` / `question` 三类向量记录均参与召回。无需任何路由逻辑，按向量相似度返回 Top-N 条记录，命中的记录统一通过 `source_chunk_id` 回溯原始 Chunk 内容，去重后传给 LLM。

1. Sparse Route (BM25)：以 2.2.1 Step 2~3 产出的”关键词 + 同义词/别名 OR 表达式”为输入 -> BM25 检索倒排索引 -> 返回 Top-N 关键词候选。
2. Dense Route (Embedding)：以 2.2.1 Step 4~5 产出的多个语义变体为输入，分别生成 Embedding -> 检索向量库（Cosine Similarity）-> **路由内 RRF 合并** -> 返回 Top-N 语义候选。

   **Dense 路内部合并设计（内层 RRF）：**
   - 每个语义变体独立检索 Milvus，各自返回 Top-N 条记录
   - 对多个变体结果集取并集，按 `id` 去重
   - 对每条记录跨变体汇总排名，应用 RRF：
     ```
     Dense_Internal_Score(d) = Σ  1 / (k + rank_i(d))
                                i ∈ variants
     ```
     其中 `rank_i(d)` 为文档 d 在第 i 个变体结果中的排名（未出现则贡献为 0）
   - 按 `Dense_Internal_Score` 降序取 Top-N，作为 Dense 路输出参与外层融合

   **为何用内层 RRF 而非取最高分：** 各变体的 Cosine Similarity 分数量纲虽相同，但不同变体命中的文档有各自的分数分布，同一分值在不同变体中代表的质量不一致。RRF 只看排名，消除变体间分数分布差异，与外层 Dense+Sparse RRF 的设计逻辑保持一致。

**结果融合 (Fusion)：**
本系统存在两层 RRF，职责不同：

| 层级 | 位置 | 融合对象 | 公式 |
|------|------|---------|------|
| **内层 RRF** | Dense 路由内部 | 多个语义变体的检索结果 | `Dense_Internal_Score(d) = Σ 1/(k + rank_i(d))` |
| **外层 RRF** | 两路融合 | Dense 路 vs Sparse 路 | `Final_Score(d) = 1/(k + Rank_Dense) + 1/(k + Rank_Sparse)` |

外层 RRF 中的 `Rank_Dense` 来自内层 RRF 产出的 Dense Top-N 排名，`Rank_Sparse` 来自 BM25 召回排名。两层 RRF 均不依赖分数绝对值，仅基于排名倒数加权，消除跨模态、跨变体的分数量纲差异。

**多向量去重 (Multi-Vector Deduplication)：**
由于 2.1.5 为每个 Chunk 生成了多条向量记录（original / summary / question），同一 Chunk 的不同向量可能同时出现在召回结果中。融合后须按 `source_chunk_id` 去重，保留同一 Chunk 下得分最高的那条记录，最终将 `original_content` 传给 LLM。去重在 RRF 融合之后、Rerank 之前执行。

### 2.2.3 精确过滤与重排

**Metadata Filtering Strategy（元数据过滤策略）**
核心原则：**能前置则前置，无法前置则后置兜底**。

- **解析**：Query Processing 阶段将结构化约束解析为通用 filters（如 collection / doc_type / language / time_range / access_level 等）。
- **Pre-filter（硬约束）**：若底层索引支持，在 Dense/Sparse 检索阶段提前过滤，缩小候选集、降低成本。
- **Post-filter（兜底）**：索引不支持或字段质量不稳的过滤，在 Rerank 前统一执行；字段缺失时默认"宽松包含"（missing → include），避免误杀召回。
- **软偏好（Soft Preference）**：如"更近期更好"，不做硬过滤，作为排序信号在融合/重排阶段加权处理。
---
**Rerank Backend（可插拔精排后端）**

在 Top-M 候选上执行高精度排序；该模块**必须可关闭**，并提供稳定回退策略。

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| **None（关闭）** | 直接返回 RRF 融合后的 Top-K | 低延迟/资源受限 |
| **Cross-Encoder** | 输入 [Query, Chunk] 对，输出相关性分数排序 | 稳定、结构化输出；CPU 环境建议 M = 10~30，提供超时回退 |
| **LLM Rerank** | 由 LLM 对候选排序/选择，输出严格结构化格式（如 JSON ranked ids） | 无本地模型或需更强指令理解；建议 M ≤ 20 以控制成本 |

**默认策略**：优先保证"可用与可控"，Cross-Encoder 与 LLM 均为可选增强。精排不可用、超时或失败时，**必须回退至 RRF Top-K**，确保系统可用性。




#### 为什么需要 Agent 多步检索

医疗诊断实际非常复杂，必须使用 Agent 分多步检索，才能有全面的考量：

- **第一步**：全库检索，`doc_type = 教材大类`，定位科室
- **第二步**：全库检索，加 `department = 心内科` filter，精细召回

#### 分步分科室的意义

> 核心结论：分科室 filter **不是为了速度，而是为了检索质量**。

性能差距几乎可以忽略——向量检索的时间复杂度主要取决于索引算法（HNSW），不是线性扫描全库：

| 场景 | 检索范围 | 典型延迟 |
|------|----------|----------|
| 全库检索（无 filter） | 100 万个 chunk | ~10-50ms |
| 加 metadata filter | 10 万个 chunk | ~8-40ms |

差距很小，不是性能瓶颈，瓶颈在 rerank 和 LLM 生成。

**检索质量的差异才是关键：**

- `"心衰的利尿剂用量"` → 全库检索可能混入肾内科、药学的相关 chunk
- 加了 `department = 心内科` filter 后，候选集更纯净，rerank 精度更高
- 但反过来也有风险：如果 Agent 判断科室错了，filter 会把正确答案直接过滤掉

#### 两步走策略

两步走比固定 filter 更稳——既不损失召回率，又能在需要时提升精度。响应时间多一次检索（约 30-80ms），完全可以接受。

**Step 1**：永远全库检索，观察 top 结果落在哪些科室

**Step 2**：根据第一步结果决定策略：

- **结果集中在某科室** → 加 filter 精细召回
- **结果分散** → 保持全库，可能是跨科室问题（如"系统性红斑狼疮"涉及风湿、肾内、皮肤科）

#### "结果分散"的解读

结果分散只是**信号**，不能直接等于结论。除了跨科室，还可能是：

1. **知识库覆盖不均**：某科室文档少，相关 chunk 本来就稀疏，分数被其他科室稀释
2. **Query 本身模糊**：如"发烧怎么办"，语义太泛，向量距离拉不开
3. **Chunk 切分质量问题**：跨页切断导致单个 chunk 语义不完整，分数普遍偏低且分散

更稳的判断方式是让 Agent 看 top 结果的**相关性分数分布**：

| 分数特征 | 判断 | 后续动作 |
|----------|------|----------|
| 整体**高**但分散 | 真的跨科室 | 保持全库检索 |
| 整体**低**且分散 | Query 有问题或知识库覆盖不足 | 考虑 query 改写后再检索 |


