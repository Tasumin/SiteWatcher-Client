#!/usr/bin/env bash
set -euo pipefail
INSTALL_PATH="/opt/nodevyu-agent"
ACTION="status"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-path) INSTALL_PATH="$2"; shift 2 ;;
    start|stop|restart|status|upgrade) ACTION="$1"; shift ;;
    *) echo "Usage: $0 [--install-path PATH] {start|stop|restart|status|upgrade}" >&2; exit 2 ;;
  esac
done
[[ "$EUID" -eq 0 ]] || { echo "Root privileges are required. Re-run with sudo." >&2; exit 1; }
SERVICE="nodevyu-agent"
case "$ACTION" in
  start) systemctl start "$SERVICE" ;;
  stop) systemctl stop "$SERVICE" ;;
  restart) systemctl restart "$SERVICE" ;;
  upgrade)
    TMP="$(mktemp /tmp/install-nodevyu-linux.XXXXXX.sh)"
    trap 'rm -f "$TMP"' EXIT
    curl -fsSL "https://raw.githubusercontent.com/Tasumin/SiteWatcher-Client/main/install-nodevyu-linux.sh" -o "$TMP"
    bash "$TMP" --install-path "$INSTALL_PATH"
    exit 0 ;;
esac
echo "NodeVyu Linux Agent"
echo "Service      : $SERVICE"
echo "Status       : $(systemctl is-active "$SERVICE" 2>/dev/null || true)"
echo "Install path : $INSTALL_PATH"
echo "Server URL   : $(grep -E '^SITEWATCH_SERVER_URL=' "$INSTALL_PATH/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
echo "Log          : $INSTALL_PATH/logs/agent.log"
echo
echo "Journal      : journalctl -u $SERVICE -f"
