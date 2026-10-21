# Changelog

## 0.2.0 - 2026-10-21

- Rename the asset from `agent-path-to-production` to
  `agent-production-readiness` and move it to the `agentops-and-readiness`
  category.
- Reframe the guide as a team-run production-readiness review rather than a
  delivery-program playbook. Remove coordinator, specialist-routing, clinic, and
  harvesting language; program process is out of scope for this repository.
- Rename `docs/runbook.md` to `docs/readiness-review.md` and restructure it as
  Frame, Diagnose, Unblock, Release and operate, and Feed learning back.
- Rename "field signals" to "common signals" in the pillar chapters.

## 0.1.0 - 2026-10-12

- Add an experimental agent path-to-production delivery workflow.
- Add six-lens triage, specialist routing, three initial delivery plays, and
  evidence-led release and operating gates.
- Add a private-workload readiness-record template and public harvesting rules.
- Add six pillar chapters under `docs/pillars/` (AI CoE & Governance, AI Landing
  Zones, Agent Ops, AI Security & Observability, Solution Optimization, and
  Proven Patterns for Agent Architectures) with an index. Each chapter covers
  the pillar definition, generalized field signals, entry questions, an approach
  linked to the runbook plays, a readiness evidence and gate table, common
  pitfalls, collaboration boundaries, and public references.
- Link the runbook triage table and overview lens table to the pillar chapters,
  and align lens names with the chapter titles.
