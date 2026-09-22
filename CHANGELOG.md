# NodeVyu Agent Changelog

## 1.2.16

- Makes Video Wall streams video-only when requested by the NodeVyu server, avoiding unnecessary AAC tracks and improving Safari/iOS fragmented-MP4 compatibility.
- Advertises the probed H.264 profile and level in the MP4 MIME codec string instead of always claiming Baseline Level 3.0.
- Uses Baseline Level 3.1 for H.265-to-H.264 live transcodes to provide a predictable mobile-compatible H.264 output profile.
- Direct single-camera live sessions can continue to include audio.


## 1.2.15

- Fixes a malformed literal `\\n` in `sitewatch_agent/__init__.py` that caused Python startup to fail after updating to 1.2.14 on Linux.
- Restores a valid agent version module so the systemd service can import and start normally.

## 1.2.14

- Adds best-effort MAC address discovery for LAN devices using the Windows ARP table or Linux neighbor/ARP tables.
- Reports normalized MAC addresses with network discovery results so DHCP-managed devices can be reconciled by stable hardware identity.
- Adds tests for MAC normalization and cross-platform neighbor parsing.

## 1.2.13

- Frames live fragmented-MP4 output on complete media fragment boundaries instead of arbitrary FFmpeg stdout chunks.
- Allows multiple Video Wall viewers to reuse one upstream camera stream while late-joining browsers start on a valid fragment/keyframe boundary.
- Normalizes the H.264/AAC MIME codec declaration for stricter Safari/iOS playback handling.

## 1.2.12

- Raises the production live-stream safety ceiling from 8 to 16 concurrent streams.
- The NodeVyu server/admin per-agent setting now controls the effective live-stream limit up to 16.
- Production startup no longer lets an older local `SITEWATCH_MAX_LIVE_STREAMS=8` value block an admin-configured limit above 8.
- Updated the example configuration to document the 16-stream ceiling.

## 1.2.11

- Stops polling `/api/agent/snmp-walk` when the agent has no SNMP monitoring checks configured.
- The SNMP worker remains idle locally and automatically resumes polling after refreshed agent configuration includes an SNMP check.
- Adds clear log messages when SNMP polling becomes idle or active.

## 1.2.10

- Corrected the live-stream worker fallback so `SITEWATCH_MAX_LIVE_STREAMS` now actually defaults to 8 in runtime code.
- Keeps the documented Video Wall stream capacity aligned with the agent implementation.
- Existing explicit `SITEWATCH_MAX_LIVE_STREAMS` values remain authoritative.

## 1.2.9

- Raised the default concurrent live-stream capacity from 2 to 8 when no local override is configured.
- Enables Video Wall layouts to open more than two cameras from the same NodeVyu Agent.
- Existing explicit `SITEWATCH_MAX_LIVE_STREAMS` values remain authoritative so appliance-specific limits can still be enforced.
- Updated the example configuration to document the new eight-stream default.
