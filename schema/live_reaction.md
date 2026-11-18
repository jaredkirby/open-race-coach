# Shadow Live Reaction proposals

Schema version: 1.
Owner: `simcoach.live.reaction`.
Write timing: one SQLite transaction per observation decision; consume an event
identity before returning its proposal. Storage failure closes the detector.
Compatibility: reopening requires the exact stored version/configuration; no
baseline is restored and no proposal is replayed. A per-journal lock prevents
cooperating concurrent writers. A changed configuration needs a separate journal.

## Required inputs and units

An explicit expected Context, journal path, source identity and observation clocks
are required. Source identity is the lowercase SHA-256 of the selected archive's
`run.json`. `observe()` takes a supported collector status v1, UTC Unix `now`
and a monotonic `elapsed`, in seconds. Layout version must be 14, lap flag a strict
boolean, sequence even, and game/race/pit must be 2/2/0. Raw lap is an integer
simulator counter, not a Lap ID or evidence of a Valid Lap. Fuel fields are unused.

Mode enum: `replay`, `shadow`; both produce **no audio**. Timestamp-basis enum:
`archive_sample`, `collector_heartbeat`. For archived replay, reconstruct the status
envelope using the original sample timestamp/offset and preserve private provenance.
For actual status input, `updated_at` is heartbeat time, not the latest sample time.
The detector cannot recover exact event timing or unobserved transitions from it.

## Edge and refusal contract

Only an observed false-to-true flag edge in the same source, expected context,
viewed slot and raw lap can create a proposal. Shadow mode requires a supported
active participant observation consistent with the current car and race/pit
fields. The viewed slot does not identify the local driver. Historical replay
may lack both slot observations and reports those views as null. A change or
disagreement in slot evidence resets the baseline. Initial true is insufficient. Source time, offset,
count and sequence must advance; identical repeated observations do not create
edges. A source/lap change, menu/pit/terminal race state, malformed field, unsupported
layout, stale/future timestamp, regressed clock/counter or gap resets the baseline.
Following rejection, a new valid false observation is required before a true edge.
A changed archive continuity generation invalidates the edge baseline even
when the timestamp, offset, sample count and sequence all advance. A present
generation is included in current edge evidence and identity. Legacy replay
without that field keeps its original event identity.

Adjacent source UTC and offset increments must agree within 0.5 seconds.
Default freshness and gap bounds are 2 seconds; expiry is the edge source observation
plus 2 seconds. These are prototype parameters, not measured response guarantees.
Expiry never refreshes on repeated polling, reopening or output assessment.

## Durable journal

SQLite contains `metadata(config TEXT NOT NULL)`,
`decisions(sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL)` and
`consumed(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)`.
Decision required fields: `schema_version`, `at`, `elapsed`, `mode`,
`timestamp_basis`, `reason`, `observation`, `proposal`.
Observation and proposal are nullable after refusal/no edge. Reasons are controlled
codes, including `false_baseline`, `initial_true_without_baseline`, `no_edge`,
`duplicate_observation`, `flag_false_to_true`, `edge_expired`, `already_consumed`,
and input/reset refusal codes. Decisions preserve unsuccessful and insufficient
observations. No raw page or participant name is stored.

Proposal required fields: `id`, `kind`, `text`, `mode`, `timestamp_basis`, `before`,
`edge`, `observation_epoch`, `detected_at`, `decision_at`, `expires_at`, `delivery`,
`causal_claim`. Kind enum is only `lap_invalidated`; text is only
“Lap marked invalid.” `causal_claim` is always null, delivery always `NOT RUN`.
Before/edge contain source hash, source UTC Unix time, run offset, observation count,
sequence, Context, raw lap and flag. New observations add nullable `view` with
the checked index, participant count and car. Older observations may omit it.
Observer epoch combines a fresh process-instance
ID and reset counter; it is not simulator session identity.

Event identity hashes the flag kind and edge source/context/raw lap/sequence/time/
offset coordinates. It excludes wrapper observation count and optional viewed-slot
metadata so a renumbered or enriched replay cannot reissue the same captured
event. Absolute source coordinates
distinguish observed resets that reuse lap and sequence counters. Events are consumed
transactionally before exposure; reopening never retries uncertain prior output.
Expired supported edges are consumed too, with their suppressed decision retained.
Physical delivery remains unattempted regardless of this consumed state.

## Optional output assessment and privacy

`assess_optional_output()` is pure, with explicit boolean mute, urgent-output and
workload-clear guards. Result enum: `expired_or_future`, `muted`,
`urgent_output_has_priority`, `workload_unknown`, `eligible_for_shadow_review`.
It never queues, plays or authorizes real audio; integration needs the single radio
owner and fresh evidence rechecks. This proposal has no Coaching Instruction,
Coach Refinement, penalty reason, caught-slide inference or driving-quality judgment.
Keep operating journals and archived replay inputs private. A missing true interval
between sparse observations is unobservable, not an event to reconstruct.
