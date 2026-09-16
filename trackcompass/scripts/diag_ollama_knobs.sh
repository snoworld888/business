#!/bin/bash
# Ollama 缓存/内存预测相关开关排查
OUT=/home/pc/diag_out.txt
: > "$OUT"
{
  echo "=== 1. 探测脚本终止确认 ==="
  pkill -f probe_ctx_coexist 2>/dev/null
  sleep 1
  if pgrep -f probe_ctx_coexist >/dev/null 2>&1; then echo "  仍在运行"; else echo "  已终止"; fi
  echo
  echo "=== 2. Ollama 版本 ==="
  ollama --version 2>&1 | head -2
  echo
  echo "=== 3. 二进制中与缓存/内存预测相关的环境变量 ==="
  strings /usr/local/bin/ollama 2>/dev/null \
    | grep -oE 'OLLAMA_[A-Z_]*(CACHE|FIT|OVERHEAD|BATCH|MEMORY|ESTIMATE)[A-Z_]*' \
    | sort -u | head -20
  echo
  echo "=== 4. 二进制中所有 OLLAMA_ 环境变量（完整清单）==="
  strings /usr/local/bin/ollama 2>/dev/null \
    | grep -oE 'OLLAMA_[A-Z_]{3,}' | sort -u | head -60
  echo
  echo "=== 5. llama-server 的 cache-ram 相关参数 ==="
  strings /usr/local/lib/ollama/llama-server 2>/dev/null \
    | grep -E 'cache-ram|cache_ram|no-cache' | sort -u | head -6
} >> "$OUT" 2>&1
cp "$OUT" /mnt/d/01code/business/trackcompass/scripts/_diag_out.txt
sync
echo OK
