#!/bin/bash
set -e
cd "$(dirname "$0")"
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "❌ cloudflared o'rnatilmagan. O'rnating:  brew install cloudflared"; exit 1
fi
if [ ! -d .venv ]; then echo "📦 Python muhiti yaratilmoqda..."; python3 -m venv .venv; fi
source .venv/bin/activate
pip install -q -r requirements.txt
set -a; source .env; set +a
export PORT=8080
echo "🌐 Tunnel ochilmoqda..."
cloudflared tunnel --url "http://localhost:$PORT" > tunnel.log 2>&1 &
TUNNEL_PID=$!
trap 'kill $TUNNEL_PID 2>/dev/null' EXIT
for i in $(seq 1 30); do
  WEBAPP_URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunnel.log | head -1 || true)
  [ -n "$WEBAPP_URL" ] && break; sleep 1
done
[ -z "$WEBAPP_URL" ] && { echo "❌ Tunnel manzili olinmadi. tunnel.log ni ko'ring."; exit 1; }
export WEBAPP_URL
echo "✅ Mini app manzili: $WEBAPP_URL"
echo "🤖 Bot ishga tushdi. To'xtatish: Ctrl + C"
python main.py
