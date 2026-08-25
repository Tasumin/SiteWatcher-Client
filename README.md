# NodeVyu Agent

Native Windows monitoring agent for **NodeVyu**.

Current version: **1.0.0**

The supported Windows agent runs as a real Windows service named **NodeVyuAgent** using WinSW. Docker and WSL are not required.

The primary NodeVyu server is:

```text
https://nodevyu.com
```

Existing SiteWatcher agents that use `https://monitoring.talondns.com` remain supported during the migration.

## Features

- Windows service with Automatic (Delayed Start)
- Automatic restart on service failure
- In-place and remote agent upgrades
- Heartbeat and server configuration polling
- Device monitoring and result upload
- Ping, TCP, HTTP and HTTPS monitoring
- Multiple-CIDR LAN discovery
- Manual discovery refresh
- RTSP monitoring and validated JPEG snapshots
- Browser RTSP preview relay
- Manual monitor retry
- ONVIF discovery and diagnostics
- ONVIF WS-Security UsernameToken authentication
- Per-camera ONVIF credential retry
- Device information, media profile and RTSP URI discovery
- Local result queue when the NodeVyu server is temporarily unavailable
- Remote console and agent host monitoring
- Duplicate-worker protection

## Windows Installation

Run PowerShell as Administrator and use the native installer/launcher.

New installations default to:

```text
C:\NodeVyu-Agent
```

and use:

```text
https://nodevyu.com
```

The installer creates the **NodeVyuAgent** Windows service.

## SiteWatcher Migration

The NodeVyu installer automatically detects an existing legacy installation at:

```text
C:\SiteWatcher-Agent
```

with service:

```text
SiteWatcherAgent
```

During migration it:

1. Preserves the existing `.env`, including the agent token and current server URL.
2. Preserves local data, logs and FFmpeg binaries where available.
3. Stops and removes the legacy SiteWatcher Windows service.
4. Installs NodeVyu under `C:\NodeVyu-Agent`.
5. Creates and starts the `NodeVyuAgent` service.
6. Reuses the existing agent identity instead of enrolling a duplicate device.
7. Leaves the old `C:\SiteWatcher-Agent` folder in place for rollback/log history.

An existing agent configured for `monitoring.talondns.com` is intentionally left on that hostname during migration. Both hostnames point to the same NodeVyu server, so the service migration does not depend on a simultaneous server URL cutover.

## Service Management

Use the native launcher:

```powershell
.\run-sitewatcher-native.ps1 -Action Status
.\run-sitewatcher-native.ps1 -Action Start
.\run-sitewatcher-native.ps1 -Action Stop
.\run-sitewatcher-native.ps1 -Action Restart
.\run-sitewatcher-native.ps1 -Action Upgrade
```

Windows service commands:

```powershell
Get-Service NodeVyuAgent
Start-Service NodeVyuAgent
Stop-Service NodeVyuAgent
Restart-Service NodeVyuAgent
```

## Configuration

NodeVyu intentionally retains the existing `SITEWATCH_*` environment variable names for backward compatibility with deployed agents and the server API.

New installations store configuration at:

```text
C:\NodeVyu-Agent\.env
```

Example:

```text
SITEWATCH_SERVER_URL=https://nodevyu.com
SITEWATCH_AGENT_TOKEN=your-agent-token
SITEWATCH_DISCOVERY_CIDRS=192.168.1.0/24,192.168.4.0/24
SITEWATCH_DISCOVERY_INTERVAL_SECONDS=900
SITEWATCH_SNAPSHOT_INTERVAL_SECONDS=300
```

## Logs

New NodeVyu agent logs are stored under:

```text
C:\NodeVyu-Agent\logs\
```

To watch the main log:

```powershell
Get-Content "C:\NodeVyu-Agent\logs\agent.log" -Wait
```

## Compatibility

The rebrand deliberately does **not** rename the internal Python package (`sitewatch_agent`), existing `SITEWATCH_*` configuration keys, local queue database names, or `/api/agent/*` server routes. These are implementation identifiers and keeping them stable makes upgrades safe for existing installations.

The legacy `/downloads/sitewatcher-agent` server route is also supported during the migration so old agents can self-update into NodeVyu.

## Requirements

- Windows 10/11 or Windows Server with PowerShell 5.1+
- Administrator access for installation/service management
- Network access to the NodeVyu server
- Network access to the devices being monitored

Python, required packages, WinSW and FFmpeg are handled by the native installer where possible.
