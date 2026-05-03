#!/bin/bash
# scripts/batch_parse_pdfs.sh — 批量 mineru hybrid 解析 raw-pdf/ 下所有 PDF
#
# 幂等:每本 PDF 检查 mineru-output/{name}/hybrid_auto/{name}.md 是否已存在,
# 已存在则跳过。中断后重跑只补未完成的本。
#
# 用法:
#   nohup bash scripts/batch_parse_pdfs.sh > /tmp/batch_mineru.log 2>&1 &
#   disown
#   tail -f /tmp/batch_mineru.log    # 看进度
set -euo pipefail

# 解析项目根目录(脚本可在任何 cwd 调用)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 激活 venv + mineru 本地模型源
source "$PROJECT_ROOT/.venv/bin/activate"
export MINERU_MODEL_SOURCE=local
export MODELSCOPE_CACHE=/data/mineru-models

PDF_DIR=/data/medical-resources/raw-pdf
OUT_DIR=/data/medical-resources/mineru-output

if [ ! -d "$PDF_DIR" ]; then
  echo "ERROR: PDF 目录不存在: $PDF_DIR" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

# 收集所有 PDF,统计总数
mapfile -t pdfs < <(find "$PDF_DIR" -maxdepth 1 -type f -name "*.pdf" | sort)
total=${#pdfs[@]}
if [ "$total" -eq 0 ]; then
  echo "WARN: $PDF_DIR 下没有 PDF" >&2
  exit 0
fi

echo "============================================================"
echo "批量 mineru 解析启动"
echo "  时间    : $(date '+%Y-%m-%d %H:%M:%S')"
echo "  输入    : $PDF_DIR"
echo "  输出    : $OUT_DIR"
echo "  共      : $total 本 PDF"
echo "  Backend : hybrid-auto-engine"
echo "============================================================"

idx=0
ok=0
skip=0
fail=0
fail_names=()

for pdf in "${pdfs[@]}"; do
  idx=$((idx + 1))
  name=$(basename "$pdf" .pdf)
  out_md="$OUT_DIR/$name/hybrid_auto/$name.md"

  if [ -f "$out_md" ]; then
    echo "[$idx/$total] [skip] $name (已解析)"
    skip=$((skip + 1))
    continue
  fi

  echo
  echo "------------------------------------------------------------"
  echo "[$idx/$total] [$(date '+%H:%M:%S')] 开始: $name"
  echo "------------------------------------------------------------"

  start_ts=$(date +%s)
  if mineru -p "$pdf" -o "$OUT_DIR" -b hybrid-auto-engine -l ch; then
    elapsed=$(($(date +%s) - start_ts))
    echo "[$idx/$total] [$(date '+%H:%M:%S')] 完成: $name (耗时 ${elapsed}s)"
    ok=$((ok + 1))
  else
    elapsed=$(($(date +%s) - start_ts))
    echo "[$idx/$total] [$(date '+%H:%M:%S')] 失败: $name (耗时 ${elapsed}s, 继续下一本)" >&2
    fail=$((fail + 1))
    fail_names+=("$name")
  fi
done

echo
echo "============================================================"
echo "全部处理完毕  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  成功 : $ok"
echo "  跳过 : $skip (已解析)"
echo "  失败 : $fail"
if [ "$fail" -gt 0 ]; then
  echo "  失败列表:"
  for n in "${fail_names[@]}"; do
    echo "    - $n"
  done
fi
echo "============================================================"

# 失败时返回非零,便于上层监控
[ "$fail" -eq 0 ]
