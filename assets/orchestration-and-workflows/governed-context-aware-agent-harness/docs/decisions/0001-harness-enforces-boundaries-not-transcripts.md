# ADR 0001: The harness enforces boundaries, not transcripts

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [Evaluation](../evaluation-and-troubleshooting.md), [ADR 0002](0002-progressive-skill-disclosure-over-all-loaded.md), [ADR 0003](0003-approval-binds-to-verified-artifact-digest.md)

## Context

A common way to make an agent demo look reliable is to fix the path: script the exact
sequence of tool calls, or assert each run against a golden transcript. That works for a
recording. It fails with a live model, for several reasons:

- A live model varies the order and wording. It may load skills in a different order,
  retrieve before it loads, retry a computation, or phrase the plan differently. None of
  these variations are wrong, yet a transcript assertion rejects all of them.
- Engineers respond by tuning prompts until the transcript reappears. That trains the
  prompt to the test and hides the real question: did the run respect the controls that
  matter?
- A replayable transcript is easy to substitute for a live run. When the live model is
  unavailable, "fall back to the script" quietly turns a demo of governance into a
  playback. The output looks the same and proves nothing.
- The model's narration ("I verified the figures and the reviewer approved") is just more
  text. A transcript-shaped check cannot tell a narrated step from a performed step.

What we need to guarantee is not *how* the model got there, but that it could not get
there without passing specific boundaries.

## Decision

1. **Boundaries are harness-side state predicates.** Each one is checked on every tool call
   by the policy gate, before the tool runs:
   - a plan covering the mandatory stages is recorded before any domain tool
   - episode state is read before acting in the execute phase
   - a skill is discovered before it is loaded
   - the owning skill is loaded before its tools are used
   - an analysis exists before rendering, and rendering uses the latest analysis
   - verification passes before an approval request
   - approval is accepted, bound to the artifact digest, before memory is retained
2. **Ordering inside the boundaries is free.** The model chooses the order of discovery,
   loading, retrieval, and retries. It also chooses its wording. The harness does not care.
3. **Refusals are tool results, not exceptions to the episode.** A refused call returns the
   reason so the model can recover. Each refusal is also a `control_rejected` event and a
   progress event. Repeated identical calls trip the no-progress stop.
4. **Completion is admitted by the harness.** Narration never completes an episode.
   `complete_episode` is accepted only when every gate holds, and the harness then issues
   the certificate.
5. **Tests assert invariants and statuses, not transcripts.** The scripted model client
   exists to exercise the gate deterministically. The governed profile refuses a scripted
   client and never substitutes one for an unavailable live client
   (`model_client_unavailable`).

## Consequences

### Positive

- Live-model variation does not break the harness or its tests. Model and prompt changes
  are judged by the boundaries they respect, not by text similarity.
- A convincing narrative cannot stand in for a performed step, because admission depends on
  ledgered state.
- The same boundaries apply in rehearsal, evaluation, and production, so a passing
  self-test means something in production.
- New refusal types after a change are an early, cheap regression signal.

### Trade-offs

- You must name the boundaries explicitly and keep the gate in sync with the skills and
  tools. A boundary you forget to encode is not enforced.
- Episodes can take extra turns while the model recovers from refusals. Ceilings and the
  no-progress stop bound this, but they also need tuning.
- Debugging needs evidence tools (ledger, certificate, progress events) rather than a diff
  against a golden transcript.
- Free ordering is wrong for processes that genuinely require a fixed sequence. Those
  should use a deterministic workflow, with the model filling bounded steps.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Golden-transcript assertions | Brittle under live-model variation. Encourages prompt overfitting. Cannot tell narration from action. |
| Scripted replay as a fallback when the live model fails | Silently turns a governed run into playback. Explicitly refused ([evaluation scenario 10](../evaluation-and-troubleshooting.md#1-harness-scenario-catalog)). |
| Prompt-only instructions ("always plan first, always verify") | Advisory only. A model that ignores or forgets the instruction is not stopped. |
| A fixed workflow graph with the model filling slots | A good choice when the sequence truly is fixed. Too rigid for open-ended analysis where the needed skills and sources vary. Can be combined: a workflow step can host this harness. |
| Review only at the end, by a human reading the output | The reviewer sees a convincing artifact, not the evidence. See [ADR 0003](0003-approval-binds-to-verified-artifact-digest.md) and [verify-before-approve](../../../verify-before-approve/README.md). |
