#!/usr/bin/env bash
# 重新构建并部署到 Cloudflare Workers。
#
# 为什么要有这个脚本：wrangler 不在系统 PATH 上，装在 WorkBuddy 的托管 node
# 目录里，路径不直观。手工敲一次很容易敲错，或者误用全局 wrangler 导致
# 用了别的版本、别的凭证。
#
# 凭证：wrangler 的 OAuth token 存在
#   C:\Users\<user>\AppData\Roaming\xdg.config\.wrangler\config\default.toml
# 不在 ~/.wrangler 下，所以 `ls ~/.wrangler` 找不到不等于没登录。
# 用 `wrangler whoami` 确认。
set -euo pipefail
cd "$(dirname "$0")"

PY="${PY:-python}"
NODE="${NODE:-node}"

# 优先用托管 node 里的 wrangler，找不到再退回 npx
WRANGLER_JS="C:/Users/cc/.workbuddy-ai/binaries/node/workspace/node_modules/wrangler/bin/wrangler.js"
if [ -f "$WRANGLER_JS" ]; then
  NODE_BIN="C:/Users/cc/.workbuddy-ai/binaries/node/versions/22.22.2-6/node.exe"
  WRANGLER=("$NODE_BIN" "$WRANGLER_JS")
else
  WRANGLER=(npx wrangler)
fi

echo "== 构建 =="
"$PY" build.py
echo
echo "== 预检 =="
"$PY" tools/check_site.py
"$PY" tools/test_calc_parity.py
echo
echo "== 部署 =="
"${WRANGLER[@]}" deploy
