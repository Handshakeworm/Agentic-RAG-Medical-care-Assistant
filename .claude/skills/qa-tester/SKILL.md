---
name: qa-tester
description: "Modular RAG MCP Server 的全自动 QA 测试代理。从 QA_TEST_PLAN.md 读取测试用例，无需人工干预自动执行所有类型的测试——CLI 命令、通过 Streamlit AppTest 无头渲染的 Dashboard UI、通过子进程 JSON-RPC 的 MCP 协议、提供商切换及数据生命周期检查。自动诊断失败、最多重试 3 轮修复，并将结果记录到 QA_TEST_PROGRESS.md。当用户说 'run QA'、'QA test'、'QA 测试'、'执行测试'、'跑测试'、'test and fix'，或需要执行 QA 测试计划时使用。"
---

# QA 测试代理

所有测试类型（CLI / Dashboard UI / MCP 协议）均**完全自动化**——零人工干预。

可选修饰符：附加章节字母（`run QA G`）或测试 ID（`run QA G-01`）。

---

> ## ⛔ 铁律
>
> ### 规则 1：严格串行
> 选择一个测试 → 运行一条命令 → 等待输出 → 在 `QA_TEST_PROGRESS.md` 中记录一行 → 然后选择下一个。
> 绝不在一条命令中运行两个测试。绝不在一次文件编辑中记录两行。绝不使用并行工具调用。绝不在记录当前测试之前规划下一个测试。
>
> ### 规则 2：通过 = 终端输出证据
> ✅ 意味着你**在本次会话中**运行了命令，且备注中包含从该输出中复制的**具体值**。
> 本次测试没有终端输出 → 标记为 ⬜。绝不标记 ✅。
>
> ### 规则 3：零交叉引用
> 绝不写 "已通过 X-YZ 验证"、"已在 C-02 中验证"、"与……相同"、"类似于……"。
> 即使 G-05 与 C-02 测试相同的功能，也需独立运行 G-05 并粘贴其自身输出。
>
> ### 规则 4：零推断
> **禁止的备注模式**（验证器会捕获）：
> "代码使用……" / "数据类验证……" / "自动创建……" → 阅读代码 ≠ 测试。
> "应该可以……" / "会抛出……" / "预期行为……" → 推测 ≠ 测试。
> "参数已接受" / "配置控制行为" → 含糊。无输出 = 不通过。
> 如果你没有为本次测试运行命令并看到输出，标记为 ⬜。
>
> ### 规则 5：对抗性思维
> 寻找缺陷，而非验证预期。10+ 次通过且零缺陷 → 重新审视你的严格程度。
>
> ### 规则 6：章节结束验证
> 完成一个章节后，运行 `python .claude/skills/qa-tester/scripts/qa_validate_notes.py`。
> 在进入下一章节之前重新执行所有被标记的测试。

---

## 流水线（严格串行）

```
1. 选择一个待测试用例（按 ID 顺序）
2. 如需要则设置系统状态
3. 运行一条命令——等待输出
4. 对比预期结果与实际输出，验证所有断言
5. 如需修复（最多 3 轮）
6. ⛔ 关卡：编辑 QA_TEST_PROGRESS.md（行 + 计数器）——每次编辑一行
7. 仅此时返回步骤 1
```

> 在任何 `python` 命令前先激活 `.venv`：`.\.venv\Scripts\Activate.ps1`

---

## 步骤 1：选择目标

1. 从 `QA_TEST_PLAN.md` 读取测试步骤和预期结果。
2. 从 `QA_TEST_PROGRESS.md` 读取当前状态。
3. 用户指定了章节/ID → 限定该范围。否则 → 第一个 ⬜ 待测试用例。
4. 如果存在任何 🔧 测试，优先重新测试这些。
5. 按章节顺序（A→O）执行，章节内按 ID 顺序执行。

### 测试类别

| 章节 | 类型 | 执行方式 |
|------|------|---------|
| A–F | Dashboard UI | AppTest 无头渲染——参见 [references/test_patterns.md](references/test_patterns.md) |
| G, H, I | CLI | 终端命令，检查退出码 + 标准输出 |
| J | MCP 协议 | JSON-RPC 子进程——参见 [references/test_patterns.md](references/test_patterns.md) |
| K, L | 提供商切换 | `qa_config.py apply <profile>` → 运行 CLI/Dashboard |
| M | 配置与容错 | 修改设置 → 运行 CLI → 验证错误处理 |
| N, O | 数据生命周期 | 使用 `qa_multistep.py <TEST_ID>` |

---

## 步骤 2：设置系统状态

```
Empty          → python .claude/skills/qa-tester/scripts/qa_bootstrap.py clear
Baseline       → python .claude/skills/qa-tester/scripts/qa_bootstrap.py baseline
DeepSeek       → python .claude/skills/qa-tester/scripts/qa_config.py apply deepseek
Rerank_LLM     → python .claude/skills/qa-tester/scripts/qa_config.py apply rerank_llm
NoVision       → python .claude/skills/qa-tester/scripts/qa_config.py apply no_vision
InvalidKey     → python .claude/skills/qa-tester/scripts/qa_config.py apply invalid_llm_key
InvalidEmbedKey→ python .claude/skills/qa-tester/scripts/qa_config.py apply invalid_embed_key
Any            → 无需更改状态
```

配置档测试完成后 → `python .claude/skills/qa-tester/scripts/qa_config.py restore`
检查状态 → `python .claude/skills/qa-tester/scripts/qa_bootstrap.py status`

---

## 步骤 3：执行与验证

### CLI 测试（G, H, I，K/L/M 的部分）

1. 从 `QA_TEST_PLAN.md` 的**步骤**列读取测试步骤。
2. 在终端运行确切的命令。
3. 对比**预期结果**检查输出：
   - **Ingest（摄取）**：exit=0，输出包含阶段名称（load/split/transform/embed/upsert）
   - **Query（查询）**：exit=0，结果包含 source_file 和 score
   - **Error（错误）**：exit≠0，stderr 包含描述性错误（非原始堆栈跟踪）
   - **Idempotency（幂等性）**：第二次运行显示 "skipped"，无重复

### Dashboard 测试（A–F）

阅读 [references/test_patterns.md](references/test_patterns.md) 获取 AppTest 模板、交互模式和文件上传解决方案。

要点：
- 每个测试编写一个内联 Python 脚本，通过 `AppTest.from_function` 渲染
- 打印特定元素值（metric 标签、selectbox 选项、expander 标签）
- 文件上传：先通过 CLI 摄取，再通过 AppTest 验证
- 数据变更：直接调用 `DataService`，通过 AppTest/CLI 验证

#### ⚠️ AppTest `st.tabs()` 限制——测试任何标签页内容前必读

AppTest 将**所有标签页内容同时渲染**到一个扁平元素列表中。标签页之间**没有空间隔离**——你无法从 `at.info`、`at.metric`、`at.markdown` 等确定哪个元素属于哪个标签页。

**后果：** 如果测试步骤说"点击标签页 X → 在标签页 X 中看到内容 Y"，AppTest **无法确认空间关系**。即使内容 Y 存在于输出中，它也可能来自不同区域（例如标签页上方的诊断区域，或不同的标签页）。

**所有标签页内容测试的强制检查：**

1. **首先验证标签页标签存在：** 打印 `[t.label for t in at.tabs]`（如果 `at.tabs` 可用）或在页面源码中检查标签页标签字符串。如果预期标签页标签**不存在** → 立即标记 ❌ — 该标签页不存在。
2. **然后验证内容元素存在**于 AppTest 输出中。
3. **在备注中承认限制：** 在备注中附加 `[AppTest: 标签页隔离不可验证]`。

**示例——标签页不存在：**
```python
# 对于期望带有 "skipped" 消息的 "🟪 Rerank" 标签页的测试：
tab_labels = [el.value for el in at.markdown if '🟪' in el.value or 'Rerank' in el.value]
print('找到 Rerank 标签页标签:', tab_labels)
# 如果为空 → 标签页不存在 → 标记 ❌，备注 "Rerank 标签页未渲染（阶段不存在）"
```

**禁止模式：** 找到内容元素（例如 "Rerank skipped" 信息）就得出标签页测试通过的结论——该元素可能来自诊断区域，而非标签页内部。

### MCP 测试（J）

阅读 [references/test_patterns.md](references/test_patterns.md) 获取 JSON-RPC 脚本模板和断言矩阵。

主要方式：`pytest tests/e2e/test_mcp_client.py -v` 涵盖大多数 J-* 用例。

### 多步骤测试（N, O, M 配置, L-07）

包含 3+ 个顺序步骤的测试**必须**使用运行脚本：

```
python .claude/skills/qa-tester/scripts/qa_multistep.py <TEST_ID>
```

**支持的测试：** `N-01`, `N-03`, `N-04`, `N-05`, `N-06`, `O-07`, `M-03`, `M-04`, `M-05`, `M-06`, `M-10`, `M-11`, `L-07`

该脚本执行每个子步骤，在每个步骤打印实际值，输出 `VERDICT: PASS/FAIL`。将 VERDICT 和关键步骤值复制到备注中。

对于脚本中不支持的测试，从 QA_TEST_PLAN.md 手动运行命令并粘贴输出。

---

## 步骤 4：修复与重试（最多 3 轮）

1. **诊断**：代码缺陷 / 配置问题 / 缺少数据 / 测试计划错误？
2. **修复**：仅进行最小改动。在备注中记录文件/行号。
3. **重试**：重新运行相同命令。
4. 3 轮失败后 → 标记 ❌ 并附详细备注。
5. 如果修复涉及共享代码 → 重新运行同一章节中之前已通过的测试。

---

## 步骤 5：记录结果

**⛔ 关卡——在选择下一个测试之前执行此步骤。**

编辑 `QA_TEST_PROGRESS.md`：更新一行测试记录 + 汇总计数器。每次文件编辑只更新一行。

### ✅ 通过要求

以下所有条件必须满足：
1. **在本次会话中**运行了命令
2. 从该命令观察到实际输出
3. 验证了预期结果中的**每项**断言
4. 备注包含来自终端输出的**≥2 个具体值**

### 备注格式

```
<方法>: <值_1>, <值_2>[, ...]
```

- **CLI**: `exit=0, stdout: 'Total chunks: 3', source_file=simple.pdf`
- **AppTest**: `at.metric[0].label='Total traces', at.metric[0].value=6`
- **多步骤**: `Step1: exit=0, chunks=3. Step2: sources=[simple.pdf]. Step3: deleted=1. Step4: sources=[]`
- **不合格**（禁止）: `"已在 C-02 中验证"`、`"代码使用 yaml.safe_load"`、`"应该可以因为……"`、`"参数已接受"`

### 状态图标

| 图标 | 含义 |
|------|------|
| ✅ | 通过——所有断言已对照实际输出验证 |
| ❌ | 失败——3 次修复尝试后仍失败 |
| ⏭️ | 跳过——缺少第三方 API 密钥（仅 K 系列） |
| 🔧 | 已应用修复——需要重新测试 |
| ⬜ | 待测试——尚未测试 |

### 计数器

在同一次编辑中更新：`✅ Pass: X | ❌ Fail: Y | ⏭️ Skip: Z | 🔧 Fix: W | ⬜ Pending: P`（总和必须等于 Total）。

### 章节结束关卡

每个章节完成后：
```
python .claude/skills/qa-tester/scripts/qa_validate_notes.py
```
重新执行所有被标记的测试。在 0 个标记之前不得继续。

---

## 关键路径

| 文件 | 用途 |
|------|------|
| `QA_TEST_PLAN.md` | 测试步骤和预期结果 |
| `QA_TEST_PROGRESS.md` | 执行状态和备注 |
| `config/settings.yaml` | 系统配置 |
| `scripts/ingest.py` / `query.py` / `evaluate.py` | CLI 命令 |
| `tests/e2e/test_mcp_client.py` | MCP 端到端测试 |
| `tests/e2e/test_dashboard_smoke.py` | Dashboard 冒烟测试 |
| `tests/fixtures/sample_documents/` | 测试 PDF 文件 |
| `tests/fixtures/golden_test_set.json` | 评估黄金数据集 |

## 测试文档

| 文件 | 语言 | 页数 | 图片数 |
|------|------|------|--------|
| `simple.pdf` | 英文 | 1 | 0 |
| `with_images.pdf` | 英文 | 1 | 1 |
| `complex_technical_doc.pdf` | 英文 | ~8 | 3 |
| `chinese_technical_doc.pdf` | 中文 | ~8 | 0 |
| `chinese_table_chart_doc.pdf` | 中文 | ~6 | 3 |
| `chinese_long_doc.pdf` | 中文 | 30+ | 0 |
| `blogger_intro.pdf` | 中文 | ~4 | 2 |

全部位于 `tests/fixtures/sample_documents/`。
