# NodeVyu Agent Changelog

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
- Updated the example agent configuration to document the new eight-stream default.

