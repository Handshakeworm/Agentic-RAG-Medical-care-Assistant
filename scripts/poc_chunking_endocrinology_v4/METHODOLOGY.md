# C2 Chunking 方法论(POC 验证)

> **⚠ 适用范围**:本目录所有规则**只在《内分泌代谢病学 第 4 版上册》上实测验证过**。
> 其他 11 本教材必须按 §6.3 "适配新书 6 步法" 重新走一遍 POC,**不要直接复用本书的 anchor pattern**。
> 但**整体策略(三步 pipeline + mineru 仅找节边界)对所有书通用**(见 §6.1)。

> 本目录是 DEV_SPEC §3.1.2 父子分块 的 POC 实现验证。Production code 不在此处,见 §10 后续工作。

---

## 0. 快速开始

### 0.1 输入文件位置(本书)

```
/data/medical-resources/mineru-output/内分泌代谢病学 第4版上册/hybrid_auto/
├── 内分泌代谢病学 第4版上册_content_list_v2.json   ← 唯一消费的输入
├── 内分泌代谢病学 第4版上册_origin.pdf              ← 人审时对照原书用
└── ...
```

两个 POC 脚本里的 `CONTENT_LIST_V2` 常量已硬编码上述路径。新书做 POC 时改这个常量。

### 0.2 跑 POC

```bash
source .venv/bin/activate

# Step 1: 构建目录字典(独立可跑)
python scripts/poc_chunking_endocrinology_v4/poc_build_toc_dict_endocrinology_v4.py
# 输出:159 条 L1-L3 字典 + 408 条 entries 树状显示

# Step 2: 正文节边界匹配(import Step 1 的 build_toc_dict)
python scripts/poc_chunking_endocrinology_v4/poc_match_body_titles_endocrinology_v4.py
# 输出:matched / missing / unmatched 三份清单

# Step 3-5: 切分主流程(import Step 1+2,完整产 parents/children)
python scripts/poc_chunking_endocrinology_v4/poc_chunk_book.py
# 输出:883 父块 + 2329 子块 + 全局统计 + Cushing 详情
```

### 0.3 依赖

仅 Python 3.12 标准库(json/re/pathlib/collections)。production 实现可能引入 `langchain.text_splitter.RecursiveCharacterTextSplitter` 兜底超大子块,POC 当前未用。

### 0.4 人审产物(/tmp 临时文件)

POC 验证过程中曾输出过下列辅助文件供人审,**不入版本控制**,需要时重跑生成:

- `/tmp/poc_toc_final.txt` — Step 1 完整目录树(408 条)
- `/tmp/poc_match_audit.txt` — Step 2 匹配大全(每 dict_title 多次 match 的位置 + REAL_START 判别)
- `/tmp/merge_full.txt` / `/tmp/merge_result.txt` — Step 3-5 切分输出(包括 Cushing 详情、超大父块清单等)

---

## 1. 背景与核心决策

### 1.1 mineru 的两个固有缺陷

1. **`title.level` 字段全部是 1**(整本书 1453 个 title block 无一例外),不能用来恢复多级标题层级
2. **`type=title` 与 `type=paragraph` 识别完全不一致**:同一类格式 mineru 标记结果不可预期

### 1.2 决策:mineru 仅用于"找节边界",节内切分自己来

由于 1.1,**节内的子块切分不能依赖 mineru 的 type=title**。最终决策:

| 任务 | 是否用 mineru |
|---|---|
| 目录页提取(找节标题清单) | ✅ 用(`type=title` + `paragraph` + `list` 全扫) |
| 正文节边界匹配(找节起始位置) | ✅ 用(扫 `type=title` + 章合并 + 篇前缀重建 + mini-TOC paragraph) |
| **节内子块切分** | ❌ **不用 mineru title 边界,自己写正则** |

### 1.3 已知 mineru bug 清单(本 POC 全程踩到的坑)

按"通用 vs 本书 specific"分类:

| # | bug | 范围 | 应对 |
|---|---|---|---|
| 1 | `title.level` 全是 1 | **通用** | 不用,改"目录权威清单" |
| 2 | 章标题 "第 N 章" 与"章名"被拆成相邻两个 type=title | **通用**(本书 18/18 一致) | A1 章合并预处理 |
| 3 | 篇标题丢失"第 N 篇"前缀,只输出主标题 | **通用** | A2 篇前缀重建(从字典反查) |
| 4 | 目录页跨条目粘连:"第 2 节...56第 3 节..." 焊一行 | **通用** | SPLIT_ANCHOR lookahead 拆分 |
| 5 | 同一 anchor pattern 识别极不一致(如(一)是 title,(二)~(八)是 paragraph) | **通用** | 节内不依赖 mineru 切,自己写正则 |
| 6 | 中文↔ASCII 之间空格风格在目录 vs 正文不一致("1 型" vs "1型") | **通用** | strict_key(去全部空白) |
| 7 | PDF 换行处插 `\n`(跟语义空格区分) | **通用** | normalize 删 \n,保留 ` ` |
| 8 | 节号空格"第 5 节" 风格不一致 | **通用** | normalize 节号合并 |
| 9 | 目录页 mineru 标记 `paragraph` 形式的 mini-TOC(扩展资源 1, 2, 8~25) | **通用** | A3 严格双条件采纳 |
| 10 | "上册"/"下册"/"全书概览" 被标 type=title | **本书 specific** | 黑名单剔除 |
| 11 | 表/图标题被识别为 type=title | **通用** | 节内切子块时排除 `^表/图\s*[\d-]+` |
| 12 | 单字残片(如"经过少",原书是"月经过少") | **通用**罕见 | 节内切子块时长度 < 4 字符的 title 跳过 |
| 13 | 参考文献内 `1. Charrow A...` 等条目被识别为子标题(切出假"子节") | **通用** | 检测 `参考文献` 标题位置,后续整段不再识别子标题(单子块) |
| 14 | 书末"中文名词索引/英文缩略语索引/彩色插图"被吃进最后一节(本书污染最后一节 22469 字) | **通用** | 扫到 BODY_END_MARKERS 标题即截断 flat 序列,后续 block 全部丢弃 |

### 1.4 反思:不要再做"猜层级"的事

POC 中走过弯路:**试图根据正文 title 文本格式(【】vs (一) vs 1.) 反推真实层级**(我们一度叫 L4 / L5 / L6),
基于这个层级再设计切分边界。

**这个方向是错的**,理由:
- mineru 的 type=title 只 50% 是【】这种,剩下用 (一) / 1. / 一、的子标题大量出现
- 同一书内同一类格式都不一致(bug 5),跨书更是混乱
- 即使本书 80% 命中率,后续做 production code 也维护不起

**正确做法**:**所有 type=title 的"格式区别"都不当层级看**,统一作为"节内的某个内容边界"。
- 在节边界(REAL_START)做 hard 切分(父块边界)— 由字典命中决定,可信
- 节内子块切分**自己用正则**找子标题(不区分层级),pattern 不命中就靠字符长度兜底

---

## 2. 整体 Pipeline

```
mineru content_list_v2.json
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ Step 1: 目录字典构建                                      │
│   poc_build_toc_dict_endocrinology_v4.py                │
│   Output:  159 个 L1-L3 标题字典 (lookup)                 │
└────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ Step 2: 正文节边界匹配 (REAL_START 选取)                   │
│   poc_match_body_titles_endocrinology_v4.py             │
│   Output:  159 个节起点                                   │
└────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 3-5 主流程 poc_chunk_book.py                              │
│                                                                │
│  【全书层面】                                                   │
│   ① 书末截断: 扫到"中文名词索引/英文缩略语索引/彩色插图"标题截断   │
│                                                                │
│  【每个节内】                                                    │
│   ② 参考文献丢弃: 扫到"参考文献"标题就截断该节,其后全丢            │
│      (含 ref 条目 + 扩展资源占位列表,RAG 召回无价值)             │
│                                                                │
│  【父块构建】                                                    │
│   ③ 节本身就是父块                                              │
│   ④ 节 > 4000 字 → 三遍切:                                     │
│      Pass 1: 段 > 4000 字 → 加【】边界(level 1)                │
│      Pass 2: 段 > 4000 字 → 加 (一)(二) 边界(level 2)           │
│      Pass 3: 段 > 4999 字 → 加 1./2. 边界(level 3)             │
│   ⑤ 小父块按层级关系合并(< 500 字):                            │
│      Forward: cur_level ≤ next_level(吸收方 ≤ 被吸收方)         │
│      Backward: prev_level ≤ cur_level                           │
│      允许:同级兄弟(BRACE/PAREN/NUM)、上级吸子主题、节首段        │
│      禁止:1.→(二)、(一)→【】、1.→【】 等下级跨上级               │
│                                                                │
│  【每个父块内,子块构建】                                          │
│   ⑥ 父块 ≤ 1200 字 → 不切,1 child = parent 整段                │
│   ⑦ 父块 > 1200 字 → 按 mineru block 累积:每加一个 block 看      │
│      "加 vs 不加"哪个更接近 600 字目标,选更近的;< 200 字时       │
│      force-add 防止孤儿;末段 < 300 字 backward 并入上一 child    │
│                                                                │
│   Output(本书):1204 父块 (median 1346) + 3012 子块 (median 616) │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
chunks 表 (PG)
   父块: parent_chunk_id=NULL, embedding_status='skip'
   子块: parent_chunk_id=父块id, embedding_status='pending'
```

---

## 3. Step 1:目录字典构建

实现:[poc_build_toc_dict_endocrinology_v4.py](poc_build_toc_dict_endocrinology_v4.py)

### 3.1 流程

1. **定位目录页**:扫 `page_header.content` 含"目录"的 page(本书是 page_idx 19~23)
2. **抽行**:对目录页所有 `paragraph` / `title` / `list` block 抽文本(三种 type 都要扫,bug 9)
3. **跨条目粘连拆分**:bug 4 应对,用 SPLIT_ANCHOR lookahead 切多条目焊一行的情况
4. **5 类 anchor pattern 分类**(本书规则):
   | Level | Pattern | 例 |
   |---|---|---|
   | L1 | `^第\s*\S{1,4}\s*篇` | 第 1 篇 内分泌代谢病学技术 |
   | L2 | `^第\s*\S{1,4}\s*章` | 第 1 章 遗传变异 |
   | L3 | `^第\s*\S{1,4}\s*节` | 第 1 节 遗传规律 |
   | L3 | `^扩展资源\s*\d+` | 扩展资源 1 基因与内分泌代谢病 |
   | L4 | `^\d+\.\d+` | 1.1 微嵌合细胞与内分泌代谢病 |

5. **黑名单剔除**:`{上册, 下册, 全书概览, 目录}`(bug 10 应对)
6. **normalize**:删 `\n` (PDF 换行残留无语义) → 折叠空白 → "第 5 节" 节号合并 → 剥页码尾 → 剥裸省略号
7. **strict_key**:在 normalize 基础上**再去掉所有空白**作为 lookup key(bug 6 应对)

### 3.2 字典剔除规则

| 类型 | 是否进 lookup | 原因 |
|---|---|---|
| L1 (篇) | ✅ | 父块边界 |
| L2 (章) | ✅ | 父块边界 |
| L3 (节) | ✅ | 父块边界 |
| **L3 扩展资源 N** | ❌ | 内容是外部二维码,书里只有占位列表(用户决策,§9.1) |
| L4 (N.N) | ❌ | 是扩展资源的子标题,正文不展开 |

### 3.3 数据快照(本书)

- 字典 lookup: **159 条** (L1=3 + L2=18 + L3=138)
- entries (含 L4): 408 条 (供人审用)
- key 冲突: 0(strict_key 后所有标题唯一)

---

## 4. Step 2:正文节边界匹配 (REAL_START 选取)

实现:[poc_match_body_titles_endocrinology_v4.py](poc_match_body_titles_endocrinology_v4.py)

### 4.1 候选收集 (3 类预处理)

正文 page_idx > max(toc_pages) 的所有 block,按以下规则收集候选:

**A1 章合并**(bug 2):`第 N 章` 与 `章名` 被 mineru 拆成相邻两个 `type=title`,合并

**A2 篇前缀重建**(bug 3):篇标题 mineru 只输出"内分泌代谢病学技术",丢失"第 1 篇"前缀。从字典 L1 反查 alias dict 补回

**A3 mini-TOC paragraph**(bug 9):扩展资源 1, 2, 8~25 等被 mineru 标成 `paragraph`。**严格双条件**采纳:
- a) 末尾匹配 `TAIL_PAGE_RE`(像目录条目带页码)
- b) `strict_key` 命中 lookup

### 4.2 REAL_START 选取规则

每个 dict title 在正文里可能出现多次(章/篇页 mini-TOC + 真章节起始)。每个 title **必须挑唯一 1 个 REAL_START**:

1. **优先级 1**:按文档顺序最后一个满足"强信号"的 match
   - action ∈ {PART_REBUILT, CHAP_MERGED} **或**
   - action == AS_IS **且** gap_chars ≥ 50(中间有正文,真章节起始)
2. **优先级 2**:都没强信号 → 取该 title 最后一次出现的位置(适用于扩展资源等 gap=0 case)

`gap_chars` 定义:**当前 match 与上一个 match 在 mineru block 序列中的距离里所有 block 字符长度之和(不含起始 match 自身的字符)**。
即 `prefix_chars[当前 pos] - prefix_chars[上一个 pos + 1]`。
直觉:`gap_chars >= 50` 意味着两个 match 之间有"真正的正文",所以当前是"真章节起始";
`gap_chars == 0` 通常是 mini-TOC 集群(连续 paragraph 形式的目录链接,中间无内容)。

### 4.3 数据快照(本书)

- 正文 type=title block: 1418 个
- 经 A1+A2+A3 收集候选: ~1577 个
- match 字典命中: 311 个(同 title 多次重复)
- **REAL_START** 去重后: **159 个** (与字典一一对应)
- mini-TOC 跳过: ~150 个

---

## 5. Step 3-5:切分主流程

实现:[poc_chunk_book.py](poc_chunk_book.py)

### 5.1 设计原则

- **不依赖 mineru 的 type=title 边界**(理由见 §1.2)
- 父块由"书的标题结构"切(节 → 【】 →(一)→ 1.),子块由"父块大小"切(不再用标题)
- **父子结构只对真正大的父块有意义**:小父块直接当 child(避免 degenerate "父=子")
- 合并策略**严格按级别**:不允许跨节、不允许跨【】(两条特例除外,见 §5.5)

### 5.2 全书层面:书末截断(bug 14 应对)

扫 flat 序列,第一个 `type=title` 文本命中 `BODY_END_MARKERS = ('中文名词索引', '英文缩略语索引', '彩色插图')` 即截断,后续所有 block 丢弃(本书丢 1676 blocks / 20721 字符)。

不这么做的话,这些索引/插图区会被吃进最后一节("第6节 肿瘤相关性神经性伴癌综合征"),产生 22469 字的污染父块。

### 5.3 父块边界识别 pattern

在每节内扫所有 paragraph / title block 的 **行首 strip 后**:

| Level | Pattern | 用途 | 例 |
|---|---|---|---|
| 1 | `^【[^】]+】` | 主题级子节边界 | 【临床表现】、【鉴别诊断】 |
| 2 | `^[（(][一二三四五六七八九十百]+[)）]` | 同主题下分点 | (一)、(二) |
| 3 | `^\d+\s*[.、]\s` | 同分点下子项 | 1.、2. |

**排除**(不当切分):
- `type=list` block 整体作为语义单元(列表整体不切)
- `^表\s*[\d-]+` / `^图\s*[\d-]+` 表/图标题(bug 11)
- 长度 < 4 字符的残片(bug 12)
- 节内"参考文献"标题之后的所有 block(bug 13,见 §5.6)

### 5.4 父块"三遍切"(逐级细化)

`_split_big_parent` 实现。节本身是默认父块。如果节字符 > `PARENT_SPLIT_THRESHOLD = 4000`,按 size 触发逐级切:

| Pass | 触发条件 | 加边界 | 边界 level |
|---|---|---|---|
| 1 | 段 > 4000 字 | 【】 | 1 (BRACE) |
| 2 | Pass 1 后段仍 > 4000 字 | (一)(二) | 2 (PAREN) |
| 3 | Pass 2 后段仍 > 4999 字 | 1./2. | 3 (NUM) |

**节首位置**(段从 0 开始)的 boundary level 标 0(SECTION),用于后续合并的特例判断。

每 pass 只对**仍超阈值**的段细化,小段不动。Pass 3 阈值高一档(4999 vs 4000)避免把刚好 4000-4999 的医学小节切碎成更细。

### 5.5 小父块合并(严格层级关系)

`_merge_tiny_parents` 实现。阈值 `PARENT_MERGE_TINY_THRESHOLD = 500` 字。

**核心原则**(用户拍板 2026-05-03):合并相当于"吸收方"扩展吃掉"被吸收方"。**吸收方的级别必须 ≤ 被吸收方**,否则就是下级跨上级边界(违反主题层级)。

**Forward**(cur 吸收 next,删 next 起始边界):
- 允许:`cur_level ≤ next_level`
- 含义:同级兄弟(BRACE-BRACE / PAREN-PAREN / NUM-NUM)、上级吸子主题(BRACE→PAREN→NUM)、节首段(SECTION=0 自动满足)
- 禁止:`cur_level > next_level`,如 1.→(二) 跨(一)、(一)→【B】 跨【】、1.→【B】

**Backward**(prev 吸收 cur,删 cur 起始边界):
- 允许:`prev_level ≤ cur_level`
- 同级兄弟、上级吸子主题
- 禁止:深级 prev 吸收浅级 cur(等价于 prev 跨上级)

forward 优先(小段并入下一段时 head 保留 cur 标题信息),否则 backward。while 循环直到稳态(支持 cascade)。

**关键说明**:节边界永远不会被这个函数接触 — section_blocks 在外部已被节边界硬切,函数只在节内运行,所以"不跨节"是 architectural guarantee 不是这里的规则。

**为什么允许 BRACE-BRACE 兄弟合并**:用户原话"虽然【】内容不同,但至少都是同一节下的"。同级 peer 在同一上级父级(节)下,合并是把两个 sibling sub-topic 拼起来,语义上比 cross-hierarchy 干净得多。

### 5.6 参考文献丢弃(用户拍板 2026-05-03)

节内扫到 `^参考文献\s*$` 标题后,**该位置及之后的所有 block 全部丢弃**(不参与父块/子块切分)。

丢弃范围包括:
- 参考文献条目本身(英文学术 reference,如 `1. Charrow A...`)
- 紧随其后的"扩展资源 N + N.N"占位列表(外部二维码占位,书内无实际内容)

理由:这两类内容对中文医学 RAG 召回无贡献(用户中文查询不会匹配英文学术 ref title 或扩展资源占位),嵌入和存储是浪费。

本书 4 个有"参考文献"标记的节触发,丢弃 607 blocks / 16257 字符。

**直接收益**:消除 IgG4 节 (十) 父块异常(原 12692 字 → ~130 字真实医学内容)+ 4 个超大子块(2000-3142 字)全部消失。

之前 bug 13 的"参考文献保护"方案(整段成 [REF] 子块)已被本方案取代。

### 5.7 子块构建(size 驱动,不再用标题)

**核心原则**(用户拍板 2026-05-03):**子块应该完全取决于父块大小,不依赖标题结构**。

| 父块大小 | 子块策略 |
|---|---|
| **≤ `CHILD_SPLIT_THRESHOLD = 1200` 字** | **不切**,1 child = parent 整段 |
| **> 1200 字** | 按 mineru block 累积切多个 child,目标 `CHILD_TARGET_SIZE = 600` 字 |

切分算法(`_split_parent_to_children_by_size`):
- 按 mineru block 顺序累积
- 每加一个 block 前,看"加 vs 不加"哪个 acc_len 更接近 600 字目标,选更近的
  - 加更近 → 加进当前 child
  - 不加更近 → 关闭当前 child,新 block 开新 child
- **强制最小约束**(`CHILD_MIN_SIZE = 200`):当前累积 < 200 字时无视"离 600 多近"
  的判断,**必须 force-add 下个 block**。修复"小标题段紧邻大 block 时算法选择
  '留小段独立'产生 43 字孤儿子块"的边界效应。代价:某些子块会到 1100-1500 字,
  超过 target,但避免极小子块(用户拍板 2026-05-03)
- 末段 < 300 字(target/2)时 backward 并入上一 child
- 单个 block 即使 > target 也独立成 child(block 是不可分的最小语义单元)

为什么这么设计:小父块 64% 是 ≤ 1200 字,父=子直接索引最干净;大父块切完子块平均落在 400-800 字,Qwen3-Embedding 的舒适区。

### 5.8 父子覆盖完整性(强不变量)

**total parent_len = total child_len = 1948718 字符**(本书),`mismatch = 0`。

任何切分逻辑改动后必须验证此不变量。如果父块的子块加起来 ≠ 父块大小,说明合并/切分有 bug 导致内容丢失或重复。

### 5.9 父子块对应关系(spec §3.1.2 + §3.1.4)

```
父块 (本书 1207 个,median 1342 字)
  chunk_id = SHA256(source_id + heading_path_id + "parent")
  parent_chunk_id = NULL
  embedding_status = 'skip'
  text = 节级 / 【】级 / (一)级 / 1.级 父块全文 (median 1342 字,max 12692)
  ↑
  └─ 子块 (本书 3031 个,median 614 字)
      chunk_id = SHA256(source_id + heading_path_id + relative_chunk_index)
      parent_chunk_id = 上面的 父块 id
      embedding_status = 'pending'
      text = 子块文本 (median 614, p95=994, max 3142)
      heading_path = 节级 (L1篇/L2章/L3节)
```

### 5.10 关键阈值速查

| 常量 | 值 | 单位 | 用途 |
|---|---|---|---|
| `PARENT_SPLIT_THRESHOLD` | 4000 | 字 | 父块切【】+(一)阈值(Pass 1+2) |
| `PARENT_PASS3_THRESHOLD` | 5000 | 字 | 父块切 1. 阈值(Pass 3,稍宽) |
| `PARENT_MERGE_TINY_THRESHOLD` | 500 | 字 | 小父块合并阈值 |
| `CHILD_SPLIT_THRESHOLD` | 1200 | 字 | 父块 ≤ 此值不切子块 |
| `CHILD_TARGET_SIZE` | 600 | 字 | 大父块切子块的目标 size |
| `CHILD_MIN_SIZE` | 200 | 字 | 子块强制最小,< 200 字 force-add 下个 block |

实测 Qwen tokenizer 1 token ≈ 1.39 字符,所以:
- 父块阈值 4000 字 ≈ 2877 token(LLM 上下文舒适)
- 子块目标 600 字 ≈ 432 token(embedding 舒适区)

### 5.11 关键测试 case 锚点(用于回归检验)

1. **Cushing 综合征(pg=626~667, 71549 字)**:整节大量切分,(二)大剂量 DXM、(三)其他动态试验等关键医学子节都成为独立父块或子块。
2. **第 1 篇 内分泌代谢病学技术(pg=34)**:A2 重建后命中字典 L1,作为单一小父块保留(篇页内容 185 字)。
3. **扩展资源 4-7(pg=64)**:gap_chars=0 case,REAL_START 选最后出现(优先级 2)。
4. **IgG4 相关疾病(pg=1107)**:参考文献内 `1. Charrow A...` 没被切成假子节,整段成 12566 字 [REF] 子块。
5. **书末截断**:第6节 肿瘤相关性神经性伴癌综合征 不再被中文名词索引/插图污染。
6. **第1节 甲状旁腺疾病常用药物**:【降钙素】+ 紧邻 (一)药理作用 不再产生 5 字裸标题父块(级别约束 + 节首特例修复)。

---

## 6. 通用 vs 单本规则

### 6.1 通用策略(适用所有书)

- mineru 只用于"找节边界",不用其 title 切子块
- 三步 pipeline:目录字典 → 正文匹配 → 节内自切
- normalize + strict_key 处理 mineru 空格风格不一致
- REAL_START 选取算法(优先级 1 强信号 + 优先级 2 fallback)
- 上述 §1.3 bug 1~9, 11~12 都是通用现象,通用策略已应对

### 6.2 单本规则(本书 specific,需重新适配)

| 项 | 本书规则 | 其他书可能差异 |
|---|---|---|
| 目录 anchor pattern | 篇/章/节/扩展资源/N.N | 章节命名约定不同 |
| 节号格式 | "第N节"(中文+阿拉伯) | "第N节"/"§N"/"N." 等 |
| 子标题 pattern | 【】/(一)/1./一、 | 同上,各书排版习惯不同 |
| L4 入字典策略 | 扩展资源 + N.N 都剔除 | 看本书 L4 在正文是否展开 |
| 黑名单 | 上册/下册/全书概览/目录 | 看本书有无分册结构 |

### 6.3 适配新书 6 步法

1. 打开 mineru 目录页(查 page_header 含"目录"的页)看真实结构
2. 按目录条目格式制定 anchor pattern(改 `poc_build_toc_dict_endocrinology_v4.py` 里 PATTERNS)
3. 跑 Step 1 看字典覆盖率(对照纸面目录人审)
4. 跑 Step 2 看 REAL_START 是否完整覆盖字典(missing 应为 0)
5. 跑 `poc_chunk_book.py` 看父子块大小分布是否合理(参考本书数据快照 §7)
6. 抽样关键 case 人审(比照 §5.11)
7. 校验父子覆盖完整性 mismatch=0(§5.8)

---

## 7. 已知数据快照(本书)

| 指标 | 值 |
|---|---|
| mineru 总页数 | 1263 |
| 目录页 page_idx | 19, 20, 21, 22, 23 |
| 正文页范围 | page_idx 24 ~ 1262 |
| 字典 L1-L3 | 159 条 |
| 字典 L1-L4 (含 L4) | 408 条 |
| 节数(节级原父块) | 159 |
| **父块数(三遍切+合并后)** | **1204** |
| 父块 size median | 1346 字(~969 token)|
| 父块 size p75 | 2138 字 |
| 父块 size p95 | 3557 字 |
| 父块 size p99 | 4378 字 |
| 父块 size max | **5218 字**(纯医学内容,第12节 原醛 >> 【分类与临床表现】)|
| 父块 < 500 字残留 | 11 个(10 个篇/章导航页 + 1 个【手术治疗】)|
| **子块数** | **3012** |
| 子块 size min | 168 字 |
| 子块 size median | 616 字(~443 token)|
| 子块 size p75 | 733 字 |
| 子块 size p95 | 992 字 |
| 子块 size max | **1528 字**(神经性厌食 (二)肥胖恐惧, 单 mineru list block)|
| 子块 < 200 字占比 | **0.07%**(仅 2 个,都是导航页父块自身)|
| 父块切分模式 — 单 child(≤1200 字直接当 child)| 约 44% |
| 父块切分模式 — 多 child(>1200 字按 size 切)| 约 56% |
| 书末截断丢弃 | 1676 blocks / 20721 字符 |
| 参考文献丢弃 | 607 blocks / 16257 字符(4 个节末的 ref 条目+扩展资源占位)|
| 父子覆盖完整性 | mismatch=0(total parent_len = total child_len = 1932461)|

---

## 8. 未决议题与已知限制

### 8.1 IgG4 (十) 12692 字异常 — **已解决**(2026-05-03)

原问题:IgG4 节 (十) 父块 12692 字,真实医学内容只 ~130 字,剩下全是参考文献条目 + 扩展资源占位。

**修复**:用户拍板丢弃节内"参考文献"标题之后的所有内容(同 §5.6)。
- 全书 4 个有"参考文献"的节统一处理
- 丢弃 607 blocks / 16257 字符(本书 0.8%)
- IgG4 (十) 父块 12692 → 130 字
- 4 个超大子块(2000-3142 字)全部消失

剩余 max:父块 5218(纯医学),子块 1528(纯医学)。

### 8.2 篇/章导航页 < 500 字父块(10 个,无法处理)

L1 篇 / L2 章 自身被识别为"节"时,内容就是篇/章标题 + mini-TOC + 短引言,本身只有 100-500 字。
它们是独立的节,**无法跨节合并**(节边界绝对不可破)。

例:
- 第2章 器官内分泌疾病(168 字)
- 第1篇 内分泌代谢病学技术(185 字)
- 第3篇 非内分泌腺内分泌疾病(236 字)

接受作为 architectural 残留(占父块总数 0.8%)。

### 8.3 mineru 漏识别子标题的兜底缺失

bug 5 导致节内"应该是子块边界"的位置 mineru 没标 type=title。当前父块切用正则弥补,但单个 mineru block 如果太大(> CHILD_TARGET_SIZE 600 字),子块也会跟着大(因为 block 不可分)。

本书子块 max=1528 字(神经性厌食 (二)肥胖恐惧 单 list block,不可分)。

**缓解**:可加 RecursiveCharacterTextSplitter 兜底切超大 block。POC 当前未启用,接受现状(子块 > 1500 字仅 1 个,占 0.03%)。

### 8.4 spec §3.2.3 父块扩展策略需重新设计

原方案:retrieval 召回子块后,展开整节给 LLM。
现在:节本身被切多个父块,且父块大小已经控制在 ~1000 字 median(~720 token),不再需要展开。

retrieval 召回时直接用父块文本即可(median ~700 token,p95 ~2500 token),适合 LLM 上下文。**spec §3.2.3 待重写**。

### 8.5 12 本教材 anchor pattern 的可复用性未知

§6.2 列了 5 项可能差异。当前只验证了 1 本(《内分泌代谢病学第4版上册》)。
12 本里可能存在的格式(待逐本审):
- 药典/字典型(《临床用药指南》已确认绕开 RAG 主流程,project memory 已记)
- 多分册书的"上册/下册"边界
- 现代英文术语为主的书(更多 ASCII↔中文 空格 case)

---

## 9. 关键决策来源

记录下"为什么这么做",便于后续 review 时知道哪些可以改、哪些是用户钦定。

| 决策 | 何时拍板 | 是否可改 |
|---|---|---|
| 弃用"基于 mineru title.level 重建" 废案,改"目录权威清单" | 用户拍板,POC 早期 | 不可改(已删旧代码) |
| L4 (N.N) 不入字典 | 用户拍板,Step 1 设计 | 看其他书 L4 是否在正文展开 |
| L3 扩展资源 N 不入字典 | 用户拍板,看截图后(扩展资源是外部二维码) | 可改(若本书规则 + 其他书规则不一致) |
| 父块 = 节(整节全文) | 用户拍板,反对"按字符切多父块" | **已松动 2026-05-03**:节级父块 > 4000 字时按【】+(一)+1. 三遍切 |
| 节内子块切分**不**依赖 mineru type=title | 用户拍板,看 Cushing case 后 | 不可改(mineru 质量不可信) |
| L4 / L5 / L6 子标题层级 **不真实存在**,统一作为"节内边界"处理 | 用户反思纠正 | 不可改(防止重新走弯路) |
| **父块阈值 4000 字 / 父块 1./2. Pass 3 阈值 4999 字** | 用户拍板 2026-05-03,基于 Qwen tokenizer 实测 1 字符 ≈ 0.72 token,目标父块 ~3000 token | 可调 |
| **父块切子块阈值 1200 字,目标子块 600 字** | 用户拍板 2026-05-03:小父块直接当 child(避免 degenerate "父=子"),大父块按 size 切目标 600 字 | 可调 |
| **子块切分按 size 累积,不用标题 pattern** | 用户拍板 2026-05-03:"子块应该完全取决于父块,不和标题有关" | 不可改(避免 degenerate) |
| **size 累积选"加 vs 不加"哪个更接近 600** | Claude 提议 2026-05-03(方案 c):比"超就纳入"和"超就另起"都更贴近目标 | 可改 |
| **子块强制最小 200 字** | 用户拍板 2026-05-03:实在太小不如合并,即使代价是某些子块超过 target 也接受 | 可调 |
| **小父块合并 = 严格层级**:吸收方 level ≤ 被吸收方 | 用户拍板 2026-05-03:不允许下级跨上级边界(如 1./2. 不能跨 (一);(一) 不能跨【】) | 不可改(原则性) |
| **同级兄弟合并任意级别都允许**(BRACE/PAREN/NUM) | 用户拍板 2026-05-03:"【A】→【B】 同节下当然可以合并",节是天然的硬上级 | 不可改 |
| **节首引言可吸收任何下级**(SECTION=0 自动满足规则) | 由统一规则覆盖 | 不可改 |
| 书末截断 marker 用 `中文名词索引/英文缩略语索引/彩色插图` | Claude default(2026-05-03) | 可改(其他书可能有不同 marker) |
| 参考文献丢弃 marker 用 `参考文献`(整段截断) | 用户拍板 2026-05-03:"反正不是正文诊断也用不到,直接丢弃" | 不可改(普适) |
| `type=list` block 不识别为切边界 | Claude default(2026-05-03):列表整体作语义单元,首项 1./2. 不当切点 | 不可改 |

---

## 10. 后续工作

- [x] 实现切分主流程 `poc_chunk_book.py`(2026-05-03)
- [x] 父块三遍切 + 严格层级合并 + size 驱动子块切(2026-05-03)
- [x] 参考文献丢弃方案(议题 §8.1 已解决,2026-05-03)
- [x] 同步 `DEV_SPEC.md §3.1.1 限制 2` / §3.1.2 / §3.2.3 / §8.4 C2(2026-05-03)
- [x] 同步 `src/rag/ingestion/chunking.py` 与 `mineru_loader.py` 与 `idempotency.py` docstring(2026-05-03)
- [ ] 跑 `python .claude/skills/auto-coder/scripts/sync_spec.py` 重新同步 skill references
- [ ] 把 POC 切分主流程 port 到 `src/rag/ingestion/chunking.py` production
- [ ] 加 RecursiveCharacterTextSplitter 兜底超大单 block 子块(议题 §8.3,可选)
- [ ] 12 本教材逐本验证(每本按 §6.3 重写 anchor pattern)

---

## 文件清单

```
poc_chunking_endocrinology_v4/
├── METHODOLOGY.md                              # 本文档
├── poc_build_toc_dict_endocrinology_v4.py      # Step 1: 目录字典构建
├── poc_match_body_titles_endocrinology_v4.py   # Step 2: 正文匹配 + REAL_START 选取
└── poc_chunk_book.py                           # Step 3+: 父块构建(三遍切+严格层级合并) + 子块构建(size 驱动) + 书末截断 + 参考文献丢弃
```
