#!/usr/bin/env bash
set -euo pipefail
INSTALL_PATH="/opt/nodevyu-agent"
PURGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-path) INSTALL_PATH="$2"; shift 2 ;;
    --purge) PURGE=1; shift ;;
    *) echo "Usage: $0 [--install-path PATH] [--purge]" >&2; exit 2 ;;
  esac
done
[[ "$EUID" -eq 0 ]] || { echo "Root privileges are required. Re-run with sudo." >&2; exit 1; }
systemctl disable --now nodevyu-agent 2>/dev/null || true
rm -f /etc/systemd/system/nodevyu-agent.service
systemctl daemon-reload
if [[ "$PURGE" -eq 1 ]]; then rm -rf "$INSTALL_PATH"; echo "NodeVyu Linux agent and local data removed."; else echo "Service removed. Agent files retained at $INSTALL_PATH. Use --purge to delete them."; fi
