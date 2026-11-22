#!/usr/bin/env bash
# VAAS 栈自愈：解封 OpenBAO + 确认 VAAS 真能读凭证。
#
# 为什么需要它（2026-11-22 实测）：
#   1. OpenBAO 用 raft 存储，**重启后必然 SEALED**（file backend 在 v2.7 不存在）。
#   2. VAAS 的 `/health` **不查 vault**——vault 锁着时它照样返回
#      `{"ok":true,"hosted":true}`。健康探针骗人。
#   3. 真调用失败时报的是 `lane_down (image/volcengine) (unconfigured)`，
#      看起来像「车道没配」，实际是 vault 读不到 → provider_peek 回退到 volcengine。
#      **报错指向错的地方**，会让人去查 registry 而不是查 vault。
#
# 所以自愈不能只看 /health：必须实际读一次 vault 才算活着。
#
# crontab: */5 * * * * bash /opt/vaas-stack/health-watch.sh >> /var/log/vaas-health-watch.log 2>&1
set -uo pipefail
STACK=/opt/vaas-stack
INIT="$STACK/openbao/init.json"
VTOK_FILE="$STACK/vaas-token.txt"
log() { echo "$(date -Is) $*"; }

[ -f "$INIT" ] || { log "FATAL: $INIT missing"; exit 1; }

# ── 1. OpenBAO 活着且已解封 ────────────────────────────────────────────────
if ! docker ps --filter name=openbao --filter status=running -q | grep -q .; then
  log "openbao not running — starting"
  docker start openbao >/dev/null 2>&1 || true
  sleep 8
fi

sealed() { docker exec -e BAO_ADDR=http://127.0.0.1:8200 openbao bao status 2>/dev/null | awk '/^Sealed/{print $2}'; }
if [ "$(sealed)" = "true" ]; then
  UK=$(python3 -c "import json;print(json.load(open('$INIT'))['unseal_keys_b64'][0])")
  log "openbao SEALED — unsealing"
  docker exec -e BAO_ADDR=http://127.0.0.1:8200 openbao bao operator unseal "$UK" >/dev/null 2>&1 || true
  sleep 2
  if [ "$(sealed)" = "true" ]; then
    log "FATAL: unseal failed"; exit 1
  fi
  log "unsealed ok"
fi

# ── 2. VAAS 活着 ──────────────────────────────────────────────────────────
if ! docker ps --filter name=vaas-wanxing --filter status=running -q | grep -q .; then
  log "vaas-wanxing not running — starting"
  docker start vaas-wanxing >/dev/null 2>&1 || true
  sleep 12
fi

# ── 3. 真读一次 vault（/health 骗人，只有这条能证明端到端可用）──────────────
VTOK=$(cat "$VTOK_FILE")
if ! docker exec -e OPENBAO_TOKEN="$VTOK" vaas-wanxing python3 -c "
import os,urllib.request
urllib.request.urlopen(urllib.request.Request(
  'http://openbao:8200/v1/secret/data/vaas/pool/wanxing/image',
  headers={'X-Vault-Token': os.environ['OPENBAO_TOKEN']}), timeout=8)
" >/dev/null 2>&1; then
  log "WARN: vault unreadable from vaas — restarting vaas (clears credential cache)"
  docker restart vaas-wanxing >/dev/null 2>&1 || true
  sleep 14
  if ! docker exec -e OPENBAO_TOKEN="$VTOK" vaas-wanxing python3 -c "
import os,urllib.request
urllib.request.urlopen(urllib.request.Request(
  'http://openbao:8200/v1/secret/data/vaas/pool/wanxing/image',
  headers={'X-Vault-Token': os.environ['OPENBAO_TOKEN']}), timeout=8)
" >/dev/null 2>&1; then
    log "FATAL: vault still unreadable after restart"; exit 1
  fi
fi

log "ok: openbao unsealed, vaas up, vault readable"