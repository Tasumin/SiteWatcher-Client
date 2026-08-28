# NodeVyu Agent

Native Windows monitoring agent for **NodeVyu**.

Current version: **1.1.7**

The supported Windows agent runs as a Windows service named **NodeVyuAgent** using WinSW. Docker and WSL are not required.

Primary NodeVyu server:

```text
https://nodevyu.com
```

Existing agents that still use `https://monitoring.talondns.com` remain supported during the migration.

## Features

- Windows service with Automatic (Delayed Start)
- Automatic restart on service failure
- In-place and remote agent upgrades
- Heartbeat and server configuration polling
- Device monitoring and result upload
- Ping, TCP, HTTP and HTTPS monitoring
- Multiple-CIDR LAN discovery
- Manual discovery refresh
- Confirmed SNMP UDP/161 discovery
- SNMP v1/v2c on-demand walks
- Persistent targeted SNMP OID monitoring
- SNMP numeric, text and regex rule evaluation
- RTSP monitoring and validated JPEG snapshots
- Browser RTSP preview relay
- Manual monitor retry
- ONVIF discovery and diagnostics
- ONVIF WS-Security UsernameToken authentication
- Per-camera ONVIF credential retry
- Device information, media profile and RTSP URI discovery
- Local result queue while the NodeVyu server is unavailable
- Remote console, reverse tunnel and agent host monitoring
- Duplicate-worker protection

## Windows installation

Run PowerShell as Administrator and use the native installer/launcher.

New installations default to:

```text
C:\NodeVyu-Agent
```

The installer creates the **NodeVyuAgent** Windows service and installs/upgrades Python dependencies from `requirements.txt`.

## SiteWatcher migration

The NodeVyu installer automatically detects a legacy installation at:

```text
C:\SiteWatcher-Agent
```

with service:

```text
SiteWatcherAgent
```

During migration it preserves the existing `.env`, local data, logs and agent identity, removes the old service, installs NodeVyu under `C:\NodeVyu-Agent`, and creates the `NodeVyuAgent` service. The old folder is retained for rollback/log history.

## Service management

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

NodeVyu intentionally retains the existing `SITEWATCH_*` environment names for backward compatibility.

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
SITEWATCH_SNMP_DISCOVERY_COMMUNITIES=public,monitoring
```

## SNMP discovery

The normal discovery pipeline checks the existing TCP service ports and also probes **UDP/161** using a lightweight SNMP GET.

A host is marked SNMP-capable only when a valid SNMP response is received. A UDP timeout is not treated as proof that port 161 is open.

Default SNMP discovery community:

```text
public
```

Additional communities can be supplied with:

```text
SITEWATCH_SNMP_DISCOVERY_COMMUNITIES=public,mycommunity,monitoring
```

The discovery result reports SNMP availability/version and device description, but the working discovery community is not sent back to the server.

## SNMP walks and monitors

Stage 1 supports on-demand SNMP v1/v2c walks queued by the NodeVyu server and executed locally by the agent.

Stage 2 uses targeted SNMP GETs for selected OIDs. Each saved monitor can use one of these healthy-state operators:

- exists
- equals / not equals
- greater than / greater than or equal
- less than / less than or equal
- contains / does not contain
- regex match / regex does not match

Numeric operators compare numeric values. Regex rules are evaluated by the Python regex engine. SNMP failures and threshold mismatches are returned as normal device check details and participate in the standard device alert/recovery pipeline.

## Logs

```text
C:\NodeVyu-Agent\logs\
```

Watch the primary log:

```powershell
Get-Content "C:\NodeVyu-Agent\logs\agent.log" -Wait
```

## Compatibility

The rebrand deliberately does **not** rename the internal Python package (`sitewatch_agent`), existing `SITEWATCH_*` configuration keys, local queue database names, or `/api/agent/*` routes. The legacy `/downloads/sitewatcher-agent` route is also preserved so older agents can self-update safely.

## Requirements

- Windows 10/11 or Windows Server
- PowerShell 5.1+
- Administrator access for installation/service management
- Network access to the NodeVyu server
- LAN access to monitored devices

Python, required packages, WinSW and FFmpeg are handled by the native installer where possible.
