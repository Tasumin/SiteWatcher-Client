#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_PATH="/opt/nodevyu-agent"
SERVER_URL="https://nodevyu.com"
AGENT_TOKEN=""
ENROLLMENT_KEY="__SITEWATCH_ENROLLMENT_KEY__"
DISCOVERY_CIDRS=""
SERVICE_NAME="nodevyu-agent"
INSTALLER_BUILD="1.1.1-linux"
AGENT_COMMIT="__SITEWATCH_AGENT_COMMIT__"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-path) INSTALL_PATH="$2"; shift 2 ;;
    --server-url) SERVER_URL="$2"; shift 2 ;;
    --agent-token) AGENT_TOKEN="$2"; shift 2 ;;
    --enrollment-key) ENROLLMENT_KEY="$2"; shift 2 ;;
    --discovery-cidrs) DISCOVERY_CIDRS="$2"; shift 2 ;;
    -h|--help) echo "Usage: sudo bash install-nodevyu-linux.sh [--install-path PATH] [--server-url URL] [--agent-token TOKEN] [--enrollment-key KEY] [--discovery-cidrs CIDRS]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$EUID" -eq 0 ]] || { echo "Root privileges are required. Re-run with sudo." >&2; exit 1; }
SERVER_URL="$(printf '%s' "$SERVER_URL" | sed 's:/*$::')"
ENV_FILE="$INSTALL_PATH/.env"
TMP_ROOT="$(mktemp -d /tmp/nodevyu-agent.XXXXXX)"
ENV_BACKUP=""
WAS_INSTALLED=0
trap 'rm -rf "$TMP_ROOT"; [[ -z "$ENV_BACKUP" ]] || rm -f "$ENV_BACKUP"' EXIT

recover() {
  rc=$?
  if [[ -n "$ENV_BACKUP" && -f "$ENV_BACKUP" ]]; then mkdir -p "$INSTALL_PATH"; cp -f "$ENV_BACKUP" "$ENV_FILE" || true; fi
  if [[ "$WAS_INSTALLED" -eq 1 ]]; then systemctl daemon-reload || true; systemctl start "$SERVICE_NAME" || true; fi
  exit "$rc"
}
trap recover ERR

echo "NodeVyu Linux installer build: $INSTALLER_BUILD"
if [[ -f "$ENV_FILE" ]]; then
  WAS_INSTALLED=1
  ENV_BACKUP="$(mktemp /tmp/nodevyu-env.XXXXXX)"
  cp -f "$ENV_FILE" "$ENV_BACKUP"
  echo "Existing NodeVyu configuration found and will be preserved."
fi

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip ffmpeg iputils-ping curl ca-certificates tar >/dev/null
else
  for cmd in python3 curl ffmpeg ffprobe ping; do command -v "$cmd" >/dev/null 2>&1 || { echo "Missing $cmd. Automatic dependency installation currently supports Debian/Ubuntu." >&2; exit 1; }; done
fi

if [[ "$WAS_INSTALLED" -eq 1 ]]; then
  OLD_SERVER="$(grep -E '^SITEWATCH_SERVER_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  OLD_TOKEN="$(grep -E '^SITEWATCH_AGENT_TOKEN=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  OLD_CIDRS="$(grep -E '^SITEWATCH_DISCOVERY_CIDRS=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  [[ -z "$OLD_SERVER" ]] || SERVER_URL="$OLD_SERVER"
  [[ -z "$OLD_TOKEN" ]] || AGENT_TOKEN="$OLD_TOKEN"
  [[ -z "$OLD_CIDRS" ]] || DISCOVERY_CIDRS="$OLD_CIDRS"
fi

if [[ -z "$AGENT_TOKEN" && "$WAS_INSTALLED" -eq 0 ]]; then
  [[ -n "$ENROLLMENT_KEY" && "$ENROLLMENT_KEY" != "__SITEWATCH_ENROLLMENT_KEY__" ]] || { echo "No token or enrollment key. Download a fresh installer from $SERVER_URL/downloads or provide --agent-token." >&2; exit 1; }
  MACHINE_ID="$(cat /etc/machine-id 2>/dev/null || true)"
  PAYLOAD="$(python3 - "$ENROLLMENT_KEY" "$(hostname)" "$MACHINE_ID" <<'PY'
import json, sys
print(json.dumps({"enrollmentKey":sys.argv[1],"hostname":sys.argv[2],"machineId":sys.argv[3]}))
PY
)"
  RESPONSE="$(curl -fsS --connect-timeout 15 --max-time 30 -H 'Content-Type: application/json' -d "$PAYLOAD" "$SERVER_URL/api/agent/enroll")"
  AGENT_TOKEN="$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("token") or "").strip())')"
  [[ -n "$AGENT_TOKEN" ]] || { echo "NodeVyu enrollment did not return an agent token." >&2; exit 1; }
  echo "Linux agent enrolled successfully."
fi
[[ -n "$AGENT_TOKEN" ]] || { echo "Agent token is required." >&2; exit 1; }

systemctl stop "$SERVICE_NAME" 2>/dev/null || true
ARCHIVE="$TMP_ROOT/agent.tar.gz"
if [[ ! "$AGENT_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "Installer was not server-pinned; resolving current production agent commit..."
  AGENT_COMMIT="$(curl -fsSL --connect-timeout 15 --max-time 30 -H 'Accept: application/vnd.github+json' -H 'User-Agent: NodeVyu' https://api.github.com/repos/Tasumin/SiteWatcher-Client/commits/main | python3 -c 'import json,sys; print((json.load(sys.stdin).get("sha") or "").strip())')"
fi
[[ "$AGENT_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Unable to resolve a valid NodeVyu agent commit." >&2; exit 1; }
curl -fsSL --connect-timeout 20 --max-time 120 "https://github.com/Tasumin/SiteWatcher-Client/archive/$AGENT_COMMIT.tar.gz" -o "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$TMP_ROOT"
REPO_ROOT="$TMP_ROOT/SiteWatcher-Client-$AGENT_COMMIT"
[[ -d "$REPO_ROOT/sitewatch_agent" ]] || { echo "Downloaded package is invalid." >&2; exit 1; }

mkdir -p "$INSTALL_PATH" "$INSTALL_PATH/logs" "$INSTALL_PATH/data"
find "$INSTALL_PATH" -mindepth 1 -maxdepth 1 ! -name '.env' ! -name '.venv' ! -name 'logs' ! -name 'data' -exec rm -rf {} +
cp -a "$REPO_ROOT/." "$INSTALL_PATH/"

if [[ ! -x "$INSTALL_PATH/.venv/bin/python" ]]; then rm -rf "$INSTALL_PATH/.venv"; python3 -m venv "$INSTALL_PATH/.venv"; fi
"$INSTALL_PATH/.venv/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade pip
"$INSTALL_PATH/.venv/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade -r "$INSTALL_PATH/requirements.txt"

if [[ "$WAS_INSTALLED" -eq 1 ]]; then
  cp -f "$ENV_BACKUP" "$ENV_FILE"
else
  printf 'SITEWATCH_SERVER_URL=%s\nSITEWATCH_AGENT_TOKEN=%s\n' "$SERVER_URL" "$AGENT_TOKEN" > "$ENV_FILE"
  [[ -z "$DISCOVERY_CIDRS" ]] || printf 'SITEWATCH_DISCOVERY_CIDRS=%s\n' "$DISCOVERY_CIDRS" >> "$ENV_FILE"
fi
grep -q '^SITEWATCH_LOCAL_ADMIN_ENABLED=' "$ENV_FILE" || printf 'SITEWATCH_LOCAL_ADMIN_ENABLED=true\n' >> "$ENV_FILE"
grep -q '^SITEWATCH_LOCAL_ADMIN_PORT=' "$ENV_FILE" || printf 'SITEWATCH_LOCAL_ADMIN_PORT=8765\n' >> "$ENV_FILE"
grep -q '^SITEWATCH_LOCAL_ADMIN_LAN_ACCESS=' "$ENV_FILE" || printf 'SITEWATCH_LOCAL_ADMIN_LAN_ACCESS=false\n' >> "$ENV_FILE"
chmod 600 "$ENV_FILE"

cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=NodeVyu Linux Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_PATH
ExecStart=$INSTALL_PATH/.venv/bin/python -u -m sitewatch_agent.service_entry
Restart=always
RestartSec=5
TimeoutStopSec=20
KillSignal=SIGINT
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

chmod +x "$INSTALL_PATH/install-nodevyu-linux.sh" "$INSTALL_PATH/run-nodevyu-linux.sh" "$INSTALL_PATH/uninstall-nodevyu-linux.sh" 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
sleep 2
systemctl is-active --quiet "$SERVICE_NAME" || { systemctl status "$SERVICE_NAME" --no-pager || true; journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true; exit 1; }

VERSION="$("$INSTALL_PATH/.venv/bin/python" -c 'from sitewatch_agent import __version__; print(__version__)' 2>/dev/null || echo unknown)"
echo "NodeVyu Linux agent installed/upgraded successfully."
echo "Service: $SERVICE_NAME ($(systemctl is-active "$SERVICE_NAME"))"
echo "Agent version: $VERSION"
echo "Install path: $INSTALL_PATH"
echo "Local admin: http://127.0.0.1:8765"
