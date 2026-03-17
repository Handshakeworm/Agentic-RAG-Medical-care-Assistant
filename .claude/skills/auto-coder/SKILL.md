---
name: auto-coder
description: Autonomous spec-driven development agent. Syncs DEV_SPEC.md into chapter-based reference files, identifies the next pending task from the schedule, implements code following spec architecture and patterns, runs tests with up to 3 auto-fix rounds, and persists progress with atomic commits. Use when user says "auto code", "自动开发", "自动写代码", "auto dev", "一键开发", "autopilot", or wants fully automated spec-to-code workflow.
---

# Auto Coder

One trigger completes **read spec → find task → code → test → persist progress**.

Optional modifiers: append a task ID (e.g. `auto code B2`) to target a specific task, or `--no-commit` to skip git commit.

---

## Pipeline

```
Sync Spec → Find Task → Implement → Test (≤3 fix rounds) → Persist
```

Pause only at the end for commit confirmation. Run everything else autonomously.

> **⚠️ CRITICAL: Activate `.venv` before ANY `python`/`pytest` command (idempotent, re-run if unsure).**
> - **Windows**: `.\.venv\Scripts\Activate.ps1`
> - **macOS/Linux**: `source .venv/bin/activate`

## Reference Map

All files under `skills/auto-coder/references/`:

| File | Content | DEV_SPEC 章节 | When to Read |
|------|---------|--------------|-------------|
| `01-overview.md` | 项目总览、架构、亮点 | Section 1（1.1 总体架构） | 首次任务或需要项目上下文 |
| `02-tech-stack.md` | 模型选型、存储选型 | Section 1.2 | 选择库/模式/配置时 |
| `03-rag-pipeline.md` | RAG 摄取与检索流程 | Section 2 | 实现 RAG 相关任务 |
| `04-agent-design.md` | Agent 工作流与上下文管理 | Section 3 | 实现 Agent 相关任务 |
| `05-infrastructure.md` | 监控、缓存、权限 | Section 4 | 实现基础设施任务 |
| `06-evaluation.md` | 评估体系 | Section 5 | 实现评估相关任务 |
| `07-skills.md` | SKILLs 说明 | Section 6 | 了解/开发 SKILL 时 |
| `08-schedule.md` | 排期与进度跟踪 | Section 7 | 每轮 Sync Spec |

---

### 1. Sync Spec

```bash
python skills/auto-coder/scripts/sync_spec.py
```

Then read the schedule file to get task statuses:
- Read `skills/auto-coder/references/08-schedule.md`

Task markers:

| Marker | Status |
|--------|--------|
| `[ ]` / `⬜` | Not started |
| `[~]` / `🔶` / `(进行中)` | In progress |
| `[x]` / `✅` / `(已完成)` | Completed |

---

### 2. Find Task

Pick the first `IN_PROGRESS` task, then the first `NOT_STARTED`. If user specified a task ID, use that directly.

Quick-check predecessor artifacts exist (file-level only). On mismatch, log a warning and continue — only stop if the target task itself is blocked.

---

### 3. Implement

1. **Read relevant spec** from `skills/auto-coder/references/`:
   - 项目架构: `01-overview.md`
   - 技术选型: `02-tech-stack.md`
   - RAG Pipeline: `03-rag-pipeline.md`
   - Agent 设计: `04-agent-design.md`
   - 基础设施: `05-infrastructure.md`
   - SKILLs: `07-skills.md`

2. **Extract** from spec: inputs/outputs, design principles (Pluggable? Config-driven? Factory?), file list, acceptance criteria.

3. **Plan** files to create/modify before writing any code.

4. **Code** — project-specific rules:
   - Treat spec as single source of truth
   - Use `config/settings.yaml` values, never hardcode
   - Match existing codebase patterns and style

5. **Write tests** alongside code:
   - Place in `tests/unit/` or `tests/integration/` per spec
   - Mock external deps in unit tests

6. **Self-review** before running tests: verify all planned files exist and tests import correctly.

---

### 4. Test & Auto-Fix

```

Round 0..2:
  Run pytest on relevant test file
  If pass → go to step 5
  If fail → analyze error, apply fix, re-run

Round 3 still failing → STOP, show failure report to user
```

---

### 5. Persist

1. **Update `DEV_SPEC.md`** (global file): change task marker `[ ]` → `[x]`
2. **Re-sync**: `python skills/auto-coder/scripts/sync_spec.py --force`
3. **Show summary & ask**:

```
✅ [A3] 配置加载与校验 — done
   Files: src/core/settings.py, tests/unit/test_settings.py
   Tests: 8/8 passed
   Commit: feat(config): [A3] implement config loader

   "commit" → git add + commit
   "skip"   → end
   "next"   → commit + start next task
```

On "next", loop back to step 1 and start the next task.
