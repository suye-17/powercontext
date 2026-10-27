#!/usr/bin/env bash
# 恢复到初始状态：停止本地 PowerContext、清空比赛专用数据目录。
# 只删除 $POWERCONTEXT_HOME（默认 ~/memory-forgery/home），不碰其他数据。
#
# 用法：./scripts/reset.sh            # 停止 + 清空
#       ./scripts/reset.sh --keep     # 只清空数据，不停止进程

set -euo pipefail

DATA_HOME="${POWERCONTEXT_HOME:-$HOME/memory-forgery/home}"

echo "数据目录：$DATA_HOME"

if [ "${1:-}" != "--keep" ]; then
  echo "== 停止 PowerContext =="
  pkill -f "powercontext server run" 2>/dev/null && echo "已发送停止信号" || echo "没有发现运行中的 PowerContext"
  sleep 2
fi

echo "== 清空数据目录 =="
if [ -d "$DATA_HOME" ]; then
  rm -rf "$DATA_HOME"
  echo "已删除 $DATA_HOME"
else
  echo "$DATA_HOME 不存在，无需清理"
fi
mkdir -p "$DATA_HOME"

cat <<'EOF'

完成。重新开始：

  cd ~/memory-forgery
  POWERCONTEXT_HOME=~/memory-forgery/home powercontext server run --no-env-file --port 8000
  ./scripts/seed.sh http://127.0.0.1:8000      # 可选：预置两条前任档案

应用侧无需改动：同一代号会得到全新的空账本。
EOF
