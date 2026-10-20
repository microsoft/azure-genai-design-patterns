# Changelog

## 0.1.0 - 2026-10-20

- Initial preview release of the secured Microsoft Foundry landing zone.
- Documented the two-stage adoption pattern: an authenticated functional
  validation profile followed by a private-by-default enterprise landing zone.
- Added the architecture deep dive covering platform/application ownership,
  the Prompt Agent and Hosted Agent tool routes, network and DNS topology, and
  the foundation/application lifecycle split.
- Added the six-stage deployment runbook with least-privilege bootstrap,
  foundation, and release identities and the offline Microsoft 365 package
  handoff.
- Added the per-user OAuth identity passthrough and Microsoft 365 publishing
  guide.
- Added ADR 0001 for the two-stage progression, split Bicep lifecycle, and
  separate operator virtual network.
- Added sanitized Bicep templates: `foundation.bicep`,
  `private-foundry-project.bicep`, `private-mcp-server.bicep`,
  `operator-connectivity.bicep`, and a fail-closed `parameters.example.json`.
