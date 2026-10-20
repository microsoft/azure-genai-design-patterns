# Changelog

## 0.1.0 - 2026-10-20

- Initial preview release of the Microsoft 365 Copilot + MCP plug-in integration pattern.
- Documented the two registration paths: declarative-agent MCP plug-in (`ai-plugin.json` v2.4 `RemoteMCPServer`) and tenant-wide agent connectors (`agentConnectors`, app manifest v1.27+).
- Added package anatomy, pinned vs. dynamic tool discovery, progressive disclosure with `SKILL.md`, MCP Apps, and action-safety guidance in `docs/architecture.md`.
- Added the authentication decision guide (provider OAuth + PKCE, direct Entra SSO, anonymous) and a troubleshooting matrix in `docs/authentication-paths.md`.
- Added ADR 0001 covering declarative-agent plug-ins vs. federated Copilot connectors and tool discovery modes.
- Added sanitized sample manifests and a sample skill under `samples/`.
