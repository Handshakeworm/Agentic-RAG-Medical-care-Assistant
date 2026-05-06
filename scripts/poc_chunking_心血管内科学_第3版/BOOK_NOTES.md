# 心血管内科学 第3版 — 本书特定笔记

通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md)。本文件只记跟 SOP 默认值的差异。

POC 完成日期:2026-05-06。最终结果:**451 父块 / 1498 子块 / mismatch=0**。

---

## 1. 基本数据

- 出版信息:韩雅玲 / 马长生主编,2022 第3版,人民卫生出版社研究生规划教材
- 总页数:790(中等)
- 上下册:无
- TOC 字典 entries:**239**(L1=11 + L2=45 + L3=183)
- TOC 页范围:pg 12..16 + pg 20..25(共 11 页,中间 pg 17-19 是评审委员会名单 / 前言)
- 正文起点:pg 26(第一篇前置目录章节列表) / 真章节内容 pg 28
- 书末截断:首个 type=title `中英文名词对照索引` 在 pg 771,丢弃 229 blocks / 4600 字(索引 + pg 781 影像库广告页)
- 字符守恒:body kept 1,158,329 字 - ref dropped 219,508 = expected 938,511 ✓

---

## 2. 章节结构 — 字典深字典 L1-L3

| 层级 | anchor | 数量 | 备注 |
|---|---|---|---|
| L1 | 第N篇 | 11 | 1..11 连续 |
| L2 | 第N章 | 45 | 跨篇连续 1..45 |
| L3 | 第N节 | 183 | 5 章 TOC 真不列节(第 14/17/19/27/40 章),其余章节内 1..N 连续 |
| L4 | 一、 | **0(字典外)** | 正文 type=title 580 个,Pass 1 anchor |
| L5 | (一) | **0(字典外)** | 正文 type=title 712 个,Pass 2 anchor |
| L6 | 1. | **0(字典外)** | 正文 type=title 111 个,Pass 3 anchor |

---

## 3. 跟协和呼吸 SOP 默认值的差异

### 3.1 Pass 顺序按本书实际子标题层级 — 不是协和呼吸 SOP 模板套用

协和呼吸 SOP 是 `节 → 【】 → (一) → 1.`(因协和呼吸字典浅、节内有【】结构)。

本书层级是 `章 → 节(已在字典) → 一、 → (一) → 1.`,**无【】**:

```python
# Pass 1 anchor:一、二、(中文+顿号)— 节内顶级子标题
RE_DUNHAO = re.compile(r"^[一二三四五六七八九十百]+、\s*\S")
# Pass 2 anchor:(一)(二) — 一、下属
RE_PAREN_CN = re.compile(r"^[（(][一二三四五六七八九十百]+[)）]")
# Pass 3 anchor:1. 2. 3. — (一)下属
RE_NUMDOT = re.compile(r"^\d+\s*[\.、]\s*\S")
```

直接套协和呼吸 4 Pass(节/【】/(一)/1.)结果错误:节已在字典不该当 Pass 1,【】 在本书 0 个无意义。错版父块 max=8029(5 个 > 6000),正版 max=5999(0 个 > 6000)。

### 3.2 absorb_levels = (1, 2, 3)

字典含 L3 节,空壳节也参与跨 section 吸收(同神经内科学/诊断学等深字典书)。

### 3.3 BODY_END = `中英文名词对照索引`

不是协和呼吸的 `索引`。本书 pg 717 也有"参考文献"但属于章末引用,不是全书末。

### 3.4 BLACKLIST 加广告页

```python
BLACKLIST = {
    ...
    "公众号登录 >>", "网站登录 >>", "进入中华临床影像库首页",
    "注册或登录", "临床影像库", "登录中华临床影像库步骤",
}
```

mineru 末尾 pg 781 抓了一堆"中华临床影像库"广告链接,污染 unmatched。

---

## 4. mineru 数据特点(本书暴露的)

### 4.1 节标题被切到下一行 paragraph(2 处)

```
原 TOC:第七节 心血管核素显像——功能与分子显像兼备 …… 120
mineru 切成两个 paragraph:
  block i:    "第七节 心血管核素显像——功能与"
  block i+1:  "分子显像兼备 120"
```

字典构建时漏读后半段 → strict_key 跟正文对不上。本书 2 处:
- 第一篇 第三章 第三节 心血管药品和器械市场现状**和趋势**
- 第三篇 第九章 第七节 心血管核素显像——功能与**分子显像兼备**

**修法**:在 build_toc_dict 加同页相邻行合并(stitch),前行无 TAIL_PAGE 尾 + 后行不是新 anchor → 合并。这是 SOP-level 升级(协和呼吸里关掉的、内分泌也没真做的)。

### 4.2 章/篇页眉错位 — 4 个真兜底章/篇

mineru 漏识别章首 type=title(也无 paragraph 章名),只 page_header 救场:

| 真兜底 | mineru 给的 page_header pg | 实际章首页 |
|---|---|---|
| 第十六章 右心衰竭 | pg 217 | pg 217 ✓(同页) |
| 第十八章 心房颤动 | pg 230 | pg 230 ✓ |
| 第二十八章 主动脉夹层 | pg 473 | pg 473 ✓ |
| 第八篇 心血管疾病危险因素管理 | pg 529 | **pg 528**(滞后 1 页) |

前 3 个章 page_header pg 跟章首一致,但 PAGE_HEADER_FB 用 blk_idx=0 排在 pg b0 之后(跟节首 `第一节 ...` 撞),sort 后排错位置 → stack 没更新到章 → 后续 disambiguation(`第一节 概述` 重复 strict_key)选错章。

第八篇 page_header 滞后 1 页 — 实际篇首在 pg 528(`第三十三章 高血压防治` 那页),page_header 落到 pg 529。

**修法 1**(SOP-level):PAGE_HEADER_FB 的 blk_idx 从 0 改为 **-1**,让排在该页 b0 之前。

**修法 2**(本书 specific):`PATCH_INJECT_CANDIDATES` 注入 `(528, -2, "第八篇 ...")` 救第八篇错位,且 dedup 时同 key 已有 HARDCODE 就丢弃 PAGE_HEADER_FB(防 (529, -1) 重复 strong)。

### 4.3 章首在 paragraph 而非 title(从 pg 22 起)

pg 21 的章名是 type=title,pg 22 起部分章名变成 type=paragraph(第十八章 / 第十九章 / 第二十章 / 第二十一章 等)。本书 _block_lines 同时读 title + paragraph + list 三种,字典构建不漏。Step 2 paragraph 分支只在有 TAIL_PAGE 尾时收(避免误匹配),所以正文区 paragraph 章名不进 candidates。

---

## 5. audit_step2 工具自身 bug 修(SOP-level)

### 5.1 chosen 按 full_path 去重(不是 strict_key)

字典 strict_key 重复 4 个(`第一节 概述` 4 章共有 / `第一节 基本概念` 2 章 / `第三节 流行病学` 2 章 / `第二节 诊断` 2 章)。

audit 校验 2/4 旧版 `chosen[strict_key]` 只能存 1 个 → 4 个章下的"第一节 概述"都用同一个 pg → 顺序单调误报、offset 误报。

**修法**:
- 校验 2/3 chosen 改按 `full_path` 去重(disambig 后 full_path 唯一)
- 校验 4 offset 跳过 strict_key 重复的 entries(declared 字典无法 disambig)

修后:6 顺序错位 → 0,3 嵌套错位 → 0(第八篇 inject 也修了),offset 偏离 → 0。

---

## 6. Step 1 字典 4 类硬校验

| # | 项 | 结果 |
|---|---|---|
| 1 | 基本计数 | L1=11 + L2=45 + L3=183 = 239 ✓ |
| 2 | unmatched / blacklist | unmatched=0(stitch 救 2 切碎节) / blacklist 3 次(目录 / 中英文索引尾页 / 广告) |
| 3a | 篇序连续 | 1..11 ✓ |
| 3b | 章序跨篇连续 | 1..45 ✓ |
| 3c | 节内序连续 | 40 章有节都连续,5 章 TOC 真不列节(肉眼 pg 21-22 确认) |
| 3d | strict_key 唯一性 | 233/239 unique,4 重复都是常见节名 |

---

## 7. Step 2 全量 5 项硬校验

| # | 项 | 结果 |
|---|---|---|
| 1 | 位置唯一性 | 233/233 全 strong,3 真兜底(第十六/十八/二十八章 mineru 漏识别章首,只 page_header) |
| 2 | 顺序单调性 | 0 错位 ✓ |
| 3 | 嵌套正确性 | 0 章错配父篇 ✓ |
| 4 | offset 一致性 | 211/211 全 offset=25(印刷 pg 1 = mineru pg 26)✓ |
| 5 | 真兜底列表 | 3 个,接受(mineru 真没识别) |

---

## 8. Step 3 切分结果

```
PARENT_SPLIT_THRESHOLD: 6000  (Pass 1 一、)
PARENT_REFINE_THRESHOLD: 6000 (Pass 2 (一))
PARENT_PASS3_THRESHOLD:  6000 (Pass 3 1.)
书末截断:    229 blocks / 4600 字
参考文献丢弃: 405 blocks / 219818 字
父块数(节):  233
父块数(切): 451  (被二次切的 section: 330 个新增)
子块数:       1498

父块 size 字符:
  min=41 med=1614 p75=2853 p90=4237 p95=5227 p99=5932 max=5999

父块 分桶:
  [    0,       499]:     2  (0.4%)   ← 41字(三)+ 363字第九节预后
  [  500,      1999]:   267  (59.2%)
  [ 2000,      4999]:   154  (34.1%)
  [ 5000,      9999]:    28  (6.2%)
  [10000,     19999]:     0  (0.0%)

子块 size 字符:
  min=41 med=609 p75=691 p90=810 p95=935 p99=1162 max=1499

子块 分桶:
  [    0,       199]:     1  (0.1%)   ← 那个 41 字父块的子块
  [  200,       499]:   255  (17.0%)
  [  500,       999]:  1187  (79.2%)
  [ 1000,      1999]:    55  (3.7%)
```

字符守恒 mismatch=0 ✓。

### 8.1 2 个 < 500 字父块都是真实数据,不强行合并

- `第六节 治疗中的探索与争议 >> (三) 药物治疗流程` size=41:教材本身就 9 字标题 + 1 句 32 字结论 + 1 张图。物理 prev 是 `4. 受体阻滞剂(level=3)` 但 (三) 跟 (二) 同级(level=2),`prev_level <= cur_level` 不成立(3 ≤ 2 False)→ `_merge_tiny_parents` 拒绝合并,正确。强行 merge prev 会让 4. 段尾巴错挂 (三),语义错配。
- `第九节 预后` size=363:某章末节,内容真就 363 字(教材"预后"小节短)。absorb_levels 不动末段(`i+1 < len(section_splits)` 阻止),也正确。

---

## 9. SOP-level 新发现汇总(等待回灌 METHODOLOGY)

1. **build_toc_dict 同页相邻行合并(stitch)** — 救 mineru paragraph 切碎节标题(§4.1)
2. **PAGE_HEADER_FB blk_idx = -1** — 让章/篇页眉排在该页 b0 之前,stack 先更新(§4.2 修法 1)
3. **PATCH_INJECT_CANDIDATES + HARDCODE dedup** — INJECT 时同 key 删 PAGE_HEADER_FB(§4.2 修法 2)
4. **audit chosen 按 full_path 去重 + strict_key 重复跳过 offset** — 修 audit 工具 bug(§5.1)
5. **Pass 顺序必须按本书实际子标题层级** — 不能直接套协和呼吸 SOP 模板,会少切大块(§3.1)
