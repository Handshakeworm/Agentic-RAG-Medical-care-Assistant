# 骨科 — 本书特定笔记

通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md)。本文件只记跟 SOP 默认值的差异。

POC 完成日期:2026-05-06。最终结果:**1076 父块 / 2165 子块 / mismatch=0**。

---

## 1. 基本数据

- 出版信息:研究生规划教材(第三轮),人民卫生出版社
- 总页数:1445(11 本里第二大,仅次于泌尿外科)
- 上下册:无
- TOC 字典 entries:**1293**(L1=6 + L2=51 + L3=253 + L4=983)— 11 本里第三大字典(神经内科 700,但本书 L4=983 比神内 487 更深)
- TOC 页范围:pg 11..43(共 33 页,**11 本最长**)
- 正文起点:pg 44(`第一篇` + `骨科学基础` alone)
- 书末截断:**paragraph 形态** `索引` 在 pg 1444 末尾混乱区,丢弃 0 blocks(混乱区 8 字 preface 算 preface_dropped)
- 字符守恒:body kept 1,338,541 - ref dropped 104,363 - preface dropped 8 = expected 1,234,170 ✓

---

## 2. 章节结构 — 字典 L1-L4(深含 一、)

| 层级 | anchor | 数量 | 备注 |
|---|---|---|---|
| L1 | 第N篇 | 6 | 1..6 连续 |
| L2 | 第N章 | 51 | 跨篇连续(第一篇 1..9 / 第二篇 1..8 / 第三篇 1..15 / 第四篇 1..11 / 第五篇 1..2 / 第六篇 1..6) |
| L3 | 第N节 / 附: | 252 + 1 = 253 | 1 个"附:关节切开引流术"(节同级) |
| L4 | 一、 + 硬编码 | 981 + 2 = 983 | 2 个无编号节子项硬编码(`非骨化性纤维瘤`/`骨内脂肪瘤`) |

---

## 3. 跟前面书 SOP 默认值的差异

### 3.1 `_find_body_end` 兼容 paragraph 形态(SOP-level 升级)

本书 BODY_END marker `索引` 在 pg 1444 是 **type=paragraph**(末尾混乱区),原 SOP 只匹配 type=title 漏掉。修法:

```python
def _find_body_end(flat):
    for i, b in enumerate(flat):
        if b["type"] in ("title", "paragraph") and RE_BODY_END.match(b["text"].strip()):
            return i
    return len(flat)
```

**SOP-level 升级**:之前书的 BODY_END marker 都在 type=title(`中英文名词对照索引` 等),本书首次出现 paragraph 形态。改后向前兼容。

### 3.2 `PATCH_FORCE_LEVEL` 硬编码字典 entries(SOP-level 升级)

本书 TOC 有 2 处节内唯一子项,书本省略了 `一、` 编号:

```
第三节 纤维性肿瘤 / 1255
非骨化性纤维瘤 / 1255       ← 无 一、,原 PATTERN 不匹配
第四节 脂肪源肿瘤 / 1257
骨内脂肪瘤 / 1257           ← 同上
```

之前 PATCH_LINE_PREPROCESS 给 TOC 加 `一、` 是错向(正文也无 `一、`,字典加反而对不上)。**新机制 `PATCH_FORCE_LEVEL: dict[str, int]`** — strict_key 命中时强行打 level 标签:

```python
PATCH_FORCE_LEVEL = {
    "非骨化性纤维瘤": 4,
    "骨内脂肪瘤": 4,
}
```

`_classify` 函数在 PATTERNS 全部失败时,fallback 检查 PATCH_FORCE_LEVEL 命中。这样字典侧和正文侧都用同样的 strict_key 命中,不需要任何修改。

**SOP-level 升级**:之前的 PATCH 都是改 raw 字符串(LINE_PREPROCESS / BODY_RAW_FIX),无法处理"无 anchor 但要纳入字典"的情况。

### 3.3 Pass 1 = (一) + 【】 合并 anchor(本书 specific)

本书子标题层级**混用两种结构**:
- 多数节用 `(一) (二) (三)`(脊柱/上肢创伤/骨盆等)
- 感染章/化脓性骨髓炎用 `【临床表现】 / 【辅助检查】 / 【治疗】`(医学专科节风格)

如果只用 `(一)`,感染章切不开;只用 `【】`,其他节切不开。修法:Pass 1 predicate 接受两种 anchor 都触发:

```python
def _is_paren_or_bracket_subheading(text, ...):
    return bool(RE_PAREN_CN.match(s) or RE_BRACKET.match(s))
```

Pass 2 = `1.`(数字+点)。**3 pass 全开** — Pass 1 (一)+【】 合并 / Pass 2 1.,不开 Pass 3。

### 3.4 absorb_levels = (1, 2, 3)

字典含 L4 → L3 节不是叶子 → L1/L2/L3 短 section 都参与跨吸收(神经内科同款)。L4 是叶子不参与。

### 3.5 PATCH_BODY_RAW_FIX 处理英文术语括号

TOC 第十二章 脊柱畸形下 2 个 L4 子项,字典侧无英文术语,正文侧带 `(English term)`:

```python
PATCH_BODY_RAW_FIX = [
    # mineru OCR 错字:踇外翻(hallux valgus)字典识"蹇",正文识"蹮"
    ("第一节 蹮外翻", "第一节 蹇外翻"),
    # 正文 L4 子标题末尾带英文术语括号
    ("二、先天性脊柱畸形(congenital spine malformation)", "二、先天性脊柱畸形"),
    ("二、强直性脊柱炎合并胸腰椎或腰椎后凸畸形(Ankylosing spondylitis)",
     "二、强直性脊柱炎合并胸腰椎或腰椎后凸畸形"),
]
```

### 3.6 单 seed `_detect_toc_pages` 需向外延伸

本书只 pg 11 一个 seed(`title='目录'`),pg 12-43 后续 33 页 TOC 无 page_header / 无 title='目录'。

**恢复原 SOP 双向延伸**(`max(extended)+1` 起向后 + `min(extended)-1` 起向前)— 跟胸心外科"双 seed 不向外延伸"的修法**相反**。这两种情况是互斥的:双 seed 已覆盖时不要延伸,单 seed 时必须延伸。

**SOP 默认仍是双向延伸**(救单 seed 大书),胸心外科是 specific 选项。

---

## 4. mineru 数据特点

### 4.1 末尾混乱区 pg 1437-1444 — type=paragraph 全书章节简表

pg 1437 后(可能更早),mineru 抓出大量 **type=paragraph** 形态的"第N章 / 第N节 / 一、xxx" 列表(无 title,无内容):

```
pg 1442:
  paragraph: '第七节 骨髓瘤'
  paragraph: '一、概述'
  paragraph: '二、临床表现及治疗'
pg 1443:
  paragraph: '第二节 良性骨肿瘤的手术'
  paragraph: '治疗 '          ← 标题被切碎
pg 1444:
  paragraph: '索引'           ← 真 BODY_END
```

这些 paragraph 形态的"假 TOC" mineru 没识别 type=title,但 strict_key 跟字典 entries 命中 → `_collect_candidates` 在 paragraph 分支只收"带尾页码"的(`TAIL_PAGE_HAS_NUM_RE`),所以这些**不带页码的混乱 paragraph 不进 candidates**。安全。

### 4.2 单 seed TOC + 33 页延伸

mineru 只在 pg 11 标 `title='目录'`,后续 32 页 TOC 既无 page_header 也无 title='目录'。靠 anchor count >= 5 双向延伸救。

### 4.3 OCR 错字 + 英文术语括号

- pg 38 TOC:`第一节 踇外翻` → mineru 识为 `第一节 蹇外翻`(罕见字 OCR 错)
- pg 1197 正文:同位置 → mineru 识为 `第一节 蹮外翻`(另一罕见字 OCR 错)
- 修法:PATCH_BODY_RAW_FIX 把正文统一到字典写法
- pg 883 / pg 898 正文 L4 标题尾带英文术语括号 → PATCH_BODY_RAW_FIX 去掉

---

## 5. Step 1 字典 4 类硬校验

| # | 项 | 结果 |
|---|---|---|
| 1 | 基本计数 | L1=6 + L2=51 + L3=253 + L4=983 = 1293 ✓ |
| 2 | unmatched / blacklist | unmatched=**0**(撤回 LINE_PREPROCESS 后,PATCH_FORCE_LEVEL 救 2 条)/ blacklist 2 次(目录页眉 / 索引尾页) |
| 3 | 篇序连续 | 1..6 ✓ |
| 4 | 每篇章序连续 | 6 篇全连续 ✓ |
| 5 | 每章节序连续 | 51 章全有节,全连续 ✓ |
| 6 | 一、序每节连续 | 233 节有 一、 子项,**全连续** ✓ |
| 7 | strict_key 唯一性 | 938/1007 unique;69 重复(`一、概述` 31 / `一、病因` 28 等通用名跨节共享) |

---

## 6. Step 2 全量校验

| 项 | 结果 |
|---|---|
| L1 篇 6/6 | 全 strong action(部分篇 14-17 个 AS_IS,因末尾混乱区也抓了篇名 type=title) |
| L2 章 51/51 | 全 strong,**0 真兜底** ✓ |
| L3 节 253/253 | **全覆盖**(蹇外翻 PATCH 救 1 个) |
| L4 一、 983/983 | **全覆盖**(PATCH_FORCE_LEVEL 救 2 个 + PATCH_BODY_RAW_FIX 救 2 个英文括号) |
| Status | OK 1156 / DISAMBIG 270 / **AMBIGUOUS 94** ← L4 通用名 disambig 失败,不影响切割 |
| Action | AS_IS 1454 + PAGE_HEADER_FB 57 + CHAP_MERGED 5 + FUZZY_TITLE 1 |

L4 AMBIGUOUS 94 都是 `一、概述` 等通用名(31 处)跨节共享,L4 装饰性不影响 chunk 边界。

---

## 7. Step 3 切分结果

```
PARENT_SPLIT_THRESHOLD: 6000  (Pass 1 (一)+【】 合并)
PARENT_REFINE_THRESHOLD: 6000 (Pass 2 1.)
PARENT_PASS3_THRESHOLD:  1e9  (未用)
书末截断:    0 blocks(BODY_END 在 paragraph 索引)
前言丢弃:     8 字(pg 44 第一篇 alone)
参考文献丢弃: 104363 字(节末)
父块数(节单位): 1268
父块数(切):    1076  (被二次切的 section: 108 个新增)
子块数:       2165

父块 size 字符:
  min=13 med=733 p75=1549 p90=2792 p95=3780 p99=5198 max=6556

父块 分桶:
  [    0,       499]:   390  (36.2%)  ← L4 短叙事,接受(见 §7.1)
  [  500,      1999]:   499  (46.4%)
  [ 2000,      4999]:   168  (15.6%)
  [ 5000,      9999]:    19  (1.8%)
  [10000,     19999]:     0  (0.0%)

子块 size 字符:
  min=13 med=589 p75=660 p90=812 p95=941 p99=1144 max=1329

子块 分桶:
  [    0,       199]:   152  (7.0%)   ← 跟 390 短父块同根因,接受
  [  200,       499]:   452  (20.9%)
  [  500,       999]:  1485  (68.6%)
  [ 1000,      1999]:    76  (3.5%)
```

字符守恒 mismatch=0 ✓。

### 7.1 390 个 < 500 父块 + 152 个 < 200 子块都是真实数据,接受

按 pg 分布:
- pg 44-199(创伤外科):42(占 11%)
- pg 200-999(脊柱/上肢):74(占 19%)
- **pg 1000-1399(第四篇 骨病):274(占 70%)**
- pg 1400+(末尾混乱区):0

第四篇 骨病的章节风格是"每个疾病简明陈述",L4 子节标题 + 1-3 句话(50-500 字)就一段。例如:

```
pg 1002 size=408  一、病因
pg 1002 size=195  二、临床表现
pg 1007 size= 52  四、诊断       ← 真实简短 L4 段
```

这是**教材作者刻意选的最细语义边界**。两种修法:
- A:`absorb_levels=(1,2,3,4)` 让 L4 也跨吸收。但会让 `一、病因 + 二、临床表现 + 三、辅助检查 + 四、诊断 + 五、治疗` 累积到 500+ 后并入"五、治疗"父块,标题挂错(内容是五段拼接)
- B:接受 — 语义边界比字数更重要

**user 拍板 B(2026-05-06)**:跟神经内科 142 个 < 500 父块同处置,L4 装饰性 + 简短叙事是真实数据。

### 7.2 唯一 1 个 6556 父块是真实数据

`一、青少年型特发性脊柱侧凸 >> 2. 手术治疗` size=6556:Pass 2 切到 `2.` 后这段还是 6556 字,内部纯叙事,无更细 anchor。差额 556 字接受。

---

## 8. SOP-level 新发现汇总

1. **`_find_body_end` 兼容 paragraph**(§3.1):BODY_END marker 可在 paragraph 形态(本书 pg 1444 末尾混乱区 paragraph "索引")
2. **`PATCH_FORCE_LEVEL` 硬编码字典 entries 机制**(§3.2):strict_key → level 直接打标签,救"无 anchor 前缀但要纳入字典"的 TOC 行(本书 2 处节内唯一子项无编号)
3. **Pass 1 anchor 合并 `(一) + 【】`**(本书 specific,§3.3):教材混用两种子标题结构(感染章用【】,其他多 (一))。诊断学/内科 9 版用单【】,本书首次合并

## 9. 可能的全局 SOP 演化(待后续书验证)

- **混用 (一) 和 【】 子标题**:本书首次,可能在外科/感染相关教材常见。如果泌尿外科也是,可考虑改 SOP 默认 Pass 1 anchor 为 `(一) + 【】` 合并
- **末尾"全书章节简表"型混乱区**(§4.1):mineru 在某些书末尾抓出 paragraph 形态的"章节列表回顾",看后续是否常见
