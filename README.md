# SiteWatcher Client

Native Windows monitoring agent for SiteWatcher.

Current version: **0.8.2**

The Windows client runs as a real Windows service named **SiteWatcherAgent** using WinSW. Docker and WSL are not required.

## Features

- Windows service with Automatic (Delayed Start)
- Automatic restart on service failure
- In-place client upgrades
- Heartbeat and server configuration polling
- Device monitoring and result upload
- Multiple-CIDR LAN discovery
- Manual discovery refresh
- RTSP monitoring and validated JPEG snapshots
- Browser RTSP preview relay
- Manual monitor retry
- ONVIF discovery
- ONVIF WS-Security UsernameToken authentication
- Per-camera ONVIF credential retry
- Device information, media profile and RTSP URI discovery
- Local result queue when the SiteWatcher server is temporarily unavailable
- Duplicate-worker protection

## Windows Installation

Run PowerShell as Administrator and download/run `run-sitewatcher-native.ps1`.

The default installation directory is:

```text
C:\SiteWatcher-Agent
```

On the first run, the installer will:

1. Check for a working Python installation and install Python if required.
2. Download the latest SiteWatcher client.
3. Create the Python virtual environment.
4. Install the required Python dependencies.
5. Install/check FFmpeg for RTSP monitoring and snapshots.
6. Ask for the SiteWatcher server URL, agent token and discovery CIDR ranges if they are not already configured.
7. Install the **SiteWatcherAgent** Windows service using WinSW.
8. Configure the service for Automatic (Delayed Start) and restart-on-failure.
9. Start the service.

Existing `.env` configuration is preserved during upgrades.

## Service Management

Use the launcher to manage the installed agent:

```powershell
.\run-sitewatcher-native.ps1 -Action Status
.\run-sitewatcher-native.ps1 -Action Start
.\run-sitewatcher-native.ps1 -Action Stop
.\run-sitewatcher-native.ps1 -Action Restart
.\run-sitewatcher-native.ps1 -Action Upgrade
```

You can also use normal Windows service commands:

```powershell
Get-Service SiteWatcherAgent
Start-Service SiteWatcherAgent
Stop-Service SiteWatcherAgent
Restart-Service SiteWatcherAgent
```

## Upgrading

Run:

```powershell
.\run-sitewatcher-native.ps1 -Action Upgrade
```

The upgrade process stops the service, downloads the latest client, preserves the existing `.env`, updates dependencies and service configuration, then starts the service again.

## Configuration

Site-specific configuration is stored at:

```text
C:\SiteWatcher-Agent\.env
```

Important settings include:

```text
SITEWATCH_SERVER_URL=https://your-sitewatcher-server.example
SITEWATCH_AGENT_TOKEN=your-agent-token
SITEWATCH_DISCOVERY_CIDRS=192.168.1.0/24,192.168.4.0/24
SITEWATCH_DISCOVERY_INTERVAL_SECONDS=900
SITEWATCH_SNAPSHOT_INTERVAL_SECONDS=300
```

Multiple discovery networks can be supplied as a comma-separated list.

## Logs

Agent and Windows service wrapper logs are stored under:

```text
C:\SiteWatcher-Agent\logs\
```

To watch the agent log from PowerShell:

```powershell
Get-Content "C:\SiteWatcher-Agent\logs\agent.log" -Wait
```

## Requirements

- Windows 10/11 or Windows Server with PowerShell 5.1+
- Administrator access for installation/service management
- Network access to the SiteWatcher server
- Network access to the devices being monitored

Python, required packages, WinSW and FFmpeg are handled by the native installer where possible.

## Deployment Model

The supported SiteWatcher Client deployment is the **native Windows service**. The previous Docker/Docker Compose deployment has been removed.
