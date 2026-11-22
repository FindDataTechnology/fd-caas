#!/usr/bin/env bash
# VAAS hosted 栈部署脚本（cheap9 实测配方，2026-11-22）。
#
# 为什么要成脚本：这套东西在 deploy/README.md 里是**设计文档**，实际组装时踩了六个坑
# （见每段注释），重来一次会原样重踩。脚本化成一条命令后，"有没有生产实例"至少是
# 可复现的，而不是一次性的手工操作。
#
# 与 README 的差异（都是实测结论，不是偏好）：
#   1. OpenBAO 的 file 存储类型在 v2.7 **不存在**（只有 raft）。照 README 写 `storage "file"` 会
#      报 `unknown storage type file` 并 crash-loop。
#   2. OpenBAO 默认**不启用 kv 引擎**（Vault 有）。必须先 `secrets enable -path=secret -version=2 kv`，
#      否则 `kv put` 报 403「preflight capability check」。
#   3. token 的 `-period=87600h` 会被 `max-lead-ttl` 砍到 768h。要真 10 年，必须先
#      `auth tune -max-lease-ttl=87600h token/`。
#   4. `bao policy write <name> <file>` 读的是**容器内**路径；宿主机文件要用 `-` 走 stdin。
#   5. VAAS 容器连 vault 不能走 `host.docker.internal`——OpenBAO 只绑了回环。两个容器要同
#      一个 docker 网络，用容器名互访。
#   6. `kv put k=v ...` 会把整串当**一个字段值**。要结构化记录必须走 HTTP API 的 JSON body。
#
# 前置：宿主机已 docker，且能拉 ccr 镜像（cheap9 已验证）。
# 用法：
#   ./deploy/provision-hosted.sh --image ccr.ccs.tencentyun.com/yizuo/vaas-mcp-server:sha-XXXX \
#       --image-provider grok --image-model grok-imagine-image \
#       --gateway https://router.finddatatech.cloud \
#       --image-key-file /path/key --tts-key-file /path/tts --public-url http://100.64.0.17:30950
set -euo pipefail

STACK=/opt/vaas-stack
IMAGE="" PROVIDER="" MODEL="" GATEWAY="" IKEY="" TKEY="" PUBLIC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2;;
    --image-provider) PROVIDER="$2"; shift 2;;
    --image-model) MODEL="$2"; shift 2;;
    --gateway) GATEWAY="$2"; shift 2;;
    --image-key-file) IKEY="$2"; shift 2;;
    --tts-key-file) TKEY="$2"; shift 2;;
    --public-url) PUBLIC="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
for v in IMAGE PROVIDER MODEL GATEWAY IKEY TKEY PUBLIC; do
  [ -n "${!v}" ] || { echo "missing --$(echo $v | tr 'A-Z_' 'a-z-')" >&2; exit 2; }
done

mkdir -p "$STACK/openbao/data" "$STACK/data" "$STACK/downloads"
cd "$STACK"
docker network create vaas-net >/dev/null 2>&1 || true

# ── 1. OpenBAO（raft 存储；首次 init 生成 unseal key + root token）─────────────
if [ ! -f "$STACK/openbao/init.json" ]; then
  cat > openbao/config.hcl <<'EOF'
storage "raft" { path = "/openbao/data" }
listener "tcp" { address = "0.0.0.0:8200" tls_disable = 1 }
api_addr = "http://openbao:8200"
cluster_addr = "http://openbao:8201"
EOF
  docker rm -f openbao >/dev/null 2>&1 || true
  docker run -d --name openbao --restart unless-stopped --network vaas-net \
    -p 127.0.0.1:8200:8200 \
    -v "$STACK/openbao/data":/openbao/data \
    -v "$STACK/openbao/config.hcl":/openbao/config.hcl:ro \
    quay.io/openbao/openbao:latest server -config=/openbao/config.hcl >/dev/null
  sleep 8
  docker exec -e BAO_ADDR=http://127.0.0.1:8200 openbao \
    bao operator init -key-shares=1 -key-threshold=1 -format=json > openbao/init.json
  chmod 600 openbao/init.json
fi
UK=$(python3 -c "import json;print(json.load(open('$STACK/openbao/init.json'))['unseal_keys_b64'][0])")
RT=$(python3 -c "import json;print(json.load(open('$STACK/openbao/init.json'))['root_token'])")
docker exec -e BAO_ADDR=http://127.0.0.1:8200 openbao bao operator unseal "$UK" >/dev/null 2>&1 || true

# ── 2. kv 引擎 + 策略 + 长期 token（坑 2/3/4）──────────────────────────────
docker exec -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$RT" openbao \
  bao secrets enable -path=secret -version=2 kv >/dev/null 2>&1 || true
echo 'path "secret/data/vaas/*" { capabilities = ["read"] }' > /tmp/vaas-policy.hcl
docker exec -i -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$RT" openbao \
  bao policy write vaas-policy - < /tmp/vaas-policy.hcl >/dev/null
docker exec -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$RT" openbao \
  bao auth tune -max-lease-ttl=87600h -default-lease-ttl=87600h token/ >/dev/null 2>&1 || true
if [ ! -s "$STACK/vaas-token.txt" ]; then
  docker exec -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$RT" openbao \
    bao token create -policy=vaas-policy -period=87600h -orphan -renewable=true -format=json \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['auth']['client_token'])" > vaas-token.txt
  chmod 600 vaas-token.txt
fi
VTOK=$(cat vaas-token.txt)

# ── 3. 凭证记录（坑 6：必须 JSON body，不能 k=v）───────────────────────────
put() { # path provider model key extra
  local body
  body=$(python3 -c "
import json,sys
d={'provider':sys.argv[1],'base_url':sys.argv[2],'api_key':sys.argv[3],'model':sys.argv[4]}
if len(sys.argv)>5 and sys.argv[5]: d.update(json.loads(sys.argv[5]))
print(json.dumps({'data':d}))" "$2" "$GATEWAY" "$4" "$3" "${5:-}")
  curl -sf -X POST "http://127.0.0.1:8200/v1/secret/data/vaas/$1" \
    -H "X-Vault-Token: $RT" -H 'Content-Type: application/json' -d "$body" >/dev/null
  echo "  $1 ok"
}
echo "== credentials =="
put pool/wanxing/image "$PROVIDER" "$MODEL" "$(cat "$IKEY")" ''
put pool/wanxing/video "$PROVIDER" "grok-imagine-video" "$(cat "$IKEY")" ''
put pool/wanxing/tts ciyuan tencent-tts "$(cat "$TKEY")" '{"voice":"101001"}'
# 下载签名密钥：缺失时 hosted 的每次出图都在最后一步失败（实测）
if ! curl -sf -H "X-Vault-Token: $RT" \
     http://127.0.0.1:8200/v1/secret/data/vaas/service/default/download-hmac >/dev/null 2>&1; then
  HMAC=$(head -c 48 /dev/urandom | base64 | tr -d '/+=' | head -c 64)
  curl -sf -X POST http://127.0.0.1:8200/v1/secret/data/vaas/service/default/download-hmac \
    -H "X-Vault-Token: $RT" -H 'Content-Type: application/json' \
    -d "{\"data\":{\"value\":\"$HMAC\"}}" >/dev/null
  echo "  download-hmac ok (generated)"
fi

# ── 4. VAAS（同网络，坑 5）────────────────────────────────────────────────
[ -s "$STACK/vaas-bearer.txt" ] || { head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40 > "$STACK/vaas-bearer.txt"; chmod 600 "$STACK/vaas-bearer.txt"; }
BEARER=$(cat vaas-bearer.txt)
echo "== start vaas =="
docker rm -f vaas-wanxing >/dev/null 2>&1 || true
docker run -d --name vaas-wanxing --restart unless-stopped --network vaas-net \
  -p 127.0.0.1:30950:30950 \
  -v "$STACK/data":/app/data -v "$STACK/downloads":/app/downloads \
  -v "$STACK/pricing.json":/run-config/pricing.json:ro \
  -e VAAS_HOSTED=1 -e VAAS_LINE=wanxing -e VAAS_PORT=30950 -e VAAS_BIND=0.0.0.0 \
  -e VAAS_BEARER_TOKEN="$BEARER" \
  -e OPENBAO_ADDR=http://openbao:8200 -e OPENBAO_TOKEN="$VTOK" \
  -e VAAS_PUBLIC_URL="$PUBLIC" -e VAAS_PRICING_FILE=/run-config/pricing.json \
  "$IMAGE" >/dev/null
sleep 14
echo -n "health: "; curl -s -m 8 http://127.0.0.1:30950/health || { echo FAIL; docker logs vaas-wanxing 2>&1 | tail -15; exit 1; }
echo
echo "done. bearer at $STACK/vaas-bearer.txt"