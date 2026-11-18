# Capture source admission

`simcoach.live.capture_gate.CaptureGate` is a nonblocking source gate for the
Driver Visit host. It starts no recorder or simulator and owns no durable files.
The host may keep the explicitly declared presence clock running in menus while
capture waits for eligible source evidence.

```python
gate = CaptureGate(expected_context, max_age=3.0)
snapshot = gate.observe(status, source_id=verified_archive_identity,
                        now=utc_unix_seconds, elapsed=monotonic_elapsed_seconds)
```

`expected_context` uses the existing `supervisor.Context` car, track, variation
and session fields as a diagnostic expectation, not a capture restriction.
The caller obtains identity from the explicitly selected
archive; a directory path alone is not that identity. No newest-folder fallback
or implicit change of the fuel plan is performed.

Admission needs two fresh, supported version-14 observations with the same
observed context and advancing sample count, source timestamp, offset and even
sequence number. Supported session states are 1–6; supported game states are
1, 2, 3, 4, 6 and 7 (front end, playing, paused, ticking menu and replay).
Usable car/track labels are still required. Fresh garage, grid, menu and replay
observations can therefore start capture; motion and an active race are not
required. Normalized Vehicle State and existing lap eligibility retain their
meaning: preserving a replay or menu does not make it driving evidence.
Fuel fields and fuel
approval are deliberately independent: missing or untrusted fuel must not
prevent preserving an otherwise eligible capture.

The transient snapshot contains `health` (`healthy` or `degraded`), nullable
`reason`, `ready`, nullable `source_id`, and nullable UTC Unix `expires_at`.
It also exposes nullable `observed_context` (car, track, variation, session_state)
and nullable boolean `expected_context_match`. Mismatch is visible but cannot
block capture or change the fuel plan. Neither field verifies the driver's intent.
An identical admitted sample remains eligible only until its original source
expiry; polling cannot refresh it. Gaps, counter/source/clock changes,
stale/future/unsupported data and observed context changes revoke admission.
A changed valid context becomes a new baseline; its next advancing observation
can admit capture even when it differs from the original plan.
A usable observation at a reset is only the first sample of a new pair.
Invalid source data erases the pair. Restart always starts without admission.

This is an in-memory host API, not a new durable artifact contract. Hosts must
record its observation time and selected identity with any saved state, and
recheck current evidence before launching work. `ready` is source admission,
not evidence that a recorder has started, fuel is approved, a headset is ready,
or the driver is ready to drive. It also cannot establish a same-context reset
that the existing archive fields do not reveal.

The gate does not implement recorder startup deadlines, process ownership,
closure or recovery. Those remain host/worker responsibilities. Offline tests
exercise malformed data, progression, resets and expiry; no live validation or
timing guarantee is established by these tests.
