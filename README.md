# SiteWatcher Client

Windows/Linux Docker agent for SiteWatcher.

Current version: **0.6.1**

Features include:

- Heartbeat and server configuration polling
- Device monitoring and result upload
- LAN discovery
- RTSP checks and validated JPEG snapshots
- Browser RTSP preview relay
- Manual monitor retry
- ONVIF discovery with WS-Security UsernameToken
- Single-process and duplicate-worker protection

## Windows Docker

Copy `.env.example` to `.env`, configure the SiteWatcher server URL and agent token, then run:

```powershell
docker compose down --remove-orphans
docker compose up -d --build --force-recreate
docker compose logs -f sitewatch-agent
```

To verify only one container is running:

```powershell
docker ps -a --filter "name=sitewatch-agent"
```
