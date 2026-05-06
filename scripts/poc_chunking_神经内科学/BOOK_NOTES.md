# 神经内科学 第2版 — 本书特定笔记

通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md)。本文件只记跟 SOP 默认值的差异。

POC 完成日期:2026-05-05。最终结果:**533 父块 / 1064 子块 / mismatch=0**。

---

## 1. 基本数据

- 出版信息:王伟主编,2017 第2版,人民卫生出版社研究生教材
- 总页数:504(中等规模,但目录就占 11 页)
- TOC 字典 entries:**700**(L1=10 + L2=38 + L3=165 + L4=487)
- TOC 页范围:pg 10..20(共 **11 页**,首本目录 > 10 页的书)
- 正文起点:pg 21
- 书末截断:无 marker

---

## 2. 章节结构 — L1-L4(同内分泌/内科学)

| 层级 | anchor | 数量 | 备注 |
|---|---|---|---|
| L1 | 第N篇 | 10 | 1..10 连续 |
| L2 | 第N章 | 38 | 跨篇 1-38 连续编号 |
| L3 | 第N节 + 附录:xxx | 163 + 2 | 附录挂 L3 同级,2 处 |
| L4 | 一、xxx | 487 | **本书数量最多**(前 4 本 L4 最多 224)|

**L4 多带来 2 个本书特有问题**:
- **strict_key 冲突严重**:17 个 章下都有"第一节 概述",1 个 strict_key 命中 17 个 entry → 修 `_real_start_positions` 用 stack 消歧 + 按 path 分组
- **空壳 L3**:L3 节标题之后 mineru 立即标 L4 title,L3 区间只剩 title 自己 → `CHAPTER_ABSORB_THRESHOLD` 扩展到 L3

---

## 3. 多页 TOC 11 页 — 多行换行严重

跟神经外科学(3 页)/ 诊断学(7 页)对比,本书 TOC 11 页 + 双栏窄列 + 多行换行严重:
- list item 内 `\n` 换行(`'二、病因与发病机制临床相关\n进展——TOAST 分型的\n广泛使用 …… 38'`)— `_normalize` 已处理
- **跨 block 切碎**:同条目被 mineru 切成 2 个相邻 paragraph(`'二、获得性因素——不同人群中的'` + `'差异 122'`)— 加同 page L3/L4 合并逻辑
- **跨 page 切碎** 1 处:第十六章第三节标题在 pg 14 末 + pg 15 首被切 — 走 PATCH_LINE_PREPROCESS
- 多种 seed + 双向延伸沿用神经外科学方案(阈值 5)

---

## 4. mineru OCR 硬错(本书 specific)

| bug | 现象 | 应对 |
|---|---|---|
| **章号 OCR 漏"第"字** | TOC pg 14 blk 24:`'十五章 急性炎性脱髓鞘性多发性 神经病 247'`(应为"第十五章") | `PATCH_LINE_PREPROCESS` 行级替换 |
| **跨 page L3 切碎** | `'第三节 与其他慢性获得性炎性'`(pg 14 末)+ `'周围神经病的关系 260'`(pg 15 首) | 同上 |
| **目录里全角标点 vs 正文半角** | 目录 `第六节 神经保护路在何方？`(全角 ?),正文 `?`(半角);多巴胺节中 `，` vs `,` | `strict_key` 加标点归一化(`？→?` `，→,` 等)|
| **正文无 type=title** | 第十章末附录"抗癫痫药物中英文名称及缩写对照"是整页 TABLE,mineru 没标 title | `PATCH_INJECT_CANDIDATES` 硬编码注入 (pg=215, blk=0) |

---

## 5. 节内子标题 — 4 种共存(本书首次)

全书 type=title 子标题分布:
- (一)(二)中文括号:463 个
- (1)(2)阿拉伯括号:340 个
- 1. 2. 阿拉伯+点:765 个
- 一、二、中文+顿号:732 个
- **【】完全 0 个**(同神经外科学)

层级关系(各节内):
```
节 (L0)
  └─ 一、 (L1, CN_NUM)        ← 节内主题切换 — Pass 1 anchor
      └─ (一) (L2, PAREN_CN)  ← 子项 — Pass 2 anchor
          └─ (1) / 1.         ← 列表(本书未启用 Pass 3)
```

---

## 6. Pass 策略

```python
PARENT_SPLIT_THRESHOLD = 6000      # 同神经外科学,baseline 后开
PARENT_REFINE_THRESHOLD = 6000     # Pass 2 (一) 启用,救 > 6000 大父块
CHAPTER_ABSORB_THRESHOLD = 500     # absorb_levels = (1, 2, 3) — L3 也参与
absorb_levels = (1, 2, 3)          # 本书 fix:字典含 L4 时 L3 不再是叶子
```

**Pass 2 启用必要性**:本书最大父块 baseline (Pass 2 关) = 14838 (`三、内科治疗`),Pass 2 开后降到 5994 < 6000 阈值。神经外科学 Pass 2 不开是因为最大才 6237。

---

## 7. SOP-level 新发现(本书暴露)

### 7.1 strict_key 冲突 → `_real_start_positions` 消歧 fix

**前 4 本没暴露**是因为它们章数少(神经外科 33 章 / 诊断 30 章)+ 节名相对具体。神经内科学 38 章 + 大量"第一节 概述"通用节名 → strict_key `第一节概述` 命中 17 个 entry。

**原代码 bug**:
```python
level, parent_path, dict_title = cands[0]      # 直接拿第 1 个
groups[(m["level"], m["title"])].append(m)     # 按 (level, title) 分组
```

17 处"第一节 概述"被合并成 1 个 group,strong selection 选 1 个,16 处位置丢失。

**fix**:用当前 stack 消歧 cands;groups 按完整 path 分组(返回值签名加 path)。

**前 4 本不需要 backport**(用户已校验位置正确)— 但内分泌/内科学(L1-L4)同样含 L4 entry,如果出现通用节名也会触发。建议 SOP 默认启用消歧。

### 7.2 L3 absorb 扩展

`CHAPTER_ABSORB_THRESHOLD = 500` 已做好.md 注释写的是"L1/L2/(L3) 短 section → 跨 section 吸收(L4 不参与)",`(L3)` 带括号表示"有 L4 时参与"。但前 4 本 chunk 脚本都写死 `level_now in (1, 2)`,L3 没参与。

**修正**:`absorb_levels = (1, 2, 3)`。本书 absorb 后 L3 空壳(size=6,只剩 L3 title)从 134 个 → 0,父块从 667 → 533。

### 7.3 前言丢弃

新增 stats 字段 `preface_dropped`:`flat[0:section_splits[0]]` 即 first section 起点之前的 block(本书是编者前言"刘鸣 谢鹏",2892 字 / 18 blocks)。前 4 本 first section 起点紧贴 body_start,没暴露这个缺口。

---

## 8. Step 2 fallback 触发情况

```
AS_IS:           1311
CHAP_MERGED:       10  (第N篇 + "概述"独立 title 的合并)
PAGE_HEADER_FB:    47  (mineru 漏标章/节 title 救助)
HARDCODE:           1  (附录抗癫痫缩写表 inject)
─────────────────────
最终 coverage:    100.0%
```

跟神经外科学(0 fallback)比,本书有更多 mineru 漏标(47 处),通过 PAGE_HEADER_FB 救回。

---

## 9. 数据结果(最终)

```
TOC 字典:    700 entries (L1=10 / L2=38 / L3=165 / L4=487)
正文匹配:    100.0% coverage
父块数(节):  688
父块数(切): 533  (Pass 1+2 二次切 28 个新增)
子块数:       1064
mismatch:    0  ✓

字符守恒:
  body kept:        821195
  preface dropped:    2892
  ref dropped:      176644
  expected:         641659
  parents sum:      641659  mismatch=0
  children sum:     641659  mismatch=0

父块 size 字符:
  min=21 med=849 p75=1538 p90=2639 p95=3750 p99=5274 max=5994

父块 分桶:
  [    0,       499]:   142  (26.6%)
  [  500,      1999]:   303  (56.8%)
  [ 2000,      4999]:    80  (15.0%)
  [ 5000,      9999]:     8  (1.5%)
  [10000,     19999]:     0  (0.0%)

子块 size 字符:
  min=21 med=598 p75=699 p90=869 p95=957 p99=1161 max=1647

子块 分桶:
  [    0,       199]:    39  (3.7%)
  [  200,       499]:   234  (22.0%)
  [  500,       999]:   744  (69.9%)
  [ 1000,      1999]:    47  (4.4%)
```

子块 < 200 字 39 个(3.7%)— 比前 4 本偏多(0~3.9%),根因是 mineru 表格 OCR 返回 0 字 + 研究生教材 L4 子节叙事简练(总论/引表型 L4 多)。**切分位置实测正确**(抽样 size=21/54/56/108 4 个父块都核实归属无误)。
