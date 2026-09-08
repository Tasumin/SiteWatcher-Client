# NodeVyu Linux Agent Project Plan

## Goal
Ship a native Linux build of the existing NodeVyu Python agent without forking the monitoring runtime. Linux is a platform target inside `Tasumin/SiteWatcher-Client`, alongside the existing Windows service packaging.

## Production target
- Ubuntu Server 24.04 LTS, Linux Mint 22.x, and Debian 12 on x86_64.
- systemd service named `nodevyu-agent`.
- Default install path: `/opt/nodevyu-agent`.
- Existing `SITEWATCH_*` configuration keys and server APIs remain unchanged.
- Same agent version for Windows and Linux.

## Phase 1 - Linux native lifecycle
- Add Bash installer equivalent to the Windows PowerShell installer.
- Install Python 3, venv dependencies, FFmpeg/FFprobe, ping, curl and CA certificates.
- Support token-based install and enrollment-key install.
- Preserve `.env`, logs, data and venv during upgrades.
- Install/start/restart through systemd.
- Add Linux status/start/stop/restart/upgrade helper and uninstall helper.

## Phase 2 - Runtime compatibility
- Keep shared ping/TCP/HTTP/HTTPS/RTSP/SNMP/ONVIF/discovery/live-stream code.
- Add Linux host CPU, memory, disk and systemd service monitoring.
- Add POSIX duplicate-process protection at service entry.
- Make remote service restart and self-update platform-aware.
- Keep Windows-only TightVNC and virtual-display maintenance disabled on Linux.

## Phase 3 - Remote management
- Support Linux diagnostic shell commands and an explicitly authorized full Bash shell.
- Report platform/architecture to the server once the server schema/UI is updated.
- Add Linux-aware download/update routing on the NodeVyu server instead of relying on the GitHub bootstrap installer.

## Phase 4 - Production hardening
- Pin Linux installs and updates to an exact agent commit resolved by the NodeVyu server.
- Run as a systemd service with automatic restart and duplicate-process protection.
- Preserve identity, logs, queue data and configuration during upgrades.
- Provide platform-aware remote restart/update and browser Live SSH.
- Provide a localhost troubleshooting UI for status, logs, connectivity, updates, OpenSSH and safe configuration.
- Keep the local admin interface bound to localhost unless LAN exposure is explicitly enabled.
- Continue release testing on Ubuntu Server 24.04 LTS, Linux Mint 22.x and Debian 12.
- Add ARM64 only after separate validation.
- Signed release artifacts and automated rollback packages remain future hardening work.

## Production acceptance criteria
1. Fresh install enrolls and starts on Ubuntu/Debian.
2. Agent heartbeat/config polling and monitoring workers stay online after reboot.
3. Ping, TCP, HTTP(S), RTSP snapshots/live streaming, SNMP and ONVIF use the shared Python runtime.
4. Host monitoring reports Linux CPU, memory, disks and requested systemd services.
5. Remote restart and remote self-update work without PowerShell.
6. Re-running the installer upgrades in place while preserving identity/configuration.
7. Existing Windows behavior remains unchanged.
8. Local troubleshooting UI is available on both Windows and Linux without exposing the agent token.
9. Linux OpenSSH can be installed/repaired from both the central NodeVyu UI and local agent UI.
10. Linux downloads and self-updates use the server-resolved pinned production payload.
11. Shared code compiles and tests on Linux; Windows service packaging remains supported.
