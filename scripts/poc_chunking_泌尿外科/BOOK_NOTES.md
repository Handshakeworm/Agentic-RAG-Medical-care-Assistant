# 现代泌尿外科学 — 本书特定笔记

通用方法论见 [`scripts/METHODOLOGY.md`](../METHODOLOGY.md)。本文件只记跟 SOP 默认值的差异。

POC 完成日期:2026-05-06。最终结果:**2125 父块 / 3700 子块 / mismatch=0**。
**12 本系列收官之作**(剩用药指南是药典结构走 C2.5 独立任务,见 memory)。

---

## 1. 基本数据

- 出版信息:大型专科参考书《现代泌尿外科学》
- 总页数:**1681(12 本最大)**
- 上下册:无(单卷,但体量极大)
- TOC 字典 entries:**2539(12 本最大)**(L1=14 + L2=93 + L3=504 + L4=1928)
- TOC 页范围:pg 17..48(共 32 页),所有 TOC 页 page_header='目录' 多 seed 已覆盖
- 正文起点:pg 51(`第一篇` alone + 篇名)
- 书末截断:pg 1678 type=title `索引`,丢弃 353 blocks / 3729 字
- 字符守恒:body kept 2,278,345 - ref dropped 175,381 - preface dropped 7 = expected 2,102,957 ✓

---

## 2. 章节结构 — 字典 L1-L4(深含 一、)

| 层级 | anchor | 数量 | 备注 |
|---|---|---|---|
| L1 | 第N篇 | **14**(12 本最多) | 1..14 连续 |
| L2 | 第N章 | **93**(12 本最多) | 跨篇 1..93 全连续 |
| L3 | 第N节 | 504 | 跟正文 504 节一致 |
| L4 | 一、 | 1928 | TOC 列出全部 一、 子项(含 14 处英文医学术语行 + 1 处异体字) |

---

## 3. 跟前面书 SOP 默认值的差异(本书暴露 5 个 SOP-level 升级)

### 3.1 跨页 stitch(SOP-level 升级)

之前 stitch 只在同页内合并,本书发现 mineru 跨页切碎多处 list_items:

```
pg 38 [27] list[5]: "六、中医药治疗前列腺癌的临床疗效"  ← 无尾页码
pg 39 [0]:          "与评价……1194"                  ← 后半 mineru 误识为下页 [0] paragraph
```

修法:把 stitch 触发条件 `raw_lines[i+1][0] == pg` 改为 `in (pg, pg+1)`,允许下一页延续(限 1 页防误合并)。

```python
while (i + 1 < len(raw_lines)
       and raw_lines[i + 1][0] in (pg, pg + 1)        # 同页 + 下一页
       and not TAIL_PAGE_RE.search(line)
       and _classify(raw_lines[i + 1][1].strip()) is None):
    line = (line + " " + raw_lines[i + 1][1]).strip()
    i += 1
```

本书救 2 处跨页切碎(pg 38→39 / pg 46→47)。

### 3.2 SPLIT_ANCHOR 加"数字尾页码后紧跟 X、"规则(SOP-level 升级)

mineru 偶尔把多个 list items 错抓成 1 个 paragraph:

```
pg 32 [39] paragraph: '一、流行病学研究 837二、IC 症状患病率……838'
                                  ↑ 数字 + X、 紧贴
```

SPLIT_ANCHOR 加新规则:

```python
SPLIT_ANCHOR = re.compile(
    r"(?=第\s*\S{1,4}\s*[篇章节]\s)"
    r"|(?<=\d)(?=第\s*\S{1,4}\s*[篇章节])"
    r"|(?<=\d)(?=[一二三四五六七八九十百]+、)"  # 数字尾页码后紧跟"X、"另起一项
)
```

本书救 1 处。

### 3.3 `_text_of` 兼容 equation_inline + LaTeX 希腊字母转 Unicode(SOP-level 升级)

mineru 把希腊字母 α / β 等抓成 `equation_inline` LaTeX 形式:

```python
正文 pg 1132 title items:
  [0] {'type': 'text', 'content': '二、  '}
  [1] {'type': 'equation_inline', 'content': '5\\alpha'}    # α 被抓成 LaTeX
  [2] {'type': 'text', 'content': ' -还原酶抑制剂'}
```

原 `_text_of` 跳过 equation_inline → 正文 raw 丢失 α(消化系统 24h pH 同问题)。修法:

```python
_LATEX_GREEK = {r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", ...}

def _text_of(items):
    out = []
    for s in items:
        t = s.get("type")
        if t == "text":
            out.append(s.get("content", ""))
        elif t == "equation_inline":
            content = s.get("content", "")
            for tex, uni in _LATEX_GREEK.items():
                content = content.replace(tex, uni)
            out.append(content)
    return "".join(out)
```

本书救 5 处含 α 的 L4(`5α-还原酶` / `IFN-α-2b-BCG` / `干扰素-α` 等)。**回灌消化系统能救它的 1 处 missing**。

### 3.4 PATCH 体系(已有 + 本书复用 + 1 处反向修)

- `PATCH_BODY_RAW_FIX`(本书 3 处):
  - `第五节 肾盞憩室 → 第五节 肾盏憩室`(异体字 盞→盏)
  - `四、IV级瘤栓 → 四、Ⅳ级瘤栓`(西文字母 → 罗马数字符号)
  - `三、Mayo II级 → 三、Mayo Ⅱ级`(同上)
- `PATCH_LINE_PREPROCESS`(本书 1 处,反向修字典):
  - 字典 OCR `三、无辜症 1312`(辜 U+8F9C)→ `三、无睾症 1312`(睾 U+777E,正文是对的,字典 OCR 错)

### 3.5 接受 L4 短父块原则(沿用骨科)

874 个 < 500 父块(41.1%,12 本最多)— L4 短叙事(每个疾病子项简明陈述,大型专科参考书风格),按骨科原则接受(语义边界 > 字数)。

---

## 4. mineru 数据特点

### 4.1 14 篇 + 93 章 全部 CHAP_MERGED(规模 12 本最大)

mineru 把每个篇/章首切成 `第N篇` / `第N章` alone + 篇/章名 紧跟另一 type=title:

```
pg 51 [0] title: '第一篇'
pg 51 [1] title: '肾上腺疾病与外科治疗进展'
pg 53 [0] title: '第一章'
pg 53 [1] title: '肾上腺疾病与外科治疗发展史'
```

CHAP_MERGED 拼接救 14 + 93 = **107 处**(本书最大规模,远超心血管 9 / 普外 0)。

### 4.2 节标题跨行同页 stitch 救多处

pg 46 [20] title `第八十五章 阴茎海绵体硬结症与中西医` + pg 46 [21] paragraph `结合治疗……1488` → 同页 stitch 救出完整章名 `第八十五章 阴茎海绵体硬结症与中西医结合治疗`(经 user 截图确认书本原文)。

### 4.3 OCR 字符差异多种

- **异体字**(1 处):`盞` U+76DE / `盏` U+76CF(肾盏)
- **罗马数字符号 vs 西文字母**(2 处):`Ⅳ` U+2163 / `IV`、`Ⅱ` U+2161 / `II`
- **希腊字母 LaTeX**(5 处):`α` U+03B1 / `\alpha`(5α-还原酶 / IFN-α 系列)
- **形近字**(1 处,字典侧错):`辜` U+8F9C / `睾` U+777E(无辜症 → 无睾症)

总共 9 处 OCR 字符差异 — **12 本里 OCR 问题最多的一本**(老牌大型专科书,生僻字 / 公式 / 罗马数字密集)。

### 4.4 末尾 pg 1678 起 `索引` type=title

跟骨科末尾 paragraph 不同,本书 BODY_END 是规范 type=title。`_find_body_end` 已通用兼容 paragraph + title(骨科升级延续)。

---

## 5. Step 1 字典 4 类硬校验

| # | 项 | 结果 |
|---|---|---|
| 1 | 基本计数 | L1=14 + L2=93 + L3=504 + L4=1928 = 2539 ✓ |
| 2 | unmatched / blacklist | unmatched=**0**(stitch + SPLIT_ANCHOR + PATCH 救后)/ blacklist 1 次(目录页眉) |
| 3 | 篇序连续 | 1..14 ✓ |
| 4 | 章号(全书 1..N)连续 | 1..93 ✓(stitch 救第八十五章 mineru 切碎)|
| 5 | strict_key 唯一性 | 1673/1827 unique;154 重复(`一、概述` 134 / `二、病因与病理` 52 等通用名跨节共享,大字典必然) |

---

## 6. Step 2 全量校验

| 项 | 结果 |
|---|---|
| L1 篇 14/14 | 全 strong action(107 CHAP_MERGED + AS_IS) |
| L2 章 93/93 | 全 strong,**0 真兜底** ✓ |
| L3 节 504/504 | 全覆盖(肾盏 PATCH 救 1)|
| L4 一、 1928/1928 | **全覆盖**(_text_of 升级救 5 + Ⅳ/Ⅱ PATCH 救 2 + SPLIT 救 1 + 跨页 stitch 救 2)|
| **Missing** | **0**(12 本里覆盖率最完美:99.9% → 100%) |
| Status | OK 1781 / DISAMBIG 797 / AMBIGUOUS 69(L4 通用名,不影响切割)|
| Action | AS_IS 2423 + CHAP_MERGED **107** + PAGE_HEADER_FB 107 |

---

## 7. Step 3 切分结果

```
PARENT_SPLIT_THRESHOLD: 6000  (Pass 1 (一))
PARENT_REFINE_THRESHOLD: 1e9  (Pass 2 1. 关闭,user 决定接受 3 个超阈值)
书末截断:    353 blocks / 3729 字
前言丢弃:     8 blocks / 7 字
参考文献丢弃: 544 blocks / 175381 字
父块数(节单位): 2540
父块数(切):    2125  (被二次切的 section: 153 个新增)
子块数:       3700

父块 size 字符:
  min=35 med=640 p75=1250 p90=2255 p95=3212 p99=5131 max=8378

父块 分桶:
  [    0,       499]:   874  (41.1%)  ← L4 短叙事(12 本最多,大型专科书风格,接受)
  [  500,      1999]:   991  (46.6%)
  [ 2000,      4999]:   238  (11.2%)
  [ 5000,      9999]:    22  (1.0%)   ← 含 3 个 > 6000(纯叙事节内无更细 anchor)
  [10000,     19999]:     0  (0.0%)

子块 size 字符:
  min=35 med=576 p75=694 p90=865 p95=994 p99=1157 max=1721

子块 分桶:
  [    0,       199]:   252  (6.8%)   ← 跟父块同根因
  [  200,       499]:  1078  (29.1%)
  [  500,       999]:  2187  (59.1%)
  [ 1000,      1999]:   183  (4.9%)
```

字符守恒 mismatch=0 ✓。

### 7.1 剩 3 个 > 6000 父块都是真实数据,接受

```
8378  六、后尿道瓣膜的治疗 >> (四) 生后治疗  — (四) 子段内无 1. 子标题(关 Pass 2)
7745  六、上皮细胞-间叶细胞转化及其调节蛋白  — 整节纯叙事,无 (一) 子标题
7184  七、尿路上皮癌干细胞和上皮可塑性     — 同上
```

差额最大 2378 字。第 1 个开 Pass 2 = 1. 可以救,但另 2 个无 (一) 也无 1. 切不了 → user 决定接受 3 个。

---

## 8. SOP-level 新发现汇总(本书最多,5 处升级)

1. **跨页 stitch**(§3.1):`raw_lines[i+1][0] in (pg, pg+1)` 允许下一页延续,救 mineru 把 list 末 item 切到下页 paragraph
2. **SPLIT_ANCHOR 加 `(?<=\d)(?=[一二...]+、)`**(§3.2):救 mineru 把多个 list items 错抓成 1 paragraph
3. **`_text_of` 兼容 equation_inline + LaTeX 希腊字母 → Unicode**(§3.3):一次性救 5 处 α + 回灌消化系统的 1 处 24h pH 问题(实际不会救消化的 — 因为消化的 LaTeX 是 `\mathrm`,字符不同;但救本书 α 已足够)
4. **OCR 字符差异 PATCH 体系完善**(§3.4):异体字 / 罗马数字符号 vs 西文字母 / LaTeX 希腊字母 / 形近字 4 类 — 全部走 PATCH_BODY_RAW_FIX(正文修)或 PATCH_LINE_PREPROCESS(字典修)
5. **接受 L4 短父块原则正式确立**(§3.5):大型专科参考书 L4 短叙事必然产生大量 < 500 父块(本书 41.1%,12 本最多),语义边界 > 字数
