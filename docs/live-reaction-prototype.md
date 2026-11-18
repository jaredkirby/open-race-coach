# Lap-invalidation reaction bench

The isolated `LapInvalidationDetector` produces shadow proposals for one supported
event: an observed simulator lap flag changing from false to true. The phrase is
“Lap marked invalid.” It does not identify a cut, spin, penalty reason or caught
slide, and gives no Coaching Instruction. It is not connected to the visit
coordinator, simulator, models or radio.

Use an explicit expected Context and private SQLite journal. Feed supported
collector statuses with their source identity and both UTC and monotonic observation
clocks. Historical replay uses original source timestamps as virtual time; never
rewrite old observations to look live. Label reconstructed status envelopes and
retain source segment hashes in private provenance. Choose `archive_sample` only
when timestamps are original capture observations; use `collector_heartbeat` for
actual status snapshots.

The detector needs a false baseline followed by true in the same active context,
source, viewed slot and raw lap, with advancing sample evidence and advancing
source clocks that agree. Shadow proposals require a consistent active viewed
slot. Historical replay can retain a flag-only observation with missing slot
evidence. Neither establishes player identity. Initial true, missing or
malformed fields, stale data, gaps, counter resets, pits and menus suppress the
edge. Following a reset, it needs a new false observation. It persists proposals
and refusals before returning them; reopening retains consumed event IDs but
starts without a baseline. A failed output is retained as evidence, not silently
retried. The caller closes the detector when finished.

`assess_optional_output()` exercises optional-output guards without invoking audio.
Mute, urgent operational output, uncertain workload or expired evidence prevents
eligibility. Even an eligible result means only “eligible for shadow review.”
Future audible use must go through the existing single radio owner with fresh
checks; this component does not grant delivery authority.

The collector targets 60 Hz capture but publishes `status.json` only about once per
second. Frequent polling cannot reconstruct missing samples, prove a fast response,
or detect a brief flag pulse between updates. Archived capture timestamps support
an offline observation-time exercise; they still do not measure simulator-event
generation time, detector-to-headset latency or what the driver heard. No subsecond
reaction target or caught-slide detector is validated here.

Synthetic tests cover edges, refusals, persistent deduplication, storage failure
and output-guard assessment. A silent replay of a saved flag edge used the next
captured pit-mode observation at the final output check and refused the phrase.
The same bench tests mute, unknown workload, priority and a phrase that would
outlast the original deadline. Existing private captures may also be replayed;
captures with no positive edge remain insufficient positive evidence. Actual
reaction usefulness, distraction, game-warning duplication and live/audio behavior
are **NOT RUN**. No additional driving maneuver is needed for this bench.

See the [Live Reaction schema](../schema/live_reaction.md) for the complete journal,
identity, timing and refusal contract.
