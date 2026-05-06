# 胸心外科 — 本书特定笔记

通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md)。本文件只记跟 SOP 默认值的差异。

POC 完成日期:2026-05-06。最终结果:**314 父块 / 1035 子块 / mismatch=0**。

---

## 1. 基本数据

- 出版信息:研究生规划教材(第三轮),人民卫生出版社
- 总页数:541
- 上下册:无
- TOC 字典 entries:**91**(L1=2 + L2=15 + L3=74)
- TOC 页范围:pg 17..18(2 页主目录,中插评审委员名单 + 主编/副主编简介 + 前言 占 pg 3-16)
- 正文起点:pg 21(第一章 胸外伤)— 中间 pg 19 是篇内章节简表 / pg 20 空
- 书末截断:首个 type=title `中英文名词对照索引` 在 pg 513,丢弃 381 blocks / 11646 字(索引 + pg 524 广告页)
- 字符守恒:body kept 858,076 - ref dropped 204,719 = expected 653,357 ✓

---

## 2. 章节结构 — 字典 L1-L3

| 层级 | anchor | 数量 | 备注 |
|---|---|---|---|
| L1 | 第N篇 | 2 | 第一篇 胸外科学 / 第二篇 心血管外科 |
| L2 | 第N章 | 15 | 跨篇连续(第一篇 1-8,第二篇 1-7) |
| L3 | 第N节 | 74 | 14/15 章有节(第三章 冠心病外科治疗无节,跟 TOC 一致) |
| L4 | 一、 | **0(字典外)** | 正文 type=title,Pass 1 anchor |
| L5 | (一) | **0(字典外)** | 正文 type=title,Pass 2 anchor |
| L6 | 1. | **0(字典外)** | 正文 type=title,Pass 3 anchor |

字典深度跟心血管同结构(篇/章/节,无 一、 在字典)。

---

## 3. 跟前面书 SOP 默认值的差异

### 3.1 `_detect_toc_pages` 不向外延伸 — pg 19 篇内章节简表防误纳入

本书 pg 17 (`title='目录'`) + pg 18 (`page_header='目录'`) 双 seed 已经**双端覆盖完整 TOC 范围**。原 SOP 在 seeds 范围外向后延伸(神经外科学末页才标 page_header 时需要),会把 pg 19 anchor count=9 ≥5 拉进来:

pg 19 是"第一篇 + 篇内 8 章名"简表(每行无尾页码),被当 TOC 处理后会重复抓 1 篇 + 8 章,导致字典 L1=3 / L2=23 错值。

**修法**:删掉向外延伸,只在 seeds 间填充间隙(`min(seeds)..max(seeds)`)。这是本书 specific 的简化 — 单 seed / 末页才标 page_header 的书仍需向外延伸,**不做 SOP 全局升级**。

```python
def _detect_toc_pages(data: list) -> list[int]:
    seeds = [i for i, p in enumerate(data) if _is_toc_page(p)]
    if not seeds: return []
    extended = set(seeds)
    for i in range(min(seeds), max(seeds) + 1):  # 只填充
        if i in extended: continue
        if _page_anchor_count(data[i]) >= 5:
            extended.add(i)
    return sorted(extended)
```

### 3.2 `TAIL_PAGE_RE` 兼容单个 `…` 字符

原 SOP `[…\.]{2,}` 要至少 2 个省略号或英文点,本书 pg 17 第四章第二节 mineru OCR 把尾页码识别为 `…67`(单字符 `…` U+2026 后直接接数字)→ `_normalize` 漏处理 → 字典里 strict_key 末尾带"…67" → 跟正文对不上 → Missing 1。

**修法**:`[…\.]+`(改 `{2,}` 为 `+`)。后置 anchor `\s*\d+\s*$` 已限定行末数字结尾,不会误中"5. 治疗"等正常文本。SOP-level 升级。

### 3.3 absorb_levels = (1, 2, 3)

字典含 L3 节,空壳节也参与跨 section 吸收(同心血管/消化等含节字典书)。

### 3.4 Pass 全开 — 字典浅 + 体量适中,大段叙事章节多

字典 91 entries(浅),正文 858K 字,平均每节 7000 字。baseline 下:
- 父块 max = 31760(第一节 心脏移植)
- 48 个父块 > 6000

Pass 1+2+3 全开后 max 6221(剩 1 个超阈值,差额 221 字接受)。这是本书与心血管(字典 239)/ 消化(字典 627) 的关键差异 — 字典越浅越依赖 Pass。

### 3.5 BLACKLIST 加广告页

```python
BLACKLIST = {
    ..., "公众号登录 >>", "网站登录 >>", "进入中华临床影像库首页",
    "注册或登录", "临床影像库", "登录中华临床影像库步骤",
}
```

跟心血管同(同出版社 + 同营销资源)。

---

## 4. mineru 数据特点(本书暴露的)

### 4.1 pg 19 篇内章节简表(无尾页码,易污染 TOC)

mineru 把每篇起始页(pg 19/20...)抓成"篇内章节简表"——纯章名列表,无尾页码:

```
pg 19:
  type=title:     第一篇 胸外科学
  type=paragraph: 第一章 胸外伤
  type=paragraph: 第二章 胸壁、胸膜疾病
  ...
```

由于这些行能匹配 PATTERNS,被 `_detect_toc_pages` 向外延伸误吸收。修法见 §3.1。

### 4.2 mineru 章首识别意外好 — 0 真兜底

15 章全部以 `type=title` 形式被 mineru 正确识别(0 CHAP_MERGED / 0 FUZZY / 17 个 PAGE_HEADER_FB 是冗余安全网)。比心血管(3 真兜底)/ 消化(0 真兜底但 9 个 CHAP_MERGED)都干净,**无需 PATCH_INJECT**。

### 4.3 节末"参考文献"占比 24%(204K 字)

跟心血管(219K)/内科 9 版(中等)同档,每节末挂"参考文献" + 段落引用列表,RE_REF_MARKER 直接丢弃。

---

## 5. Step 1 字典 4 类硬校验

| # | 项 | 结果 |
|---|---|---|
| 1 | 基本计数 | L1=2 + L2=15 + L3=74 = 91 ✓ |
| 2 | unmatched / blacklist | unmatched=**0** / blacklist 3 次(目录页眉 / 索引尾页 / 广告) |
| 3a | 篇序连续 | 1..2 ✓ |
| 3b | 章序每篇连续 | 第一篇 1..8 / 第二篇 1..7 ✓ |
| 3c | 节序每章连续 | 14 章有节,全连续 ✓ |
| 4 | strict_key 唯一性 | 83/87 unique;4 个跨章重复(`第一节 病因认知...` 跨第四/六章 等),disambig 阶段处理 |

---

## 6. Step 2 全量校验(L1/L2 切割边界关键)

| 项 | 结果 |
|---|---|
| L1 篇 2/2 | 全 AS_IS,各带 1 个 PAGE_HEADER_FB ✓ |
| L2 章 15/15 | 全 strong action(AS_IS) ✓ |
| 真兜底(只 fb 无 strong) | **0** ✓ |
| Missing | **0**(TAIL_PAGE_RE 修后) ✓ |
| AMBIGUOUS | **0** ✓ |
| L1+L2 action 分布 | 17 AS_IS + 17 PAGE_HEADER_FB(冗余安全网) |

---

## 7. Step 3 切分结果

```
PARENT_SPLIT_THRESHOLD: 6000  (Pass 1 一、)
PARENT_REFINE_THRESHOLD: 6000 (Pass 2 (一))
PARENT_PASS3_THRESHOLD:  6000 (Pass 3 1.)
书末截断:    381 blocks / 11646 字
参考文献丢弃: 427 blocks / 204719 字
父块数(节单位): 91
父块数(切):     314  (被二次切的 section: 278 个新增)
子块数:        1035

父块 size 字符:
  min=244 med=1595 p75=3056 p90=4314 p95=4958 p99=5949 max=6221

父块 分桶:
  [    0,       499]:     2  (0.6%)
  [  500,      1999]:   185  (58.9%)
  [ 2000,      4999]:   112  (35.7%)
  [ 5000,      9999]:    15  (4.8%)
  [10000,     19999]:     0  (0.0%)

子块 size 字符:
  min=206 med=610 p75=703 p90=829 p95=945 p99=1159 max=1797

子块 分桶:
  [    0,       199]:     0  (0.0%)
  [  200,       499]:   175  (16.9%)
  [  500,       999]:   824  (79.6%)
  [ 1000,      1999]:    36  (3.5%)
```

字符守恒 mismatch=0 ✓。

### 7.1 唯一 1 个 6221 父块是真实数据

`第二节 机械循环辅助 >> (一) MCSD 分类` size=6221:这个 (一) 子段内是纯叙事/列表文本,**没有 1. 子标题**,Pass 3 找不到切点。差额 221 字小事。

---

## 8. SOP-level 新发现汇总

1. **`TAIL_PAGE_RE` 单 `…` 兼容**(§3.2 升级):`[…\.]{2,}` → `[…\.]+`,救 mineru 单字符 `…` 识别情况
2. **`_detect_toc_pages` 不向外延伸**(本书 specific,§3.1):seeds 已双端覆盖时,只填充间隙;否则会把篇内章节简表误纳入。**不做 SOP 全局升级**(单 seed 书还需要向外延伸)
3. **mineru 章首识别质量随出版年代提升**(观察):本书(第三轮研究生教材)15/15 章直接 type=title,跟早期教材(协和呼吸/心血管 3 真兜底)对比明显
