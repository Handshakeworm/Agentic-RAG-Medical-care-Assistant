# C2 Chunking 方法论(POC 验证 + 适配新书 SOP)

> **适用范围**:本目录方法论已在 **4 本书完整 POC 验证** —
> 《内分泌代谢病学 第4版上册》(2026-05-03)、《诊断学 第10版》(2026-05-05)、
> 《内科学 第9版》(2026-05-05)、《神经外科学》(2026-05-05)。
> 通用策略适用所有书,但每本书的 anchor pattern / OCR 错字 / 子标题层级**必须单独适配**。
>
> 四本书结果汇总(分布对比)见 [`已做好.md`](已做好.md);本文档侧重方法论。
> 已验证书目对比详见 §8;适配新书的具体 checklist 详见 §7。

> 本目录是 DEV_SPEC §3.1.2 父子分块 的 POC 实现验证。Production code 不在此处,见 §11 后续工作。

---

## 0. 快速开始

### 0.1 文件结构

```
scripts/
├── METHODOLOGY.md                              ← 本文档(共用方法论)
├── poc_chunking_<book_name>/                   ← 每本书一个文件夹
│   ├── BOOK_NOTES.md                           ← 本书特定笔记(anchor / 边界 / OCR错字 案例)
│   ├── poc_build_toc_dict.py                   ← Step 1
│   ├── poc_match_body_titles.py                ← Step 2
│   └── poc_chunk_book.py                       ← Step 3-5
└── ...
```

每本书的 mineru 输入路径在 `poc_build_toc_dict.py` 的 `CONTENT_LIST_V2` 常量里。

### 0.2 跑 POC(任何一本书共通)

```bash
source .venv/bin/activate
cd scripts/poc_chunking_<book_name>

# Step 1: 目录字典(独立可跑)
python poc_build_toc_dict.py > /tmp/poc_toc.txt
# 输出:L1-L3 字典 + 树状显示 + unmatched 行清单(供人审)

# Step 2: 正文匹配(import Step 1)
python poc_match_body_titles.py > /tmp/poc_match.txt
# 输出:matched/missing/unmatched 三份清单 + 各 action 分布

# Step 3-5: 切分主流程(import Step 1+2)
python poc_chunk_book.py > /tmp/poc_chunk.txt
# 输出:统计 + 父块/子块 size 分布 + 极端 case + sample 节详情
```

### 0.3 依赖

仅 Python 3.12 标准库(json/re/pathlib/collections/difflib)。无外部依赖。

---

## 1. 背景与核心决策

### 1.1 mineru 的固有缺陷

1. **`title.level` 字段全部是 1**(全书 title block 无一例外),不能用来恢复多级标题层级
2. **`type=title` 与 `type=paragraph` 识别完全不一致**:同一类格式 mineru 标记结果不可预期
3. **OCR 错字常见**:章节名漏字(如"第二章 般检查",漏"一")、特殊字符(`|`/字间空格)

### 1.2 决策:mineru 仅用于"找节边界",节内切分自己来

由于 1.1,**节内的子块切分不能依赖 mineru 的 type=title**。最终决策:

| 任务 | 是否用 mineru |
|---|---|
| 目录页提取(找节标题清单) | ✅ 用(`type=title` + `paragraph` + `list` 全扫) |
| 正文节边界匹配(找节起始位置) | ✅ 用(`type=title` + 5 类 fallback 预处理) |
| **节内子块切分** | ❌ **不用 mineru title 边界,自己写正则** |

### 1.3 已知 mineru bug 清单

按"通用 vs 单本"分类。每发现一个新书的 bug 就在此追加。

| # | bug | 范围 | 应对 |
|---|---|---|---|
| 1 | `title.level` 全是 1 | 通用 | 不用,改"目录权威清单" |
| 2 | 章/篇标题 "第 N 章" 与"章名"被拆成相邻两个 type=title | 通用 | A1 章/篇合并预处理 |
| 3 | 篇标题丢失"第 N 篇"前缀,只输出主标题 | 通用 | A2 篇前缀重建(从字典反查) |
| 4 | 目录页跨条目粘连:"第 2 节...56第 3 节..." 焊一行 | 通用 | SPLIT_ANCHOR lookahead 拆分 |
| 5 | 同一 anchor pattern 识别极不一致 | 通用 | 节内不依赖 mineru 切,自己写正则 |
| 6 | 中文↔ASCII 之间空格风格在目录 vs 正文不一致 | 通用 | strict_key(去全部空白) |
| 7 | PDF 换行处插 `\n`(跟语义空格区分) | 通用 | normalize 删 \n |
| 8 | 节号空格"第 5 节" 风格不一致 | 通用 | normalize 节号合并 |
| 9 | 目录页 mineru 标记 `paragraph` 形式的 mini-TOC | 通用 | A3 严格双条件采纳 |
| 10 | 分册标识"上册"/"下册"被识别为 type=title | 内分泌 specific | 黑名单剔除 |
| 11 | 表/图标题被识别为 type=title | 通用 | 节内切子块时排除 `^表/图\s*[\d-]+` |
| 12 | 单字残片(如"经过少",原书"月经过少") | 通用罕见 | 节内切子块时长度 < 4 字符的 title 跳过 |
| 13 | 参考文献内 `1. Charrow A...` 等条目被识别为子标题 | 通用(本书有) | 检测"参考文献"标题位置,后续整段不再识别子标题 |
| 14 | 书末"中文名词索引/英文缩略语索引/彩色插图"被吃进最后一节 | 通用 | 扫到 BODY_END_MARKERS 标题即截断 flat 序列 |
| 15 | **目录跨多页但 mineru 只在第一页加 page_header"目录"**(诊断学:目录在 pg 15-21,只 pg 15 有 marker) | 诊断学 specific(可能其他书也有) | `_detect_toc_pages` 启发式延伸:首页后,后续 page anchor 命中 ≥ 2 算延续,直到不命中为止 |
| 16 | **章标题正文里完全没 type=title block**(只在 page_header 重复出现) | 诊断学 specific | A4 PAGE_HEADER_FB:首次 page_header 命中字典即作为章边界,**位置取该 page 的 (pg, 0)**(因为 page_header 通常排在 page 末尾) |
| 17 | **章标题 OCR 漏字**(诊断学:"第二章 般检查"漏"一") | 通用(罕见) | A5 FUZZY_TITLE:用 SequenceMatcher 对所有 `^第N章` 的 title block 跟字典模糊匹配,ratio ≥ 0.85 命中 |
| 18 | **正文 anchor 字符:`第N节 \| X X` 用 `\|` 分隔 + 字间空格 `发 热`** | 诊断学 specific | normalize 加 `PIPE_SEP_RE` 去 `\|`;末尾"标题."单句点也要剥(`TAIL_DOT_RE`) |
| 19 | **`第N篇` 被 OCR 成 `(N) X`(失"第N篇"前缀)** | 内科学(本书 1 处:`(4) 消化系统疾病`)| `_classify` 入口先跑 `PIAN_PAREN_RE = ^[\(（]\s*([1-9])\s*[\)）]\s+(\S{2,})`,命中后改成 `第{中文数字}篇 X` 再走 PATTERNS。后接内容 ≥ 2 字符避免误吃 `(4)` 子项编号 |
| 20 | **同篇内章号重复(OCR 二/三视觉混淆)** | 内科学(`第二十二章 糖尿病` OCR 成 `第二十三章 糖尿病`,跟"第二十三章 低血糖症"撞号)| 算法救不了(单字 fuzzy 易误判)→ 走硬编码 PATCH(见 §3.4) |
| 21 | **mineru 把 3 行 TOC 黏成 1 paragraph** | 内科学(`果\n一、珠蛋白...555\n二、异常...556`,SPLIT_ANCHOR 不识 一、)| 算法救成本高(SPLIT_ANCHOR 加 一、 lookahead 会引入歧义)→ 走硬编码 PATCH |
| 22 | **mineru 完全漏识整行(L3 节 / L4 一、)** | 内科学(本书 4 处:第二章中毒第一节 / 第二十六章第一节 / 三、混合性肾小管 / 三、缺血性心肌病)| 算法救不了 → 走硬编码 PATCH |
| 23 | **TOC 多页但 mineru 在末页才标 page_header="目录"**(神经外科学:目录 pg 5-7,只 pg 7 page_header="目录",pg 5 是 type=title="目录") | 神经外科学(可能其他小教材也有)| `_detect_toc_pages` 升级为 3 步:**双 seed**(page_header `目录` ∪ type=title `目录`)+ **填充 seeds 之间空隙** + **双向延伸**(往前往后)。延伸阈值从 2 提到 **5**(目录页通常每页 10+ anchor,正文页只有零星) |
| 24 | **TOC 章名跨多 block 拆碎**(章名被拆 2-3 个连续 title/paragraph block,正文同章是完整单 title) | 神经外科学(本书 2 处:第二十四章拆 3 段、第三十章拆 2 段)| 算法启发性强难写通用 → 走硬编码 `PATCH_REPLACE_TITLE` 把字典残缺章名补全(正文 AS_IS 自然命中完整 title) |
| 25 | **TOC 节条目用页码黏一段 paragraph**(`第三节 X 219第四节 Y 221`,中间页码"219"后无空格直接接下条) | 神经外科学(本书 1 处)| `SPLIT_ANCHOR` 加 lookbehind `(?<=\d)(?=第\s*\S{1,4}\s*[篇章节])`:允许"页码末尾紧跟新条目"作为切点 |
| 26 | **TOC 冗余残片**(原节名被截断的残段如 `出、脑膜膨出 279`,真实节名已在下一 block 完整 `第一节 脑膨出、脑膜膨出 …… 279`)| 神经外科学(本书 3 处)| 接受为 unmatched(无影响,真实条目已收)|

### 1.4 反思:不要做"猜层级"

POC 早期走过弯路:**试图根据正文 title 文本格式(【】vs (一) vs 1.) 反推真实层级**。

这个方向是错的 — mineru type=title 标记完全不可信,猜层级越猜越错。**正确做法**:用目录字典作为唯一权威层级真值,正文匹配时用 strict_key + 多种 fallback 预处理。

---

## 2. 整体 Pipeline

```
mineru content_list_v2.json
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ Step 1: 目录字典构建    poc_build_toc_dict.py           │
│   多页目录自动延伸 + 5 类 anchor + 黑名单 + normalize     │
│   Output:  L1-L3 字典 (lookup, key=strict_key)          │
└────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ Step 2: 正文节边界匹配  poc_match_body_titles.py         │
│   候选 5 种 action:                                       │
│     AS_IS / CHAP_MERGED / PART_REBUILT                  │
│     + MINI_TOC_PARA + PAGE_HEADER_FB + FUZZY_TITLE      │
│   REAL_START 选取:strong 信号优先                        │
│   Output:  每节 1 个 (pg, blk) 起点                      │
└────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 3-5 切分主流程  poc_chunk_book.py                        │
│                                                                │
│  【全书层面】                                                   │
│   ① 书末截断(BODY_END_MARKERS,本书定制)                      │
│                                                                │
│  【每个节内】                                                    │
│   ② 节内"参考文献"丢弃(若有)                                   │
│   ③ 跨 section 吸收:L1 篇 / L2 章 < 500 字 → 并入下一 section │
│      (篇/章占位符或短引言,信息保留在 heading_path)             │
│                                                                │
│  【父块构建,渐进 3 pass】                                        │
│   ④ Pass 1: 节 > 4000 字 → 用主题级 anchor 切                 │
│   ⑤ Pass 2: 父块 > 6000 字 → 用次级 anchor 救                 │
│   ⑥ Pass 3: 父块 > 6000 字 → 用最细 anchor 救                 │
│   ⑦ 小父块(< 500 字)按层级关系合并(节内,严格规则)            │
│                                                                │
│  【每个父块内,子块构建】                                          │
│   ⑧ 父块 ≤ 1200 字 → 不切,1 child = parent 整段                │
│   ⑨ 父块 > 1200 字 → 按 mineru block 累积,目标 ~600 字/child   │
│                                                                │
│   Output: parents + children + stats(含 mismatch=0 守恒检查)  │
└──────────────────────────────────────────────────────────────┘
        │
        ▼
chunks 表 (PG)
   父块: parent_chunk_id=NULL, embedding_status='skip'
   子块: parent_chunk_id=父块id, embedding_status='pending'
```

---

## 3. Step 1:目录字典构建

实现:`poc_build_toc_dict.py`

### 3.1 流程

1. **定位目录页**(`_detect_toc_pages`)— 已迭代 3 个版本应对不同 mineru 标记习惯:
   - **多种 seed**(神经外科学新加):`page_header.content` 含"目录" **OR** `type=title` 文本是"目录"
   - **填充 seeds 之间空隙**(神经外科学新加):pg 5 + pg 7 是 seed 但 pg 6 不是,需补
   - **双向延伸**(诊断学/神经外科学):从已收范围的最大值往后、最小值往前,anchor ≥ **5** 即并入(阈值 5 而非 2 — 防止误吃正文起点页面)
   - 三种 mineru 标记位置都覆盖:全标(内分泌/内科学)/ 首页标(诊断学)/ 末页+中间标(神经外科学)
   - 对应 bug 15 + bug 23
2. **抽行**:对目录页所有 `paragraph` / `title` / `list` block 抽文本(三种 type 都要扫,bug 9)
3. **跨条目粘连拆分**:bug 4 应对,用 SPLIT_ANCHOR lookahead 切多条目焊一行
4. **N 类 anchor pattern 分类**(每本书具体 pattern 不同,见 §8 已验证书目登记)
5. **黑名单剔除**(每本书具体黑名单不同,如内分泌:`{上册,下册,全书概览,目录}`)
6. **normalize**(下面 §3.2 详述)
7. **strict_key**:在 normalize 基础上**再去掉所有空白**作为 lookup key(bug 6 应对)

### 3.2 normalize 函数(关键规则集合)

每本书都需要 normalize,但具体规则可能因书有差异。当前累积规则:

```python
def _normalize(s: str) -> str:
    s = s.replace("\n", "")              # 1. 删 PDF 换行残留(通用,bug 7)
    s = PIPE_SEP_RE.sub(" ", s)          # 2. `|` 分隔符 → 空格(诊断学 bug 18)
    s = re.sub(r"\s+", " ", s).strip()   # 3. 折叠空白(通用)
    s = SECTION_NUM_RE.sub(r"第\1\2", s) # 4. "第 5 节" → "第5节"(通用,bug 8)
    while True:                          # 5. 反复剥页码尾(通用)
        new = TAIL_PAGE_RE.sub("", s).strip()
        if new == s: break
        s = new
    s = TAIL_ELLIPSIS_RE.sub("", s).strip()  # 6. 剥裸省略号(通用)
    while True:                          # 7. 反复剥单句点尾(诊断学 bug 18)
        new = TAIL_DOT_RE.sub("", s).strip()
        if new == s: break
        s = new
    return s
```

**新书必查的事**:
- 正文 anchor 是否带特殊字符(`|`/`*`/`>` 等)?需要加对应 sub 规则
- 目录条目末尾是否有"标题."单句点尾?需要 TAIL_DOT_RE
- 字间是否有空格(如"发 热")?strict_key 全去空白即可,无需特殊处理

**TAIL_PAGE_RE 加强**(协和呼吸 2026-05-05):

```python
TAIL_PAGE_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*[（(]?\s*\d+\s*[）)]?\s*$")
```

3 处兼容:
- `[（(]?` / `[）)]?` 兼容**全角括号**(协和呼吸 `…… （2576）`)
- `\s*\d+\s*` 兼容**括号内带空格**(协和呼吸 `…… ( 3 )`)
- 配合 `_PUNCT_NORMALIZE` 在 strict_key 阶段统一标点

新书必跑校验 §6.1.4(集合差集),会暴露这类 normalize 漏配。

### 3.2.1 SPLIT_ANCHOR(跨条目粘连拆分,通用增强)

```python
SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*[篇章节]\s)"            # 后跟空白(常见情况)
    r"|(?<=\d)(?=第\s*\S{1,4}\s*[篇章节])"      # 前面是数字(页码黏连,神经外科学发现)
)
```

**两条 lookahead**:
- 第 1 条 `(?=...\s)` 应对正常分隔(空白)
- 第 2 条 `(?<=\d)(?=...)` 应对页码末尾紧跟新条目(本节末"……226" + 下条"第N节" 中间无空白)

应对 bug 4(粘连)+ bug 25(页码黏连)。每本书可能踩到不同情形,但加上述两条已能覆盖。

### 3.3 字典剔除规则(每本书可能不同)

通用原则:
- L1-L3 进 lookup(协和呼吸只 L1-L2,字典最浅)
- 子结构(L4+/N.N/扩展资源)**默认不进** lookup
- **例外**:若 TOC 把"一、X" 或 "[附] X" 作为**真实独立条目**列出(不只是节内子标题),则字典必须扩到 L4(内科学是首例)。判断方法:Step 0 探查时看 TOC 行类型分布,若 一、/[附] 大量出现且配独立页码(如 `一、肺炎链球菌肺炎 45`),即真实条目

**BLACKLIST 检查需归一化后**(协和呼吸 2026-05-05):

```python
norm_s = _normalize(s)
if s in BLACKLIST or norm_s in BLACKLIST:
    skipped_blacklist.append(s)
    continue
```

非内容行(目录里的 `索引 (2634)` / `彩图插页 (2655)`)带尾页码,原文不命中 BLACKLIST 但归一化后命中。否则会跑去 unmatched 当噪音。

字典层级扩到 L4 时的连锁影响:
- `_update_stack` 槽位 4 → 5,`lookup` 收 level ≤ 4
- Pass 1 切分 anchor 要降级(原 一、 已成 section 边界,Pass 1 改用更细的 【】 或 (一))
- `CHAPTER_ABSORB` 范围扩到 L3(L3 节内容被 L4 切走后常只剩短引言)

每本书具体差异详见 §8。

### 3.4 硬编码补丁系统(算法救不了的 OCR 错)

**触发场景**:mineru 在 TOC 出过的硬错(整行漏识 / 章号 OCR 错混淆其他章号 / 跨行黏连无法 lookahead 拆 / 末尾残留特殊符号),算法层面无法可靠救。

**解决**:`_apply_patches()` 函数,3 类操作:

```python
PATCH_REPLACE_TITLE: dict[str, str] = {
    "第二十三章糖尿病": "第二十二章 糖尿病",   # OCR 二/三错
    "第十章肺血栓栓寒症": "第十章 肺血栓栓塞症",   # OCR 寒/塞错
    "三、XX男性综合征•": "三、XX 男性综合征",     # 末尾 OCR 残留
}
PATCH_INSERT_AFTER: list[tuple[int, str, str]] = [
    # (level, title_to_insert, anchor_strict_key)
    (4, "一、珠蛋白生成障碍性贫血", "第四节血红蛋白病"),  # mineru 黏行漏识
    (3, "第一节 概述", "第二章中毒"),                    # mineru 漏识整行
    ...
]

def _apply_patches(entries):
    # Step A: replace title (key 用旧 strict_key 查)
    # Step B: 章号断序救 — 若 PATCH 改了章号,path 里的旧章号同步替换
    # Step C: insert_after — 找 anchor 在其后插占位 entry(path 留空)
    # Step D: _rebuild_paths — stack-walk 重算所有 path(关键!)
```

**`_rebuild_paths` 必做**:INSERT_AFTER 后若新插的是 L3,下游 L4 的 path 还挂在 L2 章下(原来没 L3),必须走一遍 stack-walk 重算 — 把 L4 自动重新挂到新插的 L3 下。

**PDF 截图人审工作流**:
1. Step 1 跑出完整 tree(`grep -E "^  L[1-4] "` 输出 419 行 plain text)
2. 用户给 PDF TOC 截图(全部目录页,不要漏)
3. 我对照 PDF vs 字典逐篇 spot-check,标 ⚠ 真错 / ✓ 接受(原书印刷如此)
4. 真错部分写 `PATCH_REPLACE_TITLE` / `PATCH_INSERT_AFTER`
5. 重跑 Step 1 → 章号唯一性检测(同篇内重复 = 还有 OCR 错)+ 关键 path spot-check
6. Step 2 重新 audit 验证新补 entries 都有正文位置命中

**适用门槛**:数量少(< 20 处)且语义明确才走硬编码。如果某本书 OCR 错很多(数十处+),考虑换 mineru 配置或换 OCR 引擎。

---

## 4. Step 2:正文节边界匹配

实现:`poc_match_body_titles.py`

### 4.1 候选收集(5 种预处理 action)

每个候选记录 `(pg_idx, blk_idx, raw, lookup_key, action)`:

| Action | 触发条件 | 例 | 适用书 |
|---|---|---|---|
| **AS_IS** | type=title 直接命中字典 | `第一节 视诊` | 通用,主力 |
| **CHAP_MERGED** | type=title 是纯"第N章"或"第N篇",合并下一 title 作章/篇名 | `第二篇` + `问诊` 合并 | 通用 |
| **PART_REBUILT** | type=title 只剩主标题(丢了"第N篇"前缀),反查字典 alias 补全 | `常见症状` → `第一篇 常见症状` | 通用 |
| **MINI_TOC_PARA** | type=paragraph 形式的目录引用,严格双重条件(末尾带页码 + strict_key 命中字典) | `第一节 视诊 70` | 通用,罕见 |
| **PAGE_HEADER_FB**(诊断学新加) | type=page_header 命中字典(章/篇没有 title block) | `第二章 一般检查` | 诊断学 specific(其他书可能也有) |
| **FUZZY_TITLE**(诊断学新加) | type=title 是"第N章"格式但 strict_key 不命中,用 SequenceMatcher 模糊匹配 ratio ≥ 0.85 | `第二章 般检查` (OCR 漏一) → `第二章 一般检查` | 通用,救 OCR 错字 |

**PATCH_BODY_RAW_FIX 机制**(协和呼吸 2026-05-05 新加):

短标题(≤ 6 字)的 1 字 OCR 错(`第二章 鼾症` → `第二章 肝症`)FUZZY ratio ≈ 0.8 < 0.85,降阈值会引入跨章误配。改为在 `_collect_candidates` 内对 raw text 应用替换 list,**在 strict_key 之前**生效:

```python
PATCH_BODY_RAW_FIX: list[tuple[str, str]] = [
    ("第二章 肝症", "第二章 鼾症"),
]

def _apply_body_raw_fix(raw: str) -> str:
    for old, new in PATCH_BODY_RAW_FIX:
        if old in raw:
            return raw.replace(old, new)
    return raw
```

适用场景:Step 1 §6.1.4 集合差集 报告 1-2 字差正文 OCR 错字,且 FUZZY ratio < 阈值。比降 FUZZY 阈值精准、不引入跨章误配。

### 4.2 PAGE_HEADER_FB 的位置约定

⚠ **关键陷阱**:page_header block 在 mineru 排序里通常在 page 末尾(blk 11+),但表示"本页属于此章"。

如果直接用 page_header block 自身位置作为章边界,会把该页前面的所有 paragraph(实际是该章内容)误归到上一章。

**正确做法**:用 `(pg_idx, 0)` — 该 page 的开头作为章起点。该页所有内容都归本章。

### 4.3 FUZZY_TITLE 的实现

```python
from difflib import SequenceMatcher
FUZZY_RATIO_THRESHOLD = 0.85
CHAP_PATTERN = re.compile(r"^第\s*\S{1,4}\s*章")

# 对所有"第N章 ..." 形态但 strict_key 不命中字典的 title 做 fuzzy 救
for body_block in body_titles:
    if not CHAP_PATTERN.match(body_block.text): continue
    if strict_key(body_block.text) in lookup: continue  # 已 AS_IS 命中
    best = max(
        ((SequenceMatcher(None, key, dict_key).ratio(), dict_key)
         for dict_key in chapter_dict_keys),
        default=(0, None)
    )
    if best[0] >= 0.85:
        # 添加为 FUZZY_TITLE 候选
```

### 4.4 REAL_START 选取规则(神经外科学 2026-05-05 修正)

同一字典 key 可能在正文里被匹配多次(如 mini-TOC 引用 + 真章节起始 + page_header 多页重复)。选 REAL_START 两段式:

```python
# 1) 高质量信号优先(strong)
strong = [
    r for r in recs
    if r['action'] in ('PART_REBUILT', 'CHAP_MERGED', 'FUZZY_TITLE')
    or (r['action'] == 'AS_IS' and r['gap'] >= 50)
]
if strong:
    chosen = strong[-1]
else:
    # 2) 退化:AS_IS 永远优先 PAGE_HEADER_FB(神经外科学 2026-05-05 发现 bug 后修)
    #    场景:章 anchor 紧跟篇 anchor(同 page,blk 0+1),AS_IS gap=0 不算 strong,
    #    但绝对正确;不能让 PAGE_HEADER_FB 这种兜底位置抢走最终 chosen
    non_fb = [r for r in recs if r['action'] != 'PAGE_HEADER_FB']
    chosen = non_fb[-1] if non_fb else recs[-1]
```

**修正前 bug**:`chosen = strong[-1] if strong else recs[-1]` — 当 strong 为空时直接选 last,
若 last 是 PAGE_HEADER_FB 而中间有 AS_IS,会错选位置不准的 PAGE_HEADER_FB。

**修正后效果(实测 4 本)**:
- 内分泌 / 内科学 / 神经外科学:0 个最终选 PAGE_HEADER_FB
- 诊断学:2 个最终选 PAGE_HEADER_FB(真兜底,这 2 章 mineru 完全无 type=title)

新书添加新 action 时,要决定是否加入 strong 列表。FUZZY_TITLE 进 strong(因为是真 title block,只是 OCR 错字)。PAGE_HEADER_FB 不进 strong(本身位置可能不精准,只在没 AS_IS 时兜底)。

### 4.5 选择性去重

`PAGE_HEADER_FB` 在每个章页都重复(14+ 次/章)— 必须按 strict_key 去重保留首次。
其他 action **不要去重**,要让 REAL_START 选取规则用 strong 信号决定。

### 4.6 strict_key 冲突消歧(神经内科学 2026-05-05 新发现)

**触发条件**:字典里多个不同 path 的 entry 共享同一 strict_key,典型如同一书内多个章下都有"第一节 概述"。
神经内科学:1 个 strict_key `第一节概述` 命中 17 个 entry(38 章里 17 个用了通用节名)。

**原 bug**:`_real_start_positions` 直接 `level, parent_path, dict_title = cands[0]` 拿第一个,然后
`groups[(m["level"], m["title"])].append(m)` 按 (level, title) 分组 → 17 处不同章的"第一节 概述"被合并成
同一个 group → strong selection 选 1 个,**16 处真实位置丢失**(内容吸收到上一节,父块严重错位)。

**fix(神经内科学)**:
1. 用当前 stack 消歧 cands(类似 match_body_titles main 函数的 DISAMBIG):
   ```python
   if len(cands) > 1:
       for cand_level, cand_parent, cand_title in cands:
           expected_parent = " / ".join(x for x in stack[: cand_level - 1] if x)
           if expected_parent == cand_parent:
               best = (cand_level, cand_parent, cand_title)
               break
   ```
2. groups 改按 `(level, full_path)` 分组,不同章的"第一节 概述" 各自成 group
3. 返回值 `dict[pos] = (level, title, full_path)` 加 path,下游 unpack 改 3 元

**前 4 本不需要 backport** — 用户已校验位置正确(章数少 + 节名相对具体,未触发冲突)。但
建议下一本(尤其 L1-L4 字典 + 通用节名)默认启用此消歧 — 这是真正的 SOP-level fix。

---

## 5. Step 3-5:切分主流程

实现:`poc_chunk_book.py`

### 5.1 设计原则

- **mineru block 是最小不可分单元**(整 block 入或出,不切碎)
- **节是最小独立语义单元**(L3 节永远独立成 chunk,即使内容少)
- **字符总和守恒**:父块字符总和 == 子块字符总和(`mismatch=0`,强不变量)
- **粗粒度优先**:父块尽可能大(完整主题),只有超大段才二次细化

### 5.2 全书层面:书末截断

扫 flat block 序列,第一个命中 `BODY_END_MARKERS` 的 type=title 即截断,后续全丢。

每本书的 marker 不同,见 §8。

### 5.3 节内:参考文献 / 推荐阅读丢弃

节内扫 marker 标题,该位置及之后整段丢弃(reference list / 扩展资源占位)。**通用 marker 包括两种**:

```python
RE_REF_MARKER = re.compile(r"^(?:参考文献|推荐阅读)\s*$")
```

- `参考文献`:内分泌大量、诊断学 0 处、内科学 0 处
- `推荐阅读`:内科学 8 处篇末英文/中文 reference list(如 `1. Goldman L, Schafer AI...`)

新书可能有其他变体(`推荐文献` / `延伸阅读` / `参考资料` 等),Step 0 探查时全书 grep `type=title` 末尾 30 页确认。

理由:英文学术 ref 与中文医学查询语义不匹配,扩展资源是外部链接占位,均无 RAG 召回价值。算法对"无 marker"也安全(不触发)。

### 5.4 跨 section 吸收(2026-05-05 新加)

**问题**:L2 章 / L1 篇 sometimes 只有标题 block(无内容)或仅有简短引言(< 500 字),独立成 chunk 没有医学价值,只占索引位置。

**解决**:section 总长 < `CHAPTER_ABSORB_THRESHOLD = 500` 字 → blocks 累积到 pending,并入下一 section 的首父块。

```python
ABSORB_LEVELS = (1, 2)            # 内分泌/诊断学:L3 节永远保留为最小单元
# ABSORB_LEVELS = (1, 2, 3)       # 内科学:字典扩到 L4 后,L3 节也参与吸收

pending_blocks = []
for i, a in enumerate(section_splits):
    sec_blocks = flat[a:next_a]
    sec_len = sum(b['len'] for b in sec_blocks)
    level, title = real_start_pos[a]

    # 短 section → 累积到 pending,跳过本轮
    if level in ABSORB_LEVELS and sec_len < CHAPTER_ABSORB_THRESHOLD and i+1 < len(section_splits):
        pending_blocks.extend(sec_blocks)
        continue

    if pending_blocks:
        sec_blocks = pending_blocks + sec_blocks
        pending_blocks = []
    # ... 正常处理
```

**ABSORB_LEVELS 选择规则**:
- 字典最深层级(永远是最小语义单元)**不吸收**
- 内分泌/诊断学:字典 L1-L3,所以 L3 不吸收 → ABSORB={1,2}
- 内科学/神经内科学:字典 L1-L4,所以 L4 不吸收 → ABSORB={1,2,3}

效果:
- 短 section 标题 block 进入下一 section 首父块(标题信息也在 heading_path,双重保险)
- 最深层级即使内容只有 28 字 stub("简称'代酸',见第五篇第十章")也独立成块 — heading_path 精确比"凑够 500 字"更重要,stub 在召回时是"指路标"价值。user 拍板 2026-05-05

**注**:内分泌/内科学 chunk 脚本最初写死 `(1, 2)` 没让 L3 参与,因为它们 mineru 在 L3 标题后还有
paragraph 内容,L3 没空壳所以未暴露。神经内科学(L4=487 + L3 标题后立即跟 L4 title)实测必须
`(1, 2, 3)`,否则空壳 L3 父块 134 个(`size=6` 只剩 title)— 2026-05-05 神经内科学发现并修正。

### 5.4.1 前言/序章丢弃(神经内科学 2026-05-05 新加)

**问题**:`flat[0:section_splits[0]]` 即 first section 起点之前的 block 没人收。
神经内科学 pg 21-22 是编者前言("刘鸣 谢鹏",2892 字 / 18 blocks),mismatch=-2892。

**fix**:加 `preface_dropped` stats 字段,跟 `ref_dropped` 同类处理。expected 字符 = body - preface - ref。

```python
preface_blocks = flat[: section_splits[0]] if section_splits else []
preface_dropped_chars = sum(b["len"] for b in preface_blocks)
expected = flat_kept_chars - ref_dropped_chars - preface_dropped_chars
```

前 4 本 mismatch=0 是因为 first section 起点紧贴 body_start,没有"前言"间隙。研究生教材
(神经内科学是首例)有编者前言,首次暴露这个缺口。判定标准:**和医学知识无关的(序章/前言/版权页)
直接丢弃**(user 拍板 2026-05-05)。

### 5.5 父块边界识别 pattern(渐进多 pass,最多 4 级)

**核心理念**:粗粒度优先,只对超大父块二次/三次/四次细化。

```python
PARENT_SPLIT_THRESHOLD = 5000      # 节 > 此值才走 Pass 1(单本可调 6000)
PARENT_REFINE_THRESHOLD = 6000     # 父块 > 此值才走 Pass 2 救
PARENT_PASS3_THRESHOLD = 6000      # Pass 3
PARENT_PASS4_THRESHOLD = 6000      # Pass 4(协和呼吸 2026-05-05 首次启用)

def _split_big_parent(section_blocks, threshold, ref_idx, ...):
    if section_len <= threshold:
        return [(0, LEVEL_SECTION)]  # 节 ≤ 阈值直接整节作 1 父块

    pass1 = _refine([(0, LEVEL_SECTION)], _is_main_subheading, LEVEL_MAIN, threshold)
    pass2 = _refine(pass1, _is_sub_subheading, LEVEL_SUB, PARENT_REFINE_THRESHOLD)
    pass3 = _refine(pass2, _is_finest_subheading, LEVEL_FINEST, PARENT_PASS3_THRESHOLD)
    # 字典浅 + 大体量书可能需要 Pass 4
    pass4 = _refine(pass3, _is_numdot_subheading, LEVEL_NUMDOT, PARENT_PASS4_THRESHOLD)
    return pass4
```

**每本书的 pattern 顺序不同**(见 §8 注册表):
- 内分泌:Pass 1 = 【】, Pass 2 = (一), Pass 3 = 1./2.
- 诊断学:Pass 1 = 一、, Pass 2 = (一), Pass 3 = 1./2.
- 内科学:Pass 1 = 【】, Pass 2 = (一)(二),Pass 3 不启用
- 神经外科:Pass 1 = 一、,单 pass
- 神经内科:Pass 1 = 一、, Pass 2 = (一)
- **协和呼吸**:Pass 1 = 第N节, Pass 2 = 【】, Pass 3 = (一), **Pass 4 = 1.**(首次启用)

**Pass 1 选择原则**:用本书 type=title 中**占比最高且层级最高**的子标题 anchor。Step 0 全书统计 type=title 形态分布即可判断:
- 内分泌【】占主力 → Pass 1 = 【】
- 诊断学 一、占 12% 但更易感知主题切换 → Pass 1 = 一、
- 内科学【】占 46.8%(主力)→ Pass 1 = 【】
- 协和呼吸 字典浅(L1-L2),节(336)在 type=title 是首选 → Pass 1 = 第N节

**Pass 4 = `1.` 何时开**(协和呼吸 2026-05-05 新发现):
- **字典深度 ≤ L2 + 大体量书**(协和呼吸 字典 179 + 正文 3.18M 字)→ 章/节平均 14000+ 字,Pass 1+2+3 后剩 5%~10% 父块 > 5000
- 残余大父块根因:【】内部纯 `1. 2. 3.` 列表(无 (一)),或 (一) 内部连贯论述无子标题
- 正则:`^\d+\s*[\.、]\s*\S` — 数字 + 点/顿号 + 非空白
- **必须**通过 RE_TABLE_TITLE / RE_FIG_TITLE 排除"表 1-2"/"图 1-3"误命中
- 本书启用后:max 19991 → 9059 / > 5000 占比 6.0% → 4.5%

**Pass 1 anchor 不能跟字典最深 level anchor 重复**(否则 section 内不会再有这种 anchor):
- 内科学字典扩到 L4 用 一、,Pass 1 必须避开 一、 → 用 【】
- 协和呼吸字典只到 L2,所有 anchor 都可用 → Pass 1 选最高级别的 节

### 5.6 小父块合并(节内,严格层级规则)

`_merge_tiny_parents`:对节内 size < `PARENT_MERGE_TINY_THRESHOLD = 500` 字的 boundaries 合并。

**核心原则**:吸收方 level ≤ 被吸收方(禁止下级跨上级):
- Forward(cur 吸收 next):`cur_level ≤ next_level`
- Backward(prev 吸收 cur):`prev_level ≤ cur_level`

允许:同级兄弟、上级吸子主题、节首段。
禁止:`1.` 跨 `(一)` 合并 / `(一)` 跨 `【】` 合并 等。

### 5.7 子块构建(size 驱动)

```python
CHILD_SPLIT_THRESHOLD = 1200  # 父块 ≤ 此值不切,1 child = parent
CHILD_TARGET_SIZE = 600       # 大父块切子块的目标 size
CHILD_MIN_SIZE = 200          # 子块强制最小,< 此值 force-add 防孤儿
```

算法(`_split_parent_to_children_by_size`):
- 每加一个 block 看"加 vs 不加"哪个 acc_len 更接近 600,选更近的
- 当前 < 200 时无视判断,force-add(防孤儿)
- 末段 < 300 时 backward 并入上一 child
- 单 block > 600 也独立成 child(block 是不可分的最小单元)

### 5.8 父子覆盖完整性(强不变量)

```python
assert sum(p['len'] for p in parents) == sum(c['len'] for c in children)
```

任何切分逻辑改动后必须验证 mismatch=0。

---

## 6. 校验步骤(必做!)

每个 step 跑完都要做对应校验,不要直接到下一 step。

### 6.1 Step 1 校验

跑 `python poc_build_toc_dict.py > /tmp/poc_toc.txt`,**4 类硬校验全部要过**(2026-05-05 协和呼吸暴露:只看汇总数会漏 OCR 错字 / 字典 normalize bug / 正文与目录差异)。

#### 6.1.1 基础检查(快扫)

1. **TOC 页定位**:输出"TOC pages identified" 是否合理(如诊断学 [15..21],内分泌 [19..23])
2. **lookup 冲突 = 0**:strict_key 后无重名(若有冲突先看是否真重名 vs normalize 不充分)
3. **unmatched 行清单 ≤ 5**:扫一眼,应都是已知附录入口("推荐阅读"/"中英文名词对照索引"/"索引 (xxx)"/"彩图插页 (xxx)"等),不应有真章节漏出

#### 6.1.2 守恒等式(必跑)

```
TOC pages 原文非空行数 == entries 数 + unmatched 数 + blacklist 命中数
```

不等说明字典构建逻辑漏处理(如同 page 多行合并、SPLIT_ANCHOR 拆粘连等)。协和呼吸:184 = 179 + 0 + 5 ✓。

#### 6.1.3 编号连续性(必跑,不能只看数目对得上)

- 篇号 1..N 连续,无缺号无重号(`第N篇` 解析中文数字)
- **每个篇内**章号 1..M 连续,无缺号无重号
- 同理节号(若字典含 L3)

只看 "L1=16 / L2=163" 这种汇总数会**假阳性**:章号在某篇内重复 / 跨篇错位也能凑出同样总数。协和呼吸:16 篇都 1..N 连续(8/4/18/12/24/8/5/5/20/7/3/8/9/12/6/14)。

#### 6.1.4 字典 vs 正文 strict_key 集合差集(**最关键**,必跑)

```python
dict_keys_lN = {strict_key(t) for lvl,t,_,_ in entries if lvl == N}
body_keys_lN = {strict_key(raw) for raw in 正文 type=title 块 + CHAP_MERGED 合并形 if 命中 LN 锚}
miss_in_body = dict_keys - body_keys     # 字典有 / 正文无
extra_in_body = body_keys - dict_keys    # 正文有 / 字典无
```

逐 level 跑(L1 篇 / L2 章 / L3 节)。**协和呼吸暴露的 3 个真实问题全靠这一步发现**:

| 现象 | 根因 | 修法 |
|---|---|---|
| `第一章 ... …… ( 3 )`(尾页码括号内带空格)dict 留尾巴 | TAIL_PAGE_RE 在 `[（(]?` 与 `\d+` 之间没 `\s*` | 改 dict 正则 |
| `第二章 鼾症`(dict)vs `第二章 肝症`(body pg 1801)| mineru OCR 把 `鼾→肝` 错字 | Step 2 FUZZY_TITLE 救(ratio=0.8 需调阈值)|
| dict 缺 `（PET）` vs body 含 `（PET）` | 目录原文与正文字面不同 | Step 2 FUZZY_TITLE 救(ratio=0.945 自动过 0.85 阈值)|

差集为 0 是**理想**,>0 时必须能给出每条的解释(dict bug / body OCR / 真实差异),并决定哪些 Step 2 救、哪些回头改 dict。**绝不能**因为"汇总数对了"就跳过这步。

### 6.2 Step 2 校验

跑 `python poc_match_body_titles.py > /tmp/poc_match.txt`,先看基础三项:覆盖率 100% / 0 missing / 0 conflicts。然后做**全量自动校验**(优先于人眼抽样;协和呼吸 2026-05-05 验证)。

#### 6.2.1 全量自动 5 项硬校验(`audit_step2.py`,**默认必跑,代替抽样**)

| # | 检查 | 通过条件 |
|---|---|---|
| 1 | 位置唯一性 | 每个 dict_key 恰好 1 个 strong(AS_IS / CHAP_MERGED / FUZZY / PART_REBUILT / HARDCODE)位置;0 真兜底(只有 PAGE_HEADER_FB) |
| 2 | 顺序单调性 | matched 序列按 dict 顺序枚举,(pg, blk) 严格递增 |
| 3 | 嵌套正确性 | 每个 章 的 strong 匹配处,当前 stack 顶 篇 = 字典里它的父 篇 |
| 4 | **印刷-mineru offset 一致性** | TOC 末尾 `( 数字 )` 抽印刷页号 → 计算 offset = mineru_pg − printed_page → 全部 entries offset 收敛到同一值,0 离群(>5 页)|
| 5 | 真兜底列表 | 把校验 1 中 0 strong 的 entries 详列(若有,这才是 PDF 抽样要看的少数几个)|

**校验 4 是最强证据**:offset 全等是数学约束,任何 1 页以上的错配立即暴露。协和呼吸 179/179 全 offset=17(印刷 pg 1 = mineru pg 18,前置 18 页前言/目录 → 17 偏移完全合理)。

**多卷书注意**:校验 4 当前用全局 mode。若书的印刷页号在上下册各自从 1 开始(非协和呼吸这种连续编号),需要按卷分组各算 offset,扩展逻辑见后续遇到再加。

#### 6.2.2 人眼 PDF 抽样(降级为可选,自动校验通不过时才做)

只在以下情况需要人眼对 PDF:
- 校验 1 报真兜底 entries(才是潜在错位)
- 校验 4 报 offset 离群 entries
- 出现新的 action 类型(如 L4 一、/【附】首次启用)

抽样脚本 `audit_step2.py`(老版,逐 action 抽 5 个 + 前后 3 block 上下文)只用于这些异常处的深查。

#### 6.2.3 _real_start_positions 后位置(`audit_final_positions.py`,可选)

candidates 阶段 PAGE_HEADER_FB 数多 ≠ 实际有问题。`_real_start_positions` 的 strong-AS_IS 优选会覆盖 PAGE_HEADER_FB。本脚本展示 Step 3 实际用的 (pg, blk):

```python
real_start = _real_start_positions(flat, result)
# 反查每个 chosen pos 的 action
```

输出:按 chosen_action 分布(本应 AS_IS 主导,PAGE_HEADER_FB 应 = 0 或极少),"真兜底:最终位置只有 PAGE_HEADER_FB 命中的章" 列表。

**经验**:内科学阶段 1 看到 131 个 PAGE_HEADER_FB candidates,阶段 2 显示 0 个最终选中(全被 AS_IS 覆盖)。协和呼吸 174 个 PAGE_HEADER_FB 也是同样情况。

#### 6.2.4 这套自动校验的覆盖范围与盲区(诚实评估)

**能抓**:重复匹配 / 真兜底 / 倒序 / 错配父篇 / 错配同名页头 / CHAP_MERGED 拼接错 / 字典-正文文字差(Step 2 missing=0 已挡)。

**抓不到**(剩余风险概率极低):
- TOC 印刷页号本身错印 1-2 页(在 ±5 页容忍区内;但 1-2 页偏差对 chunking 几乎无影响)
- 错拼章名恰巧 strict_key 等于另一 entry(理论可能,实际未遇)

→ 综合起来,**5 项全过即可放心进 Step 3**,不需要再人眼抽 PDF。

### 6.3 Step 3 校验

跑 `python poc_chunk_book.py > /tmp/poc_chunk.txt`,检查:

1. **mismatch = 0**:`assert sum(parent_len) == sum(child_len)`
2. **节数 = TOC L1+L2+L3 数**(章/篇 < 500 被吸收的不计)
3. **父块 size 分布**:
   - median 期望 1000-2000 字(粗粒度)
   - max 期望 < 6000 字(超大已三遍切到极限)
   - min 期望 ≥ 100 字(小于此值检查是否还有占位符没吸收)
4. **子块 size 分布**:
   - median 期望接近 CHILD_TARGET_SIZE = 600 字
   - max 期望 < 1500 字(超过看是否单 block 异常)
   - < 200 字孤儿子块 < 5 个(更多说明 force-add 没生效)
5. **超大父块清单**:看 head 是否在已知"无更细子结构"medical 段落,是 → 接受;否 → 加 Pass 救
6. **占位符父块**:扫"最小 5 父块",应都是 L3 真节(L1/L2 应已被吸收)

### 6.4 节内细节抽检(可选,但有用)

挑 3-5 个 sample 节,显示其完整父块 → 子块 tree:

```python
target = '第一节 发热'
parents = [p for p in res['parents'] if p['section_title'] == target]
for p in parents:
    kids = [c for c in res['children'] if c['parent_idx'] == p['parent_idx']]
    print(f'父块[{p["parent_idx"]}] size={p["len"]} {p["head"]}')
    for c in kids:
        print(f'  └ 子块 size={c["len"]} {c["head"][:50]}')
```

人审"节切的合理吗?子块大小均匀吗?"

---

## 7. 适配新书 SOP(分步 checklist)

新书做 POC 的标准流程,~2-4 小时一本书。

### 7.1 准备工作(5 分钟)

```bash
mkdir -p scripts/poc_chunking_<新书中文名>
cd scripts/poc_chunking_<新书中文名>
# 创建 BOOK_NOTES.md 占位(下面填)
touch BOOK_NOTES.md
```

### 7.2 Step 0 探索(15-20 分钟,不写代码)

写一个 throwaway python 脚本扫 mineru 输出,回答 6 个问题:

1. **目录页位置**:扫 page_header 含"目录"的 page,看几页
2. **目录条目格式**:抽前 30 条目录,看 anchor 是 `第N篇/章/节` 还是别的
3. **TOC 行类型分布**(关键!决定字典深度):
   - 统计 TOC 范围内 `^[一二...]、` / `^\[附` 行数 + 每条是否带独立页码
   - 若 一、/[附] 大量出现且配独立页码 → 字典必须扩 L4(参考内科学)
   - 否则字典只到 L3
4. **正文 title 格式**:抽前 30 个正文 type=title block,看跟目录条目能否对上(normalize 后)
5. **节内子标题分布**(Pass 1 选择依据):全书 type=title 形态占比统计
   - 【】 / (一)(二) / 一、 / 1. / 第N章节篇 / 其他 — 谁占大头
6. **BODY_END marker + ref marker**:扫文末 30 页 + 全书 grep,找:
   - BODY_END 候选(中英文名词对照索引 / 中文名词索引 / 附录等)
   - ref marker 候选(参考文献 / 推荐阅读 / 延伸阅读 / 参考资料等)

把发现写进 `BOOK_NOTES.md`,标"跟前几本的差异"。

### 7.3 Step 1 实施(30-60 分钟)

复制内分泌或诊断学的 `poc_build_toc_dict.py`(用更接近本书 anchor 风格的那本),改:

1. **CONTENT_LIST_V2 路径**
2. **PATTERNS 列表**:增减 anchor pattern
3. **BLACKLIST**:本书特有的分册标识等
4. **normalize 规则**:加本书特有的 sub 规则(`|` 分隔/字间空格等)
5. **目录页延伸 `_detect_toc_pages`**(诊断学多页目录情况)

跑通后做 §6.1 校验。

### 7.4 Step 2 实施(20-40 分钟)

复制诊断学的 `poc_match_body_titles.py`(它包含全套 5 种 action,内分泌缺 PAGE_HEADER_FB/FUZZY_TITLE)。

直接跑,看输出:
- 覆盖率是否 ≥ 95%
- 是否有 PAGE_HEADER_FB / FUZZY_TITLE 触发(本书是否需要这些 fallback)

如果覆盖率低:
- 看 missing 章/节,在 body 里 grep 找,看 mineru 标的是什么 type
- 决定是否需要新 action(如 mineru 把章标题标成 list block?)

跑通后做 §6.2 校验(**位置抽检很重要**,不要跳)。

### 7.5 Step 3 实施(30-60 分钟,最考验耐心)

复制诊断学的 `poc_chunk_book.py`,改:

1. **BODY_END_MARKERS**(本书定制)
2. **anchor pattern + LEVEL 编号**:重新 sample 节内子标题层级,确定 Pass 1/2/3 用什么 pattern
3. **可能需要的 strong action 列表**(如本书 FUZZY_TITLE 也加)

跑通后做 §6.3 校验。

**调参顺序(强烈推荐,user 拍板 2026-05-05)**:

**先按目录粒度看 baseline,再决定加哪个 Pass**(不要上来就开 Pass 1):

```python
# Step A: 临时禁用所有 Pass,看纯字典粒度切的分布
PARENT_SPLIT_THRESHOLD = 10**9     # 不可触发
PARENT_REFINE_THRESHOLD = 10**9
```

跑一遍,观察:
- 父块 max / p99 / p95 — 多大才需要救?有几个超大?
- 父块 median — 是否过大需要全局切?
- 子块分布 — size-driven 子块切是否已能 cover 大部分情况?

**根据 baseline 决定**:
1. 如果 max < 8000 且 > 5000 的极少(< 1%)→ **接受纯目录粒度,不开 Pass**
2. 如果只是少数 outlier > 8000 → 开 Pass 1 用本书子标题主力 anchor(【】 / 一、 / (一))
3. Pass 1 后还有 > 6000 → 加 Pass 2 用次级 anchor
4. 还有 > 6000 → 再加 Pass 3 最细 anchor

**理由**:目录粒度是"原书设计的语义切分",heading_path 完全跟字典一致最干净。Pass 切分本质是"把过大整段二次拆碎",每多一 Pass 就多一层"head 跟字典不一致"(变成 `第N节 >> 【治疗】` 这种)。能不切就不切。

内科学经验:
- 纯目录粒度 baseline:339 父块 / max=31874(31874 那个 第一节 糖尿病无更细 anchor 可救,但其他大部分 < 5000 接受)
- 加 Pass 1 【】 + Pass 2 (一):664 父块 / max=9371(消掉极端 outlier,代价是子标题级别 head)
- 阈值 4000→5000 后:659 父块,4000-5000 字段保留为整段(原 4000-5000 切碎不合理)

**其他参数**:
5. 看 < 100 字父块多不多 → 可能要调 `CHAPTER_ABSORB_THRESHOLD`
6. 看 子块 max(参考文献列表导致)→ 加 ref marker(参考文献 / 推荐阅读 等)

### 7.6 验收清单

- [ ] mismatch = 0(强不变量)
- [ ] **跑 `audit_final_positions.py` — 全部 entry 落在 type=title block 上**(诊断学/神经外科学允许极少 image 兜底,见 §4.4)
- [ ] 节数 ≈ TOC L1+L2+L3 总数(短 section 吸收的除外)
- [ ] 父块 max 接受标准:**< 10000**(超大已尽力救;否则需 Pass 2/3 救)
- [ ] 子块 median 在 500-700(目标 600)
- [ ] 子块 < 200 字孤儿数 ≤ 5(否则 force-add 没生效)
- [ ] PDF 对照抽检 5-10 个父块:起头自然 + 内容连贯
- [ ] 数据填进 [`已做好.md`](已做好.md)(统一卡片格式)
- [ ] BOOK_NOTES.md 完成(后人能照着复现)

---

## 8. 已验证书目特异性速查

> 数据分布(父块/子块统计)详见 [`已做好.md`](已做好.md);本节只记**写代码时要复用的特异性参数**。

每本书细节见对应 `BOOK_NOTES.md`,下面 4 行总结只列"跟 SOP 默认有差异的部分"。

### 8.1 内分泌代谢病学 第4版上册(2026-05-03)

- **字典深度**:L1-L4(L3=第N节 + 扩展资源N,L4=N.N)
- **Pass anchor**:【】 → (一) → 1. → 严格层级合并
- **BODY_END**:`中文名词索引` / `英文缩略语索引` / `彩色插图`
- **OCR 救**:无;mineru 输出最规范

### 8.2 诊断学 第10版(2026-05-05)

- **字典深度**:L1-L3
- **结构特点**:**mixed depth**(第一篇直接挂节无章;第二~八篇 篇→章→节)
- **节 anchor 字符**:`第N节 \| X X`(`\|` 分隔 + 字间空格)→ normalize 加 `PIPE_SEP_RE`,末尾"."加 `TAIL_DOT_RE`
- **TOC 多页延伸**:首页 pg 15 标,需要往后延 6 页
- **Pass anchor**:一、 (>4000 无条件) → (一) (>6000 救) → 1. (>6000 救)
- **BODY_END**:`中英文名词对照索引`(单一)
- **OCR 救**:1 章 OCR 漏字 → FUZZY_TITLE;2 章无 type=title → PAGE_HEADER_FB(真兜底)

### 8.3 内科学 第9版(2026-05-05)

- **字典深度**:L1-**L4**(L4=一、xxx / [附] xxx,首本扩 L4)
- **结构特点**:mixed depth(第一篇绪论直接挂 L4 无章节;部分章无节直接挂 L4)
- **TOC 多页延伸**:全 16 页都标"目录" — 不需要延伸
- **Pass anchor**:【】(>5000)→ (一)(>6000 救)
- **CHAPTER_ABSORB 范围**:L1/L2/**L3**(因 L4 是最深层,L3 也参与吸收)
- **BODY_END**:`中英文名词对照索引` · **ref marker**:`推荐阅读`(8 处篇末)
- **OCR 救**:`(4) 消化系统疾病` OCR 错(应是"第四篇")/ 章号二/三混淆撞号 / 4 处整行漏识 → 走硬编码 PATCH(3 REPLACE + 6 INSERT,见 §3.4)

### 8.4 神经外科学(2026-05-05)

- **字典深度**:L1-L3
- **TOC 多页延伸**:**末页 pg 7 标 + 中间 pg 5 是 title="目录"** → 双 seed + 双向延伸 + 中间填充(§3.1 升级)
- **Pass anchor**:一、 (>**6000** 救;阈值偏离 SOP 5000 — 小书目录粒度已合理)
- **节内子标题**:(一) 主力 50.9% / 一、 35.5% / **完全无【】**(首本)
- **BODY_END**:**无**(末页直接正文) · **ref marker**:无
- **OCR 救**:TOC 章名跨多 block(2 处)→ 硬编码 PATCH_REPLACE_TITLE 补全;TOC 节条目用页码黏行(1 处)→ SPLIT_ANCHOR 加 lookbehind(下沉 SOP §3.2.1)

### 8.5 神经内科学 第2版(2026-05-05)

- **字典深度**:L1-L4(700 entries:10/38/165/487 — 字典最大的一本)
- **TOC 多页延伸**:**11 页**(首本超 10 页),双向延伸 + 间隙填充复用神经外科学方案
- **Pass anchor**:一、 + (一) (PARENT_SPLIT/REFINE 都 6000;Pass 2 启用,救 14838 → 5994)
- **节内子标题**:**4 种共存**(一、732 + (一)463 + (1)340 + 1.765;**无【】**)
- **BODY_END**:无 · **ref marker**:`参考文献`(549 blocks 丢弃)
- **OCR 救**:章号漏"第"字(1 处) + 跨 page L3 切碎(1 处)→ PATCH_LINE_PREPROCESS;
  全/半角标点不一致(`？/?`、`，/,`)→ strict_key 标点归一化;
  整页 TABLE 附录无 type=title(1 处)→ PATCH_INJECT_CANDIDATES 硬编码注入坐标
- **本书首暴露 SOP-level 修正**(详见 §4.6 + §5.4 + §5.4.1):
  - `_real_start_positions` strict_key 冲突消歧(stack 消歧 + 按 path 分组)
  - `absorb_levels = (1, 2, 3)` 让 L3 也参与跨 section 吸收
  - `preface_dropped` 字段(序章/编者前言丢弃)

### 8.6 协和呼吸病学 第二版(2026-05-05)

- **字典深度**:**L1-L2**(179 entries:16 篇 + 163 章 — **6 本最浅**,无节无 L4)
- **正文体量**:**3,178K 字**(6 本最大,2686 页),章平均 14000+ 字
- **TOC 多页延伸**:6 页(pg 12..17)
- **Pass anchor**:**第N节(P1)+ 【】(P2)+ (一)(P3)+ 1.(P4)** 四 pass 全开,**Pass 4 = `1.` 首次启用**(§5.5)— 字典浅 + 大体量必须开足
- **PARENT_SPLIT/REFINE/PASS3/PASS4 都 6000**(阈值偏离 SOP 5000)
- **节内子标题**:第N节(336)+【】(2003,密度极高)+(一)(315)+ 1.(291)+ 一、(39)
- **BODY_END**:首个 type=title `索引`(pg 2651) · **ref marker**:`参考文献`(每章末,丢 430508 字)
- **mineru OCR 救**:
  - 正文 1 字错(`鼾→肝`)→ **PATCH_BODY_RAW_FIX**(§4.1,首本)
  - TOC 全角括号 + 内空格 `( 3 )` / `（2576）` → TAIL_PAGE_RE 加强(§3.2,首本)
  - TOC `α₁` 被 mineru 当 `\alpha_{1}` equation_inline 漏读 → 字典/正文都缺,自动对齐
- ~~mineru type level 全是 1(首本)~~ / ~~content_list_v2 嵌套结构是本书 specific~~ — **2026-05-06 修正**:实测 8 本前作 + 后续 3 本待做的 mineru 全部 title.level=1、顶层 nested(list of pages),这两条是**全书共性**而非协和呼吸 specific(参见 §3 修订版 BOOK_NOTES)
- **本书首暴露 SOP-level 修正**:
  - **Pass 4 `1.` anchor**(§5.5)— 字典浅 + 大体量书必备
  - **PATCH_BODY_RAW_FIX 机制**(§4.1)— 短标题 1 字 OCR 错的精准救法
  - **TAIL_PAGE_RE 全角 + 内空格兼容**(§3.2)
  - **BLACKLIST 归一化检查**(§3.3)
  - **§6.2.1 5 项全量自动校验**(本节验证 179/179 offset 全 17,代替人眼抽样)

### 8.7 心血管内科学 第3版(2026-05-06)

- **字典深度**:L1-L3(239 entries:11 篇 + 45 章 + 183 节)
- **正文体量**:1,158K 字(790 页)
- **TOC 多页延伸**:11 页(pg 12-16 + 20-25,中间 17-19 是评审委员会 / 前言)
- **Pass anchor**:**一、(P1)+ (一)(P2)+ 1.(P3)** 三 pass — **无【】、字典已含节**,直接套协和呼吸 4 pass 模板会导致 max=8029(5 个 > 6000),正版 max=5999(0 个 > 6000)
- **PARENT_SPLIT/REFINE/PASS3 都 6000**
- **节内子标题**:一、(580)+ (一)(712)+ 1.(111)
- **BODY_END**:首个 type=title `中英文名词对照索引`(pg 771)· ref marker:`参考文献`(每章末,丢 219K 字)
- **本书首暴露 SOP-level 修正**:
  - **Pass 顺序按本书子标题层级,不直接套协和呼吸 SOP 模板**(§5.5 升级:METHODOLOGY 模板默认是协和呼吸路线,新书须按 Step 0 anchor 计数挑选 Pass 顺序)
  - **build_toc_dict 同页相邻行 stitch**(§3.2 升级):救 mineru 把 `xxxx\n yyy 页码` 切到下一行 paragraph(本书 2 处节标题被切碎)。规则:同页 + 前行无 TAIL_PAGE 尾 + 后行不是新 anchor → 合并
  - **PAGE_HEADER_FB blk_idx 改 -1**(§4.2 升级,原默认 0):章/篇页眉跟该页 b0 撞排序时,blk=0 排在节首之后 → stack 没先更新到章 → disambiguation 选错章。改 -1 排到 b0 前。本书 3 个真兜底章触发(第十六/十八/二十八章 mineru 漏识别章首 title)
  - **PATCH_INJECT_CANDIDATES + HARDCODE 同 key 删 PAGE_HEADER_FB**(§4 新增):救 mineru 篇页眉滞后(本书第八篇 page_header 落 pg 529,实际篇首 pg 528)。INJECT 后 dedup 删原 PAGE_HEADER_FB 防重复 strong
  - **audit chosen 按 full_path 去重**(§6.2.1 修正):strict_key 重复(`第一节 概述` 4 章共有)时,旧 chosen[strict_key] 只能存 1 个,导致校验 2/3/4 全误报。改按 full_path 去重 + offset 跳过 ambiguous strict_key
  - **`_merge_tiny_parents` level 平衡保留正确**:`(三) 41 字`物理 prev 是 `4. 受体阻滞剂(level 3)`,`(三)` 跟 `(二)` 同级(level 2),旧规则 `prev_level <= cur_level` 不成立(3 ≤ 2 False)拒绝合并是对的(强行 merge 会让 4. 段尾巴错挂 (三),语义错配)。41 字孤儿是真实数据(教材本身就这么短),保留

### 8.8 消化系统与疾病 第2版(2026-05-06)

- **字典深度**:L1-L3(627 entries:17 章 + 101 节 + 509 一、)
- **结构特点**:**无篇,层级是 章/节/一、**(8 本里唯一无篇结构)
- **正文体量**:**565K 字(8 本最小)**,571 页
- **TOC 多页延伸**:16 页(pg 15-30,中插评审委员名单 + 前言占 pg 7-13)
- **Pass anchor**:**(一) 单 pass**(字典已含 一、 → Pass 1 从字典外的下层 (一) 起步;user 拍板 Pass 2 1. 关闭,接受 1 个 6358 父块)
- **PARENT_SPLIT 6000**;Pass 2 设 1e9 实际关闭
- **CHAPTER_ABSORB 范围**:(1, 2, 3) — 字典含 L3 一、,所有层级参与跨吸收
- **节内子标题**:一、(509,字典内)+ (一)(963)+ 1.(1227)+ 【】(91,本书不开 Pass)
- **BODY_END**:首个 type=title `中英文名词对照索引`(pg 564)· ref marker:`参考文献` / `推荐阅读`(章末)
- **附:作 L2 节同级 anchor**:5 处 `附:小肠移植 / 附:肝移植 / 附:胰腺移植` 等加 PATTERN `^附\s*[:：]\s*\S` 进字典(节同级,不进则边界乱跨)
- **OCR 救**:
  - 字典侧:TOC 第二章第一节首项 `一、食管的发生` 误识为 `二、食管的发生` → PATCH_LINE_PREPROCESS
  - 正文侧:第四章第五节 `肠瘘` 在 title 误识为 `瘿`、page_header 误识为 `瘦` → PATCH_BODY_RAW_FIX 双修
- **本书首暴露 SOP-level 发现**:
  - **`附:xxx` 作 L2 节同级 anchor**(§3 升级):第二轮规划教材常见,加 PATTERN 即可
  - **Pass 数自适应字典深度**(§5.5 升级补充):字典含 L3 时 Pass 1 起步从 (一),不能直接套已验证书的 一、 模板(否则跟字典 L3 重复无效切)
  - **`equation_inline` block 被 `_text_of` 跳过**(§3 已知限制):mineru 把 `24h pH` 识别成数学公式,1 个 L3 子项 strict_key 对不上;由于 L3 不影响切割位置(Pass 1 用 RE_PAREN_CN 直接扫正文),本书接受 1 个 missing 不修。**跨书才有价值的修方向**:改 `_text_of` 把 equation_inline / interline_equation 的内容也算进字符串,需在所有书上验证不引入新差
  - **page_header 切碎/错位校验**(§6.2 新增子项):本书首次系统校验 — 0 真错位、0 切碎,所有 +1/+2 都是章后续页页眉的版式规律(章首页是 `03章` 简短格式,b+1 起才是全名),strict_key 重复造成的"+16 错位"是统计假象(逐章 disambig 后实际 +2)
  - **L1/L2 是切割保证关键,L3 missing/AMBIGUOUS 都不影响 chunk**(§6.2.1 重要原则):Step 2 校验只盯 L1/L2 全覆盖即可,L3 由 Pass 1 正则在正文直接定位
  - **`数字资源 AR 互动` 入 BLACKLIST**:新版规划教材的多媒体资源行污染

### 8.9 胸心外科(2026-05-06)

- **字典深度**:L1-L3(91 entries:2 篇 + 15 章 + 74 节)— **9 本最少 entries**
- **正文体量**:858K 字(541 页)
- **TOC 多页延伸**:**只 2 页**(pg 17 title='目录' + pg 18 page_header='目录'),seeds 已双端覆盖
- **Pass anchor**:**一、(P1)+ (一)(P2)+ 1.(P3)** 三 pass 全开 — 字典浅 + 节内大段叙事多,Pass 必须开足(baseline max 31760 → 全 Pass 后 6221)
- **PARENT_SPLIT/REFINE/PASS3 都 6000**
- **节内子标题**:一、(336)+ (一)(424)+ 1.(715)+ 【】(0)
- **BODY_END**:首个 type=title `中英文名词对照索引`(pg 513)+ pg 524 广告页 BLACKLIST
- **ref marker**:`参考文献`(每节末,丢 204K 字 / 24%)
- **mineru 章首识别意外好**:15/15 章 AS_IS,**0 真兜底 / 0 CHAP_MERGED**(对比心血管 3 真兜底 / 消化 9 CHAP_MERGED)
- **本书首暴露 SOP-level 修正**:
  - **`TAIL_PAGE_RE` 单 `…` 兼容**(§3.2 升级):`[…\.]{2,}` → `[…\.]+`,救 mineru 单字符 `…` 识别(`…67` 形式)。后置 anchor `\s*\d+\s*$` 已限定行末,不会误中正常文本
  - **`_detect_toc_pages` 不向外延伸**(本书 specific 选项,不做全局):seeds 已双端覆盖时(`title='目录'` + `page_header='目录'` 双 seed),只填充 seeds 间隙不向外扩,防止把篇内章节简表(无尾页码)误纳入。SOP 默认仍向外延伸(单 seed / 末页才标 page_header 的书需要)
  - **观察:mineru 章首识别质量随出版年代提升** — 本书(2024 第三轮研究生教材)0 真兜底,跟早期教材(协和呼吸 2011 / 心血管 2022)对比明显

### 8.10 普通外科(2026-05-06)

- **字典深度**:L1-L2(89 entries:12 章 + 77 节)— **10 本最少 entries**(无篇)
- **正文体量**:1252K 字 → 丢 ref 405K → keep 847K(721 页)
- **TOC**:**2 页**(pg 18 title='目录' + pg 19 page_header='目录'),seeds 已双端覆盖
- **Pass anchor**:**一、(P1)+ (一)(P2)+ 1.(P3)** 三 pass 全开 — 字典浅 + 大段叙事必备(baseline max 27791 → 全 Pass 后 6567)
- **PARENT_SPLIT/REFINE/PASS3 都 6000**;`absorb_levels=(1,2)`(字典只到 L2)
- **节内子标题**:一、(368)+ (一)(528)+ 1.(1067)+ 【】(0)
- **BODY_END**:首个 type=title `中英文名词对照索引`(pg 697)· **ref marker**:`参考文献` + `推荐阅读`(每节末,丢 405K 字 / 32%)
- **mineru 章首识别 12/12 全 type=title**(0 真兜底 / 0 CHAP_MERGED,跟胸心外科同)
- **字典 9 本里最干净** — strict_key 89/89 全 unique(节标题都是长描述`第N节 + xxx 的历史/进展`,无"概述"等通用名)→ Step 2 0 DISAMBIG / 0 AMBIGUOUS
- **复用胸心外科 SOP-level 修法**(§3.1 双 seed 不向外延伸 / §3.2 TAIL_PAGE_RE 单 `…`),**0 新增 SOP 修正** — 验证性 POC

### 8.11 骨科(2026-05-06)

- **字典深度**:L1-L4(1293 entries:6 篇 + 51 章 + 253 节 + 983 一、)— **11 本最大字典**
- **正文体量**:1234K 字(1445 页,11 本第二大,仅次于泌尿外科)
- **TOC 页数**:**33 页(最长)**,单 seed pg 11 + 双向延伸覆盖 pg 11..43
- **Pass anchor**:**`(一)+【】` 合并 anchor (P1) + `1.` (P2)** 二 pass — 教材混用两种子标题结构(感染章用【】,其他多 (一))
- **PARENT_SPLIT/REFINE 都 6000**;`absorb_levels=(1,2,3)`(L4 是叶子)
- **节内子标题**:一、(1579 字典内 + 正文)+ (一)(1972)+ 1.(2136)+ 【】(383,本书首启用合并 anchor)
- **BODY_END**:**paragraph 形态** `索引`(pg 1444 末尾混乱区)— `_find_body_end` 升级为 `b["type"] in ("title", "paragraph")`
- **OCR 救**:
  - 字典/正文 OCR 错字 `踇外翻` → `蹇/蹮`(PATCH_BODY_RAW_FIX)
  - 正文 L4 标题尾英文术语括号 `(English term)`(PATCH_BODY_RAW_FIX)
  - 2 处 TOC 节内唯一子项无 `一、` 编号(`非骨化性纤维瘤` / `骨内脂肪瘤`)→ `PATCH_FORCE_LEVEL` 硬编码字典 entries
- **本书首暴露 SOP-level 修正**:
  - **`_find_body_end` 兼容 paragraph**(§5 升级):BODY_END marker 可在 paragraph 形态(本书末尾混乱区)
  - **`PATCH_FORCE_LEVEL: dict[str, int]` 硬编码字典 entries**(§3 PATCH 体系新维度):strict_key → level,`_classify` 在 PATTERNS 失败时 fallback 检查;救"无 anchor 前缀但要纳入字典"的 TOC 行
  - **Pass 1 合并 `(一)+【】` anchor**(§5.5 升级):教材子标题混用 (一)/(【】) 时,Pass 1 predicate 接受两种 anchor 都触发(`_is_paren_or_bracket_subheading`)
  - **390 个 < 500 L4 父块接受原则**(§5.6 升级):L4 整段短(50-500 字)是教材"简明陈述"风格,absorb_levels=(1,2,3,4) 会破坏语义边界(merge 后挂错 heading_path),拒绝 absorb,接受短父块

### 8.12 现代泌尿外科学(2026-05-06,12 本系列收官)

- **字典深度**:L1-L4(2539 entries:14 篇 + 93 章 + 504 节 + 1928 一、)— **12 本最大字典**
- **正文体量**:**2103K 字(12 本最大)**(1681 页)
- **TOC 页数**:32 页(pg 17..48),所有 TOC 页 page_header='目录' 多 seed 已覆盖
- **Pass anchor**:**`(一)` 单 pass**(字典含 一、,Pass 1 起步从 (一);Pass 2 1. 关闭,user 决定接受 3 个超阈值)
- **PARENT_SPLIT 6000**;`absorb_levels=(1,2,3)`(L4 是叶子)
- **节内子标题**:一、(1928 字典内 + 正文)+ (一)(2764)+ 1.(2044)+ 【】(0)
- **BODY_END**:type=title `索引`(pg 1678)
- **CHAP_MERGED 规模 12 本最大**:14 篇 + 93 章 = 107 处 mineru 篇/章名切两 title 拼接
- **OCR 救**(4 类字符差异共 9 处):
  - 异体字 `盞/盏`(肾盏憩室,PATCH_BODY_RAW_FIX)
  - 罗马数字符号 vs 西文字母 `Ⅳ/IV` `Ⅱ/II`(PATCH_BODY_RAW_FIX 2 处)
  - LaTeX 希腊字母 `\alpha/α`(_text_of 升级 1 次救 5 处)
  - 形近字 `辜/睾`(无睾症,PATCH_LINE_PREPROCESS 反向修字典侧 OCR 错)
- **本书首暴露 SOP-level 修正(5 个,12 本最多)**:
  - **跨页 stitch**(§3.2 升级):`raw_lines[i+1][0] in (pg, pg+1)`,救 list 末 item 跨页切碎(本书 2 处)
  - **SPLIT_ANCHOR 加 `(?<=\d)(?=[一二...]+、)`**(§3.2 升级):救 mineru 把多 list items 错抓成 1 paragraph(粘贴式)
  - **`_text_of` 兼容 equation_inline + LaTeX 希腊字母 → Unicode**(§3 升级):`\alpha→α \beta→β` 等 19 字母,救 mineru 把希腊字母抓成数学公式
  - **OCR 字符差异 PATCH 体系完善**(§4 升级):4 类字符差异统一走 PATCH_BODY_RAW_FIX(正文修)+ PATCH_LINE_PREPROCESS 反向(字典侧 OCR 错)
  - **接受 L4 短父块原则正式确立**(§5 升级):大型专科书 L4 短叙事必然产生 41% < 500 父块(本书 12 本最多),语义边界 > 字数

### 8.x 模板(下一本书填)

复制本节模板格式(只记差异,数据放 已做好.md)。

---

## 9. 关键阈值速查

| 常量 | 默认值 | 说明 |
|---|---|---|
| `PARENT_SPLIT_THRESHOLD` | **5000** 字 | section > 此值才走 Pass 1。三本书统一(2026-05-05 user 拍板,从 4000 调高,4000-5000 字医学段落本来合理 chunk,不该被切碎)|
| `PARENT_REFINE_THRESHOLD` / `PARENT_PASS3_THRESHOLD` / `PARENT_PASS4_THRESHOLD` | **6000** 字 | Pass 1 后某段仍 > 此值 → 触发 Pass 2/3/4 救超大(协和呼吸首启 Pass 4)|
| `PARENT_MERGE_TINY_THRESHOLD` | 500 字 | 节内小父块 < 此值 + 同/上级相邻 → 合并 |
| `CHAPTER_ABSORB_THRESHOLD` | 500 字 | 短 section < 此值并入下一 section(参与 level 见 §5.4)|
| `CHILD_SPLIT_THRESHOLD` | 1200 字 | 父块 ≤ 此值不切子块 |
| `CHILD_TARGET_SIZE` | 600 字 | 大父块切子块的目标 size |
| `CHILD_MIN_SIZE` | 200 字 | 子块强制最小,防孤儿 |
| `FUZZY_RATIO_THRESHOLD` | 0.85 | FUZZY_TITLE 命中阈值 |

调阈值原则:阈值变动后要全套校验跑一遍,看分布是否合理。不要凭感觉调。

---

## 10. 已知未决议题

### 10.1 篇/章导航页 < 500 字父块(已部分解决)

之前内分泌发现的 10 个篇/章导航页占位符问题,通过诊断学 POC 引入的 §5.4 跨 section 吸收 已解决。
内分泌可以 backport 这个修复,但暂未做。

### 10.2 mineru 漏识别子标题的兜底缺失

mineru 偶尔会把"(三) 听诊内容"这种子标题完全漏识别(连 paragraph 都不标),无法救。
影响:超大父块切不开,只能接受。
当前发现 1 个 outlier(诊断学 6291 字 二、白细胞检测),全书 < 1% 影响。

### 10.3 spec §3.2.3 父块扩展策略需重新设计

新切分方案父块 median 1346~1722 字,本身已是中等 size,直接塞 LLM prompt 即可,不需要"展开整节为多 chunk"的额外扩展逻辑。spec §3.2.3 父块扩展段落需要更新。

### 10.4 12 本教材 anchor pattern 的可复用性

已验证 5 本(内分泌 / 诊断学 / 内科学 / 神经外科学 / 神经内科学),剩 7 本(内科分支 3 + 外科 4)。
- 用药指南是药典结构,已知绕开 C2 主流程(走 C2.5 独立任务)
- 其他书可能有新 anchor 风格,逐本走 §7 SOP,每本 2-4 小时

---

## 11. 关键决策来源

按时间顺序汇总用户拍板的关键决策(每个新决策追加):

| 日期 | 决策 | 触发场景 |
|---|---|---|
| 2026-05-03 | 完全放弃 mineru title.level,改"目录权威清单" | 内分泌 POC 早期 |
| 2026-05-03 | 节内子块切分不再用 mineru title 边界,改 size 驱动 | 内分泌 POC 中期 |
| 2026-05-03 | PARENT_MERGE_TINY_THRESHOLD = 500,严格层级合并 | 内分泌 POC 晚期 |
| 2026-05-03 | 节内"参考文献"标题后整段丢弃 | 内分泌 IgG4 12692 字异常 |
| 2026-05-05 | 跨 section 吸收:L1/L2 < 500 并入下一 section | 诊断学 占位符父块问题 |
| 2026-05-05 | FUZZY_TITLE 救 OCR 错字章名 | 诊断学 "第二章 般检查" 漏一 |
| 2026-05-05 | PAGE_HEADER_FB:位置取 (pg, 0) 而非 page_header 块自身 | 诊断学 page_header 排在 page 末尾 |
| 2026-05-05 | 父块 > 6000 才二次细化(Pass 2/3),普通父块只 Pass 1 粗粒度 | 诊断学 用户偏好粗粒度 |
| 2026-05-05 | TOC 把 一、/[附] 列为独立条目 → 字典扩 L4(原 SOP 只到 L3) | 内科学 TOC 含 107 条 L4 真目录条目 |
| 2026-05-05 | 算法救不了的 OCR 错走硬编码 PATCH(REPLACE/INSERT + path rebuild)| 内科学 mineru 漏识 6 处整行 + 2 处 OCR 错章号 |
| 2026-05-05 | `RE_REF_MARKER` 扩展为 `(?:参考文献\|推荐阅读)` | 内科学 8 处篇末"推荐阅读" + 英文 reference list |
| 2026-05-05 | `PARENT_SPLIT_THRESHOLD`: 4000 → 5000(三本书统一) | 内科学评估认为 4000-5000 字段被 Pass 切碎不合理 |
| 2026-05-05 | 字典最深层级(L3 或 L4) 永远不参与 ABSORB,即使内容只 28 字 | 内科学 L4 stub 接受独立(heading_path 价值 > 凑字数)|
| 2026-05-05 | **每本书 Step 3 先按纯目录粒度切看 baseline**,再决定开哪个 Pass(不要上来就开 Pass 1)| 用户工作流原则 — 目录粒度是原书语义切分,Pass 是不得已二次拆;能不切就不切,heading_path 跟字典一致最干净 |
| 2026-05-05 | `_detect_toc_pages` 升级 3 步(双 seed + 中间填充 + 双向延伸 + 阈值 5)| 神经外科学 mineru 末页才标 page_header,中间页 pg 6 完全没标 |
| 2026-05-05 | `SPLIT_ANCHOR` 加 lookbehind `(?<=\d)(?=...)` 通用规则 | 神经外科学 TOC 节条目用页码黏行(`第三节 X 219第四节 Y 221`)|
| 2026-05-05 | RE_CN_NUM 兼容顿号后无空格(`[、.]\s*\S` 而非 `[、.]\s`)| 神经外科学"一、自然史" 顿号紧跟中文,SOP 原 `[、.]\s` 不命中导致 Pass 1 0 触发 |
| 2026-05-05 | 阈值允许按书调(神经外科学 SPLIT=6000 偏离 SOP 5000) | 小书目录粒度已较合理,5000 切碎中等父块不必要;允许按 baseline 分布个性化 |
| 2026-05-05 | `_real_start_pos` 退化时 **AS_IS 永远优先 PAGE_HEADER_FB** | 神经外科学 audit 发现:章 anchor 紧跟篇 anchor gap=0 不算 strong,原代码 `recs[-1]` 错选 PAGE_HEADER_FB(影响诊断学 4 处 / 神经外科学 1 处);修后 4 本 mismatch=0 全保持 |
| 2026-05-05 | **每本书 Step 2 完成后必须跑 audit_final_positions.py 抽检** | 仅看 Missing=0 不够,要验证每个 entry 最终位置是不是真落在 type=title block 上。神经外科学 audit 暴露通用 SOP bug,sec 4.4 修正后 4 本全部 entry 落 title 块(诊断学 2 个 image 是 mineru 漏识 type=title 的真兜底,接受)|
| 2026-05-05 | `_real_start_positions` **strict_key 冲突 → stack 消歧 + 按 path 分组** | 神经内科学 17 处"第一节 概述" 共享同一 strict_key,原代码按 (level, title) 分组合并导致 16 处位置丢失;字典含 L4 + 通用节名时必修(§4.6) |
| 2026-05-05 | `absorb_levels = (1, 2, 3)` — L3 也参与跨 section 吸收 | 神经内科学 L4=487 多 + L3 标题后立即跟 L4 → 134 个空壳 L3 (size=6 仅剩 title);前 4 本 chunk 脚本写死 (1,2) 没暴露,神经内科学触发后修正 |
| 2026-05-05 | `preface_dropped` stats 字段 — first section 起点之前内容丢弃 | 神经内科学有编者前言(2892 字 / 18 blocks),前 4 本 first section 紧贴 body_start 没暴露;研究生教材首例 |
| 2026-05-05 | strict_key 加标点全/半角归一化(`？→?` `，→,` `：→:` 等)| 神经内科学目录页用全角标点,正文页 OCR 半角,strict_key 不一致;救 2 处 missing |
| 2026-05-05 | 正文无 type=title 的 entry → `PATCH_INJECT_CANDIDATES` 硬编码坐标注入 | 神经内科学附录"抗癫痫药物缩写对照"整页是 TABLE,mineru 没标 title 无法常规匹配;给 (pg, blk) 坐标兜底 |
| 2026-05-05 | **§6.1 字典完整性 4 类硬校验**(基础 + 守恒等式 + 编号连续性 + 集合差集) | 协和呼吸暴露:只看 16+163 汇总数对得上,会漏 OCR 错字/字典 normalize bug/正文-目录差异;集合差集 vs 正文 title block 是最强证据,必跑 |
| 2026-05-05 | **§6.2.1 全量自动 5 项硬校验**(位置唯一/顺序单调/嵌套正确/offset 一致/真兜底)代替人眼抽样 | 协和呼吸 179/179 entries offset 全 17(印刷 pg 1 = mineru pg 18),offset 全等是数学约束,任何 1 页错配立即暴露 — 比抽 5 样本看 PDF 更全更稳 |
| 2026-05-05 | **Pass 4 = `1.` anchor 启用** | 协和呼吸字典浅(L1-L2)+ 体量大(3.18M 字),Pass 1+2+3 后剩 80 个 > 5000(6%);Pass 4 把【】内部纯列表段救掉,降到 64(4.5%)/ max 19991→9059 |
| 2026-05-05 | **PATCH_BODY_RAW_FIX 机制** — 正文 raw text 在 strict_key 前替换 | 协和呼吸 pg 1801 `第二章 鼾症` 被 OCR 成 `肝症`,FUZZY ratio=0.8 < 0.85;短标题 1 字差降阈值会引入跨章误配,改为精准 patch |
| 2026-05-05 | **TAIL_PAGE_RE 兼容全角括号 + 内空格** `[（(]?\s*\d+\s*[）)]?` | 协和呼吸 `…… ( 3 )`(括号内空格)/`…… （2576）`(全角括号)旧规则失败 |
| 2026-05-05 | **BLACKLIST 检查需归一化后** `s in BLACKLIST or _normalize(s) in BLACKLIST` | 协和呼吸 `索引 (2634)` / `彩图插页 (2655)` 带尾页码不命中原 BLACKLIST 跑去 unmatched 当噪音 |
| 2026-05-05 | ~~mineru content_list_v2 顶层结构差异(协和呼吸 nested vs 其他 flat)~~ — **2026-05-06 撤销**:实测全 11 本(8 前作 + 3 待做)mineru content_list_v2 一律 nested(list of pages),无 flat 案例,这条差异不存在 | flatten 写法 `for pg, blocks in enumerate(data): for b in blocks` 是**所有书统一**的标准写法 |
| 2026-05-06 | **Pass 顺序按本书实际子标题层级,不直接套协和呼吸 SOP 模板** | 心血管字典已含节 + 无【】 + 节内层级 一、→(一)→1.,直接套协和呼吸 4 pass(节/【】/(一)/1.)第一 pass 是节(已在字典)空切,【】(本书 0)空切,实际只 (一)+1. 两 pass 救父块,max=8029(5 个 > 6000);改三 pass 一、+(一)+1. 后 max=5999(0 个 > 6000)。新书必须看 Step 0 anchor 计数挑 Pass 顺序 |
| 2026-05-06 | **build_toc_dict 加同页相邻行 stitch** | 心血管 mineru 把 `第七节 心血管核素显像——功能与\n 分子显像兼备 120` 切成两个独立 paragraph,字典只收前半段 → strict_key 跟正文对不上(本书 2 处)。规则:同页 + 前行无 TAIL_PAGE 尾 + 后行不是新 anchor → 拼到前行。协和呼吸里关掉的、内分泌也没真做,本书暴露后回灌模板 |
| 2026-05-06 | **PAGE_HEADER_FB blk_idx 改 -1**(原 0) | 心血管 3 个真兜底章(第十六/十八/二十八)mineru 漏识别章首 type=title,只 page_header 救;page_header 给 blk=0 时跟该页节首 b0 撞,sort 后 PAGE_HEADER_FB 排在节首之后 → stack 没先更新到章 → 后续 disambiguation(`第一节 概述` 重复 strict_key)选错章。改 blk=-1 排到 b0 前 |
| 2026-05-06 | **PATCH_INJECT_CANDIDATES + HARDCODE 同 key 删 PAGE_HEADER_FB** | 心血管第八篇 mineru page_header 滞后 1 页(落 pg 529,实际篇首 pg 528)。INJECT (528, -2, "第八篇 ...") 救错位,且 dedup 时若同 strict_key 已有 HARDCODE 就丢弃 PAGE_HEADER_FB,防 (529, -1) 仍当 strong 位置造成重复 |
| 2026-05-06 | **audit chosen 按 full_path 去重(不是 strict_key)+ offset 跳过 ambiguous strict_key** | 心血管字典 `第一节 概述` 4 章共有 strict_key,旧 audit chosen[strict_key] 只能存 1 个,4 处都用同一个 pg → 校验 2/3/4 全误报。改按 full_path 去重(disambig 后唯一),校验 4 offset 跳过 strict_key 重复 entries(declared 字典也无法 disambig)|
| 2026-05-06 | **`_merge_tiny_parents` level 平衡逻辑保留(不强行合并跨语义层级孤儿)** | 心血管 `(三) 41 字` 物理 prev 是 `4. 受体阻滞剂(level 3)`,(三) 跟 (二) 同级(level 2),`prev_level <= cur_level` 不成立 → 拒绝合并是对的(强行 merge 会让 4. 段尾巴错挂 (三),语义错配)。41 字孤儿是真实数据(教材本身就这么短),允许独立 |
| 2026-05-06 | **`附:xxx` 作 L2 节同级 anchor** | 消化系统第二轮规划教材里 5 处 `附:小肠移植 / 附:肝移植 / 附:胰腺移植` 等节后扩展章节(几页内容),不进字典则正文 type=title 这块边界乱跨;PATTERN 加 `^附\s*[:：]\s*\S` 跟 第N节 同级 |
| 2026-05-06 | **Pass 数自适应字典深度** | 消化字典已含 L3 一、,Pass 1 必须从字典外的下层 (一) 起步;若直接套已验证书的 一、 模板,Pass 1 跟字典 L3 重复无效切。由此延伸:每本书 Pass 起点 = 字典最深层级的下一层 |
| 2026-05-06 | **`equation_inline` block 被 `_text_of` 跳过(已知限制,接受)** | 消化 mineru 把"24h pH"识别成数学公式,1 个 L3 子项 strict_key 对不上;由于 L3 不影响切割位置(Pass 1 用 RE_PAREN_CN 在正文直接定位),本书接受 1 个 missing 不修。**跨书才有价值的修方向**:改 `_text_of` 把 equation_inline / interline_equation 的内容也算进字符串,需在所有书上验证不引入新差 |
| 2026-05-06 | **page_header 切碎/错位校验** — 加入 §6.2 校验子项 | 消化首次系统校验 — 0 真错位、0 切碎,所有 +1/+2 都是章后续页页眉的版式规律(章首页是 `03章` 简短格式,b+1 起才是全名),strict_key 重复造成的"+16 错位"是统计假象,逐章 disambig 后实际 +2 |
| 2026-05-06 | **L1/L2 是切割保证关键,L3 missing/AMBIGUOUS 都不影响 chunk** — Step 2 校验只盯 L1/L2 全覆盖即可 | 消化 27 个 L3 AMBIGUOUS + 1 个 L3 missing,初看吓人但实际 chunk 用 Pass 1 RE_PAREN_CN 在正文直接扫 (一),不依赖字典 disambig;校验只需 L1 章 + L2 节 100% 覆盖。L3 装饰性 |
| 2026-05-06 | **`TAIL_PAGE_RE` 单 `…` 兼容**(SOP-level 升级) | 胸心外科 pg 17 第四章第二节 mineru OCR 把尾页码识别为单字符 `…67`(无空格),原 `[…\.]{2,}` 漏处理 → 字典 strict_key 末尾带 "…67" 跟正文对不上。改 `[…\.]+`,后置 anchor `\s*\d+\s*$` 已限定行末数字结尾,不会误中"5. 治疗"等正常文本 |
| 2026-05-06 | **`_detect_toc_pages` seeds 已双端覆盖时不向外延伸**(本书 specific 选项) | 胸心外科 pg 17 (title='目录') + pg 18 (page_header='目录') 双 seed,原 SOP 向外延伸把 pg 19 篇内章节简表(每行无尾页码)anchor count=9 拉进来,字典重复抓 1 篇 + 8 章。修法:删向外延伸,只填 seeds 间隙。**不做 SOP 全局升级**(单 seed / 末页才标 page_header 的书仍需向外延伸) |
| 2026-05-06 | **mineru 章首识别质量随出版年代提升的观察**(经验性) | 胸心外科(2024 第三轮)15/15 章直接 type=title,0 真兜底 / 0 CHAP_MERGED;对比协和呼吸(2011)/ 心血管(2022)章首漏识别率显著降低。新书 POC 时**先信 mineru 直识别**,再看是否需要 CHAP_MERGED / PAGE_HEADER_FB 兜底 |
| 2026-05-06 | **`_find_body_end` 兼容 paragraph 形态**(SOP-level 升级) | 骨科 BODY_END marker `索引` 在 pg 1444 是 type=paragraph(末尾混乱区),原 SOP 只匹配 type=title 漏掉 → flat_kept 包含末尾混乱区 paragraph 列表污染。改为 `b["type"] in ("title", "paragraph")` 同时匹配,向前兼容 |
| 2026-05-06 | **`PATCH_FORCE_LEVEL: dict[str, int]` 硬编码字典 entries**(PATCH 体系新维度) | 骨科 2 处节内唯一子项书本省略 `一、` 编号(`非骨化性纤维瘤` / `骨内脂肪瘤`),原 PATCH_LINE_PREPROCESS 加 `一、` 是错向(正文也无 `一、`)。新机制:strict_key → level 直接打标签,`_classify` 在 PATTERNS 失败时 fallback;字典侧 + 正文侧用同 strict_key 自动对齐。**之前 PATCH 都是改 raw 字符串(LINE_PREPROCESS / BODY_RAW_FIX),这是新维度** |
| 2026-05-06 | **Pass 1 anchor 合并 `(一) + 【】`**(本书 specific 选项) | 骨科教材混用两种子标题结构 — 感染章用 `【临床表现】/【辅助检查】`,其他章多 `(一)(二)(三)`。单开 (一) 切不开感染章,单开【】 切不开其他章。`_is_paren_or_bracket_subheading` 接受两种 anchor 都触发,Pass 1 一次切两种结构。诊断学/内科 9 版用单【】,本书首次合并 |
| 2026-05-06 | **接受 L4 整段短父块原则**(切割原则补充) | 骨科第四篇骨病 274 个 < 500 父块,L4 整段短(50-500 字)是教材"每个疾病简明陈述"的真实风格(`一、病因`+`二、临床表现`+`三、辅助检查`+`四、诊断`+`五、治疗` 各几十到几百字)。**absorb_levels=(1,2,3,4) 会破坏标题语义**(merge 多 L4 后挂错 heading_path,父块标题挂"五、治疗"但内容是五段拼接)。语义边界比字数重要 → 拒绝 absorb,接受短父块 |
| 2026-05-06 | **跨页 stitch**(SOP-level 升级) | 泌尿外科 mineru 把 list 末 item 切碎到下页 paragraph(本书 2 处 list_items 跨页)。修法:把 stitch 触发条件 `raw_lines[i+1][0] == pg` 改为 `in (pg, pg+1)`(同页 + 下一页),限 1 页防误合并 |
| 2026-05-06 | **SPLIT_ANCHOR 加 `(?<=\d)(?=[一二...]+、)`**(SOP-level 升级) | 泌尿外科 mineru 把多个 list items 错抓成 1 paragraph(`一、流行病学研究 837二、IC 症状患病率`这种粘贴式)。SPLIT_ANCHOR 加新规则:数字尾页码后紧跟"X、"另起一项 |
| 2026-05-06 | **`_text_of` 兼容 equation_inline + LaTeX 希腊字母 → Unicode**(SOP-level 升级) | 泌尿外科 mineru 把希腊字母 α/β 等抓成 equation_inline LaTeX 形式(`5\alpha-还原酶`等)。原 `_text_of` 跳过 equation_inline → 字符丢失。修法:`_text_of` 处理 equation_inline 时按字典 `_LATEX_GREEK = {r"\alpha": "α", ...}` 19 个希腊字母映射回 Unicode。**回灌消化系统类似 `\mathrm` 问题(略需扩展)** |
| 2026-05-06 | **`PATCH_LINE_PREPROCESS` 反向修字典侧 OCR 错**(用法新维度) | 泌尿外科 TOC pg42 字典 OCR 错"无睾症"为"无辜症"(辜 U+8F9C,睾 U+777E),正文识别正确。之前 PATCH_LINE_PREPROCESS 都是字典对正文加内容(给短 一、 加序号),本书首次**反向用** — 字典侧改字符以对齐正确正文 |

---

## 12. 后续工作

- [x] 内分泌完整 POC 验证(2026-05-03)
- [x] 诊断学完整 POC 验证(2026-05-05)
- [x] 内科学 第9版 完整 POC 验证(2026-05-05)
- [x] 神经外科学 完整 POC 验证(2026-05-05)
- [x] 神经内科学 第2版 完整 POC 验证(2026-05-05)
- [x] 协和呼吸病学 第二版 完整 POC 验证(2026-05-05)
- [x] 心血管内科学 第3版 完整 POC 验证(2026-05-06)
- [x] 消化系统与疾病 第2版 完整 POC 验证(2026-05-06)
- [x] 胸心外科 完整 POC 验证(2026-05-06)
- [x] 普通外科 完整 POC 验证(2026-05-06,验证性 — 0 新 SOP 修正)
- [x] 骨科 完整 POC 验证(2026-05-06,3 个新 SOP-level 修正)
- [x] 现代泌尿外科学 完整 POC 验证(2026-05-06,**5 个新 SOP-level 修正,12 本系列收官**)
- [x] 跨 section 吸收 / FUZZY_TITLE / PAGE_HEADER_FB 加入通用 SOP
- [x] 字典层级扩 L4 / 硬编码 PATCH 系统 / 推荐阅读 marker 加入通用 SOP
- [x] _detect_toc_pages 双 seed + 双向延伸 + 中间填充 / SPLIT_ANCHOR lookbehind / RE_CN_NUM 兼容性 加入通用 SOP
- [x] 三本书阈值统一(SPLIT 5000 / REFINE 6000;神经外科学/神经内科学/协和呼吸/心血管按书调到 6000)
- [x] strict_key 冲突消歧 + L3 absorb + preface_dropped + 标点归一化 + INJECT_CANDIDATES 加入通用 SOP
- [x] Pass 4 (`1.`) 加入通用 SOP(协和呼吸暴露)
- [x] PATCH_BODY_RAW_FIX / TAIL_PAGE_RE 全角兼容 / BLACKLIST 归一化(协和呼吸暴露)
- [x] 同页相邻行 stitch / PAGE_HEADER_FB blk=-1 / HARDCODE 同 key 删 PAGE_HEADER_FB / audit chosen 按 full_path / Pass 顺序按本书层级(心血管暴露)
- [x] `附:` 作 L2 节同级 / Pass 数自适应字典深度 / page_header 切碎校验 / L1-L2 是切割保证关键 L3 装饰性(消化暴露)
- [x] `TAIL_PAGE_RE` 单 `…` 兼容 / `_detect_toc_pages` 双 seed 时不向外延伸(胸心外科暴露,普外验证稳定)
- [x] `_find_body_end` 兼容 paragraph / `PATCH_FORCE_LEVEL` 硬编码字典 entries / Pass 1 (一)+【】 合并 anchor / L4 短父块接受原则(骨科暴露)
- [x] 跨页 stitch / SPLIT_ANCHOR 数字+X、 / `_text_of` 兼容 equation_inline LaTeX 希腊字母 / PATCH_LINE_PREPROCESS 反向修字典(泌尿外科暴露)
- [x] **12 本教材完整 POC 验证收官**(2026-05-06)
- [ ] 把 POC port 到 production `src/rag/ingestion/chunking.py`
- [ ] 表格双粒度(整表 chunk + 逐行 chunk)— 见 spec §3.1.2,本 POC 未涵盖
- [ ] 内分泌 backport 跨 section 吸收 / `_text_of` LaTeX 升级回灌前面书消化系统等(优先级低)
- [ ] 内分泌 backport 跨 section 吸收(消除 10 个占位符父块)
- [ ] `_text_of` 是否纳入 equation_inline / interline_equation(待跨书验证)

---

## 文件清单

```
scripts/
├── METHODOLOGY.md                          ← 本文件(共用方法论)
├── 已做好.md                                ← 12 本书结果分布(统一格式)
├── poc_chunking_内分泌代谢病学_第4版上册/
│   ├── poc_build_toc_dict.py
│   ├── poc_match_body_titles.py
│   └── poc_chunk_book.py
├── poc_chunking_诊断学_第10版/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py
│   ├── poc_match_body_titles.py
│   └── poc_chunk_book.py
├── poc_chunking_内科学_第9版/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← 含 PATCH_REPLACE_TITLE / PATCH_INSERT_AFTER
│   ├── poc_match_body_titles.py
│   ├── poc_chunk_book.py
│   ├── audit_step2.py                      ← 抽检脚本(各 action 样本)
│   └── audit_final_positions.py            ← 抽检脚本(strong 选择后最终位置)
├── poc_chunking_神经外科学/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← 双 seed + 双向延伸 + 中间填充 + PATCH_REPLACE_TITLE
│   ├── poc_match_body_titles.py
│   └── poc_chunk_book.py                   ← Pass 1 = 一、,SPLIT 阈值 6000(偏离 SOP 默认 5000)
├── poc_chunking_神经内科学/                   ← 实际书是《神经内科学》第 2 版,目录无后缀
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← strict_key 冲突 stack 消歧 / L4 字典
│   ├── poc_match_body_titles.py
│   ├── audit_final_positions.py
│   └── poc_chunk_book.py                   ← absorb_levels=(1,2,3) / preface_dropped
├── poc_chunking_协和呼吸病学_第二版/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← TAIL_PAGE 全角兼容 / BLACKLIST 归一化
│   ├── poc_match_body_titles.py            ← PATCH_BODY_RAW_FIX
│   ├── audit_step2.py                      ← 全量 5 项硬校验
│   └── poc_chunk_book.py                   ← 4 Pass(节/【】/(一)/1.)
├── poc_chunking_心血管内科学_第3版/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← 同页相邻行 stitch / INJECT 救篇页眉错位
│   ├── poc_match_body_titles.py            ← PAGE_HEADER_FB blk=-1
│   ├── audit_step2.py                      ← chosen 按 full_path 去重
│   └── poc_chunk_book.py                   ← 3 Pass(一、/(一)/1.,无【】)
├── poc_chunking_消化系统与疾病_第2版/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← 附: 作 L2 / PATCH_LINE_PREPROCESS 救字典 OCR
│   ├── poc_match_body_titles.py            ← PATCH_BODY_RAW_FIX 救肠瘘 OCR
│   └── poc_chunk_book.py                   ← 1 Pass(只 (一);Pass 2 关闭)
├── poc_chunking_胸心外科/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← TAIL_PAGE_RE 单 `…` / _detect_toc_pages 不向外延伸
│   ├── poc_match_body_titles.py
│   └── poc_chunk_book.py                   ← 3 Pass 全开(一、/(一)/1.,字典浅必须开足)
├── poc_chunking_普通外科/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← 复用胸心外科 SOP 修法 / 无篇
│   ├── poc_match_body_titles.py
│   └── poc_chunk_book.py                   ← 3 Pass 全开,字典 89/89 全 unique 0 disambig
├── poc_chunking_骨科/
│   ├── BOOK_NOTES.md
│   ├── poc_build_toc_dict.py               ← PATCH_FORCE_LEVEL 硬编码 entries / 单 seed 双向延伸
│   ├── poc_match_body_titles.py            ← PATCH_BODY_RAW_FIX 救 OCR 错字 + 英文括号
│   └── poc_chunk_book.py                   ← Pass 1 (一)+【】 合并 / Pass 2 1. / paragraph BODY_END
└── poc_chunking_泌尿外科/                   ← 12 本系列收官,5 个新 SOP-level 升级
    ├── BOOK_NOTES.md
    ├── poc_build_toc_dict.py               ← 跨页 stitch / SPLIT_ANCHOR 数字+X、 / _text_of LaTeX 希腊字母 / PATCH 反向修字典
    ├── poc_match_body_titles.py            ← PATCH_BODY_RAW_FIX 异体字 + 罗马数字 vs 西文字母
    └── poc_chunk_book.py                   ← Pass 1 (一) 单 pass(接受 3 个超阈值)
```
