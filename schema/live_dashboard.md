# Read-only live dashboard

Schema version: 1. Owner: `simcoach.live.dashboard`, `simcoach.cli.dashboard` and
`docs/live-dashboard.mjs`. Write timing: private bridge run metadata once; bridge
status about once per second; HTTP display envelopes on authenticated requests.
Compatibility: a separate live page; existing offline debrief/export contracts
are unchanged. Unknown versions are refused. Raw pages, names, account details,
machine paths, source errors and arbitrary source fields are never HTTP payloads.

## Source and display envelope

The publisher explicitly selects one archive directory. It reads only run.json
and status.json, with a 256 KiB bound per file, path confinement, strict JSON and
an identity/status/identity check. It never scans for the newest archive. The
source ID is SHA256 of the validated exact run.json bytes, not its private path.
This binds reported source evidence; it does not prove current process liveness.

Required fields: schema_version, publisher_id (new 32-hex per bridge process),
frame (sample-loop integer), request_id (echoed request nonce), state, reason,
source_id, source_sequence, source_samples, source_at, source_age_s, expires_at,
valid_for_ms, telemetry, fuel_supervision, presence=not_inferred. New additive
feedback/operations panels are described below; absence means unavailable.
An additive `source_epoch` is a nonnegative integer scoped to the publisher.
It advances when the publisher observes a transition from fresh to nonfresh.
The additive `source_epoch_at` is the first admitted source timestamp in that
epoch, or null while awaiting new evidence. Missing epochs are supported for
older publishers. Fresh envelopes also include `continuity_generation` when
the collector supplies it. This is the integer scoped to the archive run.
A generation change withholds telemetry until an advancing pair is observed,
even if the publisher missed the degraded heartbeat.
Nullable fields: reason, source identity/progress/times and telemetry. Source
timestamps use UTC Unix seconds; age uses seconds. Remaining lifetime uses integer
milliseconds capped at 2000, assessed against the publisher monotonic deadline.
No client may interpret HTTP response time as a new source observation.

Publisher state enum: waiting/fresh/stale. Two advancing validated observations
are required for fresh, using ActivityEvidence source/clock/sequence/progression
checks. Repeated source evidence may remain fresh only to its original expiry.
Changed controls on identical source progress, view/context/source changes,
lap-counter regressions, source faults, clock changes and observer gaps revoke
freshness. More than 1s without publisher sampling clears readings even if the
HTTP responder still works. Missing or unreadable sources yield stale, not an
automatic source switch. Any nonfresh envelope has telemetry=null.

Telemetry is an allowlist: context {car,track,variation,session_state}, view
{identity=viewed_car_unconfirmed,index,count}, speed_mps, rpm, gear,
throttle_fraction, brake_fraction, steering_fraction, fuel_litres, lap,
lap_distance_m, lap_invalidated, simulator_last_lap_s, simulator_best_lap_s,
simulator_current_lap_s. Optional unavailable/malformed/out-of-range values remain
null. Numeric units are encoded in names, RPM, integer gear/lap and normalized
pedal fractions 0–1 and steering -1–1. Fuel is validated capacity times fraction.
Lap timing is simulator-reported, not captured lap timing, an Analysis Run,
leaderboard rank or a prediction. Legacy collectors without timing fields emit
nulls. Car/track labels are bounded display text; the page uses textContent.
The viewed slot is not authenticated driver identity. A fuel quantity is not a
fuel-supervision approval; neither simulator activity nor connection proves presence.

## Transport and receiver

The implementation binds only 127.0.0.1. GET `/` and `/live-dashboard.mjs` serve
fixed public assets. GET `/v1/telemetry` requires a process-local random bearer
code and X-ORC-Request-ID (16–64 alphanumeric/hyphen characters). No token in a
URL, CORS grant, control endpoint, dynamic file route or raw archive endpoint.
Host must be localhost/127.0.0.1; Origin, when supplied, must match Host. Responses
are no-store with a restrictive page policy and no-referrer. The server permits
at most 4 request threads, four queued connections, and a 2s socket timeout.
No authorization/request content is printed by the server. The page keeps its
code in memory only and clears the input after connection.

Receiver states: waiting/fresh/stale/disconnected. It validates schema, publisher,
nonce, source progress and numeric payloads. It subtracts the longer of the
monotonic and wall-clock request durations from the returned lifetime and keeps
the original deadline on repeated samples. An already expired response cannot
seed new progress. New source
or publisher histories require another advancing reading. The receiver also
reestablishes progress across a changed source epoch, context, viewed slot or
lap-counter regression, even if it missed the transition envelope. Expiry or malformed
data clears readings. Request failures disconnect immediately; no response for 3s
also disconnects, including when a response arrives before the next repaint.
The receiver checks wall/monotonic agreement against a fixed local offset
anchor and records the current clock epoch before each request. Regression
or disagreement over 500ms clears telemetry, feedback and operational state.
A response crossing a detected change is discarded even if repainting has
already re-established the clock anchor. Live readings then require two
usable observations. Hiding the page clears it and stops polling until visible.
It polls at 500ms with a 1500ms request timeout.

Loopback tests are not a verified Mac connection. A reviewed authenticated,
encrypted private route preserving the local-origin contract is still required.
No firewall rule, tunnel, Tailscale peer or public hosting is configured here.

## Private service evidence

The CLI requires a new output directory separate from the source archive. It
creates connection-code.txt, run.json and status.json. run requires mode
read_only_loopback, source archive locator, publisher ID, native process snapshot,
started_at, address, port, private_mac_route=NOT RUN and live_acceptance=NOT RUN.
Status requires state enum serving/stopped, at, sample count and false
mac_route_verified. The code and all service artifacts remain private. Duration
is bounded 0.1–86400s, or a stop file in this bridge output ends only this service.
Missing/stale status is not proof of process exit. Failure can leave an older
status; retain it and reconcile the actual process before another launch.

No collector, recorder, clock, radio, game, source artifact or active task is
modified. No restart or retry policy is added. Browser, synthetic service,
private route, real simulator and driver feedback are distinct acceptance scopes.

## Feedback and operations panels

Optional `--visit` selects its explicit dashboard-selection.json. The
[feedback contract](dashboard_feedback.md) defines historical segment cards,
all laps/exclusions and reviewed consistency targets. No arbitrary review notes,
paths or unapproved output are displayed. Response/client limit is 128 KiB.

Optional `--operations` selects one service directory. Its exact run bytes are
pinned. Archive binding and native PID/creation identity must match; fresh running
status/heartbeat and observed progress are required. Injected/replaced services
are refused. Armed/return_due also requires healthy supervision, matching source
hash, context, viewed index, continuity generation when available, and unexpired
source evidence from the current source epoch. The service sample cannot precede the first admitted timestamp
or be newer than the displayed sample, and its sequence must agree with that
ordering. The publisher clears the previous panel deadline on source
interruption. Legacy/embedded status without that
binding remains unverified. No approval, plan change, audio or restart occurs.

Operations required fields: state enum not_reported/unknown/unarmed/armed/
return_due/suspended, nullable reason, valid_for_ms. Usable states add service_id
and updated_at. Armed/return_due may include radio_delivery (healthy/degraded/
disabled/unknown) and false audible_delivery_verified; none proves hearing.
fuel_supervision mirrors this state. Bridge and browser expire it independently,
cap it by fresh telemetry, subtract read/transit delay, and never extend a repeated
service sample. Clock/gap/process/source/context/publication faults clear active
display. The return-due text is a fuel pit reminder, not a technique instruction
or a claim that the radio spoke it.
