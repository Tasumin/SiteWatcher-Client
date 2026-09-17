# NodeVyu Agent Changelog

## 1.2.10

- Corrected the live-stream worker fallback so `SITEWATCH_MAX_LIVE_STREAMS` now actually defaults to 8 in runtime code.
- Keeps the documented Video Wall stream capacity aligned with the agent implementation.
- Existing explicit `SITEWATCH_MAX_LIVE_STREAMS` values remain authoritative.

## 1.2.9

- Raised the default concurrent live-stream capacity from 2 to 8 when no local override is configured.
- Enables Video Wall layouts to open more than two cameras from the same NodeVyu Agent.
- Existing explicit `SITEWATCH_MAX_LIVE_STREAMS` values remain authoritative so appliance-specific limits can still be enforced.
- Updated the example agent configuration to document the new eight-stream default.

