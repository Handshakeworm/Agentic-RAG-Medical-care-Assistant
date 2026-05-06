# 消化系统与疾病 第2版 — 本书特定笔记

通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md)。本文件只记跟 SOP 默认值的差异。

POC 完成日期:2026-05-06。最终结果:**318 父块 / 890 子块 / mismatch=0**。

---

## 1. 基本数据

- 出版信息:第二轮规划教材(8 年制,2022 第2版)
- 总页数:571(中等)
- 上下册:无
- TOC 字典 entries:**627**(L1=17 + L2=101(96 节 + 5 附) + L3=509)
- TOC 页范围:pg 15..30(共 16 页,中插评审委员名单 pg 7-13)
- 正文起点:pg 31(第一章 总论)
- 书末截断:首个 type=title `中英文名词对照索引` 在 pg 564,丢弃 381 blocks / 11646 字(索引)
- 字符守恒:body kept 565,008 - ref dropped 1,792 = expected 563,216 ✓

---

## 2. 章节结构 — 字典 L1-L3(无篇)

| 层级 | anchor | 数量 | 备注 |
|---|---|---|---|
| L1 | 第N章 | 17 | 1..17 连续,**无篇这一层** |
| L2 | 第N节 / 附: | 96+5=101 | 96 节 + 5 个 `附:xxx`(节同级,见 §3.1) |
| L3 | 一、 | 509 | TOC 把所有 一、 子标题全列出(类似内分泌/神经内科学风格) |
| L4 | (一) | **0(字典外)** | 正文 type=title,Pass 1 anchor |
| L5 | 1. | **0(字典外)** | 正文 type=title,Pass 2 anchor(本书关闭) |

---

## 3. 跟前面书 SOP 默认值的差异

### 3.1 `附:xxx` 作 L2 节同级 anchor

本书 5 处 `附:xxx`(节后挂的扩展章节,有几页内容):

```
附:小肠移植         pg 20  (第四章 小肠疾病)
附:大肠侧向发育型肿瘤 pg 22  (第六章 结、直肠和肛管疾病)
附:肝移植           pg 25  (第九章 肝胆疾病)
附:胆道疾病常见并发症 pg 26  (第九章 肝胆疾病)
附:胰腺移植与胰岛移植 pg 27  (第十章 胰腺疾病)
```

PATTERNS 加 L2 anchor `^附\s*[:：]\s*\S`,跟 第N节 同级。不进字典会漏定位 → 父块边界乱跨。

### 3.2 absorb_levels = (1, 2, 3)

字典含 L3 一、,所有层级都参与跨 section 吸收(626 → 278 baseline 有 348 个 < 500 字 一、 跨吸收;Pass 1 后 318 个父块,即 baseline absorb 节奏跟内分泌/神经内科学 类似)。

### 3.3 Pass 顺序自适应字典深度

本书字典深到 L3 一、,Pass 数比心血管少 1 层:

```python
# Pass 1 anchor:(一)(二) — 一、下属
RE_PAREN_CN = re.compile(r"^[（(][一二三四五六七八九十百]+[)）]")
# Pass 2 anchor:1. 2. 3. — (一)下属(本书关闭)
RE_NUMDOT = re.compile(r"^\d+\s*[\.、]\s*\S")
```

Pass 1 (一) 切完后:max 10372 → 6358(降 38.7%),只剩 1 个 6358 父块(纯叙事节内无 (一) 子标题)。user 拍板不开 Pass 2 1.,差额 358 字接受。

错版:把心血管 SOP `一、+(一)+1.` 直接套(Pass 1 写成 一、),会跟字典 L3 重复无效切;正版必须从字典外的下层(本书是 (一))起 Pass。

### 3.4 BODY_END = `中英文名词对照索引`

跟心血管同(非协和呼吸的"索引")。本书 pg 562/563 也有"本章小结/推荐阅读",但属于章末,不是全书末,由 RE_REF_MARKER 单独丢弃。

### 3.5 BLACKLIST 加多媒体资源行

```python
BLACKLIST = {
    ...
    "OSBC 目录",                         # 页眉残留(每 TOC 页有)
    "推荐阅读",                          # 章末 / 书末参考列表
    "数字资源 AR 互动",                  # 第二轮教材多媒体资源行
    "数字资源 AR 互动 | AR图 3-2、AR图 9-2、AR图 9-3",  # 完整版
}
```

---

## 4. mineru 数据特点(本书暴露的)

### 4.1 字典侧 OCR bug — `一、食管的发生` 误识为 `二、食管的发生`

TOC pg16 list block 第二章 第一节 下首项被 mineru OCR 错为"二、食管的发生",
导致字典里第一节下 一、 序变成 `[2,2,3,4]` 而非 `[1,2,3,4]`。正文 pg119 是
"一、食管的发生" 正确,所以**只改 TOC 行不改正文**:

```python
PATCH_LINE_PREPROCESS = [
    ("二、食管的发生", "一、食管的发生"),
]
```

### 4.2 正文侧 OCR bug — `肠瘘` 错识为 `肠瘿` / `肠瘦`

第四章 第五节 肠瘘:
- pg237 [4] type=title:`'第五节 肠 瘿'`(瘘→瘿)
- pg237 [14] type=page_header:`'第五节 肠 瘦'`(瘘→瘦)

字典里"第五节 肠瘘"对不上正文 → 第五节 missing → stack 卡在第四节 → 后续 27 个 一、 子项 disambig 失败(都跟切割边界无关,但数字难看)。

修法:`PATCH_BODY_RAW_FIX` 在 strict_key 前替换:

```python
PATCH_BODY_RAW_FIX = [
    ("第五节 肠 瘿", "第五节 肠瘘"),     # title
    ("第五节 肠 瘦", "第五节 肠瘘"),     # page_header
]
```

### 4.3 mineru `equation_inline` block 被 `_text_of` 跳过(已知限制,接受)

正文 pg99 [0] title `三、食管 24h pH- 阻抗值监测` 被 mineru 切成 3 个 item:

```python
[
  {'type': 'text', 'content': '三、食管  '},
  {'type': 'equation_inline', 'content': '24 \\mathrm{~h} \\mathrm{pH}'},  # 当成数学公式
  {'type': 'text', 'content': '-阻抗值监测'}
]
```

`_text_of` 只 join `type=='text'` 的 item,equation_inline 被丢 → 正文 raw 实际是 `三、食管  -阻抗值监测`,strict_key 对不上字典 `三、食管24hpH-阻抗值监测`。

**接受 1 个 L3 missing**:这是字典层级最细的 一、 子项,Pass 1 在正文用 `RE_PAREN_CN` 直接定位 (一) 切点,**不依赖字典 disambig 结果** → 对切割位置无影响。

修方向(未实施,跨书才有价值):改 `_text_of` 把 equation_inline / interline_equation 也算进字符串(如 `\\m...\\}}` 形式带回拼接),需在所有书上验证不引入新的字符差。

### 4.4 章首页 page_header 是简短格式 `03章` `14章` ...

mineru 在每个章首页(章名 type=title 那一页)的 page_header 抓的是简短格式 `03章` / `06富(OCR 错)` / `14章`,**对不上字典**。但**章后续页**(b+1 起)的 page_header 是全名格式 `第三章 胃、十二指肠疾病`,正常进 candidates 作 PAGE_HEADER_FB。校验 0 真兜底,17 章全 strong action 覆盖(8 AS_IS + 9 CHAP_MERGED)。

### 4.5 `第N章` 单独 type=title + 章名紧跟另一 type=title(9/17 章触发 CHAP_MERGED)

mineru 把"第三章 胃、十二指肠疾病"切成两 title 的情况:

| 章 | mineru pg | mineru blk |
|---|---|---|
| 第三章 / 第五章 / 第六章 / 第八章 / 第十一章 / 第十三章 / 第十五章 / 第十六章 / 第十七章 | 同页相邻 | b 跟 b+1 |

`TITLE_ALONE_RE = r"^第\s*\S{1,4}\s*[篇章]\s*$"` + 拼接 + skip_until 下一 title 即是 CHAP_MERGED action。

---

## 5. Step 1 字典 4 类硬校验

| # | 项 | 结果 |
|---|---|---|
| 1 | 基本计数 | L1=17 + L2=101 + L3=509 = 627 ✓ |
| 2 | unmatched / blacklist | unmatched=**0**(stitch + L3=一、 + L2=附 + 4 BLACKLIST 后) |
| 3a | 章序连续 | 1..17 连续 ✓ |
| 3b | 节序每章连续 | 0 非连续 ✓ |
| 3c | 一、序每节连续 | 0 非连续 ✓(PATCH 救 1 处 mineru OCR `二、食管的发生`→`一、食管的发生`) |
| 4 | strict_key 唯一性 | 302/333 unique;31 个常见节名重复(`一、概述` 47 / `四、临床表现` 28 等),正常 |

---

## 6. Step 2 全量 5 项硬校验(只看 L1/L2,L3 不影响切割)

| # | 项 | 结果 |
|---|---|---|
| 1 | L1 章 17 个全定位 | 8 AS_IS + 9 CHAP_MERGED + 17 PAGE_HEADER_FB 兜底,**0 真兜底** ✓ |
| 2 | L2 节 101 个全定位 | 0 missing(肠瘘 PATCH 救后) ✓ |
| 3 | page_header pg 错位检查 | 全部 +1 / +2(章后续页规则,正常) ✓ |
| 4 | 同页多 page_header(切碎) | 0 ✓ |
| 5 | strict_key 重复假象排除 | `第一节 概述` 重复 2 次造成的 +16 看起来像错位,实际逐章 diff=+2 正常 |

L3 一、 的 27 AMBIGUOUS / 1 missing 跟切割位置**无关**(Pass 1 用 RE_PAREN_CN 直接扫正文),不修。

---

## 7. Step 3 切分结果

```
PARENT_SPLIT_THRESHOLD: 6000  (Pass 1 (一))
PARENT_REFINE_THRESHOLD: 1e9  (Pass 2 1. 关闭 — user 决定不下钻)
书末截断:    381 blocks / 11646 字
参考文献丢弃: 4 blocks / 1792 字
父块数(节单位): 626
父块数(切):     318  (被二次切的 section: 50 个新增)
子块数:         890

父块 size 字符:
  min=459 med=1448 p75=2253 p90=3302 p95=4483 p99=5425 max=6358

父块 分桶:
  [    0,       499]:     1  (0.3%)
  [  500,      1999]:   213  (67.0%)
  [ 2000,      4999]:    93  (29.2%)
  [ 5000,      9999]:    11  (3.5%)
  [10000,     19999]:     0  (0.0%)

子块 size 字符:
  min=242 med=608 p75=683 p90=837 p95=938 p99=1151 max=1263

子块 分桶:
  [    0,       199]:     0  (0.0%)
  [  200,       499]:   112  (12.6%)
  [  500,       999]:   745  (83.7%)
  [ 1000,      1999]:    33  (3.7%)
```

字符守恒 mismatch=0 ✓。

### 7.1 唯一 1 个 6358 父块是真实数据

`三、胃、十二指肠的功能 >> 三、胃、十二指肠的功能` size=6358:节内是纯叙事文本,没有 (一) 子标题,Pass 1 找不到切点。差额 358 字小事,user 拍板接受。

---

## 8. SOP-level 新发现汇总

1. **`附:xxx` 作 L2 节同级 anchor** — 第二轮规划教材常见,跟 第N节 同级进字典(§3.1)
2. **Pass 数自适应字典深度** — 字典含 L3 一、 时 Pass 1 起步从 (一),不能直接套 SOP 模板的 `一、` 起 Pass 1(§3.3)
3. **`equation_inline` 被 `_text_of` 跳过** — mineru 把"24h pH"识别成数学公式,导致少数 L3 子项 strict_key 对不上;由于 L3 不影响切割,本书接受 1 个 missing 不修(§4.3,跨书才有价值)
4. **`数字资源 AR 互动` 入 BLACKLIST** — 新版规划教材的多媒体资源行(§3.5)
5. **L1/L2 是切割保证关键,L3 missing/AMBIGUOUS 都不影响 chunk** — Step 2 校验只盯 L1/L2 全覆盖即可,L3 由 Pass 1 正则在正文直接定位(§6 注脚)
