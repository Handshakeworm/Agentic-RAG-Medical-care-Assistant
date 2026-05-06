# 协和呼吸病学 第二版 — 本书特定笔记

通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md)。本文件只记跟 SOP 默认值的差异。

POC 完成日期:2026-05-05。最终结果:**1414 父块 / 4228 子块 / mismatch=0**。

---

## 1. 基本数据

- 出版信息:蔡柏蔷/李龙芸主编,2011 第二版,人民卫生出版社专科参考书(协和系列)
- 总页数:2686(**6 本里最大**,前 5 本最大内分泌 1276 页)
- 上下册:目录里"上册""下册"分隔,但**正文里 mineru 没记 `下册` title block**(物理分卷在 mineru 拼接时已合并)
- TOC 字典 entries:**179**(**6 本里最浅**,L1=16 + L2=163,**0 节**)
- TOC 页范围:pg 12..17(共 6 页)
- 正文起点:pg 18(第一篇 cover)
- 书末截断:首个 type=title `索引` 在 pg 2651,丢弃 1929 blocks / 28345 字
- 字符守恒:body kept 3,178,320 字 − ref dropped 430,508 = expected 2,747,812 ✓

---

## 2. 章节结构 — 字典只到 L2(最浅)

| 层级 | anchor | 数量 | 备注 |
|---|---|---|---|
| L1 | 第N篇 | 16 | 1..16 连续(8/4/18/12/24/8/5/5/20/7/3/8/9/12/6/14)|
| L2 | 第N章 | 163 | 跨篇连续 |
| L3 | 第N节 | **0(字典外)** | 正文 type=title 中 336 个,Pass 1 anchor |
| L4 | 【...】 | **0(字典外)** | 正文 type=title 中 **2003 个**(密度极高),Pass 2 anchor |
| L5 | (一)(二) | **0(字典外)** | 正文 type=title 中 315 个,Pass 3 anchor |
| L6 | 1. 2. | **0(字典外)** | 正文 type=title 中 291 个,Pass 4 anchor |

**字典最浅 → 必须开 4 个 Pass**(前 5 本最多 2 个 pass)。基线(Pass off)结果:163 父块 / max=98373 / 87.7% > 5000,完全不可用。开到 Pass 4 才达标(64 个 > 5000 占 4.5%, max=9059)。

---

## 3. mineru 输出结构(全书共性,非本书 specific)

修正:之前以为"前 5 本 level 有 1..3、协和呼吸 nested vs 神经内科学 flat" — 实测前 8 本(协和呼吸 + 之前的 7 本)+ 后续 3 本待做的 mineru `content_list_v2.json` 全部:

- 顶层 = `list of pages`,每页 = `list of blocks`(无任何 flat 案例)
- 所有 type=title 的 `content.level` **都是 1**(mineru 不给真实层级)
- block schema = 嵌套 `{type, content: {<type>_content: [...], level?}, bbox}`,文字在 `content.<type>_content[*].content`

所以**层级分类全靠 anchor 正则**(`第N篇` / `第N章` / `第N节` / `【` / `(一)` / `1.`),这是所有书共性,不是本书 specific。

```python
# 本书 type 统计:
#   title=3521(全 level=1)/ paragraph=15128 / page_header=5297
#   list=1833 / table=655 / chart=55 / image=473
```

---

## 4. mineru OCR 硬错(本书 specific)

| bug | 现象 | 应对 |
|---|---|---|
| **正文 OCR 错字** | pg 1801 `第二章 鼾症` 被 OCR 成 `第二章 肝症`(只 `鼾→肝` 一字) | `PATCH_BODY_RAW_FIX` 在 strict_key 前替换。FUZZY ratio=0.8 < 0.85 阈值,降阈值会引入跨章误配 → 走精确 patch |
| **正文章名比 TOC 多了 (PET)** | pg 438 正文 `第十三章 ...（PET）...`,TOC 无 PET | FUZZY_TITLE 自动救(ratio=0.945) |
| **TOC 含 equation_inline** | 第十五篇 第四章 `α₁-抗胰蛋白酶缺乏症` 被 mineru 当 LaTeX `\alpha_{1}` 漏读 → 字典 `第四章 -抗胰蛋白酶缺乏症` | 字典/正文都缺,strict_key 自动对齐 ✓ |
| **TOC 尾页码括号内带空格** | `第一章 ... …… ( 3 )`(`(` 与 `3` 之间有空格) | TAIL_PAGE_RE 加 `[（(]?\s*\d+\s*[）)]?` |
| **TOC 全角括号尾页码** | `…… （2576）`(全角括号) | 同上 |

---

## 5. 节内子标题 — 4 种共存(本书首次同时启用 4 个 Pass)

全书 type=title 子标题分布:
- 第N节:**336 个**(Pass 1 anchor)
- 【】:**2003 个**(Pass 2 anchor,本书最多)
- (一)(二)中文括号:315 个(Pass 3 anchor)
- 1. 2. 阿拉伯+点:291 个(Pass 4 anchor,**本书首次启用**)
- 一、二、中文+顿号:39 个(本书极少,未启用)

层级关系(各章内):
```
章 (L0)
  └─ 第N节 (L1)              ← Pass 1
      └─ 【...】 (L2)          ← Pass 2
          └─ (一) (L3)         ← Pass 3
              └─ 1. (L4)       ← Pass 4  ← 本书首次
```

部分章节没有节(或节内无【】),Pass 顺序自动处理(第N节 / 【】 等不强制嵌套)。

---

## 6. Pass 策略

```python
PARENT_SPLIT_THRESHOLD = 6000      # Pass 1 第N节
PARENT_REFINE_THRESHOLD = 6000     # Pass 2 【】
PARENT_PASS3_THRESHOLD = 6000      # Pass 3 (一)
PARENT_PASS4_THRESHOLD = 6000      # Pass 4 1.  ← 本书首次启用
CHAPTER_ABSORB_THRESHOLD = 500
absorb_levels = (1, 2)             # 字典只 L1-L2,没 L3 参与
```

**Pass 启用必要性**(每开一 pass 后大父块剩余):

| Pass 状态 | 父块数 | > 5000 | > 10000 | max |
|---|---|---|---|---|
| 全关 | 163 | 143 (88%) | 80 | 98373 |
| +Pass 1 节 | 431 | 186 (43%) | 76 | 35178 |
| +Pass 2 【】 | 1150 | 111 (10%) | 14 | 28859 |
| +Pass 3 (一) | 1324 | 80 (6%) | 3 | 19991 |
| **+Pass 4 1.** | **1414** | **64 (4.5%)** | **0** | **9059** |

→ 4 个 Pass 全开后,剩 **5 个 > 6000 父块全部是真连贯论述**(无任何子标题可切),已是极限。

---

## 7. SOP-level 新发现(本书暴露)

### 7.1 Pass 4 = `1.` anchor

正则:`^\d+\s*[\.、]\s*\S` — 数字 + 点/顿号 + 非空白。
关键守门:在 `_is_numdot_subheading` 内必须排除 `表 1-2` / `图 1-3`(已通过 RE_TABLE_TITLE / RE_FIG_TITLE 排除)。

适用场景:【】内部纯列表段(`【肺癌分子靶向治疗药物】` 内部直接 `1. 表皮生长因子受体 (EGFR)...` `2. ALK 抑制剂...`),Pass 3 (一) 救不到。

### 7.2 PATCH_BODY_RAW_FIX 机制

正文 OCR 错字字典救不动时(短标题 1 字差 ratio < 0.85),在 `_collect_candidates` 内对 raw text 应用替换 list,**在 strict_key 之前**生效。比降 FUZZY 阈值更精准、不引入跨章误配。

```python
PATCH_BODY_RAW_FIX: list[tuple[str, str]] = [
    ("第二章 肝症", "第二章 鼾症"),
]
```

### 7.3 TAIL_PAGE_RE 全角括号 + 内空格

```python
TAIL_PAGE_RE = re.compile(r"(?:[…\.]{2,}|\s|/)\s*[（(]?\s*\d+\s*[）)]?\s*$")
```

3 处加强:
- `[（(]?` 兼容全角 `（`
- `\s*\d+\s*` 兼容括号内带空格 `( 3 )`
- `[）)]?` 兼容全角 `）`

### 7.4 BLACKLIST 归一化检查

`s in BLACKLIST or _normalize(s) in BLACKLIST`,救尾页码污染:`索引 (2634)` 归一化后 `索引` 命中 BLACKLIST,不走 unmatched。

### 7.5 mineru content_list_v2 顶层结构差异

```python
data = json.load(open(p))
# 协和呼吸:data 是 list of pages, page 是 list of blocks(嵌套)
# 神经内科学等:data 是 flat list of blocks,每个 block 有 page_idx 字段
```

flatten 时本书直接 `for pg, blocks in enumerate(data): for b in blocks`,前 5 本是 `for b in data: pg = b['page_idx']`。

---

## 8. Step 2 全量自动校验结果(`audit_step2.py`,§6.2.1 必跑)

| # | 检查 | 结果 |
|---|---|---|
| 1 | 位置唯一性 | 179/179 strong,0 真兜底 ✓ |
| 2 | 顺序单调性 | 0 错位 ✓ |
| 3 | 嵌套正确性 | 0 章错配父篇 ✓ |
| 4 | offset 一致性 | **179/179 全部 offset=17** ✓(印刷 pg 1 = mineru pg 18) |
| 5 | 真兜底列表 | 空 ✓ |

**179 个章节名全部精准定位到 mineru 实际位置,0 错配**。校验 4 是最强证据 — 全等 offset 是数学约束,任何 1 页错配立即暴露。

---

## 9. Step 2 fallback 触发情况

```
AS_IS:           3462
CHAP_MERGED:       16  (第N篇 + 篇名 独立 title 的合并)
PAGE_HEADER_FB:   174  (页头 fallback,实际全被 AS_IS 覆盖)
FUZZY_TITLE:        1  (PET 案例,ratio=0.945)
HARDCODE:           0
─────────────────────
最终 coverage:    100.0%
```

---

## 10. 数据结果(最终)

```
TOC 字典:    179 entries (L1=16 / L2=163)
正文匹配:    100.0% coverage
父块数(节):  179
父块数(切): 1414  (Pass 1+2+3+4 二次切 1385 个新增)
子块数:       4228
mismatch:    0  ✓

字符守恒:
  body kept:        3178320
  preface dropped:        0
  ref dropped:       430508
  expected:         2747812
  parents sum:      2747812  mismatch=0
  children sum:     2747812  mismatch=0

父块 size 字符:
  min=355 med=1435 p75=2598 p90=4223 p95=4875 p99=5859 max=9059

父块 分桶:
  [    0,       499]:     2  (0.1%)
  [  500,      1999]:   915  (64.7%)
  [ 2000,      4999]:   433  (30.6%)
  [ 5000,      9999]:    64  (4.5%)
  [10000,     19999]:     0  (0.0%)

子块 size 字符:
  min=201 med=616 p75=721 p90=898 p95=1033 p99=1280 max=1861

子块 分桶:
  [    0,       199]:     0  (0.0%)
  [  200,       499]:   658  (15.6%)
  [  500,       999]:  3329  (78.7%)
  [ 1000,      1999]:   241  (5.7%)
```

剩 5 个 > 6000 字父块全部是**真连贯论述**(无任何子标题可切),Pass 救不到:

| size | 父块 head | 卡点 |
|---|---|---|
| 9059 | `【COPD 稳定期治疗】` | 【】内纯论述 |
| 8723 | `（三）加强生命支持(ALS)` | (一) 内 ALS 抢救流程连贯讲解 |
| 8588 | `3. 治疗 PAH...` | 1. 内连贯论述 |
| 6361 | `【原因】` | 【】内短连贯文段 |
| 6244 | `第二节 纵隔炎` | 节内全连贯无子标题 |
