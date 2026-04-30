---
name: auto-coder
description: 自主式规范驱动开发代理。将 DEV_SPEC.md 同步拆分为按章节组织的参考文件，从排期中识别下一个待完成任务，按规范架构和模式实现代码，运行测试并自动修复（最多 3 轮），最后通过原子提交持久化进度。当用户说 "auto code"、"自动开发"、"自动写代码"、"auto dev"、"一键开发"、"autopilot"，或需要全自动的规范到代码工作流时使用。
---

# 自动编码器

一次触发即可完成 **读取规范 → 查找任务 → 编码 → 测试 → 持久化进度**。

可选修饰符：追加任务 ID（如 `auto code B2`）以指定特定任务，或 `--no-commit` 跳过 git 提交。

---

## 流水线

```
同步规范 → 查找任务 → 实现 → 测试（≤3 轮自动修复）→ 持久化
```

仅在最后暂停以确认提交。其余步骤全部自主运行。

> **⚠️ 关键：在执行任何 `python`/`pytest` 命令前必须激活 `.venv`（幂等操作，不确定时重新执行即可）。**
> - **Windows**: `.\.venv\Scripts\Activate.ps1`
> - **macOS/Linux**: `source .venv/bin/activate`

## 冲突处理（贯穿全流程的 0 级规则）

遇到规范内部或规范与代码冲突时，按下表决策；**不允许沉默选择**：

| 冲突类型 | 处理规则 |
|---------|---------|
| §9 全局契约 vs 业务章节（§3 / §4 / §6 等） | **§9 为准** |
| §9.3 全量清单 / §9.5 Schema / §9.7 常量表 vs 任务描述里的字段列表 | **清单为准**（§9 权威；清单中不存在的 LLM 调用点视为违规） |
| 设计章节（§1-§7）vs 排期表任务描述（§8） | **设计章节为准**（§8 是执行指引，不是设计决策） |
| 规范 vs 现有代码（同一文件已有不同实现） | **停下问用户**："规范要求 X，现有代码为 Y，是否重写 / 合并 / 保留？" |
| 测试失败且无法判断 **代码 bug / 测试 bug / 规范 bug** | **停下问用户**，不自动修（详见第 4 步失败分类表） |
| `references/*.md` vs `DEV_SPEC.md` | **`DEV_SPEC.md` 为准**（references 是 sync_spec.py 生成的副本） |

**禁止行为**：
- **禁止修改 `.claude/skills/auto-coder/references/*.md`** —— 由 sync_spec.py 从 DEV_SPEC.md 生成，手动修改会被下次 sync 覆盖。要改规范只能改 `DEV_SPEC.md`。
- **禁止在无法判断 ground truth 时自己做决定** —— 无法判断即停下询问，绝不沉默选择。
- **禁止为迎合测试而改规范，或为迎合规范而改测试** —— 先分类失败来源，见第 4 步。

## 参考文件索引

`.claude/skills/auto-coder/references/` 下的所有文件：

| 文件 | 内容 | DEV_SPEC 章节 | 何时读取 |
|------|---------|--------------|-------------|
| `01-overview.md` | 项目总览、架构、目录结构 | `# 1. 项目总览` | 首次任务或需要项目上下文 |
| `02-tech-stack.md` | 模型选型、存储选型与设计 | `# 2 技术选型` | 选择库/模式/配置时 |
| `03-rag-pipeline.md` | RAG 摄取与检索流程 | `# 3. RAG系统pipeline` | 实现 RAG 相关任务 |
| `04-agent-design.md` | Agent 工作流与上下文管理 | `# 4. Agent设计` | 实现 Agent 相关任务 |
| `05-infrastructure.md` | 监控、缓存、权限 | `# 5. 基础设施` | 实现基础设施任务 |
| `06-evaluation.md` | 评估体系 | `# 6. 评估` | 实现评估相关任务 |
| `07-prompts.md` | Prompt 模板设计 | `# 7. Prompt 模板` | 实现涉及 LLM 调用的任务时 |
| `08-schedule.md` | 排期与进度跟踪 | `# 8. 项目排期` | 每轮同步规范时 |
| `09-contracts.md` | **全局实现契约（跨章节）**：结构化输出统一策略、Schema 演进兼容性、全量 Pydantic Schema 定义 | `# 9. 全局实现契约` | **强制**：实现任何涉及 LLM 调用 / Pydantic Schema / 结构化数据的任务时必读（C / F / I 阶段大多数任务） |

---

### 1. 同步规范

```bash
python .claude/skills/auto-coder/scripts/sync_spec.py
```

然后读取排期文件获取任务状态：
- 读取 `.claude/skills/auto-coder/references/08-schedule.md`

任务标记：

| 标记 | 状态 |
|--------|--------|
| `[ ]` / `⬜` | 未开始 |
| `[~]` / `🔶` / `(进行中)` | 进行中 |
| `[x]` / `✅` / `(已完成)` | 已完成 |

---

### 2. 查找任务

优先选择第一个 `IN_PROGRESS`（进行中）任务，其次选第一个 `NOT_STARTED`（未开始）任务。若用户指定了任务 ID，则直接使用。

**终止条件**：若 §8.4 进度跟踪表中所有任务均为 `[x]`（或用户指定的任务本身已 `[x]`）→ 打印总结（已完成任务数 / 阶段 A-J 各阶段完成度）+ **退出整个流水线**，不进入第 3 步。

**前置依赖硬检查**（文件存在 ≠ 功能已实现，必须双重验证）：

1. **进度表状态检查**：按 §8.3 阶段依赖关系（A→B→C→D→E→F→G→H→I→J，及同阶段内编号递增）列出本任务的前置任务。若有前置任务在 §8.4 进度跟踪表中**未标记 `[x]`** → **停下询问用户**："前置任务 [XX] 未完成，是否仍强制继续？"（允许用户显式覆盖，但必须显式）。
2. **最小导入冒烟**：对前置任务产出的文件做 `python -c "from <path> import <symbol>"` 冒烟测试（`<symbol>` 取任务描述里提到的关键类 / 函数 / 常量）。import 失败 → 视为前置未实际实现，同上停下询问。

只有两项都通过或用户显式覆盖后，才进入第 3 步。

---

### 3. 实现

#### 3.1 读取相关规范

来自 `.claude/skills/auto-coder/references/`：
- 项目架构: `01-overview.md`
- 技术选型: `02-tech-stack.md`
- RAG Pipeline: `03-rag-pipeline.md`
- Agent 设计: `04-agent-design.md`
- 基础设施: `05-infrastructure.md`
- Prompt 模板: `07-prompts.md`
- **全局实现契约（跨章节）: `09-contracts.md`** — **强制规则**：只要当前任务涉及 LLM 调用（C4 enrichment / F2-F14 各 Agent 节点 / I1-I3 LLM Judge / G 阶段含 LLM 的路由等）、Pydantic Schema 定义、或结构化输出，必须读本文件。本文件定义 `llm.with_structured_output(Schema).with_retry(stop_after_attempt=N)` + `try/except/finally` 裸代码模板 + 按 §9.1 "可观测性要求"表手动上报 6 个 Prometheus 指标的统一实现契约。**严禁**自行封装装饰器 / helper 函数 / 上下文管理器——每个 LLM 调用点独立裸写，样板重复属于有意设计（见 §9.1 "实现风格约定"）。与业务章节描述冲突时以本文件为准（详见"冲突处理"决策表）。

#### 3.2 从规范中提取

输入/输出、设计原则（可插拔？配置驱动？工厂模式？）、文件清单、验收标准。

#### 3.3 规划

在编写代码前列出需要创建/修改的文件，以及本任务涉及的所有 LLM 调用点（如有）、Schema 文件（如有）、运行时常量引用（如有）。

#### 3.4 实现前强制自检（BLOCKED 前置，不通过不允许动手编码）

**已知本任务的全量实现清单**（来自 3.3 规划产物）后，对照 §9 做以下 3 项硬检查；任何一项不通过就**停下询问用户**，不要自作主张实现：

1. **LLM 调用点清单核对**：3.3 列出的每个 LLM 调用点是否都在 `09-contracts.md` §9.3 清单？不在 → 停止，询问用户"是否新增 Schema？需要先补 §9.3 / §9.5 再实现"
2. **Schema 字段一致性**：3.3 列出的产出 `src/agent/schemas/*.py` 字段是否与 §9.5 一一对应？（字段名、类型、默认值、`Literal` 枚举取值）有差异 → 停止，列出差异项并询问"以 §9.5 为准还是以任务描述为准？"
3. **常量来源合规**：3.3 列出的运行时常量（`MAX_FOLLOWUP_ROUNDS` / `MAX_EXAM_ROUNDS` / `MAX_FOLLOWUP_QUESTIONS` / `RETRIEVE_TOP_N` / `ASKABLE_GAIN_THRESHOLD` / `ENTITY_LINKING_TIER2_THRESHOLD` / `RERANKER_CUTOFF_LAYERS`）是否都计划通过 `settings.agent_limits.XXX` 引用？若打算 hardcode（如 `MAX_X = 8`）→ 停止，改为 `from config.settings import settings` 的引用方式

自检通过后再进入 3.5 编码。

#### 3.5 编码 — 项目专属规则

- 将规范视为唯一事实来源（以 `DEV_SPEC.md` 为准，不以 references 为准）
- 使用 `config/settings.py` 的值（从环境变量/.env 加载），**禁止硬编码**
- 匹配现有代码库的模式和风格
- 发现与现有代码冲突时按"冲突处理"决策表执行

#### 3.6 同步编写测试

- 按规范放在 `tests/unit/` / `tests/integration/` / `tests/e2e/` 目录下（按 §1.3.1 目录树约定）
- 单元测试中 Mock 外部依赖（LLM / DB / Milvus）
- 集成测试可用真实依赖但仍 Mock LLM；E2E 测试（J 阶段）走真实 DashScope + 真实后端

#### 3.7 运行测试前自检

- 所有计划文件已创建
- 测试文件 import 路径正确
- `.venv` 已激活

---

### 4. 测试与自动修复

每轮测试失败后，**先按下表分类，再决定是否自动修复**。只有"代码 bug"允许自动修；其他三类一律停下询问用户。

| 失败类型 | 判断线索 | 处理 |
|---------|---------|------|
| **代码 bug** | 测试断言明确且与规范（§9.5 Schema / §9.7 常量 / 任务验收标准）一致，但当前代码行为不符 | ✅ 自动修（本步自动化范围） |
| **测试 bug** | 测试断言与规范冲突（如 `assert rounds == 10` 但 §9.7 定义 `MAX_FOLLOWUP_ROUNDS=8`） | ⏸️ 停下问用户："测试断言与 §9.7 不一致，以哪边为准？" |
| **规范 bug** | 规范内部矛盾导致代码和测试都没错但对不齐（如 §4.1.2 说 13 字段，§9.5 说 12 字段） | ⏸️ 停下问用户，**不自动改规范** |
| **环境 bug** | `ModuleNotFoundError` / `ConnectionRefusedError` / `FileNotFoundError` 等 | ⏸️ 停下提示用户：".venv 未激活 / 依赖未装 / Docker 未起" |

```
第 0..2 轮：
  对相关测试文件运行 pytest
  若通过 → 进入第 5 步
  若失败 → 分类（按上表）
    → 代码 bug：应用修复，重新运行
    → 测试/规范/环境 bug：立即停下，向用户展示失败类型 + 证据，等用户决策

第 3 轮仍未通过（且全是代码 bug 类）→ 停止，向用户展示失败报告
```

**反模式（严禁）**：
- 为了让测试通过而修改测试断言（除非用户已确认该测试就是错的）
- 为了让代码通过测试而偷改规范（`DEV_SPEC.md` 在本步内**只读**）
- 反复在 "改代码 → 再失败 → 改测试 → 再失败" 之间震荡

---

### 5. 持久化

1. **一致性自检**（提交前最后一关，任一项不通过则停下询问用户，**不自动改规范**）：
   - 新建的源代码文件是否都在 §1.3.1 目录树？不在 → 提示"需补目录树"
   - 新建的 LLM 调用点是否都在 §9.3 清单？不在 → 提示"需补 §9.3 / §9.5"
   - 新建的运行时常量是否都在 §9.7 `agent_limits` 段？不在 → 提示"需补 §9.7"
   - 源代码中是否存在 `MAX_X = 常量` / 阈值 hardcode？有 → 提示改为 `settings.agent_limits.XXX`
2. **更新 `DEV_SPEC.md`**（**唯一可改的规范文件**）：将任务标记 `[ ]` → `[x]`
3. **重新同步 references**：`python .claude/skills/auto-coder/scripts/sync_spec.py --force`
4. **展示摘要并询问**：

```
✅ [A3] 配置加载与校验 — 完成
   文件: src/core/settings.py, tests/unit/test_settings.py
   测试: 8/8 通过
   提交: feat(config): [A3] implement config loader

   "commit" → git add + commit
   "skip"   → 结束
   "next"   → 提交 + 开始下一个任务
```

选择 "next" 时，循环回到第 1 步开始下一个任务。

**提交约定**：
- commit message 格式：`<type>(<scope>): [<task-id>] <desc>`，例：`feat(agent): [F6] implement select_discriminative_symptom with askability threshold`
- 原子提交：一个任务一次 commit；多文件/多模块的大任务（如 H2）可拆分为多个有序 commit，但均属于同一任务 ID
- **禁止**提交 `.claude/skills/auto-coder/references/*.md` —— 它们由 sync_spec.py 生成，应在 `.gitignore` 或手动避免 `git add`
