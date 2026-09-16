#!/bin/bash
# 两模型共存实测（可指定加载顺序）
# 用法：bash probe_two_order.sh <chat_model> <num_ctx> <embed_first|chat_first> <label>
API=http://localhost:11434
OUT=/home/pc/case_out.txt
CHAT="$1"; CTX="$2"; ORDER="$3"; LABEL="$4"
: > "$OUT"
export no_proxy='*'

log() { echo "$@" >> "$OUT"; }
ps_info() {
  curl -s -m 10 "$API/api/ps" | python3 -c '
import sys,json
ms=json.load(sys.stdin).get("models",[])
tot=sum(m.get("size_vram",0) for m in ms)/1e9
print("count=%d | %s | VRAM=%.2fGB" % (len(ms), ", ".join(m["name"] for m in ms), tot))
' 2>/dev/null || echo "读取失败"
}
load_embed() {
  curl -s -m 120 "$API/api/embed" \
    -d '{"model":"bge-m3","input":"预热向量化","keep_alive":"10m"}' >/dev/null 2>&1
}
load_chat() {
  curl -s -m 290 "$API/api/generate" \
    -d "{\"model\":\"$CHAT\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":\"10m\",\"options\":{\"num_ctx\":$CTX,\"num_predict\":1}}" >/dev/null 2>&1
}

log "案例：$LABEL  (chat=$CHAT @$CTX + embed=bge-m3；顺序=$ORDER)"

# 彻底清场：stop 后轮询确认归零（避免异步卸载污染判定）
for i in 1 2 3 4 5; do
  for m in qwen3.8:27b qwen2.5:7b bge-m3; do ollama stop "$m" >/dev/null 2>&1; done
  sleep 3
  [ "$(curl -s -m 10 $API/api/ps | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("models",[])))' 2>/dev/null)" = "0" ] && break
done
log "  [清场后] $(ps_info)"

T0=$(date '+%Y-%m-%d %H:%M:%S')
sleep 2

if [ "$ORDER" = "embed_first" ]; then
  load_embed; log "  [① 载入嵌入后] $(ps_info)"
  sleep 2
  load_chat;  log "  [② 载入对话后] $(ps_info)"
else
  load_chat;  log "  [① 载入对话后] $(ps_info)"
  sleep 2
  load_embed; log "  [② 载入嵌入后] $(ps_info)"
fi

# 驱逐判定（严格匹配完整消息文本）
EV=$(journalctl -u ollama --no-pager --since "$T0" 2>/dev/null \
      | grep -E 'predicted to exceed available memory, evicting|fits alongside existing models' \
      | sed -E 's/.*msg="//' | tail -3)
if [ -n "$EV" ]; then log "  [调度判定]"; echo "$EV" | sed 's/^/    /' >> "$OUT"
else log "  [调度判定] 无调度判定日志"; fi

FINAL=$(ps_info)
log "  [最终] $FINAL"
if [ "$(echo "$FINAL" | sed -E 's/^count=([0-9]+).*/\1/')" -ge 2 ]; then
  log "  ==> ✅ 可共存"
else
  log "  ==> ❌ 不可共存（有模型被卸载）"
fi

cp "$OUT" /mnt/d/01code/business/trackcompass/scripts/_case_out.txt
sync
