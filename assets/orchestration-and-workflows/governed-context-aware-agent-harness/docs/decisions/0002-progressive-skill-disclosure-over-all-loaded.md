# ADR 0002: Progressive skill disclosure over loading every skill

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture section 9.1](../architecture.md#91-progressive-skill-disclosure), [Run profiles](../architecture.md#7-run-profiles-and-control-matrix), [README](../../README.md), [ADR 0001](0001-harness-enforces-boundaries-not-transcripts.md)

## Context

The harness packages domain knowledge as **skills**. Each skill is a folder with a
`SKILL.md` that holds frontmatter (name, description, version, always-on flag) and
instructions. The reference catalog has eleven skills. Only some of them are relevant to
a given goal. Others are deliberate distractors: plausible but irrelevant capabilities.

There are two basic ways to give the model those skills:

- **All-loaded.** Put every skill's instructions and every tool into the context at the
  start of the episode.
- **Progressive.** Show the model a short catalog (name, description, version). Let it
  load instructions on demand. Expose a skill's tools only after it is loaded.

All-loaded is simpler and needs fewer turns. But:

- Context grows with the catalog, not with the task. Each new skill costs every episode.
- Irrelevant instructions compete with relevant ones. Distractor methods can leak into
  the answer.
- Every tool is callable from the first turn, so the model can act before it has read
  the method it is supposed to follow.
- The audit record cannot say which knowledge the answer actually used.

## Decision

1. **Progressive disclosure is the default** for the governed and offline profiles.
   `list_skills` returns only metadata. `load_skill` returns one skill's instructions and
   requires prior discovery in the same episode.
2. **Tools are unlocked by skill ownership.** The harness holds a map from domain tool to
   owning skill. A tool called before its skill is loaded is refused with
   "Load skills before use: ...".
3. **A small always-on set is preloaded** (the research policy and evidence-verification
   rules). Configuration cannot disable it.
4. **Availability is configurable, disclosure is not bypassable.** A skill removed from
   `available_skills` is not discoverable and cannot be loaded. The change applies from the
   next episode.
5. **Every load is ledgered** with the skill's version and content hash. The certificate
   lists the loaded skills, so reviewers and auditors see which knowledge was in play.
6. **All-loaded remains available only in the observe-only profile**, as a baseline for
   comparison and evaluation. Its output is an unverified preview and can never be
   approved.

## Consequences

### Positive

- Context scales with the task. New skills cost a catalog line until they are needed.
- The model reads the method before it can act on it.
- "Skills loaded per episode" becomes a cheap quality signal. Loading distractors points
  at weak descriptions or a confused model.
- The certificate pins exactly which skill versions shaped the artifact.

### Trade-offs

- Extra turns for discovery and loading.
- Correctness depends on description quality. A vague description causes missed or
  unnecessary loads, so descriptions need review like code.
- The model can fail to load a skill it needs. The gate then refuses the tool, which
  recovers but costs turns. Persistent misses need description or instruction fixes.
- The tool-to-skill ownership map is one more artifact to keep in sync with the skills.
- Agent framework support for adding tools mid-session may be experimental. Verify against
  the current Agent Framework docs, or expose all tools and rely on the gate's ownership
  check (the gate is the control either way).

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| All-loaded for every profile | Context grows with the catalog. Distractor instructions compete. Tools are usable before the method is read. Kept only as an observe-only baseline. |
| Harness auto-injects skills chosen by embedding similarity to the goal | Hides selection from the model and the audit. Similarity misses procedural needs (for example, date alignment is rarely "similar" to the question). Could be a future aid that pre-ranks the catalog. |
| One sub-agent per skill with a router | Adds coordination, identity, and evidence handoffs between agents. The reference is a single agent. Revisit when skills need different tools or identities ([known gaps](../architecture.md#13-known-gaps-and-honest-limitations)). |
| Bake skills into the system prompt or fine-tune them into the model | Not versioned per episode, not auditable, and expensive to change. |
