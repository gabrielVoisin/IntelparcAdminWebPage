#!/usr/bin/env bash
# Intel Park prototype: EVERYTHING on one Raspberry Pi 4 or 5 (Raspberry Pi OS Bookworm 64-bit).
#   * server + PostgreSQL 16 + website (Docker)       -> http://<IP of the Pi>
#   * control cabinet agent (systemd)                  -> talks to the server on 127.0.0.1:8000
#   * simulated car detectors (HTTP on port 8090)      -> POST /simulate/bay/EXT/05 {"occupied": true}
#   * Bluetooth GATT server for the drivers' phones    -> "IntelPark-CTRL-01"
#
# Run as root from anywhere:  sudo ./deploy/raspberry-pi/install-all-in-one.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
# The cabinet agent lives in its own repository (CarDetector), cloned next to this one.
AGENT_DIR="${AGENT_DIR:-$ROOT/../CarDetector}"
[ -f "$AGENT_DIR/install.sh" ] || { echo "Cabinet agent not found in $AGENT_DIR: git clone https://github.com/gabrielVoisin/CarDetector.git next to this folder, or set AGENT_DIR"; exit 1; }
COMPOSE=(docker compose -f docker-compose.yml -f deploy/raspberry-pi/docker-compose.pi.yml)

echo ">>> 1/5 Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

echo ">>> 2/5 Secrets (.env)"
if [ ! -f .env ]; then
  rand() { python3 -c "import secrets; print(secrets.token_urlsafe(48))"; }
  cat > .env <<EOF
DOMAIN=:80
POSTGRES_PASSWORD=$(rand)
JWT_SECRET=$(rand)
BLE_SIGNING_KEY=$(python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())")
APP_ENV=production
COOKIE_SECURE=false
DEFAULT_LOCALE=fr
SMS_DEBUG=true
CORS_ORIGINS=
EOF
  chmod 600 .env
fi

echo ">>> 3/5 Server, database and website"
"${COMPOSE[@]}" up -d --build
for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:8000/healthz >/dev/null || { echo "The server did not start: ${COMPOSE[*]} logs api"; exit 1; }
if [ ! -f .demo-loaded ]; then
  "${COMPOSE[@]}" exec -T api python -m app.cli seed-demo
  touch .demo-loaded
fi
"${COMPOSE[@]}" exec -T api python -m app.cli bay-labels P1 EXT /tmp/bay-labels-P1-EXT.pdf
"${COMPOSE[@]}" cp api:/tmp/bay-labels-P1-EXT.pdf ./bay-labels-P1-EXT.pdf

echo ">>> 4/5 Cabinet agent, simulated detectors, Bluetooth"
install -d -m 755 /etc/parking-agent
if [ ! -f /etc/parking-agent/config.yaml ]; then
  install -m 600 "$AGENT_DIR/config.prototype.yaml" /etc/parking-agent/config.yaml
fi
(cd "$AGENT_DIR" && ./install.sh)
chown parking-agent:parking-agent /etc/parking-agent/config.yaml
systemctl restart parking-agent

echo ">>> 5/5 Done"
IP="$(hostname -I | awk '{print $1}')"
cat <<EOF

  Website (back office) : http://$IP            responsable@example.com / ChangeMe-2026!
  Android app           : server address http://$IP   (Welcome screen > gear icon)
  API documentation     : http://$IP/api/docs
  QR stickers           : $ROOT/bay-labels-P1-EXT.pdf
  Agent logs            : journalctl -u parking-agent -f

  Simulate a car on bay P1 / EXT / 05:
    curl -X POST http://$IP:8090/simulate/bay/EXT/05 -H 'Content-Type: application/json' -d '{"occupied": true}'
  ...and its departure:
    curl -X POST http://$IP:8090/simulate/bay/EXT/05 -H 'Content-Type: application/json' -d '{"occupied": false}'

  Change the demo passwords and keys before showing it outside your network.
EOF
